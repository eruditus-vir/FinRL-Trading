"""Regression suite for the FinRL-Trading data layer.

Codifies every check run manually during the weakness #1-#3 investigation.
Runs against data/cache/finrl_trading.db. Exits 0 if all checks pass, 1 otherwise.

Each check returns (passed: bool, summary: str, details: str|None).
Known-exception checks (e.g., MON ticker reuse) assert that the expected
quirk is still present — they'll fail if someone accidentally "fixes" it
without updating the doc at docs/migration/07_data_quality.md.

Usage:
    python scripts/verify_data_quality.py           # run all checks
    python scripts/verify_data_quality.py --verbose # print per-check details
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "cache" / "finrl_trading.db"

# Alias map used by any join between fundamentals and prices.
# fundamentals uses period-separated symbols, FMP price data uses dash-separated.
# FB/META: pre-rename FB history is under META in FMP; post-2022 FB is a new company.
SYMBOL_ALIAS_FUND_TO_PRICE = {"BRK.B": "BRK-B", "BF.B": "BF-B"}


# ─── utilities ──────────────────────────────────────────────────────────────

class Check:
    def __init__(self, name: str, fn: Callable[[sqlite3.Connection], tuple[bool, str, str | None]]):
        self.name = name
        self.fn = fn

    def run(self, conn) -> tuple[bool, str, str | None]:
        try:
            return self.fn(conn)
        except Exception as e:
            return False, f"check raised {type(e).__name__}", str(e)


def _ok(msg: str, details: str | None = None) -> tuple[bool, str, str | None]:
    return True, msg, details


def _fail(msg: str, details: str | None = None) -> tuple[bool, str, str | None]:
    return False, msg, details


# ─── schema & integrity ─────────────────────────────────────────────────────

def check_tables_exist(conn):
    expected = {"price_data", "fundamental_data", "raw_payloads",
                "news_articles", "sp500_components_details", "news_fetch_log"}
    got = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    missing = expected - got
    if missing:
        return _fail(f"missing tables: {sorted(missing)}")
    return _ok(f"all {len(expected)} expected tables present")


def check_no_duplicates_prices(conn):
    n = conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM price_data GROUP BY ticker, date HAVING COUNT(*)>1)"
    ).fetchone()[0]
    return (_ok("0 duplicate (ticker,date) in price_data") if n == 0
            else _fail(f"{n} duplicate (ticker,date) rows in price_data"))


def check_no_duplicates_fundamentals(conn):
    n = conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM fundamental_data GROUP BY ticker, datadate HAVING COUNT(*)>1)"
    ).fetchone()[0]
    return (_ok("0 duplicate (ticker,datadate) in fundamental_data") if n == 0
            else _fail(f"{n} duplicate (ticker,datadate) rows"))


def check_no_null_prices(conn):
    row = conn.execute("""
        SELECT SUM(close IS NULL OR close <= 0),
               SUM(adj_close IS NULL OR adj_close <= 0),
               SUM(volume IS NULL OR volume < 0),
               COUNT(*)
        FROM price_data
    """).fetchone()
    cb, ab, vb, total = row
    if cb or ab or vb:
        return _fail(f"bad price rows: close={cb} adj_close={ab} volume={vb}")
    return _ok(f"no NULL/zero close/adj_close/volume across {total:,} rows")


# ─── price-adjustment semantics ────────────────────────────────────────────

def check_close_equals_adj_close(conn):
    """After weakness #2 fix, close should equal adj_close on every row."""
    row = conn.execute("""
        SELECT SUM(ABS(close - adj_close) >= 0.005), COUNT(*) FROM price_data
    """).fetchone()
    diffs, total = row
    if diffs:
        sample = conn.execute("""
            SELECT ticker, COUNT(*) FROM price_data
            WHERE ABS(close - adj_close) >= 0.005 GROUP BY ticker LIMIT 10
        """).fetchall()
        return _fail(
            f"{diffs} rows where close != adj_close (expected 0 after SPY/QQQ refresh)",
            f"top tickers: {sample}"
        )
    return _ok(f"close == adj_close on all {total:,} rows")


def check_known_splits_smooth(conn):
    """Pre-split and post-split close should be within ~5% (continuous, split-adjusted)."""
    cases = [
        ("AAPL", "2020-08-28", "2020-08-31", "4:1"),
        ("TSLA", "2022-08-24", "2022-08-25", "3:1"),
        ("NVDA", "2024-06-07", "2024-06-10", "10:1"),
        ("AMZN", "2022-06-03", "2022-06-06", "20:1"),
    ]
    failures = []
    for ticker, before, after, ratio in cases:
        rows = conn.execute(
            "SELECT date, close FROM price_data WHERE ticker=? AND date IN (?,?)",
            (ticker, before, after)
        ).fetchall()
        if len(rows) != 2:
            failures.append(f"{ticker} {ratio}: missing rows {rows}")
            continue
        d = dict(rows)
        rel = abs(d[after] - d[before]) / d[before]
        if rel > 0.08:  # 8% is generous for split day volatility
            failures.append(f"{ticker} {ratio}: {before}={d[before]} {after}={d[after]} rel_diff={rel:.1%}")
    if failures:
        return _fail("split adjustments appear broken", "; ".join(failures))
    return _ok(f"{len(cases)} known splits show smooth adjusted close")


