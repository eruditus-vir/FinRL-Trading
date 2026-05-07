"""Bulk-fetch current shares-float snapshot for the universe.

Step 4 Component 3 (2026-04-26). One call per ticker → one row in
shares_float. UNIQUE(ticker, snapshot_date) means same-day reruns are no-ops;
weekly/monthly reruns build a dilution timeline.

Usage:
    python scripts/bulk_shares_float.py                      # all 715 tickers
    python scripts/bulk_shares_float.py --tickers AAPL,MSFT  # subset
    python scripts/bulk_shares_float.py --throttle-ms 50
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
    print(f"[bulk_shares_float] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=None,
                    help="Comma-separated subset (default: DISTINCT tickers from fundamental_data)")
    ap.add_argument("--throttle-ms", type=int, default=0,
                    help="Optional sleep between tickers (ms)")
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.ownership import fetch_shares_float
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

    _log(f"Fetching shares-float for {len(tickers)} tickers")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    saved = 0
    failures: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        t0 = time.time()
        try:
            df = fetch_shares_float(ticker, client=client, data_store=ds)
            n = len(df)
        except Exception as exc:
            failures.append(ticker)
            _log(f"[{i}/{len(tickers)}] {ticker:<7} FAILED: {exc}")
            continue
        saved += n
        _log(f"[{i}/{len(tickers)}] {ticker:<7} +{n} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. "
         f"Rows saved (cumulative cache view): {saved}. Failures: {len(failures)}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers,
                      MIN(snapshot_date) AS min_d, MAX(snapshot_date) AS max_d
               FROM shares_float""",
            conn,
        )
    if not summary.empty:
        r = summary.iloc[0]
        _log(f"shares_float: rows={r['rows']} tickers={r['tickers']} "
             f"{r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
