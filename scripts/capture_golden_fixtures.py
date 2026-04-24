"""Snapshot current-code outputs of the public fetcher API as golden fixtures.

Run ONCE before the Step 3 refactor. The fixtures become the regression detector
that every refactor sub-step runs against. Any deviation in the refactored code's
DataFrames beyond atol=1e-9 is a bug.

Output: tests/fixtures/*.pkl

Usage:
    python scripts/capture_golden_fixtures.py
    python scripts/capture_golden_fixtures.py --skip-network  # reuse existing pickles
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

# Ensure repo root is importable (matches how other scripts in this repo behave).
sys.path.insert(0, str(REPO_ROOT))

# Per the Step 3 plan:
TICKERS_FUND = ["AAPL", "MSFT", "JPM", "XOM", "NVDA", "PFE", "WMT", "BA", "KO", "T"]
FUND_START, FUND_END = "2019-01-01", "2023-12-31"

TICKERS_PX = ["AAPL", "TSLA", "NVDA", "SPY", "AABA"]
PX_START, PX_END = "2020-01-01", "2023-12-31"

TICKERS_NEWS = ["AAPL", "NVDA", "TSLA"]
NEWS_START, NEWS_END = "2024-06-01", "2024-09-01"

# Macro (Step 4 Component 1): small samples covering both sources + mixed frequencies
MACRO_FIXTURES = [
    ("DGS10",   "FRED",  "2024-01-02", "2024-01-31"),  # daily rate
    ("CPIAUCSL", "FRED", "2023-01-01", "2023-12-31"),  # monthly inflation
    ("^VIX",    "YAHOO", "2024-01-02", "2024-01-31"),  # daily volatility (market)
]

# Earnings (Step 4 Component 2): stable historical windows for 3 tickers from the
# per-ticker path. Avoids upcoming-announcement drift. Source filter excludes
# FMP_CALENDAR rows to make the fixture deterministic under hybrid population.
EARNINGS_FIXTURES = [
    ("AAPL", "2020-01-01", "2023-12-31"),
    ("MSFT", "2020-01-01", "2023-12-31"),
    ("NVDA", "2020-01-01", "2023-12-31"),
]


def _log(msg: str) -> None:
    print(f"[capture] {msg}", flush=True)


def capture_fundamentals() -> None:
    from src.data.data_fetcher import fetch_fundamental_data
    for align in (False, True):
        path = FIXTURE_DIR / f"fundamentals_align_{align}.pkl"
        t0 = time.time()
        _log(f"fundamentals align={align} → {path.name} ...")
        df = fetch_fundamental_data(TICKERS_FUND, FUND_START, FUND_END, align_quarter_dates=align)
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_prices() -> None:
    from src.data.data_fetcher import fetch_price_data
    path = FIXTURE_DIR / "prices.pkl"
    t0 = time.time()
    _log(f"prices → {path.name} ...")
    df = fetch_price_data(TICKERS_PX, PX_START, PX_END)
    df.to_pickle(path)
    _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_news() -> None:
    from src.data.data_fetcher import fetch_news
    for t in TICKERS_NEWS:
        path = FIXTURE_DIR / f"news_{t}.pkl"
        t0 = time.time()
        _log(f"news {t} (no sentiment) → {path.name} ...")
        df = fetch_news(t, NEWS_START, NEWS_END, analyze_sentiment=False)
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_macro() -> None:
    """Capture one snapshot per (series_id, source) window from `MACRO_FIXTURES`.

    We read from the DB (already-populated by bulk_macro.py) rather than re-fetching
    from FRED/Yahoo. This isolates the fixture from upstream vendor drift — the
    contract we're locking in is "whatever my current code returns from the DB for
    this series + window", which is what the equivalence checker compares.
    """
    from src.data.data_store import get_data_store
    ds = get_data_store()
    for series_id, source, start, end in MACRO_FIXTURES:
        path = FIXTURE_DIR / f"macro_{source}_{series_id.replace('^','').replace('=','').replace('.','').replace('-','_')}.pkl"
        t0 = time.time()
        _log(f"macro {source} {series_id} {start}..{end} → {path.name} ...")
        df = ds.get_macro_series(series_id=series_id, source=source,
                                 start_date=start, end_date=end)
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_earnings() -> None:
    """Per-ticker earnings fixtures. Reads from DB (populated by
    bulk_earnings_per_ticker.py), filtering to source=FMP_EARNINGS to keep the
    fixture stable across hybrid calendar-vs-per-ticker writes."""
    from src.data.data_store import get_data_store
    from src.data.fetcher.earnings import PER_TICKER_SOURCE
    ds = get_data_store()
    for ticker, start, end in EARNINGS_FIXTURES:
        path = FIXTURE_DIR / f"earnings_{ticker}.pkl"
        t0 = time.time()
        _log(f"earnings {ticker} {start}..{end} → {path.name} ...")
        df = ds.get_earnings_calendar(
            ticker=ticker, source=PER_TICKER_SOURCE,
            start_date=start, end_date=end,
        )
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_sp500() -> None:
    from src.data.data_fetcher import fetch_sp500_tickers
    path = FIXTURE_DIR / "sp500.pkl"
    t0 = time.time()
    _log(f"sp500 constituents → {path.name} ...")
    df = fetch_sp500_tickers()
    df.to_pickle(path)
    _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-network", action="store_true",
                    help="Verify existing fixtures only (no API calls)")
    args = ap.parse_args()

    socket.setdefaulttimeout(30)

    if args.skip_network:
        _log("--skip-network: listing existing fixtures")
        for p in sorted(FIXTURE_DIR.glob("*.pkl")):
            df = pd.read_pickle(p)
            _log(f"  {p.name}  rows={len(df):,}  cols={len(df.columns)}  bytes={p.stat().st_size:,}")
        return 0

    capture_sp500()
    capture_prices()
    capture_fundamentals()
    capture_news()
    capture_macro()
    capture_earnings()

    _log("DONE. Fixtures in " + str(FIXTURE_DIR))
    for p in sorted(FIXTURE_DIR.glob("*.pkl")):
        _log(f"  {p.name}  {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    sys.exit(main() or 0)
