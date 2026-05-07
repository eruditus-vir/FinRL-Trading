"""Analyst fetcher — FMP `/grades` + `/price-target-consensus` + `/analyst-estimates`.

Step 4 Component 6 (2026-05-07). Three FMP analyst endpoints bundled into one
module — they share the analyst surface and consumer pattern.

Endpoint behavior:
- `/grades` — 1 call returns all-history of analyst upgrades/downgrades.
  Class B: always-fetch + UNIQUE(ticker, date, grading_company) dedup.
- `/price-target-consensus` — 1 call returns current consensus snapshot only,
  NO date field. We synthesize `snapshot_date = today` so weekly re-runs build
  a target-revision history (matches shares_float pattern).
- `/analyst-estimates` — `period=quarter|annual`; 2 calls/ticker for full
  forward coverage. UNIQUE(ticker, date, period) dedups across re-runs.

Public API:
- `fetch_analyst_grades(ticker, start_date, end_date, client, data_store)`
- `fetch_price_target_consensus(ticker, client, data_store)`
- `fetch_analyst_estimates(ticker, period, client, data_store)`
- `fetch_all_analyst_grades(...)`, `fetch_all_price_targets(...)`,
  `fetch_all_analyst_estimates(...)` batch wrappers.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

GRADES_COLUMNS = [
    "ticker", "date", "grading_company", "previous_grade", "new_grade", "action",
]
PRICE_TARGET_COLUMNS = [
    "ticker", "snapshot_date", "target_high", "target_low",
    "target_consensus", "target_median",
]
ESTIMATES_COLUMNS = [
    "ticker", "date", "period",
    "revenue_low", "revenue_high", "revenue_avg",
    "ebitda_low", "ebitda_high", "ebitda_avg",
    "ebit_low", "ebit_high", "ebit_avg",
    "net_income_low", "net_income_high", "net_income_avg",
    "sga_low", "sga_high", "sga_avg",
    "eps_low", "eps_high", "eps_avg",
    "num_analysts_revenue", "num_analysts_eps",
]
ESTIMATE_PERIODS = ("quarter", "annual")


# ── Helpers ────────────────────────────────────────────────────────────────


def _empty_grades() -> pd.DataFrame:
    return pd.DataFrame(columns=GRADES_COLUMNS)


def _empty_price_targets() -> pd.DataFrame:
    return pd.DataFrame(columns=PRICE_TARGET_COLUMNS)


def _empty_estimates() -> pd.DataFrame:
    return pd.DataFrame(columns=ESTIMATES_COLUMNS)


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


def _normalize_grades(records: Iterable[dict]) -> pd.DataFrame:
    out = []
    for r in records or []:
        sym = r.get("symbol")
        date = r.get("date")
        if not sym or not date:
            continue
        out.append({
            "ticker": sym,
            "date": str(date)[:10],
            "grading_company": r.get("gradingCompany"),
            "previous_grade": r.get("previousGrade"),
            "new_grade": r.get("newGrade"),
            "action": r.get("action"),
        })
    if not out:
        return _empty_grades()
    return pd.DataFrame(out)


def _normalize_price_targets(records: Iterable[dict], snapshot_date: str) -> pd.DataFrame:
    """FMP returns no date — caller passes synthesized snapshot_date."""
    out = []
    for r in records or []:
        sym = r.get("symbol")
        if not sym:
            continue
        out.append({
            "ticker": sym,
            "snapshot_date": snapshot_date,
            "target_high": r.get("targetHigh"),
            "target_low": r.get("targetLow"),
            "target_consensus": r.get("targetConsensus"),
            "target_median": r.get("targetMedian"),
        })
    if not out:
        return _empty_price_targets()
    df = pd.DataFrame(out)
    for col in ("target_high", "target_low", "target_consensus", "target_median"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _normalize_estimates(records: Iterable[dict], period: str) -> pd.DataFrame:
    """`period` ∈ {'quarter', 'annual'}. FMP doesn't echo the period in
    payload; caller supplies it explicitly."""
    out = []
    for r in records or []:
        sym = r.get("symbol")
        date = r.get("date")
        if not sym or not date:
            continue
        out.append({
            "ticker": sym,
            "date": str(date)[:10],
            "period": period,
            "revenue_low": r.get("revenueLow"),
            "revenue_high": r.get("revenueHigh"),
            "revenue_avg": r.get("revenueAvg"),
            "ebitda_low": r.get("ebitdaLow"),
            "ebitda_high": r.get("ebitdaHigh"),
            "ebitda_avg": r.get("ebitdaAvg"),
            "ebit_low": r.get("ebitLow"),
            "ebit_high": r.get("ebitHigh"),
            "ebit_avg": r.get("ebitAvg"),
            "net_income_low": r.get("netIncomeLow"),
            "net_income_high": r.get("netIncomeHigh"),
            "net_income_avg": r.get("netIncomeAvg"),
            "sga_low": r.get("sgaExpenseLow"),
            "sga_high": r.get("sgaExpenseHigh"),
            "sga_avg": r.get("sgaExpenseAvg"),
            "eps_low": r.get("epsLow"),
            "eps_high": r.get("epsHigh"),
            "eps_avg": r.get("epsAvg"),
            "num_analysts_revenue": r.get("numAnalystsRevenue"),
            "num_analysts_eps": r.get("numAnalystsEps"),
        })
    if not out:
        return _empty_estimates()
    df = pd.DataFrame(out)
    numeric_cols = [c for c in ESTIMATES_COLUMNS
                    if c not in ("ticker", "date", "period")]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ── Analyst grades ─────────────────────────────────────────────────────────


def fetch_analyst_grades(
    ticker: str,
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """One call to /grades?symbol=X (returns all-history). Saves only rows in
    [start_date, end_date]. UNIQUE(ticker, date, grading_company) dedups."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /grades for {ticker}")
        return ds.get_analyst_grades(ticker=ticker, start_date=start_date, end_date=end_date)

    try:
        raw = c.get_json("grades", symbol=ticker)
    except Exception as exc:
        logger.warning(f"/grades {ticker} failed: {exc}")
        return _empty_grades()

    df = _normalize_grades(raw)
    if df.empty:
        return _empty_grades()

    save_df = df
    if start_date:
        save_df = save_df[save_df["date"] >= start_date]
    if end_date:
        save_df = save_df[save_df["date"] <= end_date]

    if save_df.empty:
        return _empty_grades()

    ds.save_analyst_grades(save_df)
    return ds.get_analyst_grades(ticker=ticker, start_date=start_date, end_date=end_date)


