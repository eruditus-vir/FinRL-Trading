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

# Ownership (Step 4 Component 3): historical insider window for AAPL +
# multi-ticker shares_float snapshot. Both read from DB (post-bulk).
INSIDER_FIXTURES = [
    ("AAPL", "2022-01-01", "2022-12-31"),
]
SHARES_FLOAT_TICKERS = ["AAPL", "MSFT", "NVDA"]

# Corporate actions (Step 4 Component 4): historical windows for 2 dividend
# tickers + AAPL splits. Stable historical data — no upcoming-event drift.
DIVIDEND_FIXTURES = [
    ("AAPL", "2018-01-01", "2024-12-31"),
    ("KO",   "2018-01-01", "2024-12-31"),
]
SPLIT_FIXTURES = [
    ("AAPL", "2015-01-01", "2024-12-31"),
]

# ETFs (Step 4 Component 5): SPY current snapshot + XLK historical price slice.
ETF_HOLDINGS_FIXTURE_ETF = "SPY"
ETF_PRICES_FIXTURES = [
    ("XLK", "2020-01-01", "2023-12-31"),
]

# Analyst (Step 4 Component 6): historical grades window + 3-ticker price target
# snapshot + AAPL estimates per period. Pinned to specific dates/periods for
# fixture stability under accumulating snapshots.
ANALYST_GRADES_FIXTURE = ("AAPL", "2018-01-01", "2024-12-31")
ANALYST_TARGETS_FIXTURE_TICKERS = ["AAPL", "MSFT", "NVDA"]
ANALYST_ESTIMATES_FIXTURE_TICKER = "AAPL"

# SEC filings (Step 4 Component 7): historical AAPL window + form-type slice.
SEC_FILINGS_AAPL_WINDOW = ("AAPL", "2024-01-01", "2024-12-31")
SEC_FILINGS_AAPL_FORM4_WINDOW = ("AAPL", "4", "2023-01-01", "2023-12-31")


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


