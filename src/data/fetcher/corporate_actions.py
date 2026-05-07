"""Corporate actions fetcher — FMP `/dividends` + `/splits`.

Step 4 Component 4 (2026-05-07). Two trivial Class B endpoints — each returns
all-history in one call per ticker. Pattern mirrors `earnings.py`: always
fetch + UNIQUE(ticker, date) dedup, save-time scope filter to keep the DB
focused on our 2015+ universe.

Public API:
- `fetch_dividends(ticker, start_date, end_date, client, data_store)`
- `fetch_splits(ticker, start_date, end_date, client, data_store)`
- `fetch_all_dividends(tickers, start_date, end_date, client, data_store)`
- `fetch_all_splits(tickers, start_date, end_date, client, data_store)`
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DIVIDEND_COLUMNS = [
    "ticker", "date", "record_date", "payment_date", "declaration_date",
    "adj_dividend", "dividend", "yield_pct", "frequency",
]
SPLIT_COLUMNS = ["ticker", "date", "numerator", "denominator", "split_type"]


# ── Helpers ────────────────────────────────────────────────────────────────


def _empty_dividends() -> pd.DataFrame:
    return pd.DataFrame(columns=DIVIDEND_COLUMNS)


def _empty_splits() -> pd.DataFrame:
    return pd.DataFrame(columns=SPLIT_COLUMNS)


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


def _normalize_dividends(records: Iterable[dict]) -> pd.DataFrame:
    """Turn FMP /dividends dicts into our column shape. Renames `yield`
    (Python keyword) to `yield_pct`. Drops rows with no ex-date."""
    out = []
    for r in records or []:
        sym = r.get("symbol")
        date = r.get("date")
        if not sym or not date:
            continue
        out.append({
            "ticker": sym,
            "date": str(date)[:10],
            "record_date": str(r["recordDate"])[:10] if r.get("recordDate") else None,
            "payment_date": str(r["paymentDate"])[:10] if r.get("paymentDate") else None,
            "declaration_date": str(r["declarationDate"])[:10] if r.get("declarationDate") else None,
            "adj_dividend": r.get("adjDividend"),
            "dividend": r.get("dividend"),
            "yield_pct": r.get("yield"),
            "frequency": r.get("frequency"),
        })
    if not out:
        return _empty_dividends()
    df = pd.DataFrame(out)
    for col in ("adj_dividend", "dividend", "yield_pct"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _normalize_splits(records: Iterable[dict]) -> pd.DataFrame:
    """Turn FMP /splits dicts into our column shape."""
    out = []
    for r in records or []:
        sym = r.get("symbol")
        date = r.get("date")
        if not sym or not date:
            continue
        out.append({
            "ticker": sym,
            "date": str(date)[:10],
            "numerator": r.get("numerator"),
            "denominator": r.get("denominator"),
            "split_type": r.get("splitType"),
        })
    if not out:
        return _empty_splits()
    df = pd.DataFrame(out)
    for col in ("numerator", "denominator"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ── Dividends ──────────────────────────────────────────────────────────────


def fetch_dividends(
    ticker: str,
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """One call to /dividends?symbol=X (returns all-history). Saves only rows
    in [start_date, end_date]. UNIQUE(ticker, date) handles re-runs."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /dividends fetch for {ticker}")
        return ds.get_dividends(ticker=ticker, start_date=start_date, end_date=end_date)

    try:
        raw = c.get_json("dividends", symbol=ticker)
    except Exception as exc:
        logger.warning(f"/dividends fetch failed for {ticker}: {exc}")
        return _empty_dividends()

    df = _normalize_dividends(raw)
    if df.empty:
        return _empty_dividends()

    save_df = df
    if start_date:
        save_df = save_df[save_df["date"] >= start_date]
    if end_date:
        save_df = save_df[save_df["date"] <= end_date]

    if save_df.empty:
        return _empty_dividends()

    ds.save_dividends(save_df)
    return ds.get_dividends(ticker=ticker, start_date=start_date, end_date=end_date)


# ── Splits ─────────────────────────────────────────────────────────────────


def fetch_splits(
    ticker: str,
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """One call to /splits?symbol=X (returns all-history). Saves only rows
    in [start_date, end_date]. UNIQUE(ticker, date) handles re-runs."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /splits fetch for {ticker}")
        return ds.get_splits(ticker=ticker, start_date=start_date, end_date=end_date)

    try:
        raw = c.get_json("splits", symbol=ticker)
    except Exception as exc:
        logger.warning(f"/splits fetch failed for {ticker}: {exc}")
        return _empty_splits()

    df = _normalize_splits(raw)
    if df.empty:
        return _empty_splits()

    save_df = df
    if start_date:
        save_df = save_df[save_df["date"] >= start_date]
    if end_date:
        save_df = save_df[save_df["date"] <= end_date]

    if save_df.empty:
        return _empty_splits()

    ds.save_splits(save_df)
    return ds.get_splits(ticker=ticker, start_date=start_date, end_date=end_date)


# ── Batch convenience ─────────────────────────────────────────────────────


def fetch_all_dividends(
    tickers: Optional[List[str]] = None,
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Pull dividends for the universe. Per-ticker failures log a warning;
    batch continues."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if tickers is None:
        from src.data.fetcher.universes import get_universe_tickers
        tickers = sorted(get_universe_tickers(ds))

    end_date = end_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    for t in tickers:
        try:
            fetch_dividends(t, start_date=start_date, end_date=end_date,
                            client=c, data_store=ds)
        except Exception as exc:
            logger.warning(f"fetch_all_dividends: {t} failed: {exc}")

    frames = [ds.get_dividends(ticker=t, start_date=start_date, end_date=end_date)
              for t in tickers]
    non_empty = [f for f in frames if not f.empty]
    return pd.concat(non_empty, ignore_index=True) if non_empty else _empty_dividends()


def fetch_all_splits(
    tickers: Optional[List[str]] = None,
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Pull splits for the universe. Per-ticker failures log a warning;
    batch continues."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if tickers is None:
        from src.data.fetcher.universes import get_universe_tickers
        tickers = sorted(get_universe_tickers(ds))

    end_date = end_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    for t in tickers:
        try:
            fetch_splits(t, start_date=start_date, end_date=end_date,
                         client=c, data_store=ds)
        except Exception as exc:
            logger.warning(f"fetch_all_splits: {t} failed: {exc}")

    frames = [ds.get_splits(ticker=t, start_date=start_date, end_date=end_date)
              for t in tickers]
    non_empty = [f for f in frames if not f.empty]
    return pd.concat(non_empty, ignore_index=True) if non_empty else _empty_splits()
