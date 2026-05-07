"""Bulk-fetch analyst estimates (quarterly + annual) for the universe.

Step 4 Component 6 (2026-05-07). Two calls per ticker (one per period).
UNIQUE(ticker, date, period) handles re-runs idempotently.

Usage:
    python scripts/bulk_analyst_estimates.py
    python scripts/bulk_analyst_estimates.py --tickers AAPL,MSFT,NVDA
    python scripts/bulk_analyst_estimates.py --periods quarter   # one period only
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
    print(f"[bulk_analyst_estimates] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--periods", default="quarter,annual",
                    help="Comma-separated subset of {quarter, annual}")
    ap.add_argument("--throttle-ms", type=int, default=0)
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

    from src.data.fetcher.analyst import fetch_analyst_estimates, ESTIMATE_PERIODS
    from src.data.fetcher.universes import get_universe_tickers
    from src.data.fetcher.client import FMPClient
    from src.data.data_store import get_data_store
    from src.config.settings import get_config

    ds = get_data_store()
    if args.tickers:
        tickers = sorted({t.strip() for t in args.tickers.split(",") if t.strip()})
    else:
        tickers = sorted(get_universe_tickers(ds))

    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    bad_periods = [p for p in periods if p not in ESTIMATE_PERIODS]
    if bad_periods:
        _log(f"ERROR: bad periods {bad_periods}; allowed {ESTIMATE_PERIODS}")
        return 1

    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    client = FMPClient(api_key, ds)
    if client.offline_mode:
        _log("WARNING: offline mode (no FMP_API_KEY)")
        return 1

    n_calls = len(tickers) * len(periods)
    _log(f"Fetching estimates for {len(tickers)} tickers × {len(periods)} periods "
         f"({periods}) = {n_calls} calls")
    _log(f"DB: {ds.db_path}")

    t_start = time.time()
    total = 0
    failures: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        for period in periods:
            t0 = time.time()
            try:
                df = fetch_analyst_estimates(ticker, period=period,
                                              client=client, data_store=ds)
                n = len(df)
            except Exception as exc:
                failures.append(f"{ticker}:{period}")
                _log(f"[{i}/{len(tickers)}] {ticker:<7} {period:<7} FAILED: {exc}")
                continue
            total += n
            _log(f"[{i}/{len(tickers)}] {ticker:<7} {period:<7} {n:>3} rows in DB  ({time.time()-t0:.1f}s)")
            if args.throttle_ms:
                time.sleep(args.throttle_ms / 1000)

    _log(f"Done. Elapsed {time.time()-t_start:.1f}s. Failures: {len(failures)}")

    import sqlite3
    with sqlite3.connect(ds.db_path) as conn:
        summary = pd.read_sql(
            """SELECT period, COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers,
                      MIN(date) AS min_d, MAX(date) AS max_d
               FROM analyst_estimates GROUP BY period ORDER BY period""",
            conn,
        )
    if not summary.empty:
        for _, r in summary.iterrows():
            _log(f"analyst_estimates {r['period']}: rows={r['rows']:,} "
                 f"tickers={r['tickers']} {r['min_d']} .. {r['max_d']}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
