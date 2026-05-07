"""Bulk-fetch insider-trading filings for the universe (paginated).

Step 4 Component 3 (2026-04-26). For each ticker, paginates `?page=N&limit=100`
until empty. Crash-safe: per-ticker checkpoint in `insider_trading_fetch_log`.
A re-run with `--resume` (default) skips fully-fetched tickers and resumes
partial ones from `last_page + 1`. Use `--no-resume` to force re-scan from
page 0 (e.g. for picking up new filings on already-fetched tickers).

Usage:
    python scripts/bulk_insider_trading.py
    python scripts/bulk_insider_trading.py --tickers AAPL,MSFT --max-pages 3
    python scripts/bulk_insider_trading.py --no-resume   # full re-scan
    python scripts/bulk_insider_trading.py --throttle-ms 50
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
    print(f"[bulk_insider_trading] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=None,
                    help="Comma-separated subset (default: DISTINCT tickers from fundamental_data)")
    ap.add_argument("--max-pages", type=int, default=200,
                    help="Per-ticker page cap (default 200, FMP page size = 100)")
    resume_grp = ap.add_mutually_exclusive_group()
    resume_grp.add_argument("--resume", dest="resume", action="store_true", default=True,
                            help="Skip pages already fetched (default)")
    resume_grp.add_argument("--no-resume", dest="resume", action="store_false",
                            help="Re-scan from page 0 for every ticker")
    ap.add_argument("--throttle-ms", type=int, default=0,
                    help="Optional sleep between pages (ms)")
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.ownership import fetch_insider_trading
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

    _log(f"Fetching insider trading for {len(tickers)} tickers "
         f"(resume={args.resume}, max_pages={args.max_pages})")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    total_new = 0
    failures: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        t0 = time.time()
        try:
            df = fetch_insider_trading(
                ticker, client=client, data_store=ds,
                resume=args.resume, max_pages=args.max_pages,
            )
            n = len(df)
        except Exception as exc:
            failures.append(ticker)
            _log(f"[{i}/{len(tickers)}] {ticker:<7} FAILED: {exc}")
            continue
        total_new += n
        _log(f"[{i}/{len(tickers)}] {ticker:<7} +{n:>5} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. "
         f"Rows newly saved: {total_new:,}. Failures: {len(failures)}")
    if failures:
        _log(f"Failed: {','.join(failures[:30])}{' ...' if len(failures) > 30 else ''}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers,
                      MIN(filing_date) AS min_d, MAX(filing_date) AS max_d
               FROM insider_trading""",
            conn,
        )
    if not summary.empty:
        r = summary.iloc[0]
        _log(f"insider_trading: rows={r['rows']:,} tickers={r['tickers']} "
             f"{r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
