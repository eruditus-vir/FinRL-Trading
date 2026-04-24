"""Bulk-fetch macro series (FRED + Yahoo) into macro_series table.

Step 4 Component 1 (2026-04-23). Incremental + resumable: for each series,
reads the DB's latest date and only fetches forward from there. Safe to re-run
any time — re-running the same day is a no-op once all series are current.

Usage:
    python scripts/bulk_macro.py                     # defaults 2015-01-01 -> today
    python scripts/bulk_macro.py --start 2010-01-01  # go further back
    python scripts/bulk_macro.py --only FRED         # or --only YAHOO
    python scripts/bulk_macro.py --series DGS10,DGS2,^VIX  # subset
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
    print(f"[bulk_macro] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2015-01-01", help="ISO start date (default 2015-01-01)")
    ap.add_argument("--end", default=None, help="ISO end date (default today)")
    ap.add_argument("--only", choices=["FRED", "YAHOO"], default=None,
                    help="Restrict to one source")
    ap.add_argument("--series", default=None,
                    help="Comma-separated subset (e.g. DGS10,DGS2,^VIX)")
    ap.add_argument("--throttle-ms", type=int, default=0,
                    help="Optional sleep between series (ms); FRED is well under rate limit")
    args = ap.parse_args()

    socket.setdefaulttimeout(30)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.macro import (
        FRED_SERIES, YAHOO_SYMBOLS,
        fetch_fred_series, fetch_yahoo_series,
    )
    from src.data.data_store import get_data_store

    subset = None
    if args.series:
        subset = {s.strip() for s in args.series.split(",") if s.strip()}

    fred_ids = [s for s in FRED_SERIES if (subset is None or s in subset)]
    yahoo_ids = [s for s in YAHOO_SYMBOLS if (subset is None or s in subset)]

    if args.only == "FRED":
        yahoo_ids = []
    elif args.only == "YAHOO":
        fred_ids = []

    total = len(fred_ids) + len(yahoo_ids)
    if total == 0:
        _log("Nothing to fetch (empty series list after filtering).")
        return 0

    end_date = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    _log(f"Pulling {len(fred_ids)} FRED + {len(yahoo_ids)} Yahoo series "
         f"for {args.start} .. {end_date}")

    ds = get_data_store()
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    total_new = 0

    # FRED
    for i, sid in enumerate(fred_ids, 1):
        t0 = time.time()
        df = fetch_fred_series(sid, args.start, end_date, data_store=ds)
        n = len(df)
        total_new += n
        _log(f"[{i}/{len(fred_ids)}] FRED  {sid:<15} +{n:>5} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    # Yahoo
    for i, sym in enumerate(yahoo_ids, 1):
        t0 = time.time()
        df = fetch_yahoo_series(sym, args.start, end_date, data_store=ds)
        n = len(df)
        total_new += n
        _log(f"[{i}/{len(yahoo_ids)}] YAHOO {sym:<15} +{n:>5} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. Total rows fetched: {total_new:,}")

    # Summary
    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT source, series_id, COUNT(*) AS n,
                      MIN(date) AS min_d, MAX(date) AS max_d
               FROM macro_series
               GROUP BY source, series_id
               ORDER BY source, series_id""",
            conn,
        )
    if not summary.empty:
        _log("\nCoverage per series:")
        for _, r in summary.iterrows():
            print(f"  {r['source']:<6} {r['series_id']:<15} n={r['n']:>5}  "
                  f"{r['min_d']} .. {r['max_d']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
