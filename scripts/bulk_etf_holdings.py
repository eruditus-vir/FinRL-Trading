"""Bulk-fetch ETF constituent holdings.

Step 4 Component 5 (2026-05-07). One call per ETF in `ETF_UNIVERSE`. Each
call writes ~50-2000 rows depending on the ETF (sector ETFs: ~50-100;
broad-market like SPY: ~500; small-cap like IWM: ~2000).

UNIQUE(etf_symbol, asset, snapshot_date) handles re-runs idempotently —
weekly re-runs build a constituent-change history.

Usage:
    python scripts/bulk_etf_holdings.py
    python scripts/bulk_etf_holdings.py --etfs SPY,XLK,IWM
    python scripts/bulk_etf_holdings.py --throttle-ms 50
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
    print(f"[bulk_etf_holdings] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--etfs", default=None,
                    help="Comma-separated subset (default: full ETF_UNIVERSE)")
    ap.add_argument("--throttle-ms", type=int, default=0)
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.etf import fetch_etf_holdings, ETF_UNIVERSE
    from src.data.fetcher.client import FMPClient
    from src.data.data_store import get_data_store
    from src.config.settings import get_config

    ds = get_data_store()
    if args.etfs:
        etfs = [e.strip() for e in args.etfs.split(",") if e.strip()]
    else:
        etfs = list(ETF_UNIVERSE.keys())

    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    client = FMPClient(api_key, ds)
    if client.offline_mode:
        _log("WARNING: offline mode (no FMP_API_KEY)")
        return 1

    _log(f"Fetching holdings for {len(etfs)} ETFs")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    total = 0
    failures: list[str] = []

    for i, etf in enumerate(etfs, 1):
        t0 = time.time()
        try:
            df = fetch_etf_holdings(etf, client=client, data_store=ds)
            n = len(df)
        except Exception as exc:
            failures.append(etf)
            _log(f"[{i}/{len(etfs)}] {etf:<6} FAILED: {exc}")
            continue
        total += n
        _log(f"[{i}/{len(etfs)}] {etf:<6} +{n:>4} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. Rows in latest snapshot: {total:,}. Failures: {len(failures)}")
    if failures:
        _log(f"Failed: {','.join(failures)}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT COUNT(*) AS rows, COUNT(DISTINCT etf_symbol) AS etfs,
                      COUNT(DISTINCT asset) AS assets,
                      MIN(snapshot_date) AS min_d, MAX(snapshot_date) AS max_d
               FROM etf_holdings""",
            conn,
        )
    if not summary.empty:
        r = summary.iloc[0]
        _log(f"etf_holdings: rows={r['rows']:,} etfs={r['etfs']} "
             f"distinct_assets={r['assets']:,} {r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
