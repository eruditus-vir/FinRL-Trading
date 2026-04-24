"""Bulk-fetch per-ticker earnings history into earnings_calendar.

Step 4 Component 2 (2026-04-24). Incremental + resumable: for each ticker,
reads the DB's latest date and only fetches forward from there. Safe to
re-run any time — re-running the same day is a no-op once all tickers are
current.

Universe defaults to DISTINCT ticker FROM fundamental_data (~715 S&P-500
survivorship-free symbols), matching the rest of the Step 4 bulk scripts.

Usage:
    python scripts/bulk_earnings_per_ticker.py                 # defaults 2015-01-01 -> today+1y
    python scripts/bulk_earnings_per_ticker.py --start 2010-01-01
    python scripts/bulk_earnings_per_ticker.py --tickers AAPL,MSFT,NVDA
    python scripts/bulk_earnings_per_ticker.py --throttle-ms 50
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _log(msg: str) -> None:
    print(f"[bulk_earnings_per_ticker] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2015-01-01",
                    help="ISO start date (default 2015-01-01)")
    ap.add_argument("--end", default=None,
                    help="ISO end date (default today+1y to include upcoming)")
    ap.add_argument("--tickers", default=None,
                    help="Comma-separated subset (default: DISTINCT tickers from fundamental_data)")
    ap.add_argument("--limit", type=int, default=100,
                    help="FMP /earnings limit param (default 100)")
    ap.add_argument("--throttle-ms", type=int, default=0,
                    help="Optional sleep between tickers (ms)")
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.earnings import (
        fetch_earnings_per_ticker, PER_TICKER_SOURCE, _universe_tickers,
    )
    from src.data.fetcher.client import FMPClient
    from src.data.data_store import get_data_store
    from src.config.settings import get_config

    ds = get_data_store()

    if args.tickers:
        tickers = sorted({t.strip() for t in args.tickers.split(",") if t.strip()})
    else:
        tickers = sorted(_universe_tickers(ds))

    if not tickers:
        _log("No tickers to fetch. Ensure fundamental_data is populated, or pass --tickers.")
        return 1

    end_date = args.end or (pd.Timestamp.today() + pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    _log(f"Pulling earnings for {len(tickers)} tickers, {args.start} .. {end_date}")
    _log(f"DB: {ds.db_path}")

    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    client = FMPClient(api_key, ds)
    if client.offline_mode:
        _log("WARNING: offline mode (no FMP_API_KEY) — nothing will be fetched")
        return 1

    t_start = time.time()
    total_new = 0
    failures: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        t0 = time.time()
        try:
            df = fetch_earnings_per_ticker(
                ticker, start_date=args.start, end_date=end_date,
                client=client, data_store=ds, limit=args.limit,
            )
            n = len(df)
        except Exception as exc:
            failures.append(ticker)
            _log(f"[{i}/{len(tickers)}] {ticker:<7} FAILED: {exc}")
            continue

        total_new += n
        _log(f"[{i}/{len(tickers)}] {ticker:<7} +{n:>4} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. "
         f"Total rows (incremental): {total_new:,}. Failures: {len(failures)}")
    if failures:
        _log(f"Failed tickers: {','.join(failures[:30])}"
             f"{' ...' if len(failures) > 30 else ''}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT source, COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers,
                      MIN(date) AS min_d, MAX(date) AS max_d
               FROM earnings_calendar
               GROUP BY source ORDER BY source""",
            conn,
        )
    if not summary.empty:
        _log("\nearnings_calendar coverage:")
        for _, r in summary.iterrows():
            print(f"  {r['source']:<14} rows={r['rows']:>6}  "
                  f"tickers={r['tickers']:>4}  {r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
