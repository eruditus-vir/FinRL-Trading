"""Compare each golden fixture against current-code output. Zero-diff == refactor safe.

Reads tests/fixtures/*.pkl captured before the Step 3 refactor, re-runs the same
inputs through the current public fetcher API, and asserts DataFrames are
equal within `atol=1e-9` (user's choice — tolerates float-reorder noise).

Run this after every Step-3 sub-step. Exits 0 if all fixtures match, 1 otherwise.

Usage:
    python scripts/verify_fixture_equivalence.py
    python scripts/verify_fixture_equivalence.py --only fundamentals  # subset
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
sys.path.insert(0, str(REPO_ROOT))

# Must match capture_golden_fixtures.py exactly.
TICKERS_FUND = ["AAPL", "MSFT", "JPM", "XOM", "NVDA", "PFE", "WMT", "BA", "KO", "T"]
FUND_START, FUND_END = "2019-01-01", "2023-12-31"
TICKERS_PX = ["AAPL", "TSLA", "NVDA", "SPY", "AABA"]
PX_START, PX_END = "2020-01-01", "2023-12-31"
TICKERS_NEWS = ["AAPL", "NVDA", "TSLA"]
NEWS_START, NEWS_END = "2024-06-01", "2024-09-01"

# Macro fixtures (Step 4 Component 1) — mirror capture_golden_fixtures.py.
MACRO_FIXTURES = [
    ("DGS10",   "FRED",  "2024-01-02", "2024-01-31"),
    ("CPIAUCSL", "FRED", "2023-01-01", "2023-12-31"),
    ("^VIX",    "YAHOO", "2024-01-02", "2024-01-31"),
]

# Earnings fixtures (Step 4 Component 2) — mirror capture_golden_fixtures.py.
EARNINGS_FIXTURES = [
    ("AAPL", "2020-01-01", "2023-12-31"),
    ("MSFT", "2020-01-01", "2023-12-31"),
    ("NVDA", "2020-01-01", "2023-12-31"),
]

ATOL = 1e-9


def _log(msg: str) -> None:
    print(f"[verify] {msg}", flush=True)


def _sort_for_compare(df: pd.DataFrame, sort_cols: list[str]) -> pd.DataFrame:
    present = [c for c in sort_cols if c in df.columns]
    if present:
        df = df.sort_values(present, kind="mergesort").reset_index(drop=True)
    else:
        df = df.reset_index(drop=True)
    return df


def _compare(name: str, golden: pd.DataFrame, actual: pd.DataFrame,
             sort_cols: list[str]) -> tuple[bool, str]:
    g = _sort_for_compare(golden, sort_cols)
    a = _sort_for_compare(actual, sort_cols)
    try:
        pd.testing.assert_frame_equal(
            g, a, atol=ATOL, rtol=0, check_dtype=True, check_like=False
        )
    except AssertionError as e:
        return False, f"{name}: MISMATCH\n  {str(e).splitlines()[0]}\n  golden={g.shape} actual={a.shape}"
    return True, f"{name}: OK ({a.shape[0]:,} rows × {a.shape[1]} cols)"


def check_sp500(selection: set[str]) -> tuple[bool, str] | None:
    if "sp500" not in selection:
        return None
    from src.data.data_fetcher import fetch_sp500_tickers
    golden = pd.read_pickle(FIXTURE_DIR / "sp500.pkl")
    actual = fetch_sp500_tickers()
    return _compare("sp500", golden, actual, ["tickers"])


def check_prices(selection: set[str]) -> tuple[bool, str] | None:
    if "prices" not in selection:
        return None
    from src.data.data_fetcher import fetch_price_data
    golden = pd.read_pickle(FIXTURE_DIR / "prices.pkl")
    actual = fetch_price_data(TICKERS_PX, PX_START, PX_END)
    return _compare("prices", golden, actual, ["tic", "datadate"])


def check_fundamentals(selection: set[str]) -> list[tuple[bool, str]]:
    if "fundamentals" not in selection:
        return []
    from src.data.data_fetcher import fetch_fundamental_data
    results = []
    for align in (False, True):
        name = f"fundamentals_align_{align}"
        golden = pd.read_pickle(FIXTURE_DIR / f"{name}.pkl")
        actual = fetch_fundamental_data(TICKERS_FUND, FUND_START, FUND_END,
                                        align_quarter_dates=align)
        results.append(_compare(name, golden, actual, ["tic", "datadate"]))
    return results


def check_news(selection: set[str]) -> list[tuple[bool, str]]:
    if "news" not in selection:
        return []
    from src.data.data_fetcher import fetch_news
    results = []
    for t in TICKERS_NEWS:
        name = f"news_{t}"
        golden = pd.read_pickle(FIXTURE_DIR / f"{name}.pkl")
        actual = fetch_news(t, NEWS_START, NEWS_END, analyze_sentiment=False)
        # news is keyed by (ticker, published_datetime, title)
        results.append(_compare(name, golden, actual,
                                ["ticker", "published_datetime", "title"]))
    return results


def check_macro(selection: set[str]) -> list[tuple[bool, str]]:
    if "macro" not in selection:
        return []
    from src.data.data_store import get_data_store
    ds = get_data_store()
    results = []
    for series_id, source, start, end in MACRO_FIXTURES:
        name = f"macro_{source}_{series_id.replace('^','').replace('=','').replace('.','').replace('-','_')}"
        golden = pd.read_pickle(FIXTURE_DIR / f"{name}.pkl")
        actual = ds.get_macro_series(series_id=series_id, source=source,
                                     start_date=start, end_date=end)
        results.append(_compare(name, golden, actual,
                                ["series_id", "source", "date"]))
    return results


def check_earnings(selection: set[str]) -> list[tuple[bool, str]]:
    if "earnings" not in selection:
        return []
    from src.data.data_store import get_data_store
    from src.data.fetcher.earnings import PER_TICKER_SOURCE
    ds = get_data_store()
    results = []
    for ticker, start, end in EARNINGS_FIXTURES:
        name = f"earnings_{ticker}"
        golden = pd.read_pickle(FIXTURE_DIR / f"{name}.pkl")
        actual = ds.get_earnings_calendar(
            ticker=ticker, source=PER_TICKER_SOURCE,
            start_date=start, end_date=end,
        )
        results.append(_compare(name, golden, actual, ["ticker", "date"]))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="sp500,prices,fundamentals,news,macro,earnings",
                    help="comma-separated subset to verify")
    args = ap.parse_args()
    selection = set(s.strip() for s in args.only.split(",") if s.strip())

    socket.setdefaulttimeout(30)

    t0 = time.time()
    all_results: list[tuple[bool, str]] = []
    r = check_sp500(selection)
    if r: all_results.append(r)
    r = check_prices(selection)
    if r: all_results.append(r)
    all_results.extend(check_fundamentals(selection))
    all_results.extend(check_news(selection))
    all_results.extend(check_macro(selection))
    all_results.extend(check_earnings(selection))

    print()
    for ok, msg in all_results:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {msg}")

    n_pass = sum(1 for ok, _ in all_results if ok)
    n_fail = len(all_results) - n_pass
    print(f"\n{n_pass}/{len(all_results)} passed, {n_fail} failed  ({time.time()-t0:.1f}s)")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
