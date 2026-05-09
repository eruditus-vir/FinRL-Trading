"""SEC filings fetcher — FMP `/sec-filings-search/symbol`.

Step 4 Component 7 (2026-05-08). Second Class C (paginated) module after
ownership.py's insider_trading. Same pattern: per-ticker page-by-page
pagination + per-ticker `sec_filings_fetch_log` checkpoint for crash-safe
resume.

NOTE: Unlike `/insider-trading/search`, this endpoint REQUIRES `from` and
`to` date params. We use a single fixed window (default 2015-01-01 → today)
and paginate within it via `page=N`. UNIQUE 4-tuple handles cross-run dedup.

Form-type coverage is unfiltered: Form 4 (insider) through 8-K (events),
10-K/10-Q (financials), DEF 14A (proxies), 13D/13G (ownership), 144
(restricted-stock), S-1/S-3 (issuance), etc. Filtering is a query-time
concern, not a save-time one.

Note on overlap with ownership.insider_trading: Form 4 filings appear in
BOTH tables. insider_trading has parsed transactions (who/what/qty/price);
sec_filings has filing-level metadata (date, link). Different schemas,
different uses, both retained.

Public API:
- `fetch_sec_filings_page(ticker, page, from_date, to_date, client, data_store)`
- `fetch_sec_filings(ticker, from_date, to_date, client, data_store, resume, max_pages)`
- `fetch_all_sec_filings(tickers, from_date, to_date, client, data_store, resume, max_pages)`
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

SEC_FILINGS_PAGE_LIMIT = 100  # FMP stable API cap
SEC_FILINGS_DEFAULT_FROM = "2015-01-01"

SEC_FILINGS_COLUMNS = [
    "ticker", "cik", "filing_date", "accepted_date",
    "form_type", "link", "final_link",
]


# ── Helpers ────────────────────────────────────────────────────────────────


def _empty_filings() -> pd.DataFrame:
    return pd.DataFrame(columns=SEC_FILINGS_COLUMNS)


def _get_data_store(data_store=None):
    if data_store is not None:
        return data_store
    from src.data.data_store import get_data_store
    return get_data_store()


def _get_client(client=None, data_store=None):
    if client is not None:
        return client
    from src.data.fetcher.client import FMPClient
    from src.config.settings import get_config
    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    return FMPClient(api_key, _get_data_store(data_store))


def _normalize_sec_filings(records: Iterable[dict]) -> pd.DataFrame:
    """Turn FMP /sec-filings-search/symbol dicts into our column shape.
    Drops rows missing symbol/filingDate. Stores accepted_date verbatim
    (full timestamp) but truncates filing_date to YYYY-MM-DD."""
    out = []
    for r in records or []:
        sym = r.get("symbol")
        fdate = r.get("filingDate")
        if not sym or not fdate:
            continue
        out.append({
            "ticker": sym,
            "cik": r.get("cik"),
            "filing_date": str(fdate)[:10],
            "accepted_date": str(r["acceptedDate"]) if r.get("acceptedDate") else None,
            "form_type": r.get("formType"),
            "link": r.get("link"),
            "final_link": r.get("finalLink"),
        })
    if not out:
        return _empty_filings()
    return pd.DataFrame(out)


# ── Public API ─────────────────────────────────────────────────────────────


def _today() -> str:
    return pd.Timestamp.today().strftime("%Y-%m-%d")


def fetch_sec_filings_page(
    ticker: str,
    page: int,
    from_date: str = SEC_FILINGS_DEFAULT_FROM,
    to_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Fetch one page of /sec-filings-search/symbol for a ticker over the
    [from_date, to_date] window. The endpoint REQUIRES from/to params.
    Returns normalized DataFrame; may be empty. Does NOT save."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)
    to_date = to_date or _today()

    if c.offline_mode:
        logger.info(f"Offline mode: skip /sec-filings-search/symbol for {ticker} p={page}")
        return _empty_filings()

    try:
        # FMP requires `from` and `to` params; `from` is a Python keyword
        # so kwargs trick via dict-spread.
        raw = c.get_json("sec-filings-search/symbol",
                         symbol=ticker, page=page, limit=SEC_FILINGS_PAGE_LIMIT,
                         **{"from": from_date, "to": to_date})
    except Exception as exc:
        logger.warning(f"/sec-filings-search/symbol {ticker} p={page} failed: {exc}")
        return _empty_filings()

    return _normalize_sec_filings(raw)


def fetch_sec_filings(
    ticker: str,
    from_date: str = SEC_FILINGS_DEFAULT_FROM,
    to_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
    resume: bool = True,
    max_pages: int = 200,
) -> pd.DataFrame:
    """Paginate /sec-filings-search/symbol over [from_date, to_date] until
    empty page or max_pages.

    Saves after each page and updates `sec_filings_fetch_log` checkpoint.
    If `resume=True`, starts from `last_page + 1`. Empty-page sentinel marks
    the ticker fully fetched so subsequent resume runs skip it.

    Returns rows newly saved this call (empty if no new pages).
    """
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)
    to_date = to_date or _today()

    if c.offline_mode:
        return ds.get_sec_filings(ticker=ticker)

    start_page = 0
    if resume:
        progress = ds.get_sec_fetch_progress(ticker)
        if progress and progress.get("last_page") is not None:
            start_page = progress["last_page"] + 1

    saved_frames: list[pd.DataFrame] = []
    last_page_done = start_page - 1

    for page in range(start_page, max_pages):
        df = fetch_sec_filings_page(ticker, page, from_date, to_date,
                                    client=c, data_store=ds)
        if df.empty:
            ds.update_sec_fetch_progress(ticker, last_page_done, None)
            break

        ds.save_sec_filings(df)
        last_filing = df["filing_date"].max() if "filing_date" in df.columns else None
        ds.update_sec_fetch_progress(ticker, page, last_filing)
        saved_frames.append(df)
        last_page_done = page

        if len(df) < SEC_FILINGS_PAGE_LIMIT:
            break
    else:
        logger.warning(
            f"/sec-filings-search/symbol {ticker}: hit max_pages={max_pages} cap; "
            f"may need to bump --max-pages and re-run with --no-resume"
        )

    if not saved_frames:
        return _empty_filings()
    return pd.concat(saved_frames, ignore_index=True)


def fetch_all_sec_filings(
    tickers: Optional[List[str]] = None,
    from_date: str = SEC_FILINGS_DEFAULT_FROM,
    to_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
    resume: bool = True,
    max_pages: int = 200,
) -> pd.DataFrame:
    """Pull SEC filings for the universe. Per-ticker failures log a warning;
    batch continues. Returns concatenation of all newly-saved rows."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)
    to_date = to_date or _today()

    if tickers is None:
        from src.data.fetcher.universes import get_universe_tickers
        tickers = sorted(get_universe_tickers(ds))

    frames = []
    for t in tickers:
        try:
            df = fetch_sec_filings(t, from_date=from_date, to_date=to_date,
                                   client=c, data_store=ds,
                                   resume=resume, max_pages=max_pages)
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            logger.warning(f"fetch_all_sec_filings: {t} failed: {exc}")

    if not frames:
        return _empty_filings()
    return pd.concat(frames, ignore_index=True)
