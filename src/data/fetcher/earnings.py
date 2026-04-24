"""Earnings fetcher — FMP `/earnings` (per-ticker) + `/earnings-calendar` (global).

Step 4 Component 2 (2026-04-24). First FMP-endpoint topic module added since
Step 3. Data from both endpoints share schema and land in one table
`earnings_calendar` with `UNIQUE(ticker, date)`; the `source` column records
provenance (`FMP_EARNINGS` for per-ticker, `FMP_CALENDAR` for global).

Hybrid usage:
- `fetch_earnings_per_ticker` — 2015→now historical backfill, 1 call/ticker,
  clean S&P-500 universe scope. Drives `bulk_earnings_per_ticker.py`.
- `fetch_earnings_calendar` — forward upcoming-announcements window, filter at
  save time to the universe to drop Tokyo/European noise. Drives
  `bulk_earnings_calendar.py`.
- `fetch_all_earnings` — batch convenience wrapper around per-ticker.

Public API:
- `fetch_earnings_per_ticker(ticker, start_date, end_date, client, data_store)`
- `fetch_earnings_calendar(from_date, to_date, client, data_store, tickers_filter)`
- `fetch_all_earnings(tickers, start_date, end_date, client, data_store)`
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

CALENDAR_SOURCE = "FMP_CALENDAR"
PER_TICKER_SOURCE = "FMP_EARNINGS"

EARNINGS_COLUMNS = [
    "ticker", "source", "date",
    "eps_actual", "eps_estimated",
    "revenue_actual", "revenue_estimated",
    "last_updated",
]


# ── Helpers ────────────────────────────────────────────────────────────────


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=EARNINGS_COLUMNS)


def _get_data_store(data_store=None):
    if data_store is not None:
        return data_store
    from src.data.data_store import get_data_store
    return get_data_store()


def _get_client(client=None, data_store=None):
    """Return an FMPClient; lazy-construct from config if not provided."""
    if client is not None:
        return client
    from src.data.fetcher.client import FMPClient
    from src.config.settings import get_config
    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    return FMPClient(api_key, _get_data_store(data_store))


def _universe_tickers(ds) -> set:
    """Distinct tickers in fundamental_data — our backfill universe."""
    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM fundamental_data"
        ).fetchall()
    return {r[0] for r in rows}


def _normalize_records(records: Iterable[dict]) -> pd.DataFrame:
    """Turn FMP earnings dicts into our column shape. Drops rows missing symbol
    or date. Numeric fields become float-or-NaN."""
    out = []
    for r in records or []:
        sym = r.get("symbol")
        date = r.get("date")
        if not sym or not date:
            continue
        out.append({
            "ticker": sym,
            "date": str(date)[:10],
            "eps_actual": r.get("epsActual"),
            "eps_estimated": r.get("epsEstimated"),
            "revenue_actual": r.get("revenueActual"),
            "revenue_estimated": r.get("revenueEstimated"),
            "last_updated": r.get("lastUpdated"),
        })
    if not out:
        return _empty_frame()
    df = pd.DataFrame(out)
    for col in ("eps_actual", "eps_estimated", "revenue_actual", "revenue_estimated"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # FMP returns placeholder rows (all numeric fields NULL) for
    # delisted/merged-before-report tickers. These carry no signal — drop them
    # so downstream event-guards don't trip on ghost announcements.
    signal = (
        df["eps_actual"].notna() | df["eps_estimated"].notna()
        | df["revenue_actual"].notna() | df["revenue_estimated"].notna()
    )
    return df[signal].reset_index(drop=True)


# ── Per-ticker ─────────────────────────────────────────────────────────────


def fetch_earnings_per_ticker(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Fetch one ticker's earnings history (~40 quarters) via `/earnings`.

    Always fetches the full endpoint response (one call, ~100 rows max) and
    upserts via UNIQUE(ticker, date). `start_date`/`end_date` window the
    returned DataFrame; they don't reduce the API call. Rationale: FMP's
    /earnings returns all-history in one call, and upcoming-earnings rows
    get revised when actuals come in — we want those updates. Writes
    `source=FMP_EARNINGS`.
    """
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /earnings fetch for {ticker}")
        return ds.get_earnings_calendar(ticker=ticker,
                                        start_date=start_date, end_date=end_date)

    try:
        raw = c.get_json("earnings", symbol=ticker, limit=limit)
    except Exception as exc:
        logger.warning(f"/earnings fetch failed for {ticker}: {exc}")
        return _empty_frame()

    df = _normalize_records(raw)
    if df.empty:
        logger.info(f"/earnings {ticker}: empty response")
        return _empty_frame()

    if len(df) == limit:
        logger.warning(
            f"/earnings {ticker}: hit limit={limit} — may be truncated; "
            f"consider paginating if earnings history > {limit} quarters"
        )

    # Apply caller's date window as a save-time scope filter. Always made the
    # API call; this just narrows what lands in DB. Keeps default bulk runs
    # aligned with our 2015+ fundamentals universe.
    save_df = df
    if start_date:
        save_df = save_df[save_df["date"] >= start_date]
    if end_date:
        save_df = save_df[save_df["date"] <= end_date]

    if save_df.empty:
        logger.info(f"/earnings {ticker}: no rows in {start_date}..{end_date}")
        return _empty_frame()

    ds.save_earnings_calendar(save_df.assign(source=PER_TICKER_SOURCE), PER_TICKER_SOURCE)

    return ds.get_earnings_calendar(
        ticker=ticker, source=PER_TICKER_SOURCE,
        start_date=start_date, end_date=end_date,
    )


