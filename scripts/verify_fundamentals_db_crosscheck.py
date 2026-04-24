"""DIAGNOSTIC: compare fresh `get_fundamental_data` output vs. current DB rows.

This is NOT a refactor-regression check — the authoritative Step 3 safety net
is scripts/verify_fixture_equivalence.py (atol=1e-9 byte-identity on captured
fixtures).

The DB rows were populated by an older version of the fetcher and have since
been post-processed by:
- src/data/fix_adj_close.py        (overwrites adj_close_q + y_return with yfinance)
- src/data/fill_recent_yreturn.py  (re-fills recent-quarter y_return)
- The git log shows a 2026-04-17 'updated y_return calculation rule' — rows
  written before that commit used a different formula.

As a result, 50+ of 57 columns diverge between current code output and DB
state for any sample of tickers. This script reports that divergence — useful
for understanding how much of the DB needs to be rebuilt from scratch if you
ever want the DB to be a clean snapshot of the current fetcher's output.

Run:
    python scripts/verify_fundamentals_db_crosscheck.py
"""
from __future__ import annotations

import socket
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "cache" / "finrl_trading.db"
sys.path.insert(0, str(REPO_ROOT))

TICKERS = ["AAPL", "XOM", "JPM"]
START, END = "2019-01-01", "2023-12-31"
ATOL = 1e-9


def main() -> int:
    socket.setdefaulttimeout(30)

    from src.data.data_fetcher import fetch_fundamental_data

    print(f"Fresh fetch: {TICKERS} {START}..{END}")
    fresh = fetch_fundamental_data(TICKERS, START, END).rename(columns={"tic": "ticker"})
    fresh["datadate"] = pd.to_datetime(fresh["datadate"]).dt.strftime("%Y-%m-%d")

    with sqlite3.connect(DB_PATH) as conn:
        placeholders = ",".join("?" for _ in TICKERS)
        stored = pd.read_sql(
            f"""SELECT * FROM fundamental_data
                WHERE ticker IN ({placeholders})
                  AND datadate BETWEEN ? AND ?
                ORDER BY ticker, datadate""",
            conn, params=[*TICKERS, START, END],
        )
    stored["datadate"] = pd.to_datetime(stored["datadate"]).dt.strftime("%Y-%m-%d")

    m = fresh.merge(stored, on=["ticker", "datadate"], suffixes=("_fresh", "_db"))
    shared = [c for c in fresh.columns if c in stored.columns and c not in ("ticker", "datadate")]

    diverge = []
    for col in shared:
        a = pd.to_numeric(m[f"{col}_fresh"], errors="coerce")
        b = pd.to_numeric(m[f"{col}_db"], errors="coerce")
        both_na = a.isna() & b.isna()
        eq = np.isclose(a.fillna(0), b.fillna(0), atol=ATOL) | both_na
        n_diff = int((~eq).sum())
        if n_diff:
            diverge.append((col, n_diff))

    total = len(m)
    print(f"\nRows compared: {total}")
    print(f"Columns matching within atol={ATOL}: {len(shared) - len(diverge)}/{len(shared)}")
    print(f"Columns diverging:                    {len(diverge)}/{len(shared)}")

    if diverge:
        print("\nDivergence report (informational — see module docstring for context):")
        for col, n in sorted(diverge, key=lambda x: -x[1]):
            print(f"  {col:<25} {n}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