def check_benchmark_closes(conn):
    """Spot-check a handful of dates against publicly-known values."""
    cases = [
        ("AAPL", "2022-01-03", 182.01, 0.01),
        ("TSLA", "2020-01-02", 28.68, 0.01),
        ("SPY",  "2020-03-23", 222.95, 0.01),
    ]
    failures = []
    for ticker, date, expected, tol in cases:
        row = conn.execute(
            "SELECT close FROM price_data WHERE ticker=? AND date=?",
            (ticker, date)
        ).fetchone()
        if row is None:
            failures.append(f"{ticker} {date}: row missing")
            continue
        got = row[0]
        if abs(got - expected) / expected > tol:
            failures.append(f"{ticker} {date}: got {got:.2f} expected {expected:.2f}")
    if failures:
        return _fail("benchmark close mismatch", "; ".join(failures))
    return _ok(f"{len(cases)} benchmark closes match public values within 1%")


# ─── fundamentals formula & distribution ───────────────────────────────────

def check_y_return_formula(conn):
    """y_return must equal ln(next_trade_price / this_trade_price) exactly."""
    f = pd.read_sql(
        "SELECT ticker, datadate, trade_price, y_return FROM fundamental_data "
        "ORDER BY ticker, datadate",
        conn,
    )
    f["next_tp"] = f.groupby("ticker")["trade_price"].shift(-1)
    mask = (f["trade_price"] > 0) & (f["next_tp"] > 0) & f["y_return"].notna() & (f["y_return"] != 0)
    f_ok = f[mask].copy()
    f_ok["computed"] = np.log(f_ok["next_tp"] / f_ok["trade_price"])
    f_ok["diff"] = (f_ok["y_return"] - f_ok["computed"]).abs()
    mismatches = int((f_ok["diff"] > 1e-4).sum())
    if mismatches:
        return _fail(f"{mismatches} formula mismatches (|diff|>1e-4) out of {len(f_ok):,}")
    return _ok(f"y_return formula exact across {len(f_ok):,} computable rows")


def check_y_return_distribution(conn):
    """Sanity bounds on quarterly log returns."""
    vals = pd.read_sql(
        "SELECT y_return FROM fundamental_data WHERE y_return IS NOT NULL AND y_return != 0",
        conn,
    )["y_return"]
    mean, std = vals.mean(), vals.std()
    extreme = int((vals.abs() > 2.0).sum())
    problems = []
    if not (-0.05 <= mean <= 0.05):
        problems.append(f"mean={mean:+.4f} outside ±5%")
    if not (0.10 <= std <= 0.25):
        problems.append(f"std={std:.4f} outside 10-25%")
    if extreme > 50:
        problems.append(f"{extreme} rows with |y_return|>2 (expected <50)")
    if problems:
        return _fail("; ".join(problems), f"n={len(vals):,}")
    return _ok(f"n={len(vals):,} mean={mean:+.4f} std={std:.4f} |>2|={extreme}")


# ─── fundamentals ↔ prices alignment ───────────────────────────────────────

def check_fundamentals_prices_alignment(conn):
    """Join fundamentals on (ticker, actual_tradedate) → prices.close.
    Target: >= 99.9% coverage on rows with usable tradedate+trade_price.
    """
    f = pd.read_sql(
        "SELECT ticker, actual_tradedate, trade_price FROM fundamental_data "
        "WHERE trade_price > 0 AND actual_tradedate IS NOT NULL",
        conn,
    )
    p = pd.read_sql("SELECT ticker AS p_ticker, date AS p_date, close FROM price_data", conn)
    f["price_ticker"] = f["ticker"].replace(SYMBOL_ALIAS_FUND_TO_PRICE)
    m = f.merge(p, left_on=["price_ticker", "actual_tradedate"],
                right_on=["p_ticker", "p_date"], how="left")
    matched = int(m["close"].notna().sum())
    total = len(m)
    coverage = matched / total if total else 0
    ok = m[m["close"].notna()].copy()
    ok["rel"] = (ok["trade_price"] - ok["close"]).abs() / ok["close"]
    pct_tight = float((ok["rel"] < 0.005).mean())
    if coverage < 0.999:
        return _fail(f"alignment coverage {coverage:.4%} below 99.9% threshold",
                     f"matched={matched:,}/{total:,}")
    if pct_tight < 0.999:
        return _fail(f"only {pct_tight:.4%} of matched rows within 0.5% (expected ≥99.9%)")
    return _ok(f"coverage={coverage:.4%} tight-match={pct_tight:.4%} (matched {matched:,}/{total:,})")


