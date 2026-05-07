"""Bulk-fetch price-target consensus snapshots for the universe.

Step 4 Component 6 (2026-05-07). One call per ticker — `/price-target-consensus`
returns the current snapshot. We synthesize snapshot_date = today; weekly
re-runs build a target-revision history via UNIQUE(ticker, snapshot_date).

Usage:
    python scripts/bulk_price_targets.py
    python scripts/bulk_price_targets.py --tickers AAPL,MSFT,NVDA
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
    print(f"[bulk_price_targets] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--throttle-ms", type=int, default=0)
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.analyst import fetch_price_target_consensus
    from src.data.fetcher.universes import get_universe_tickers
    from src.data.fetcher.client import FMPClient
    from src.data.data_store import get_data_store
    from src.config.settings import get_config

    ds = get_data_store()
    if args.tickers:
        tickers = sorted({t.strip() for t in args.tickers.split(",") if t.strip()})
    else:
        tickers = sorted(get_universe_tickers(ds))

    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    client = FMPClient(api_key, ds)
    if client.offline_mode:
        _log("WARNING: offline mode (no FMP_API_KEY)")
        return 1

    snapshot_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    _log(f"Fetching price targets for {len(tickers)} tickers (snapshot_date={snapshot_date})")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    saved = 0
    failures: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        t0 = time.time()
        try:
            df = fetch_price_target_consensus(ticker, client=client, data_store=ds)
            n = len(df)
        except Exception as exc:
            failures.append(ticker)
            _log(f"[{i}/{len(tickers)}] {ticker:<7} FAILED: {exc}")
            continue
        saved += n
        _log(f"[{i}/{len(tickers)}] {ticker:<7} +{n} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. Snapshot rows: {saved}. Failures: {len(failures)}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers,
                      MIN(snapshot_date) AS min_d, MAX(snapshot_date) AS max_d
               FROM price_target_consensus""",
            conn,
        )
    if not summary.empty:
        r = summary.iloc[0]
        _log(f"price_target_consensus: rows={r['rows']:,} tickers={r['tickers']} "
             f"{r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
