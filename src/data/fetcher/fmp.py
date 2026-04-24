"""FMPFetcher — Financial Modeling Prep data source facade.

Step 3 (2026-04-23) of the refactor ended here: this class is a thin composition
layer over the topic modules (prices, fundamentals, news, universes, realtime).
All network behavior (timeout + retry + cache) lives in the shared FMPClient
at src/data/fetcher/client.py.

Kept as a class — not flattened to module-level functions — because
DataSourceManager selects between concrete DataSource implementations at
runtime, and the user wants that hook preserved for future sources
(WRDS, Polygon, ...).
"""

from __future__ import annotations

import logging
from typing import List, Optional

import pandas as pd

from src.data.fetcher.base import BaseDataFetcher, DataSource
from src.data.fetcher.client import FMPClient
from src.data.fetcher import (
    fundamentals as fundamentals_mod,
    news as news_mod,
    prices,
    realtime,
    universes,
)

logger = logging.getLogger(__name__)


class FMPFetcher(BaseDataFetcher, DataSource):
    """Financial Modeling Prep data fetcher — composes topic modules."""

    def __init__(self, cache_dir: str = "./data/cache"):
        super().__init__(cache_dir)
        self.api_key = self._get_api_key()
        self.offline_mode = not bool(self.api_key)
        self.client = FMPClient(self.api_key, self.data_store)
        self._openai_client = None
        self._openai_api_key: Optional[str] = None
        self._sentiment_model: Optional[str] = None
        self._sentiment_request_timeout: int = 30
        self._init_sentiment_settings()

    def is_available(self) -> bool:
        """Always True — FMPFetcher can serve cached payloads offline."""
        return True

    # ── auth / sentiment (kept here — these bridge the config layer) ────────

    def _get_api_key(self) -> Optional[str]:
        try:
            from src.config.settings import get_config
            config = get_config()
            if config.fmp.api_key:
                return config.fmp.api_key.get_secret_value()
            return None
        except Exception as e:
            logger.error(f"Failed to get FMP API key: {e}")
            return None

    def _init_sentiment_settings(self) -> None:
        try:
            from src.config.settings import get_config
            config = get_config()
            openai_cfg = getattr(config, 'openai', None)
            if openai_cfg and openai_cfg.api_key:
                self._openai_api_key = openai_cfg.api_key.get_secret_value()
                self._sentiment_model = openai_cfg.model
                self._sentiment_request_timeout = getattr(openai_cfg, 'request_timeout', 30) or 30
            else:
                self._openai_api_key = None
                self._sentiment_model = None
        except Exception as exc:
            logger.debug(f"Failed to initialize OpenAI settings: {exc}")
            self._openai_api_key = None
            self._sentiment_model = None

    def _get_openai_client(self):
        """Lazily instantiate the OpenAI client for sentiment analysis."""
        if not self._openai_api_key:
            return None
        if self._openai_client is not None:
            return self._openai_client
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("openai package not installed; sentiment analysis unavailable")
            return None
        try:
            self._openai_client = OpenAI(api_key=self._openai_api_key)
        except Exception as exc:
            logger.warning(f"Failed to initialize OpenAI client: {exc}")
            self._openai_client = None
        return self._openai_client

    # ── delegates to topic modules ──────────────────────────────────────────

    def get_sp500_components(self, date: str = None) -> pd.DataFrame:
        return universes.get_sp500_components(self.client, self.data_store, date)

    def get_price_data(self, tickers: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
        return prices.get_price_data(self.client, self.data_store, tickers, start_date, end_date)

    def get_fundamental_data(self, tickers: pd.DataFrame, start_date: str, end_date: str,
                             align_quarter_dates: bool = False) -> pd.DataFrame:
        return fundamentals_mod.get_fundamental_data(
            self.client, self.data_store, tickers, start_date, end_date, align_quarter_dates,
        )

    def get_news(self, ticker: str, from_date: str, to_date: str,
                 analyze_sentiment: bool = False,
                 sentiment_model: Optional[str] = None,
                 force_refresh: bool = False) -> pd.DataFrame:
        """OpenAI client is constructed lazily here and passed through so
        news.py stays free of the openai package dependency."""
        return news_mod.get_news(
            self.client, self.data_store, ticker, from_date, to_date,
            analyze_sentiment=analyze_sentiment,
            openai_client=self._get_openai_client(),
            sentiment_model=sentiment_model or self._sentiment_model,
            force_refresh=force_refresh,
        )

    def get_active_trading_list(self):
        return realtime.get_active_trading_list(self.client)

    def get_realtime_single_price_data(self, ticker: str):
        return realtime.get_realtime_single_price(self.client, ticker)

    def get_realtime_batch_price_data(self, tickers: List[str]):
        return realtime.get_realtime_batch_prices(self.client, tickers)
