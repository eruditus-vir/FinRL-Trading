"""Macro fetcher — FRED economic series + Yahoo Finance market series.

Step 4 Component 1 (2026-04-23). First non-FMP data source in the fetcher/
package. Does NOT take an FMPClient; instantiates fredapi directly and uses
yfinance for Yahoo-hosted series (indices, futures, FX).

Storage: one long-format SQLite table `macro_series(series_id, source, date, value)`.
Retrieval: `fetch_macro_data(start, end, series=None)` reads from cache first,
fetches only forward from the per-series latest date.

Public API:
- `fetch_fred_series(series_id, start, end, data_store=None, api_key=None)`
- `fetch_yahoo_series(symbol, start, end, data_store=None)`
- `fetch_macro_data(start='2015-01-01', end=None, series=None, data_store=None)`
"""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Series catalog (the editable source of truth for what we pull) ────────

FRED_SERIES: dict[str, str] = {
    # Rates
    "DGS10":         "10-year Treasury yield",
    "DGS5":          "5-year Treasury yield",
    "DGS2":          "2-year Treasury yield",
    "DGS30":         "30-year Treasury yield",
    "DGS3MO":        "3-month Treasury yield",
    "DFF":           "Effective Fed funds rate",
    # Credit spreads
    "BAMLH0A0HYM2":  "ICE BofA High Yield OAS",
    "BAMLC0A0CM":    "ICE BofA Investment Grade OAS",
    # Inflation
    "CPIAUCSL":      "CPI All Urban Consumers",
    "CPILFESL":      "Core CPI",
    "PCEPI":         "PCE Price Index",
    # Labor
    "UNRATE":        "Unemployment rate",
    "PAYEMS":        "Nonfarm payrolls",
    "CIVPART":       "Labor force participation rate",
    "ICSA":          "Initial jobless claims",
    # Growth
    "GDP":           "Nominal GDP",
    "GDPC1":         "Real GDP",
    "INDPRO":        "Industrial production index",
    "RRSFS":         "Real retail sales",
    # Housing
    "HOUST":         "Housing starts",
    "PERMIT":        "Building permits",
    # Monetary + commodities
    "M2SL":          "M2 money supply",
    "DCOILWTICO":    "WTI crude oil spot",
    # Volatility
    "VIXCLS":        "VIX close (FRED mirror)",
}

YAHOO_SYMBOLS: dict[str, str] = {
    "^VIX":      "CBOE Volatility Index",
    "^GSPC":     "S&P 500",
    "^IXIC":     "NASDAQ Composite",
    "^RUT":      "Russell 2000",
    "DX-Y.NYB":  "US Dollar Index (ICE)",
    "GC=F":      "Gold futures",
    "CL=F":      "WTI Crude Oil futures",
    "HG=F":      "Copper futures",
}

FRED_SOURCE = "FRED"
YAHOO_SOURCE = "YAHOO"


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_data_store(data_store=None):
    """Lazy-import to avoid circular deps; match the pattern used elsewhere."""
    if data_store is not None:
        return data_store
    from src.data.data_store import get_data_store
    return get_data_store()


def _resolve_fred_api_key(api_key: Optional[str]) -> Optional[str]:
    """Prefer explicit arg → config.fred.api_key → FRED_API_KEY env var."""
    if api_key:
        return api_key
    try:
        from src.config.settings import get_config
        cfg = get_config()
        if cfg.fred and cfg.fred.api_key:
            return cfg.fred.api_key.get_secret_value()
    except Exception as exc:
        logger.debug(f"FRED config lookup failed: {exc}")
    return os.environ.get("FRED_API_KEY")


