"""Bulk-fetch dividend history for the universe.

Step 4 Component 4 (2026-05-07). One call per ticker — `/dividends?symbol=X`
returns all-history. UNIQUE(ticker, date) handles re-runs idempotently.
Default save scope is 2015-01-01 → today.

Usage:
    python scripts/bulk_dividends.py
    python scripts/bulk_dividends.py --tickers AAPL,KO,JPM
    python scripts/bulk_dividends.py --start 2010-01-01
    python scripts/bulk_dividends.py --throttle-ms 50
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
    print(f"[bulk_dividends] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--tickers", default=None,
                    help="Comma-separated subset (default: DISTINCT tickers from fundamental_data)")
    ap.add_argument("--throttle-ms", type=int, default=0)
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.corporate_actions import fetch_dividends
    from src.data.fetcher.universes import get_universe_tickers
    from src.data.fetcher.client import FMPClient
    from src.data.data_store import get_data_store
    from src.config.settings import get_config

    ds = get_data_store()
    if args.tickers:
        tickers = sorted({t.strip() for t in args.tickers.split(",") if t.strip()})
    else:
        tickers = sorted(get_universe_tickers(ds))

    if not tickers:
        _log("No tickers. Run fundamentals backfill first or pass --tickers.")
        return 1

    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    client = FMPClient(api_key, ds)
    if client.offline_mode:
        _log("WARNING: offline mode (no FMP_API_KEY)")
        return 1

    end_date = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    _log(f"Fetching dividends for {len(tickers)} tickers, {args.start}..{end_date}")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    total = 0
    failures: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        t0 = time.time()
        try:
            df = fetch_dividends(ticker, start_date=args.start, end_date=end_date,
                                 client=client, data_store=ds)
            n = len(df)
        except Exception as exc:
            failures.append(ticker)
            _log(f"[{i}/{len(tickers)}] {ticker:<7} FAILED: {exc}")
            continue
        total += n
        _log(f"[{i}/{len(tickers)}] {ticker:<7} +{n:>3} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. Rows in range: {total:,}. Failures: {len(failures)}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers,
                      MIN(date) AS min_d, MAX(date) AS max_d FROM dividends""",
            conn,
        )
    if not summary.empty:
        r = summary.iloc[0]
        _log(f"dividends: rows={r['rows']:,} tickers={r['tickers']} "
             f"{r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