# ─── coverage ──────────────────────────────────────────────────────────────

def check_ticker_counts(conn):
    p_tickers = conn.execute("SELECT COUNT(DISTINCT ticker) FROM price_data").fetchone()[0]
    f_tickers = conn.execute("SELECT COUNT(DISTINCT ticker) FROM fundamental_data").fetchone()[0]
    if p_tickers < 700:
        return _fail(f"price_data has only {p_tickers} tickers (expected ≥700)")
    if f_tickers < 700:
        return _fail(f"fundamental_data has only {f_tickers} tickers (expected ≥700)")
    return _ok(f"price_data tickers={p_tickers} fundamental_data tickers={f_tickers}")


def check_yearly_coverage(conn):
    """Every year 2015-2025 should have ≥600 tickers with ≥200 trading days."""
    df = pd.read_sql("""
        SELECT SUBSTR(date,1,4) AS yr, ticker, COUNT(*) AS n
        FROM price_data WHERE date >= '2015-01-01' AND date < '2026-01-01'
        GROUP BY yr, ticker
    """, conn)
    coverage = df[df["n"] >= 200].groupby("yr").size()
    bad_years = coverage[coverage < 600]
    if not bad_years.empty:
        return _fail("years with <600 tickers at ≥200 days: "
                     + str(bad_years.to_dict()))
    return _ok(f"all years 2015-2025 have ≥600 tickers with ≥200 trading days "
               f"(min={int(coverage.min())} in {coverage.idxmin()})")


def check_benchmark_coverage(conn):
    """SPY and QQQ must span 2015-01-02 → recent."""
    failures = []
    for t in ("SPY", "QQQ"):
        row = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM price_data WHERE ticker=?", (t,)
        ).fetchone()
        mn, mx, n = row
        if mn != "2015-01-02":
            failures.append(f"{t} starts {mn}, expected 2015-01-02")
        if n < 2800:
            failures.append(f"{t} has only {n} rows, expected ≥2800")
    if failures:
        return _fail("benchmark coverage incomplete", "; ".join(failures))
    return _ok("SPY and QQQ both span 2015-01-02 onward with ≥2800 rows")


# ─── known exceptions (asserting expected quirks still present) ────────────

