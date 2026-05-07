"""Ownership fetcher — FMP `/insider-trading/search` (paginated) + `/shares-float`.

Step 4 Component 3 (2026-04-26). First Class C (paginated date-based) module
per docs/migration/09_step4_build_plan.md. Two endpoints, one module:

- Insider trading: paginated (?page=N&limit=100). Crash-safe via per-ticker
  `insider_trading_fetch_log` checkpoint — bulk pulls resume from `last_page+1`
  after a crash instead of restarting from page 0.
- Shares float: 1 call per ticker → 1 row. Stored as accumulating history
  (UNIQUE(ticker, snapshot_date)) so weekly re-runs build a dilution timeline.

Public API:
- `fetch_insider_trading_page(ticker, page, client, data_store)`
- `fetch_insider_trading(ticker, client, data_store, resume, max_pages)`
- `fetch_shares_float(ticker, client, data_store)`
- `fetch_all_insider_trading(tickers, client, data_store, resume, max_pages)`
- `fetch_all_shares_float(tickers, client, data_store)`
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

INSIDER_PAGE_LIMIT = 100  # FMP stable API cap on /insider-trading/search

INSIDER_COLUMNS = [
    "ticker", "filing_date", "transaction_date",
    "reporting_cik", "company_cik", "transaction_type",
    "securities_owned", "securities_transacted", "price",
    "reporting_name", "type_of_owner",
    "acquisition_or_disposition", "direct_or_indirect",
    "form_type", "security_name", "url",
]

SHARES_FLOAT_COLUMNS = [
    "ticker", "snapshot_date",
    "free_float", "float_shares", "outstanding_shares", "source",
]


# ── Helpers ────────────────────────────────────────────────────────────────


def _empty_insider() -> pd.DataFrame:
    return pd.DataFrame(columns=INSIDER_COLUMNS)


def _empty_shares_float() -> pd.DataFrame:
    return pd.DataFrame(columns=SHARES_FLOAT_COLUMNS)


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


def _normalize_insider(records: Iterable[dict], ticker: str) -> pd.DataFrame:
    """Turn FMP insider-trading dicts into our column shape."""
    out = []
    for r in records or []:
        if not r.get("symbol") or not r.get("filingDate"):
            continue
        out.append({
            "ticker": r.get("symbol"),
            "filing_date": str(r.get("filingDate"))[:10],
            "transaction_date": str(r.get("transactionDate"))[:10] if r.get("transactionDate") else None,
            "reporting_cik": r.get("reportingCik"),
            "company_cik": r.get("companyCik"),
            "transaction_type": r.get("transactionType"),
            "securities_owned": r.get("securitiesOwned"),
            "securities_transacted": r.get("securitiesTransacted"),
            "price": r.get("price"),
            "reporting_name": r.get("reportingName"),
            "type_of_owner": r.get("typeOfOwner"),
            "acquisition_or_disposition": r.get("acquisitionOrDisposition"),
            "direct_or_indirect": r.get("directOrIndirect"),
            "form_type": r.get("formType"),
            "security_name": r.get("securityName"),
            "url": r.get("url"),
        })
    if not out:
        return _empty_insider()
    df = pd.DataFrame(out)
    for col in ("securities_owned", "securities_transacted", "price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _normalize_shares_float(records: Iterable[dict]) -> pd.DataFrame:
    """Turn FMP shares-float dicts into our column shape. Normalizes date to YYYY-MM-DD."""
    out = []
    for r in records or []:
        sym = r.get("symbol")
        date = r.get("date")
        if not sym or not date:
            continue
        out.append({
            "ticker": sym,
            "snapshot_date": str(date)[:10],
            "free_float": r.get("freeFloat"),
            "float_shares": r.get("floatShares"),
            "outstanding_shares": r.get("outstandingShares"),
            "source": r.get("source"),
        })
    if not out:
        return _empty_shares_float()
    df = pd.DataFrame(out)
    for col in ("free_float", "float_shares", "outstanding_shares"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # FMP returns placeholder snapshots (float_shares = 0 or NULL) for
    # delisted/acquired tickers (e.g. TWTR-taken-private-day, ABMD post-J&J,
    # FB-renaming gotcha). These carry no signal — drop them.
    return df[df["float_shares"].notna() & (df["float_shares"] > 0)].reset_index(drop=True)


# ── Insider trading ────────────────────────────────────────────────────────


def fetch_insider_trading_page(
    ticker: str,
    page: int,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Fetch one page of `/insider-trading/search` for a ticker. Returns
    normalized DataFrame; may be empty. Does NOT save — caller is responsible."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /insider-trading/search for {ticker} p={page}")
        return _empty_insider()

    try:
        raw = c.get_json("insider-trading/search",
                         symbol=ticker, page=page, limit=INSIDER_PAGE_LIMIT)
    except Exception as exc:
        logger.warning(f"/insider-trading/search {ticker} p={page} failed: {exc}")
        return _empty_insider()

    return _normalize_insider(raw, ticker)


def fetch_insider_trading(
    ticker: str,
    client: Any = None,
    data_store: Any = None,
    resume: bool = True,
    max_pages: int = 200,
) -> pd.DataFrame:
    """Paginate `/insider-trading/search` until empty page or max_pages.

    Saves after each page and updates `insider_trading_fetch_log` checkpoint —
    a crash mid-bulk leaves a consistent state. If `resume=True`, starts from
    `last_page + 1` (skipping pages already fully written). On finish (clean
    end-of-data), writes a sentinel page number so resume on a fully-fetched
    ticker is a no-op until you pass `resume=False` for a full re-scan.

    Returns rows newly saved this call (empty if no new pages); use
    `data_store.get_insider_trading(ticker=...)` for the full history.
    """
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        return ds.get_insider_trading(ticker=ticker)

    start_page = 0
    if resume:
        progress = ds.get_insider_fetch_progress(ticker)
        if progress and progress.get("last_page") is not None:
            start_page = progress["last_page"] + 1

    saved_frames: list[pd.DataFrame] = []
    last_page_done = start_page - 1

    for page in range(start_page, max_pages):
        df = fetch_insider_trading_page(ticker, page, client=c, data_store=ds)
        if df.empty:
            # End of data — record sentinel so subsequent resume runs skip re-fetch.
            ds.update_insider_fetch_progress(ticker, last_page_done, None)
            break

        ds.save_insider_trading(df)
        last_filing = df["filing_date"].max() if "filing_date" in df.columns else None
        ds.update_insider_fetch_progress(ticker, page, last_filing)
        saved_frames.append(df)
        last_page_done = page

        if len(df) < INSIDER_PAGE_LIMIT:
            break  # partial page — no more data
    else:
        logger.warning(
            f"/insider-trading/search {ticker}: hit max_pages={max_pages} cap; "
            f"may need to bump --max-pages and re-run with --no-resume"
        )

    if not saved_frames:
        return _empty_insider()
    return pd.concat(saved_frames, ignore_index=True)


# ── Shares float ───────────────────────────────────────────────────────────


def fetch_shares_float(
    ticker: str,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Fetch current shares-float snapshot for a ticker. Saves 1 row.
    UNIQUE(ticker, snapshot_date) handles same-day re-runs (no dup)."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        return ds.get_shares_float(ticker=ticker)

    try:
        raw = c.get_json("shares-float", symbol=ticker)
    except Exception as exc:
        logger.warning(f"/shares-float {ticker} failed: {exc}")
        return _empty_shares_float()

    df = _normalize_shares_float(raw)
    if df.empty:
        return _empty_shares_float()

    ds.save_shares_float(df)
    return ds.get_shares_float(ticker=ticker,
                               start_date=df["snapshot_date"].min(),
                               end_date=df["snapshot_date"].max())


# ── Batch convenience ─────────────────────────────────────────────────────


def fetch_all_insider_trading(
    tickers: Optional[List[str]] = None,
    client: Any = None,
    data_store: Any = None,
    resume: bool = True,
    max_pages: int = 200,
) -> pd.DataFrame:
    """Pull insider trading for the universe. Per-ticker failures log a
    warning, batch continues. Returns concatenation of all newly-saved rows."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if tickers is None:
        from src.data.fetcher.universes import get_universe_tickers
        tickers = sorted(get_universe_tickers(ds))

    frames = []
    for t in tickers:
        try:
            df = fetch_insider_trading(t, client=c, data_store=ds,
                                       resume=resume, max_pages=max_pages)
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            logger.warning(f"fetch_all_insider_trading: {t} failed: {exc}")

    if not frames:
        return _empty_insider()
    return pd.concat(frames, ignore_index=True)


def fetch_all_shares_float(
    tickers: Optional[List[str]] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Pull current shares-float for the universe. Per-ticker failures log a
    warning, batch continues."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if tickers is None:
        from src.data.fetcher.universes import get_universe_tickers
        tickers = sorted(get_universe_tickers(ds))

    frames = []
    for t in tickers:
        try:
            df = fetch_shares_float(t, client=c, data_store=ds)
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            logger.warning(f"fetch_all_shares_float: {t} failed: {exc}")

    if not frames:
        return _empty_shares_float()
    return pd.concat(frames, ignore_index=True)
