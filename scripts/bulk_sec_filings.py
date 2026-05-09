"""Bulk-fetch SEC filings for the universe (paginated, date-windowed).

Step 4 Component 7 (2026-05-08). For each ticker, paginates
`/sec-filings-search/symbol?from=...&to=...&page=N` until empty.
Crash-safe: per-ticker checkpoint in `sec_filings_fetch_log`. A re-run
with `--resume` (default) skips fully-fetched tickers and resumes partial
ones from `last_page + 1`. Use `--no-resume` to force re-scan from page 0
(e.g. for picking up new filings on already-fetched tickers).

Usage:
    python scripts/bulk_sec_filings.py
    python scripts/bulk_sec_filings.py --tickers AAPL,MSFT --max-pages 3
    python scripts/bulk_sec_filings.py --no-resume   # full re-scan
    python scripts/bulk_sec_filings.py --from 2010-01-01
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
    print(f"[bulk_sec_filings] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_date", default="2015-01-01")
    ap.add_argument("--to", dest="to_date", default=None)
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--max-pages", type=int, default=200)
    resume_grp = ap.add_mutually_exclusive_group()
    resume_grp.add_argument("--resume", dest="resume", action="store_true", default=True)
    resume_grp.add_argument("--no-resume", dest="resume", action="store_false")
    ap.add_argument("--throttle-ms", type=int, default=0)
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.filings import fetch_sec_filings
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

    to_date = args.to_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    _log(f"Fetching SEC filings for {len(tickers)} tickers, {args.from_date}..{to_date} "
         f"(resume={args.resume}, max_pages={args.max_pages})")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    total_new = 0
    failures: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        t0 = time.time()
        try:
            df = fetch_sec_filings(
                ticker, from_date=args.from_date, to_date=to_date,
                client=client, data_store=ds,
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
                      COUNT(DISTINCT form_type) AS form_types,
                      MIN(filing_date) AS min_d, MAX(filing_date) AS max_d
               FROM sec_filings""",
            conn,
        )
    if not summary.empty:
        r = summary.iloc[0]
        _log(f"sec_filings: rows={r['rows']:,} tickers={r['tickers']} "
             f"form_types={r['form_types']} {r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