def check_macro_coverage(conn):
    """macro_series table must have broad coverage: ≥20 distinct FRED series and
    ≥6 Yahoo series, each covering 2015 to recent. Sentinel daily series must
    have ≥2,500 rows (≈10 years of business days)."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='macro_series'"
    ).fetchone()
    if row is None:
        return _fail("macro_series table is missing")

    counts = conn.execute(
        """SELECT source, COUNT(DISTINCT series_id)
           FROM macro_series GROUP BY source"""
    ).fetchall()
    by_source = dict(counts)
    if by_source.get("FRED", 0) < 20:
        return _fail(f"FRED series count {by_source.get('FRED', 0)} < 20")
    if by_source.get("YAHOO", 0) < 6:
        return _fail(f"YAHOO series count {by_source.get('YAHOO', 0)} < 6")

    # Daily sentinel series should have dense history
    daily_sentinels = [("FRED", "DGS10"), ("FRED", "VIXCLS"), ("YAHOO", "^VIX"), ("YAHOO", "^GSPC")]
    problems = []
    for source, sid in daily_sentinels:
        r = conn.execute(
            """SELECT COUNT(*), MIN(date), MAX(date) FROM macro_series
               WHERE source=? AND series_id=?""", (source, sid)
        ).fetchone()
        n, mn, _ = r
        if n < 2500:
            problems.append(f"{source}/{sid} only {n} rows (expected ≥2500)")
        if mn is None or mn > "2015-03-01":
            problems.append(f"{source}/{sid} starts {mn} (expected ≤2015-03-01)")
    if problems:
        return _fail("macro sentinel coverage issue", "; ".join(problems))

    total = conn.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0]
    return _ok(f"macro_series has {by_source.get('FRED', 0)} FRED + "
               f"{by_source.get('YAHOO', 0)} YAHOO series ({total:,} rows)")


def check_earnings_coverage(conn):
    """earnings_calendar must have ≥500 tickers with ≥4 rows each. No row may
    have ALL of {eps_actual, eps_estimated, revenue_actual, revenue_estimated}
    null — that's junk. Upcoming announcements with only estimates populated
    are fine (actual-nulls allowed when estimates present)."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='earnings_calendar'"
    ).fetchone()
    if row is None:
        return _fail("earnings_calendar table is missing")

    total = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
    if total == 0:
        return _fail("earnings_calendar is empty — run bulk_earnings_per_ticker.py")

    coverage = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT ticker FROM earnings_calendar
            GROUP BY ticker HAVING COUNT(*) >= 4
        )
    """).fetchone()[0]
    if coverage < 500:
        return _fail(f"only {coverage} tickers have ≥4 earnings rows (expected ≥500)")

    all_null = conn.execute("""
        SELECT COUNT(*) FROM earnings_calendar
        WHERE eps_actual IS NULL AND eps_estimated IS NULL
          AND revenue_actual IS NULL AND revenue_estimated IS NULL
    """).fetchone()[0]
    if all_null > 0:
        return _fail(f"{all_null} rows have no actual or estimate data (junk)")

    return _ok(f"{coverage} tickers with ≥4 earnings rows ({total:,} total rows)")


def check_merged_tickers_no_prices(conn):
    """BXLT, PCL, RHT, SNI, TWC have fundamentals but no prices (merged out; FMP dropped them)."""
    expected_zero = ["BXLT", "PCL", "RHT", "SNI", "TWC"]
    actual = []
    for t in expected_zero:
        n = conn.execute("SELECT COUNT(*) FROM price_data WHERE ticker=?", (t,)).fetchone()[0]
        actual.append((t, n))
    violations = [(t, n) for t, n in actual if n > 0]
    if violations:
        return _fail("tickers expected empty now have price data "
                     "(good news — investigate and update doc)",
                     str(violations))
    return _ok("5 merged-out tickers (BXLT, PCL, RHT, SNI, TWC) correctly have no prices")


def check_mon_ticker_reuse(conn):
    """MON prices 2021-03 onward are a different company (not Monsanto, which was acquired 2018)."""
    row = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM price_data WHERE ticker='MON'"
    ).fetchone()
    mn, mx, n = row
    if n == 0:
        return _fail("MON has no prices — investigate, this changes the ticker-reuse story")
    if mn is None or mn < "2021-01-01":
        return _fail(f"MON price data starts {mn} — unexpected, may include real Monsanto pre-2018 data",
                     f"range={mn}..{mx} n={n}")
    return _ok(f"MON ticker-reuse still present as expected (range {mn}..{mx}, n={n})")


# ─── driver ─────────────────────────────────────────────────────────────────

ALL_CHECKS = [
    # integrity
    Check("tables_exist",                         check_tables_exist),
    Check("no_duplicates_prices",                 check_no_duplicates_prices),
    Check("no_duplicates_fundamentals",           check_no_duplicates_fundamentals),
    Check("no_null_prices",                       check_no_null_prices),
    # adjustment semantics
    Check("close_equals_adj_close",               check_close_equals_adj_close),
    Check("known_splits_smooth",                  check_known_splits_smooth),
    Check("benchmark_closes_match_public_values", check_benchmark_closes),
    # fundamentals
    Check("y_return_formula_exact",               check_y_return_formula),
    Check("y_return_distribution_sane",           check_y_return_distribution),
    Check("fundamentals_prices_alignment",        check_fundamentals_prices_alignment),
    # coverage
    Check("ticker_counts",                        check_ticker_counts),
    Check("yearly_coverage",                      check_yearly_coverage),
    Check("benchmark_coverage",                   check_benchmark_coverage),
    # known quirks (expected to be present)
    # macro (Step 4 Component 1)
    Check("macro_coverage",                       check_macro_coverage),
    # earnings (Step 4 Component 2)
    Check("earnings_coverage",                    check_earnings_coverage),
    # known exceptions
    Check("merged_tickers_have_no_prices",        check_merged_tickers_no_prices),
    Check("mon_ticker_reuse_still_present",       check_mon_ticker_reuse),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print details for each check")
    parser.add_argument("--db", default=str(DB_PATH),
                        help=f"SQLite DB path (default: {DB_PATH})")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: DB not found at {args.db}", file=sys.stderr)
        return 2

    print(f"Verifying data quality in {args.db}")
    print(f"Running {len(ALL_CHECKS)} checks...\n")

    with sqlite3.connect(args.db) as conn:
        results = [(c.name, *c.run(conn)) for c in ALL_CHECKS]

    n_pass = sum(1 for _, ok, *_ in results if ok)
    n_fail = len(results) - n_pass

    for name, ok, summary, details in results:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {summary}")
        if details and (args.verbose or not ok):
            for line in details.splitlines():
                print(f"         {line}")

    print(f"\n{n_pass}/{len(results)} passed, {n_fail} failed")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