# ── Price target consensus ─────────────────────────────────────────────────


def fetch_price_target_consensus(
    ticker: str,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """One call to /price-target-consensus?symbol=X. FMP returns no date —
    we synthesize snapshot_date = today so weekly re-runs build a
    revision timeline. UNIQUE(ticker, snapshot_date) dedups same-day reruns."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /price-target-consensus for {ticker}")
        return ds.get_price_target_consensus(ticker=ticker)

    try:
        raw = c.get_json("price-target-consensus", symbol=ticker)
    except Exception as exc:
        logger.warning(f"/price-target-consensus {ticker} failed: {exc}")
        return _empty_price_targets()

    snapshot_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    df = _normalize_price_targets(raw, snapshot_date)
    if df.empty:
        return _empty_price_targets()

    ds.save_price_target_consensus(df)
    return ds.get_price_target_consensus(
        ticker=ticker, start_date=snapshot_date, end_date=snapshot_date,
    )


# ── Analyst estimates ──────────────────────────────────────────────────────


def fetch_analyst_estimates(
    ticker: str,
    period: str = "quarter",
    client: Any = None,
    data_store: Any = None,
    limit: int = 40,
) -> pd.DataFrame:
    """One call to /analyst-estimates?symbol=X&period=Y. `period` ∈
    {'quarter', 'annual'}. UNIQUE(ticker, date, period) dedups."""
    if period not in ESTIMATE_PERIODS:
        raise ValueError(f"period must be one of {ESTIMATE_PERIODS}, got {period!r}")

    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /analyst-estimates for {ticker} period={period}")
        return ds.get_analyst_estimates(ticker=ticker, period=period)

    try:
        raw = c.get_json("analyst-estimates", symbol=ticker, period=period, limit=limit)
    except Exception as exc:
        logger.warning(f"/analyst-estimates {ticker} period={period} failed: {exc}")
        return _empty_estimates()

    df = _normalize_estimates(raw, period)
    if df.empty:
        return _empty_estimates()

    ds.save_analyst_estimates(df)
    return ds.get_analyst_estimates(ticker=ticker, period=period)


# ── Batch convenience ─────────────────────────────────────────────────────


def fetch_all_analyst_grades(
    tickers: Optional[List[str]] = None,
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Pull analyst grades for the universe."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if tickers is None:
        from src.data.fetcher.universes import get_universe_tickers
        tickers = sorted(get_universe_tickers(ds))

    end_date = end_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    for t in tickers:
        try:
            fetch_analyst_grades(t, start_date=start_date, end_date=end_date,
                                 client=c, data_store=ds)
        except Exception as exc:
            logger.warning(f"fetch_all_analyst_grades: {t} failed: {exc}")

    frames = [ds.get_analyst_grades(ticker=t, start_date=start_date, end_date=end_date)
              for t in tickers]
    non_empty = [f for f in frames if not f.empty]
    return pd.concat(non_empty, ignore_index=True) if non_empty else _empty_grades()


def fetch_all_price_targets(
    tickers: Optional[List[str]] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Pull current price-target consensus for the universe."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if tickers is None:
        from src.data.fetcher.universes import get_universe_tickers
        tickers = sorted(get_universe_tickers(ds))

    snapshot_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    for t in tickers:
        try:
            fetch_price_target_consensus(t, client=c, data_store=ds)
        except Exception as exc:
            logger.warning(f"fetch_all_price_targets: {t} failed: {exc}")

    frames = [ds.get_price_target_consensus(ticker=t,
                                            start_date=snapshot_date,
                                            end_date=snapshot_date)
              for t in tickers]
    non_empty = [f for f in frames if not f.empty]
    return pd.concat(non_empty, ignore_index=True) if non_empty else _empty_price_targets()


def fetch_all_analyst_estimates(
    tickers: Optional[List[str]] = None,
    periods: Iterable[str] = ESTIMATE_PERIODS,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Pull analyst estimates for both quarter + annual periods (default)."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if tickers is None:
        from src.data.fetcher.universes import get_universe_tickers
        tickers = sorted(get_universe_tickers(ds))

    for t in tickers:
        for p in periods:
            try:
                fetch_analyst_estimates(t, period=p, client=c, data_store=ds)
            except Exception as exc:
                logger.warning(f"fetch_all_analyst_estimates: {t} period={p} failed: {exc}")

    frames = []
    for t in tickers:
        frames.append(ds.get_analyst_estimates(ticker=t))
    non_empty = [f for f in frames if not f.empty]
    return pd.concat(non_empty, ignore_index=True) if non_empty else _empty_estimates()
