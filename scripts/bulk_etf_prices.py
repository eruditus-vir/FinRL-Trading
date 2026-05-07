"""Bulk-fetch historical price data for the 46 ETFs in ETF_UNIVERSE.

Step 4 Component 5 (2026-05-07). Reuses the existing `fetch_price_data`
pipeline — no new endpoint code. Writes into the existing `price_data`
table (UNIQUE(ticker, date) for dedup).

Universe is `ETF_UNIVERSE` from src/data/fetcher/etf.py. Verified empty
intersection with the S&P-500 stock universe — no risk of overwriting
existing equity prices.

Usage:
    python scripts/bulk_etf_prices.py                    # 2015-01-01 -> today
    python scripts/bulk_etf_prices.py --start 2010-01-01
    python scripts/bulk_etf_prices.py --etfs SPY,XLK,IWM
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
    print(f"[bulk_etf_prices] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--etfs", default=None,
                    help="Comma-separated subset (default: full ETF_UNIVERSE)")
    ap.add_argument("--throttle-ms", type=int, default=0)
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.etf import ETF_UNIVERSE
    from src.data.fetcher.universes import get_universe_tickers
    from src.data.data_fetcher import fetch_price_data
    from src.data.data_store import get_data_store

    ds = get_data_store()
    if args.etfs:
        etfs = [e.strip() for e in args.etfs.split(",") if e.strip()]
    else:
        etfs = list(ETF_UNIVERSE.keys())

    # Defense: confirm no collision with the S&P-500 stock universe.
    stock_universe = get_universe_tickers(ds)
    collision = set(etfs) & stock_universe
    if collision:
        _log(f"ERROR: ETF universe collides with stock universe: {sorted(collision)}")
        _log("Refusing to run — would overwrite existing equity price rows.")
        return 1

    end_date = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    _log(f"Fetching prices for {len(etfs)} ETFs, {args.start}..{end_date}")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    failures: list[str] = []

    for i, etf in enumerate(etfs, 1):
        t0 = time.time()
        try:
            # fetch_price_data accepts a list and writes to price_data internally
            df = fetch_price_data([etf], args.start, end_date)
            n = len(df)
        except Exception as exc:
            failures.append(etf)
            _log(f"[{i}/{len(etfs)}] {etf:<6} FAILED: {exc}")
            continue
        _log(f"[{i}/{len(etfs)}] {etf:<6} {n:>5} price rows in DB  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. Failures: {len(failures)}")
    if failures:
        _log(f"Failed: {','.join(failures)}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        placeholders = ','.join(['?'] * len(etfs))
        summary = pd.read_sql(
            f"""SELECT COUNT(*) AS rows, COUNT(DISTINCT ticker) AS etfs,
                       MIN(date) AS min_d, MAX(date) AS max_d
                FROM price_data WHERE ticker IN ({placeholders})""",
            conn, params=etfs,
        )
    if not summary.empty:
        r = summary.iloc[0]
        _log(f"price_data (ETF subset): rows={r['rows']:,} etfs={r['etfs']} "
             f"{r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
