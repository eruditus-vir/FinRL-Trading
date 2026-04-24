"""Bulk-fetch the global earnings calendar into earnings_calendar.

Step 4 Component 2 (2026-04-24). Iterates monthly date windows and pulls
/earnings-calendar?from=...&to=.... Filters each window's response to our
S&P-500 universe (DISTINCT ticker FROM fundamental_data) before saving.

Intended for forward windows (upcoming announcements). Historical rows should
normally come from bulk_earnings_per_ticker.py — running this script over a
2015→now window would overwrite historical rows the per-ticker endpoint had
populated (with generally equivalent data, source column records the swap).
Default --from is today to avoid that.

Usage:
    python scripts/bulk_earnings_calendar.py                 # today .. today+90d
    python scripts/bulk_earnings_calendar.py --from 2026-04-01 --to 2026-05-31
    python scripts/bulk_earnings_calendar.py --window-days 30
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _log(msg: str) -> None:
    print(f"[bulk_earnings_calendar] {msg}", flush=True)


def _iter_windows(start: datetime, end: datetime, window_days: int):
    """Yield (from_str, to_str) windows of `window_days` each, covering [start, end]."""
    cur = start
    while cur <= end:
        window_end = min(cur + timedelta(days=window_days - 1), end)
        yield cur.strftime("%Y-%m-%d"), window_end.strftime("%Y-%m-%d")
        cur = window_end + timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    today = pd.Timestamp.today().normalize()
    ap.add_argument("--from", dest="from_date", default=today.strftime("%Y-%m-%d"),
                    help="ISO start date (default today)")
    ap.add_argument("--to", dest="to_date",
                    default=(today + pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
                    help="ISO end date (default today+90d)")
    ap.add_argument("--window-days", type=int, default=30,
                    help="Days per API call (default 30; FMP caps responses)")
    ap.add_argument("--throttle-ms", type=int, default=0,
                    help="Optional sleep between windows (ms)")
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.earnings import (
        fetch_earnings_calendar, CALENDAR_SOURCE, _universe_tickers,
    )
    from src.data.fetcher.client import FMPClient
    from src.data.data_store import get_data_store
    from src.config.settings import get_config

    ds = get_data_store()
    universe = _universe_tickers(ds)
    if not universe:
        _log("ERROR: fundamental_data is empty — can't determine universe. "
             "Run fundamentals backfill first, or pass your own filter.")
        return 1

    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    client = FMPClient(api_key, ds)
    if client.offline_mode:
        _log("WARNING: offline mode (no FMP_API_KEY) — nothing will be fetched")
        return 1

    start = pd.to_datetime(args.from_date).to_pydatetime()
    end = pd.to_datetime(args.to_date).to_pydatetime()
    if start > end:
        _log(f"--from ({args.from_date}) is after --to ({args.to_date}); nothing to do")
        return 1

    windows = list(_iter_windows(start, end, args.window_days))
    _log(f"Pulling calendar for {args.from_date} .. {args.to_date} "
         f"({len(windows)} windows × {args.window_days}d). "
         f"Universe: {len(universe)} tickers")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    total_new = 0
    failures: list[tuple[str, str]] = []

    for i, (w_from, w_to) in enumerate(windows, 1):
        t0 = time.time()
        try:
            df = fetch_earnings_calendar(
                w_from, w_to,
                client=client, data_store=ds, tickers_filter=universe,
            )
            n = len(df)
        except Exception as exc:
            failures.append((w_from, w_to))
            _log(f"[{i}/{len(windows)}] {w_from}..{w_to} FAILED: {exc}")
            continue

        total_new += n
        _log(f"[{i}/{len(windows)}] {w_from}..{w_to} +{n:>4} rows  ({time.time()-t0:.1f}s)")
        if args.throttle_ms:
            time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. "
         f"Rows in range (universe-filtered): {total_new:,}. Failures: {len(failures)}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT source, COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers,
                      MIN(date) AS min_d, MAX(date) AS max_d
               FROM earnings_calendar
               WHERE source = ?
               GROUP BY source""",
            conn, params=(CALENDAR_SOURCE,),
        )
    if not summary.empty:
        r = summary.iloc[0]
        _log(f"\n{CALENDAR_SOURCE} coverage: rows={r['rows']} "
             f"tickers={r['tickers']}  {r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