# ── Global calendar ────────────────────────────────────────────────────────


def fetch_earnings_calendar(
    from_date: str,
    to_date: str,
    client: Any = None,
    data_store: Any = None,
    tickers_filter: Optional[set] = None,
) -> pd.DataFrame:
    """Fetch the global earnings calendar for a date window via `/earnings-calendar`.

    Returns all symbols by default; pass `tickers_filter` (a set of symbols) to
    narrow to our S&P-500 universe before saving. This avoids persisting
    Tokyo/European noise seen in probes.

    Writes `source=FMP_CALENDAR`. Intended for forward windows (upcoming
    announcements); historical rows should normally come from per-ticker.
    """
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /earnings-calendar for {from_date}..{to_date}")
        return ds.get_earnings_calendar(start_date=from_date, end_date=to_date,
                                        source=CALENDAR_SOURCE)

    try:
        raw = c.get_json("earnings-calendar", **{"from": from_date, "to": to_date})
    except Exception as exc:
        logger.warning(f"/earnings-calendar {from_date}..{to_date}: {exc}")
        return _empty_frame()

    df = _normalize_records(raw)
    if df.empty:
        logger.info(f"/earnings-calendar {from_date}..{to_date}: empty response")
        return _empty_frame()

    if tickers_filter is None:
        tickers_filter = _universe_tickers(ds)

    before = len(df)
    df = df[df["ticker"].isin(tickers_filter)].copy()
    after = len(df)
    logger.info(
        f"/earnings-calendar {from_date}..{to_date}: "
        f"kept {after}/{before} rows matching universe ({len(tickers_filter)} tickers)"
    )

    if df.empty:
        return _empty_frame()

    ds.save_earnings_calendar(df.assign(source=CALENDAR_SOURCE), CALENDAR_SOURCE)

    return ds.get_earnings_calendar(
        source=CALENDAR_SOURCE, start_date=from_date, end_date=to_date,
    )


# ── Batch convenience ─────────────────────────────────────────────────────


def fetch_all_earnings(
    tickers: Optional[List[str]] = None,
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Pull per-ticker earnings for the given universe (or DISTINCT tickers
    from fundamental_data if tickers is None). Individual failures log a
    warning but don't abort the batch. Returns everything in range from DB.
    """
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if tickers is None:
        tickers = sorted(_universe_tickers(ds))

    end_date = end_date or pd.Timestamp.today().strftime("%Y-%m-%d")

    for t in tickers:
        try:
            fetch_earnings_per_ticker(
                t, start_date=start_date, end_date=end_date,
                client=c, data_store=ds, limit=limit,
            )
        except Exception as exc:
            logger.warning(f"fetch_all_earnings: {t} failed: {exc}")

    frames = []
    for t in tickers:
        frames.append(ds.get_earnings_calendar(
            ticker=t, start_date=start_date, end_date=end_date,
        ))
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return _empty_frame()
    return pd.concat(non_empty, ignore_index=True)