def capture_ownership() -> None:
    """Insider trading window + shares_float snapshot per ticker."""
    from src.data.data_store import get_data_store
    ds = get_data_store()
    for ticker, start, end in INSIDER_FIXTURES:
        path = FIXTURE_DIR / f"insider_{ticker}_{start[:4]}.pkl"
        t0 = time.time()
        _log(f"insider {ticker} {start}..{end} → {path.name} ...")
        df = ds.get_insider_trading(ticker=ticker, start_date=start, end_date=end)
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")

    path = FIXTURE_DIR / "shares_float_sample.pkl"
    t0 = time.time()
    _log(f"shares_float sample {SHARES_FLOAT_TICKERS} → {path.name} ...")
    frames = [ds.get_shares_float(ticker=t) for t in SHARES_FLOAT_TICKERS]
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) else pd.DataFrame()
    df.to_pickle(path)
    _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_corporate_actions() -> None:
    """Dividend + split historical windows."""
    from src.data.data_store import get_data_store
    ds = get_data_store()
    for ticker, start, end in DIVIDEND_FIXTURES:
        path = FIXTURE_DIR / f"dividends_{ticker}.pkl"
        t0 = time.time()
        _log(f"dividends {ticker} {start}..{end} → {path.name} ...")
        df = ds.get_dividends(ticker=ticker, start_date=start, end_date=end)
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")
    for ticker, start, end in SPLIT_FIXTURES:
        path = FIXTURE_DIR / f"splits_{ticker}.pkl"
        t0 = time.time()
        _log(f"splits {ticker} {start}..{end} → {path.name} ...")
        df = ds.get_splits(ticker=ticker, start_date=start, end_date=end)
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_etf() -> None:
    """SPY holdings snapshot + XLK historical price slice. Read from DB
    post-bulk to keep deterministic."""
    from src.data.data_store import get_data_store
    ds = get_data_store()

    # SPY holdings — latest snapshot in DB
    path = FIXTURE_DIR / f"etf_holdings_{ETF_HOLDINGS_FIXTURE_ETF}.pkl"
    t0 = time.time()
    _log(f"etf_holdings {ETF_HOLDINGS_FIXTURE_ETF} → {path.name} ...")
    df = ds.get_etf_holdings(etf_symbol=ETF_HOLDINGS_FIXTURE_ETF)
    # Pin to the latest snapshot_date so re-runs across multiple weekly snapshots
    # don't break equivalence.
    if not df.empty:
        latest = df["snapshot_date"].max()
        df = df[df["snapshot_date"] == latest].reset_index(drop=True)
    df.to_pickle(path)
    _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")

    # ETF prices — historical slice
    from src.data.data_fetcher import fetch_price_data
    for etf, start, end in ETF_PRICES_FIXTURES:
        path = FIXTURE_DIR / f"etf_prices_{etf}.pkl"
        t0 = time.time()
        _log(f"etf_prices {etf} {start}..{end} → {path.name} ...")
        df = fetch_price_data([etf], start, end)
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_analyst() -> None:
    """4 analyst fixtures: grades window, price targets sample (latest snapshot),
    AAPL estimates per period."""
    from src.data.data_store import get_data_store
    ds = get_data_store()

    ticker, start, end = ANALYST_GRADES_FIXTURE
    path = FIXTURE_DIR / f"grades_{ticker}_{start[:4]}_{end[:4]}.pkl"
    t0 = time.time()
    _log(f"grades {ticker} {start}..{end} → {path.name} ...")
    df = ds.get_analyst_grades(ticker=ticker, start_date=start, end_date=end)
    df.to_pickle(path)
    _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")

    path = FIXTURE_DIR / "price_targets_sample.pkl"
    t0 = time.time()
    _log(f"price_targets sample {ANALYST_TARGETS_FIXTURE_TICKERS} → {path.name} ...")
    frames = [ds.get_price_target_consensus(ticker=t) for t in ANALYST_TARGETS_FIXTURE_TICKERS]
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) else pd.DataFrame()
    # Pin to latest snapshot_date so re-runs across multiple weekly snapshots
    # don't break equivalence.
    if not df.empty:
        latest = df["snapshot_date"].max()
        df = df[df["snapshot_date"] == latest].reset_index(drop=True)
    df.to_pickle(path)
    _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")

    for period in ("quarter", "annual"):
        path = FIXTURE_DIR / f"estimates_{ANALYST_ESTIMATES_FIXTURE_TICKER}_{period}.pkl"
        t0 = time.time()
        _log(f"estimates {ANALYST_ESTIMATES_FIXTURE_TICKER} {period} → {path.name} ...")
        df = ds.get_analyst_estimates(ticker=ANALYST_ESTIMATES_FIXTURE_TICKER, period=period)
        df.to_pickle(path)
        _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")


def capture_filings() -> None:
    """SEC filings: historical AAPL year + Form-4 slice."""
    from src.data.data_store import get_data_store
    ds = get_data_store()

    ticker, start, end = SEC_FILINGS_AAPL_WINDOW
    path = FIXTURE_DIR / f"sec_filings_{ticker}_{start[:4]}.pkl"
    t0 = time.time()
    _log(f"sec_filings {ticker} {start}..{end} → {path.name} ...")
    df = ds.get_sec_filings(ticker=ticker, start_date=start, end_date=end)
    df.to_pickle(path)
    _log(f"  rows={len(df):,} cols={len(df.columns)}  {time.time()-t0:.1f}s")

    ticker, form, start, end = SEC_FILINGS_AAPL_FORM4_WINDOW
    path = FIXTURE_DIR / f"sec_filings_{ticker}_form{form}_{start[:4]}.pkl"
    t0 = time.time()
    _log(f"sec_filings {ticker} form={form} {start}..{end} → {path.name} ...")
    df = ds.get_sec_filings(ticker=ticker, form_type=form,
                            start_date=start, end_date=end)
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
    capture_ownership()
    capture_corporate_actions()
    capture_etf()
    capture_analyst()
    capture_filings()

    _log("DONE. Fixtures in " + str(FIXTURE_DIR))
    for p in sorted(FIXTURE_DIR.glob("*.pkl")):
        _log(f"  {p.name}  {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    sys.exit(main() or 0)
