"""News fetcher — FMP /news/stock with optional GPT sentiment annotation.

Step 3.6 of the 2026-04-23 refactor: relocated intact from FMPFetcher.get_news
and its three sentiment helpers. Network call now goes through FMPClient for
uniform timeout+retry. The `analyze_sentiment=False` path is the one used by
bulk pulls; `analyze_sentiment=True` still works when an OpenAI client is
available, preserved byte-for-byte.

Public API:
- `get_news(client, data_store, ticker, from_date, to_date, ...)`

The sentiment helpers stay module-private. The OpenAI client is injected by
the caller (FMPFetcher) rather than instantiated here — news.py has no direct
dependency on openai's Python package.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.data.fetcher.client import FMPClient

logger = logging.getLogger(__name__)


# ── sentiment helpers (private) ─────────────────────────────────────────────


def _parse_sentiment_response(content: str) -> Dict[str, Any]:
    """Parse a GPT response into a `{sentiment, confidence}` payload."""
    if not content:
        return {}
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            sentiment = str(parsed.get('sentiment') or parsed.get('label') or '').lower()
            if sentiment in {'positive', 'neutral', 'negative'}:
                confidence = parsed.get('confidence') or parsed.get('score')
                try:
                    confidence_val = float(confidence) if confidence is not None else None
                except (TypeError, ValueError):
                    confidence_val = None
                return {'sentiment': sentiment, 'confidence': confidence_val}
    except json.JSONDecodeError:
        pass

    lowered = content.strip().lower()
    for sentiment in ('positive', 'neutral', 'negative'):
        if sentiment in lowered:
            return {'sentiment': sentiment, 'confidence': None}
    return {}


def _annotate_sentiment(
    articles: List[Dict[str, Any]],
    openai_client,
    sentiment_model: Optional[str],
) -> None:
    """Annotate each article dict in-place with `sentiment`, `sentiment_confidence`,
    `sentiment_model` when the OpenAI client + model are configured.

    Called only when `analyze_sentiment=True`. Defensive against a missing
    client or model — silently returns without modifying input.
    """
    if not articles:
        return
    if not openai_client:
        logger.info("OpenAI client unavailable, skip sentiment analysis")
        return
    if not sentiment_model:
        logger.info("Sentiment model not configured, skip sentiment analysis")
        return

    for article in articles:
        title = (article.get('title') or '').strip()
        body = (article.get('text') or article.get('body') or '').strip()
        if not title and not body:
            continue
        if len(body) > 1500:
            body = body[:1500]

        prompt = (
            "请阅读以下新闻并判断整体情绪是 positive、neutral 还是 negative。"
            " 仅返回 JSON，如 {\"sentiment\": \"neutral\", \"confidence\": 0.65}。"
            f"\n标题: {title}\n内容: {body}"
        )

        try:
            response = openai_client.chat.completions.create(
                model=sentiment_model,
                messages=[
                    {"role": "system", "content": "你是一名金融新闻情绪分析助手，只输出JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=60,
            )
            message = response.choices[0].message.content.strip()
            parsed = _parse_sentiment_response(message)
            if parsed.get('sentiment'):
                article['sentiment'] = parsed['sentiment']
                article['sentiment_confidence'] = parsed.get('confidence')
                article['sentiment_model'] = sentiment_model
        except Exception as exc:
            logger.debug(f"Sentiment analysis failed for news '{title}': {exc}")


def _ensure_news_sentiment(
    data_store,
    ticker: str,
    start_date: str,
    end_date: str,
    df: pd.DataFrame,
    openai_client,
    sentiment_model: Optional[str],
) -> pd.DataFrame:
    """Fill sentiment for cached rows that are missing it."""
    if df.empty or 'sentiment' not in df.columns:
        return df

    mask_missing = df['sentiment'].isna() | (df['sentiment'].astype(str).str.strip() == '')
    if not mask_missing.any():
        return df

    articles = df[mask_missing].to_dict('records')
    _annotate_sentiment(articles, openai_client, sentiment_model)

    updated = False
    for article in articles:
        sentiment = article.get('sentiment')
        if sentiment:
            data_store.update_news_sentiment(
                ticker,
                article.get('published_datetime'),
                sentiment,
                article.get('sentiment_confidence'),
                article.get('sentiment_model') or sentiment_model,
            )
            updated = True

    if updated:
        return data_store.get_news_articles(ticker, start_date, end_date)
    return df


# ── public API ──────────────────────────────────────────────────────────────


def get_news(
    client: FMPClient,
    data_store,
    ticker: str,
    from_date: str,
    to_date: str,
    analyze_sentiment: bool = False,
    openai_client=None,
    sentiment_model: Optional[str] = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch news articles with local caching. Logic mirrors the original
    FMPFetcher.get_news exactly; only the HTTP call is routed through FMPClient
    for uniform timeout+retry."""
    if not ticker:
        raise ValueError("ticker is required for get_news")

    try:
        start_date = pd.to_datetime(from_date).strftime('%Y-%m-%d')
        end_date = pd.to_datetime(to_date).strftime('%Y-%m-%d')
    except Exception as exc:
        raise ValueError(f"Invalid from/to date: {exc}") from exc

    if pd.to_datetime(start_date) > pd.to_datetime(end_date):
        raise ValueError("from_date must be earlier than to_date")

    cached = data_store.get_news_articles(ticker, start_date, end_date)
    logger.info(f"Loaded {len(cached)} cached news rows for {ticker} ({start_date} -> {end_date})")

    if client.offline_mode or not client.api_key:
        logger.info("Offline or missing API key: returning cached news only")
        if analyze_sentiment:
            cached = _ensure_news_sentiment(
                data_store, ticker, start_date, end_date, cached,
                openai_client, sentiment_model,
            )
        return cached

    missing_ranges = (
        [(start_date, end_date)]
        if force_refresh
        else data_store.get_missing_news_ranges(ticker, start_date, end_date)
    )

    new_articles: List[Dict[str, Any]] = []
    completed_ranges: List[Tuple[str, str, int]] = []
    if missing_ranges:
        for range_start, range_end in missing_ranges:
            try:
                payload = client.get_json(
                    "news/stock",
                    symbols=ticker, **{"from": range_start, "to": range_end},
                )
                if isinstance(payload, dict) and 'news' in payload:
                    news_items = payload['news']
                elif isinstance(payload, list):
                    news_items = payload
                else:
                    news_items = []

                logger.info(
                    f"Fetched {len(news_items)} news entries for {ticker} "
                    f"({range_start}->{range_end})"
                )

                for item in news_items:
                    item.setdefault('symbol', ticker)
                    if not item.get('publishedDate'):
                        item['publishedDate'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

                new_articles.extend(news_items)
                completed_ranges.append((range_start, range_end, len(news_items)))
            except Exception as exc:
                logger.warning(
                    f"Failed to fetch news for {ticker} ({range_start}->{range_end}): {exc}"
                )
    else:
        logger.info(f"News cache already covers requested range for {ticker}")

    if new_articles:
        if analyze_sentiment:
            _annotate_sentiment(new_articles, openai_client, sentiment_model)
        data_store.save_news_articles(ticker, new_articles)

    if completed_ranges:
        for range_start, range_end, count in completed_ranges:
            data_store.save_news_fetch_range(ticker, range_start, range_end, count)

    result = data_store.get_news_articles(ticker, start_date, end_date)
    if analyze_sentiment:
        result = _ensure_news_sentiment(
            data_store, ticker, start_date, end_date, result,
            openai_client, sentiment_model,
        )
    return result