def _effective_start(ds, series_id: str, source: str, requested_start: str) -> str:
    """Return `requested_start`, bumped forward if we already have data past it.

    If the DB's latest date for this series is >= requested_start, resume from
    `latest_date + 1 day` (incremental fetch). If nothing cached, honor the
    requested start.
    """
    latest = ds.get_macro_series_latest_date(series_id, source)
    if latest is None:
        return requested_start
    next_day = (pd.to_datetime(latest) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if next_day > requested_start:
        return next_day
    return requested_start


# ── FRED fetcher ──────────────────────────────────────────────────────────


def fetch_fred_series(
    series_id: str,
    start_date: str,
    end_date: Optional[str] = None,
    data_store: Any = None,
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch one FRED series. Incremental: only pulls dates past the DB's
    current max. Returns the portion newly fetched (may be empty if cache
    already covers the range). Always returns a DataFrame with
    columns ['series_id', 'source', 'date', 'value']."""
    ds = _get_data_store(data_store)
    eff_start = _effective_start(ds, series_id, FRED_SOURCE, start_date)
    end_date = end_date or pd.Timestamp.today().strftime("%Y-%m-%d")

    if eff_start > end_date:
        logger.info(f"FRED {series_id}: cache already covers {start_date}..{end_date}, skipping")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    key = _resolve_fred_api_key(api_key)
    if not key:
        logger.warning(
            "FRED_API_KEY not set — register free at "
            "fred.stlouisfed.org/docs/api/api_key.html and add to .env"
        )
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    try:
        from fredapi import Fred
    except ImportError:
        logger.error("fredapi not installed. Run: pip install fredapi>=0.5.0")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    try:
        fred = Fred(api_key=key)
        series = fred.get_series(series_id, observation_start=eff_start, observation_end=end_date)
    except Exception as exc:
        logger.warning(f"FRED fetch failed for {series_id} ({eff_start}..{end_date}): {exc}")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    if series is None or len(series) == 0:
        logger.info(f"FRED {series_id}: empty response for {eff_start}..{end_date}")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    # fredapi returns a Series with DatetimeIndex.
    df_in = pd.DataFrame({"value": series.values}, index=series.index)
    ds.save_macro_series(df_in, series_id, FRED_SOURCE)

    # Return the normalized long-format view of just what we fetched.
    return ds.get_macro_series(series_id=series_id, source=FRED_SOURCE,
                               start_date=eff_start, end_date=end_date)


# ── Yahoo fetcher ──────────────────────────────────────────────────────────


def fetch_yahoo_series(
    symbol: str,
    start_date: str,
    end_date: Optional[str] = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Fetch one Yahoo-hosted series (index, future, FX). Uses the symbol's
    daily close. Incremental via DB latest-date. No API key needed.
    Returns a long-format DataFrame matching the FRED shape."""
    ds = _get_data_store(data_store)
    eff_start = _effective_start(ds, symbol, YAHOO_SOURCE, start_date)
    end_date = end_date or pd.Timestamp.today().strftime("%Y-%m-%d")

    if eff_start > end_date:
        logger.info(f"YAHOO {symbol}: cache covers {start_date}..{end_date}, skipping")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed.")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    try:
        # yfinance.download with end exclusive — add 1 day to be inclusive.
        end_inclusive = (pd.to_datetime(end_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        data = yf.download(
            symbol, start=eff_start, end=end_inclusive,
            progress=False, auto_adjust=True, threads=False,
        )
    except Exception as exc:
        logger.warning(f"Yahoo fetch failed for {symbol} ({eff_start}..{end_date}): {exc}")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    if data is None or len(data) == 0:
        logger.info(f"YAHOO {symbol}: empty response for {eff_start}..{end_date}")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    # yfinance returns MultiIndex columns when threads or multiple tickers;
    # flatten and extract Close.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [c[0] if isinstance(c, tuple) else c for c in data.columns]

    if "Close" not in data.columns:
        logger.warning(f"YAHOO {symbol}: no Close column in response (cols={list(data.columns)})")
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    df_in = pd.DataFrame({"value": data["Close"].values}, index=data.index)
    ds.save_macro_series(df_in, symbol, YAHOO_SOURCE)

    return ds.get_macro_series(series_id=symbol, source=YAHOO_SOURCE,
                               start_date=eff_start, end_date=end_date)


# ── Batch convenience ─────────────────────────────────────────────────────


def fetch_macro_data(
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    series: Optional[List[str]] = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Pull all default series (or a subset) for the requested date range.

    If `series` is None, pulls every FRED + Yahoo default. Individual series
    failures log a warning but don't abort the batch. Returns long-format
    DataFrame with columns [series_id, source, date, value] covering whatever
    was successfully saved.
    """
    ds = _get_data_store(data_store)
    end_date = end_date or pd.Timestamp.today().strftime("%Y-%m-%d")

    if series is None:
        fred_list = list(FRED_SERIES.keys())
        yahoo_list = list(YAHOO_SYMBOLS.keys())
    else:
        s = set(series)
        fred_list = [x for x in FRED_SERIES if x in s]
        yahoo_list = [x for x in YAHOO_SYMBOLS if x in s]
        unknown = s - set(FRED_SERIES) - set(YAHOO_SYMBOLS)
        if unknown:
            logger.warning(f"Unknown series requested (skipped): {sorted(unknown)}")

    for sid in fred_list:
        fetch_fred_series(sid, start_date, end_date, data_store=ds)

    for sym in yahoo_list:
        fetch_yahoo_series(sym, start_date, end_date, data_store=ds)

    # Return everything in the requested range from DB.
    all_ids = fred_list + yahoo_list
    if not all_ids:
        return pd.DataFrame(columns=["series_id", "source", "date", "value"])

    frames = []
    for sid in fred_list:
        frames.append(ds.get_macro_series(series_id=sid, source=FRED_SOURCE,
                                          start_date=start_date, end_date=end_date))
    for sym in yahoo_list:
        frames.append(ds.get_macro_series(series_id=sym, source=YAHOO_SOURCE,
                                          start_date=start_date, end_date=end_date))
    out = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) \
        else pd.DataFrame(columns=["series_id", "source", "date", "value"])
    return out
