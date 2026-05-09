# Current State — read this first after context reset

Last updated: **2026-04-24**. This is the resumption document. Any fresh session (human or Claude) should read this before touching code.

If it conflicts with an earlier migration doc (01-09), this doc is authoritative.

## One-screen snapshot

- **Data layer**: refactored; FMP topic modules + FRED/Yahoo macro/earnings/ownership/corporate_actions/etf/analyst/filings modules; single shared HTTP client with timeout + retry.
- **DB**: SQLite at `data/cache/finrl_trading.db`. Contains 1.94M price rows (715 stocks + 46 ETFs), 22.9K quarterly fundamentals, 51.3K macro observations, 29.5K earnings rows, **1.80M insider-trading filings**, 619 shares_float snapshots, 21.1K dividends, 203 stock splits, 11.2K ETF holdings, **182.5K analyst grades**, 651 price-target snapshots, 41.5K analyst estimates, **808K SEC filings**. Transcripts / 13F tables do not exist yet.
- **Test infrastructure**: L3 **22/22** ✓, fixture equivalence **26/26** ✓.
- **Step 4 build progress**: **7 of 10 components shipped** (`macro.py`, `earnings.py`, `ownership.py`, `corporate_actions.py`, `etf.py`, `analyst.py`, `filings.py`). Next: `transcripts.py`.
- **Known blockers**: none. Known pre-existing quirks: listed in "Gotchas" below.

## DB state as of 2026-04-24

| Table | Rows | Distinct keys | Date range | Status |
|---|---|---|---|---|
| `price_data` | 1,816,414 | 712 tickers | 2015-01-02 → 2026-04-21 | ✓ Healthy |
| `fundamental_data` | 22,909 | 715 tickers | 2015-06-30 → 2026-03-31 | ✓ Healthy |
| `macro_series` | 51,269 | 32 series (24 FRED + 8 Yahoo) | 2015-01-01 → 2026-04-23 | ✓ **Component 1 output** |
| `earnings_calendar` | 29,515 | 687 tickers (133 rows FMP_CALENDAR, rest FMP_EARNINGS) | 2015-01-03 → 2027-01-27 | ✓ **Component 2 output** |
| `insider_trading` | 1,802,939 | 668 tickers (≥10 filings) / 672 distinct | 2003-05-05 → 2026-04-27 | ✓ **Component 3 output** |
| `shares_float` | 619 | 619 tickers | 2021-10-04 → 2026-04-28 | ✓ **Component 3 output** |
| `insider_trading_fetch_log` | 715 | 715 tickers | — | Component 3 pagination checkpoint |
| `dividends` | 21,140 | 544 tickers | 2015-01-02 → 2026-05-11 | ✓ **Component 4 output** |
| `stock_splits` | 203 | 155 tickers | 2015-01-02 → 2026-05-07 | ✓ **Component 4 output** |
| `etf_holdings` | 11,233 | 36 ETFs / 5,556 distinct assets | 2023-04-17 → 2026-05-07 | ✓ **Component 5 Part A output** |
| `price_data` (ETF subset) | 130,128 | 46 ETFs | 2015-01-02 → 2026-05-06 | ✓ **Component 5 Part B added** to existing table |
| `analyst_grades` | 182,523 | 680 tickers | 2015-01-02 → 2026-05-07 | ✓ **Component 6 output** |
| `price_target_consensus` | 651 | 651 tickers | 2026-05-07 → 2026-05-07 | ✓ **Component 6 output** (synthesized snapshot_date) |
| `analyst_estimates` | 41,486 | 636 tickers / 16,578 annual + 24,908 quarter | 1993-12-30 → 2034-01-03 | ✓ **Component 6 output** (forward + historical) |
| `sec_filings` | 808,130 | 675 tickers / 182 form types | 2009-01-15 → 2026-05-07 | ✓ **Component 7 output** |
| `sec_filings_fetch_log` | 715 | 715 tickers | — | Component 7 pagination checkpoint |
| `raw_payloads` | 77,364 | 503 tickers | 2015-10-24 → 2025-09-30 | Cached FMP JSON for fundamentals |
| `news_articles` | 60 | 3 tickers | 2024-08-28 → 2024-09-01 | Smoke-test data only; not yet bulk-pulled |
| `sp500_components_details` | 4 | — | — | Point-in-time snapshots (survivorship-free source is the CSV) |

## Code layout (after Step 3 refactor)

```text
src/data/
├── data_fetcher.py (50 lines)    — facade: re-exports everything for backwards compat
└── fetcher/
    ├── __init__.py (79 lines)    — canonical public API
    ├── base.py (65)              — DataSource protocol + BaseDataFetcher ABC
    ├── client.py (244)           — FMPClient (HTTP + uniform timeout + retry + cache hook)
    ├── fmp.py (135)              — FMPFetcher class: thin facade composing topic modules
    ├── manager.py (141)          — DataSourceManager (future multi-source hook)
    ├── universes.py (103)        — sp500 constituents
    ├── prices.py (264)           — historical OHLCV + gap detection
    ├── fundamentals.py (490)     — quarterly pipeline + derived ratios (+ 2 hoisted closures)
    ├── news.py (248)             — news + optional GPT sentiment helpers
    ├── realtime.py (42)          — quote + batch-quote + actively-trading-list
    ├── macro.py (285)            — FRED + Yahoo series (Step 4 Component 1)
    ├── earnings.py (~230)        — FMP /earnings + /earnings-calendar (Step 4 Component 2)
    ├── ownership.py (~290)       — FMP /insider-trading/search + /shares-float (Step 4 Component 3)
    ├── corporate_actions.py (~230) — FMP /dividends + /splits (Step 4 Component 4)
    ├── etf.py (~210)             — FMP /etf/holdings + 46-ETF universe catalog (Step 4 Component 5)
    ├── analyst.py (~340)         — FMP /grades + /price-target-consensus + /analyst-estimates (Step 4 Component 6)
    └── filings.py (~210)         — FMP /sec-filings-search/symbol (Step 4 Component 7)
```

Every module follows the same shape: module-level public functions, takes `client` + `data_store` as explicit args (except `macro.py` which has its own FRED/Yahoo clients since it's non-FMP).

## Test infrastructure

All scripts under `scripts/`. Run from repo root with `PYTHONPATH=$(pwd)`.

| Script | Lines | Purpose | When to run |
|---|---|---|---|
| `verify_data_quality.py` | 418 | **L3 data-quality suite** — 16 checks on live DB (duplicates, adjustments, y_return formula, alignment, coverage, known-exceptions, macro coverage) | After any data-layer change; expected 16/16 pass |
| `verify_fixture_equivalence.py` | 165 | **Fixture equivalence** — 10 fixtures compared at `atol=1e-9`. Detects silent output drift from refactors | After any code-change to fetcher logic |
| `capture_golden_fixtures.py` | 141 | Captures baseline fixtures to `tests/fixtures/*.pkl`. Writes 10 pickles covering sp500, prices, fundamentals, news, macro | One-off; re-run only when expected output legitimately changes |
| `bulk_macro.py` | 123 | Incremental + resumable pull of all 32 default FRED/Yahoo series | Once to populate; re-run anytime (idempotent) |
| `bulk_earnings_per_ticker.py` | ~130 | Per-ticker `/earnings` pull for DISTINCT tickers in fundamental_data (~715) | Once to populate; re-run to refresh upcoming-announcement actuals (~13 min) |
| `bulk_earnings_calendar.py` | ~130 | Global `/earnings-calendar` forward window (today..today+90d by default), universe-filtered | As needed for upcoming-announcements watch |
| `bulk_shares_float.py` | ~110 | One-shot per ticker; accumulates snapshots via UNIQUE(ticker, snapshot_date) | Once to populate; weekly/monthly to track dilution |
| `bulk_insider_trading.py` | ~140 | Paginated per-ticker; crash-safe via `insider_trading_fetch_log`. Auto-resumes from `last_page + 1` on re-run. `--no-resume` for full re-scan | Once to populate (~6h wall); periodic re-runs for new filings |
| `bulk_dividends.py` | ~110 | One call per ticker; UNIQUE(ticker, date) for dedup. Default save scope 2015+. `--start` / `--tickers` overrides | Once to populate (~14 min); periodic re-runs to pick up new dividends |
| `bulk_splits.py` | ~110 | Same shape as bulk_dividends; most tickers have 0-1 splits in scope | Once to populate (~14 min); rare re-runs |
| `bulk_etf_holdings.py` | ~110 | One call per ETF in `ETF_UNIVERSE` (46 ETFs); accumulating snapshot history via UNIQUE(etf_symbol, asset, snapshot_date) | Once to populate (~1 min); weekly to track constituent drift |
| `bulk_etf_prices.py` | ~110 | Reuses `fetch_price_data` for the 46 ETFs into existing `price_data` table; defensive collision-check vs. stock universe | Once to populate (~2 min); periodic for fresh prices |
| `bulk_analyst_grades.py` | ~110 | One call per ticker — `/grades` returns all-history; UNIQUE(ticker, date, grading_company) dedup; default save scope 2015+ | Once to populate (~14 min); daily for new actions |
| `bulk_price_targets.py` | ~110 | One call per ticker — singleton snapshot with synthesized snapshot_date; UNIQUE(ticker, snapshot_date) dedups same-day reruns | Weekly to track target revisions |
| `bulk_analyst_estimates.py` | ~120 | Two calls per ticker (quarter + annual); UNIQUE(ticker, date, period); rows include both historical + forward periods | Once to populate (~28 min); after each earnings season |
| `bulk_sec_filings.py` | ~140 | Paginated per-ticker over [from, to] window; crash-safe via `sec_filings_fetch_log`. Auto-resumes from `last_page + 1`. `--no-resume` for full re-scan | Once to populate (~3.5h wall); periodic re-runs for new filings |
| `probe_fmp_endpoints.py` | 214 | Live probes for FMP endpoints (used to build the Step 4 inventory in 08_step4_endpoint_inventory.md) | As needed when adding new FMP endpoints |
| `verify_fundamentals_db_crosscheck.py` | 87 | **Diagnostic only** (not a regression check) — compares fetcher output vs DB rows for AAPL/XOM/JPM. See its docstring for why they diverge. | Rarely — only if debugging post-processor drift |

Golden fixtures live in `tests/fixtures/` (10 `.pkl` files, ~500 KB total). Probe sample JSONs live in `tests/probes/`.

### Expected suite output

```bash
python scripts/verify_data_quality.py        # → 22/22 passed
python scripts/verify_fixture_equivalence.py # → 26/26 passed
```

## Completion log — what's been built

### Step 1 (data acquisition) — done

- Extracted pre-populated 437 MB DB from `data/finrl_trading.7z` into `data/cache/finrl_trading.db`
- Imported 22,909 quarterly fundamentals from `fundamental_data_full.csv`
- Backfilled ~520K new price rows for the 209 survivorship-free tickers that the extracted DB had fundamentals for but no prices
- **3 weaknesses investigated & documented in 07_data_quality.md**
  - #1: `close` == `adj_close` across all rows (both hold split-adjusted, FMP stable endpoint is split-adjusted but not dividend-adjusted)
  - #2: SPY/QQQ started 2020-12-01 — fixed by delete + re-fetch 2015→now
  - #3: Sector ETFs / short interest — sector ETFs deferred to Step 4; short interest confirmed unavailable on stable API for our tier (16 URL variants tried)

### Step 2 (mechanical refactor) — done

- Moved `src/data/data_fetcher.py` (1488 lines) into `src/data/fetcher/{base, fmp, manager, api}.py`
- `data_fetcher.py` became a 38-line facade. Zero behavior changes, L3 + fixture equivalence both pass.
- Fixed a dormant `NameError` on `project_root` in `get_sp500_members_at_date` / `get_all_historical_sp500_tickers` that my Step 2 move introduced and no test caught

### Step 3 (real refactor) — done

- Extracted shared `FMPClient` with 30s timeout + retry-on-5xx + 429 backoff + local-first cache path
- Split `fmp.py` into 5 topic modules: `prices`, `fundamentals`, `news`, `universes`, `realtime`
- Promoted 3 realtime methods (previously zero callers) into `realtime.py` with timeout + retry — per user's call, not deleted
- Deleted dead code: duplicate `mcal` imports, `ajexdi` fallback, `_standardize_fundamental_data` (unused on FMP path)
- Hoisted 2 local closures in `get_fundamental_data` to module-level helpers (`_align_to_mjsd_first`, `_index_by_date`)
- `fmp.py` shrunk from 1034 → 135 lines
- Captured 7 golden fixtures before refactor; all 7 matched byte-identically (`atol=1e-9`) after refactor
- **Finding**: the DB's `fundamental_data` rows diverge from `get_fundamental_data`'s current output on 54 of 57 columns. This is pre-existing post-processing drift (`fix_adj_close.py` + `fill_recent_yreturn.py` recompute `y_return`/`adj_close_q` using yfinance), not a refactor regression. Documented in `verify_fundamentals_db_crosscheck.py` docstring.

### Step 4 Component 1 — `macro.py` — done (2026-04-23 → 2026-04-24)

- New module `src/data/fetcher/macro.py` — 32 series (24 FRED + 8 Yahoo)
- New table `macro_series` with `UNIQUE(series_id, source, date)` + index
- 3 new `DataStore` methods (`save_macro_series`, `get_macro_series`, `get_macro_series_latest_date`)
- `FREDSettings` added to `src/config/settings.py` + `FRED_API_KEY` in `.env` (user-provided)
- `fredapi>=0.5.0` added to `requirements.txt`
- `scripts/bulk_macro.py` — resumable incremental pull; first run populated 51,263 rows in 60 seconds
- L3 suite extended: 15 → 16 checks (added `check_macro_coverage`)
- Fixture suite extended: 7 → 10 fixtures (DGS10 daily, CPIAUCSL monthly, ^VIX daily)
- All re-exports: `from src.data.data_fetcher import fetch_macro_data` works
- **Pre-existing bug fixed during this work**: `.env` had `DATA_BASE_DIR=./data` but the real DB lives at `./data/cache/` (consolidated in Step 1). Only `FMPFetcher` masked this via a hardcoded `cache_dir="./data/cache"` override; the 4 no-arg `get_data_store()` callers (including `fetch_and_store_fundamentals.py` and `backfill_historical_sp500.py`) were silently broken. Fixed: changed `.env` to `DATA_BASE_DIR=./data/cache`. Noted inline in `.env` with a comment.

### Step 4 Component 2 — `earnings.py` — done (2026-04-24)

- New module `src/data/fetcher/earnings.py` — 3 public functions (`fetch_earnings_per_ticker`, `fetch_earnings_calendar`, `fetch_all_earnings`)
- New table `earnings_calendar` with `UNIQUE(ticker, date)` — hybrid feed from 2 FMP endpoints, `source` column tracks provenance (`FMP_EARNINGS` vs `FMP_CALENDAR`), last-write-wins
- 3 new `DataStore` methods (`save_earnings_calendar`, `get_earnings_calendar`, `get_earnings_latest_date`)
- 2 new bulk scripts: `bulk_earnings_per_ticker.py` (~715 calls, ~13 min) + `bulk_earnings_calendar.py` (monthly windows, forward-only default)
- L3 suite extended: 16 → 17 checks (added `check_earnings_coverage` — asserts ≥500 tickers with ≥4 rows, no all-null rows)
- Fixture suite extended: 10 → 13 fixtures (earnings_AAPL/MSFT/NVDA 2020-2023)
- All re-exports: `from src.data.data_fetcher import fetch_earnings_per_ticker` works
- **First bulk run**: 29,454 rows pulled across 685 of 715 tickers (30 had no data — delisted/merged). 30 all-null placeholder rows from FMP (defunct tickers like BBBY, SIVB, FRC, ATVI) filtered at save time.
- **Ticker-format migration (2026-04-24, post-bulk)**: discovered `fundamental_data` stored `BRK.B`/`BF.B` (dot format, legacy from Step 1 CSV import) while everything else uses FMP-native hyphen (`BRK-B`/`BF-B`). Migrated dots → hyphens in `fundamental_data` (80 rows) and `data/sp500_historical_constituents.csv` (2,709 daily snapshots). Removed `SYMBOL_ALIAS_FUND_TO_PRICE` map and its `.replace()` call from `scripts/verify_data_quality.py` alignment check — join now works directly on the ticker column. Updated `ML_STOCK_SELECTION.md:513` doc note. Refetched earnings for `BRK-B`/`BF-B` — 91 new rows, coverage now 687/715 tickers. CSV backup at `data/sp500_historical_constituents.csv.bak`. L3 alignment check (99.95% coverage) still green — critical proof of end-to-end consistency.
- **Design note on hybrid sources**: per-ticker endpoint is authoritative for historical (2015→now, estimates populated, clean S&P-500 scope). Global-calendar is for forward watch only; running it with `--from today` avoids overwriting historical per-ticker rows via the `UNIQUE(ticker, date)` constraint.
- **Design note on incremental**: `fetch_earnings_per_ticker` does NOT skip based on DB freshness — it always makes the 1 API call and upserts. Rationale: upcoming-earnings rows need their actuals updated post-announcement, and FMP occasionally revises historical EPS; skipping creates a freshness gap for a zero-call saving.

### Step 4 Component 3 — `ownership.py` — done (2026-04-26 → 2026-04-27)

- New module `src/data/fetcher/ownership.py` (~290 lines) — first **Class C (paginated)** module in the fetcher. Public API: `fetch_insider_trading`, `fetch_insider_trading_page`, `fetch_shares_float`, `fetch_all_insider_trading`, `fetch_all_shares_float`.
- 3 new tables: `insider_trading` (UNIQUE 5-col), `shares_float` (accumulating history via UNIQUE(ticker, snapshot_date)), `insider_trading_fetch_log` (per-ticker pagination checkpoint, crash-safe resume).
- 5 new `DataStore` methods: save/get for both tables + `get_insider_fetch_progress` / `update_insider_fetch_progress`.
- 2 new bulk scripts: `bulk_shares_float.py` (one-shot per ticker, ~4h wall in practice due to FMP retries) + `bulk_insider_trading.py` (paginated, resumable; estimated ~6h wall, 942K rows after first 410/715 tickers).
- L3 suite: 17 → 18 checks (added `check_ownership_coverage` — asserts ≥400 tickers with ≥10 insider filings + ≥600 tickers with positive `float_shares`).
- Fixture suite: 13 → 15 fixtures (`insider_AAPL_2022.pkl` historical window + `shares_float_sample.pkl` for AAPL/MSFT/NVDA).
- **Refactor (scope-tight)**: promoted private `_universe_tickers` from `earnings.py` to public `get_universe_tickers` in `universes.py`. Updated 2 callers in `bulk_earnings_per_ticker.py` + `bulk_earnings_calendar.py`. `earnings.py` keeps a backwards-compat shim.
- **Pagination + crash-resume design**: each successful page write updates the `insider_trading_fetch_log` checkpoint (`last_page`, `last_filing_date`). On re-run, `fetch_insider_trading` skips through `last_page + 1`. Empty-page sentinel records the "fully fetched" state so subsequent resume runs skip the ticker entirely until `--no-resume` is used.
- **Why so slow**: FMP's `/insider-trading/search` runs ~25-100s per ticker (5-100+ pages × 1-2s/page). Apparent retries on rate-limit lengthen the wall further. shares_float (1 call/ticker) similarly took 4h vs. expected 15min — heavy 429 backoff.
- **Bulk completed in two passes**: paused at 410/715 on 2026-04-26 to defer to overnight; resumed 2026-04-27 → 2026-04-28 to finish remaining 305. Final: 1,802,939 rows / 672 distinct tickers (668 with ≥10 filings).
- **Late-discovered placeholder filter**: 5 shares_float rows had `float_shares = 0 or NULL` (delisted tickers like TWTR-taken-private-day, ABMD post-J&J, FB-renaming gotcha, SPLS, CAM). Added a save-time filter in `_normalize_shares_float` mirroring the earnings all-null pattern. Cleaned existing 5 rows; L3 flipped to 18/18.

### Step 4 Component 4 — `corporate_actions.py` — done (2026-05-07)

- New module `src/data/fetcher/corporate_actions.py` (~230 lines) — bundles `/dividends` + `/splits` (both trivial Class B, 1 call returns all-history). Public API: `fetch_dividends`, `fetch_splits`, `fetch_all_dividends`, `fetch_all_splits`.
- 2 new tables: `dividends` (UNIQUE(ticker, date)) + `stock_splits` (UNIQUE(ticker, date)). Standard pattern, no fetch_log needed.
- 4 new `DataStore` methods: `save_dividends`, `get_dividends`, `save_splits`, `get_splits`.
- 2 new bulk scripts: `bulk_dividends.py` + `bulk_splits.py` (~14 min wall each, ran in parallel).
- L3 suite: 18 → 19 checks (`check_corporate_actions_coverage` — ≥200 dividend tickers + sanity bounds, ≥10 split tickers).
- Fixture suite: 15 → 18 fixtures (dividends_AAPL, dividends_KO 2018-2024 historical windows + splits_AAPL all-time).
- **`yield` → `yield_pct` rename**: FMP returns the column as `yield` (Python keyword). Renamed at the normalizer layer to keep downstream code clean (`df.yield_pct` instead of forced `df["yield"]`). Single-line `_normalize_dividends` change.
- **First bulk run**: 21,140 dividends across 544 tickers (~75% of universe pays dividends), 203 splits across 155 tickers. AAPL 2020 4:1 split present and verified by L3.
- **Bundle vs split**: kept as one module rather than `dividends.py` + `splits.py`. Both endpoints share fetch shape and bulk pattern; splitting would create 2 near-identical files. Module name `corporate_actions.py` keeps room for future related events (spinoffs, M&A) if ever added.

### Step 4 Component 5 — `etf.py` + 46-ETF price backfill — done (2026-05-07)

- New module `src/data/fetcher/etf.py` (~210 lines) with module-level `ETF_UNIVERSE` dict (46 entries: 11 sectors, 3 broad, 7 style, 7 bonds, 4 commodities, 2 international, 1 vol, 1 currency, 10 sub-sectors, plus SPY). Public API: `fetch_etf_holdings`, `fetch_all_etf_holdings`.
- New table `etf_holdings` with `UNIQUE(etf_symbol, asset, snapshot_date)` — accumulating constituent history, snapshot_date derived from FMP's `updatedAt[:10]`.
- 2 new `DataStore` methods (`save_etf_holdings`, `get_etf_holdings`) — `get_etf_holdings` supports reverse-lookup (`asset='AAPL'` → which ETFs hold it).
- 2 new bulk scripts: `bulk_etf_holdings.py` (~1 min wall) + `bulk_etf_prices.py` (~2 min wall, reuses existing `fetch_price_data`).
- L3 suite: 19 → 20 checks (`check_etf_coverage`).
- Fixture suite: 18 → 20 fixtures (`etf_holdings_SPY` pinned to latest snapshot_date for stability + `etf_prices_XLK` 2020-2023 historical slice).
- **Defense check**: `bulk_etf_prices.py` verifies `set(ETF_UNIVERSE) ∩ get_universe_tickers() == ∅` before running, refusing to start otherwise. Prevents accidental overwrite of equity prices.
- **FMP coverage gap**: 10 of 46 ETFs return 0 holdings rows (AGG, GLD, HYG, IEF, LQD, SHY, SLV, TIP, TLT, USO — all bond/commodity ETFs whose constituents aren't equities and aren't exposed via `/etf/holdings`). Plus UUP/VXX/DBC are 1-2 asset funds by design. Equity-style ETFs (sectors, broad, style, sub-sectors) are fully covered — 33 ETFs with ≥20 holdings, totaling 11,233 rows. **L3 thresholds were relaxed from initial spec (≥40 ETFs / ≥30 holdings) to (≥25 / ≥20)** to reflect this reality without compromising the "do we have meaningful data?" check.
- **First bulk run**: 11,233 holdings rows from 36 ETFs (snapshot dates ranging 2023-04-17 → 2026-05-07 — FMP's per-ETF refresh cadence varies). Price backfill: 130,128 rows across all 46 ETFs, 2015-01-02 → 2026-05-06.

### Step 4 Component 6 — `analyst.py` — done (2026-05-07)

- New module `src/data/fetcher/analyst.py` (~340 lines) bundling 3 FMP analyst endpoints. Public API: `fetch_analyst_grades`, `fetch_price_target_consensus`, `fetch_analyst_estimates`, plus 3 batch wrappers + `ESTIMATE_PERIODS`.
- 3 new tables: `analyst_grades` (UNIQUE(ticker, date, grading_company)), `price_target_consensus` (UNIQUE(ticker, snapshot_date), accumulating via synthesized `snapshot_date = today`), `analyst_estimates` (UNIQUE(ticker, date, period); 22 numeric columns + 2 analyst counts).
- 6 new `DataStore` methods: save/get for each table.
- 3 new bulk scripts (separate cadences justify separation): `bulk_analyst_grades.py` (~14 min, daily), `bulk_price_targets.py` (~14 min, weekly), `bulk_analyst_estimates.py` (~28 min, after earnings seasons).
- L3 suite: 20 → 21 checks (`check_analyst_coverage` — combined for all 3 tables: ≥400 grades-tickers, ≥500 targets-tickers, ≥500 estimates-tickers).
- Fixture suite: 20 → 24 fixtures (`grades_AAPL_2018_2024` historical window, `price_targets_sample` 3-ticker snapshot pinned to latest snapshot_date, `estimates_AAPL_quarter`, `estimates_AAPL_annual`).
- **Bulk wall time**: grades + targets ran in parallel (~14 min wall); estimates sequentially (~28 min). Total ~42 min, no FMP retry storms — analyst endpoints are well-behaved on the stable API.
- **Schema notes**:
  - `price_target_consensus` synthesizes `snapshot_date = today` (FMP returns no date); same accumulating pattern as `shares_float`.
  - `analyst_estimates` returns BOTH historical and forward rows in a single call. Historical estimates (e.g. AAPL annual back to 1996) are real point-in-time consensus values worth keeping. UNIQUE(ticker, date, period) dedups across re-runs; INSERT OR REPLACE refreshes if FMP revises.
  - The `period` column distinguishes quarter/annual rows in one table — cleaner than two parallel tables for identical 22-column schemas.
- **First bulk run**: 182,523 grades across 680 tickers (2015-2026), 651 price-target snapshots (one per covered ticker), 41,486 estimates rows (16,578 annual + 24,908 quarter, range 1993-12-30 → 2034-01-03).
- **Late fix**: forgot to add `analyst` to the `--only` default in `verify_fixture_equivalence.py` initially — fixture suite reported 20/20 instead of 24/24 until I added it. Quick one-line fix.

### Step 4 Component 7 — `filings.py` — done (2026-05-08)

- New module `src/data/fetcher/filings.py` (~210 lines) — second Class C (paginated) module after Component 3's ownership. Public API: `fetch_sec_filings_page`, `fetch_sec_filings`, `fetch_all_sec_filings`.
- 2 new tables: `sec_filings` (UNIQUE 4-tuple ticker+filing_date+form_type+accepted_date) and `sec_filings_fetch_log` (per-ticker pagination checkpoint, mirrors `insider_trading_fetch_log`).
- 4 new `DataStore` methods: `save_sec_filings`, `get_sec_filings`, `get_sec_fetch_progress`, `update_sec_fetch_progress`.
- 1 new bulk script: `scripts/bulk_sec_filings.py` (~3.5h wall for full backfill).
- L3 suite: 21 → 22 checks (`check_sec_filings_coverage`).
- Fixture suite: 24 → 26 fixtures (`sec_filings_AAPL_2024` window + `sec_filings_AAPL_form4_2023` form-type slice).
- **Endpoint quirk caught during smoke**: `/sec-filings-search/symbol` REQUIRES `from` and `to` params — returns HTTP 400 without them. Inventory row 13 lists them, but I missed they were required (not optional). Adapted module to take `from_date` (default 2015-01-01) + `to_date` (default today). The `from` param is a Python keyword so it's passed via dict-spread (`**{"from": from_date, "to": to_date}`).
- **Form-type coverage unfiltered by design**: probe shows Form 4 dominates (46% of AAPL sample), but 8-K, 10-K, 10-Q, DEF 14A, 13D/13G, 144, S-1 etc. are all valuable for different analyses. 182 distinct form types landed in DB. Storage trivial (~250 MB). Filter at query time via `WHERE form_type = 'X'`.
- **Form 4 overlap with `insider_trading`**: documented design choice. `insider_trading` has parsed transactions (who/qty/price); `sec_filings` has filing-level metadata. Both retained, joinable via `(ticker, filing_date)` if ever needed.
- **First bulk run**: 808,130 rows across 675 tickers (40 tickers returned 0 — delisted/ADR/no-EDGAR), 182 distinct form types, 2009-01-15 → 2026-05-07 (some tickers go pre-2015 since FMP returns whatever's in the window). Wall: 12,966s ≈ 3.6h. 0 failures.
- **UNIQUE collisions on FMP duplicates**: 8 rows out of 1500 in the 3-ticker smoke were collapsed by the UNIQUE 4-tuple — FMP occasionally returns the same filing twice across page boundaries. Expected; INSERT OR REPLACE handles it cleanly.

## Step 4 remaining — 3 components

Per the component-by-component execution agreed with the user (1 component → review → next). Overview lives in [09_step4_build_plan.md](./09_step4_build_plan.md). Detailed per-endpoint schemas live in [08_step4_endpoint_inventory.md](./08_step4_endpoint_inventory.md).

| Order | Component | Status | Notes |
|---|---|---|---|
| 1 | `macro.py` (FRED + Yahoo) | ✅ Done | — |
| 2 | `earnings.py` (calendar + per-ticker) | ✅ Done | Hybrid source strategy: per-ticker for history, global for forward window |
| 3 | `ownership.py` (insider + shares_float) | ✅ Done | First Class C paginated module; per-ticker `insider_trading_fetch_log` for crash-safe resume |
| 4 | `corporate_actions.py` (dividends + splits) | ✅ Done | Bundled module; 21K dividends + 203 splits |
| 5 | `etf.py` (holdings) + ETF price backfill | ✅ Done | 11K holdings (36 ETFs) + 130K ETF prices (46 ETFs); 10 bond/commodity ETFs lack FMP holdings coverage |
| 6 | `analyst.py` (grades + targets + estimates) | ✅ Done | 182K grades + 651 target snapshots + 41K estimates; 3 separate bulk scripts for different cadences |
| 7 | `filings.py` (SEC) | ✅ Done | 808K filings / 675 tickers / 182 form types; second Class C paginated module |
| 8 | `transcripts.py` | — | long background pull: 42,529 transcripts ~ 12-24h wall |
| 9 | `institutional.py` (13F) | — | curate ~100-CIK seed list first; pull per-CIK for all quarters |
| 10 | News bulk | — | `news/stock` hard-capped at 250 per call; need date-window splitting; ~85K calls; ~12-24h wall |

Short interest is **dropped** — unavailable on stable API for our tier (16 variants tried).

## Critical files to know

### Configuration

- **`.env`** (user-local, not committed): `FMP_API_KEY`, `FRED_API_KEY`, `DATA_BASE_DIR=./data/cache`. Last one is critical — see the `.env` comment explaining why it moved.
- **`.env.example`** (committed): matches the real `.env` structure.
- **`src/config/settings.py`**: `FinRLSettings` → `.fmp.api_key`, `.fred.api_key`, `.openai.api_key`, `.data.base_dir`. Uses `pydantic-settings`.

### Code

- **Public API consumers should always use**: `from src.data.data_fetcher import ...` (facade) OR `from src.data.fetcher import ...` (canonical). Never import from deep topic modules except for internal tooling like `scripts/bulk_*.py`.
- **Adding new endpoints**: drop a topic module into `src/data/fetcher/*.py`, add the re-export in `src/data/fetcher/__init__.py` and `src/data/data_fetcher.py`, add the data_store table + save/get methods, add a bulk script, add L3 check + fixture. Pattern is established — see `macro.py` + associated pieces as the most recent template.

### Data

- **`data/cache/finrl_trading.db`** (482 MB): the authoritative DB. Never commit; 7z archive exists at `data/finrl_trading.7z` for disaster recovery.
- **`data/sp500_historical_constituents.csv`**: survivorship-free universe source (1996–2026). Used by `get_sp500_members_at_date` and `get_all_historical_sp500_tickers`.

## Gotchas (pre-existing — don't "fix" without context)

1. **MON ticker reuse**: price rows 2021-03-16 → 2022-12-23 under `MON` are a different company (not Monsanto, which was acquired 2018). Fundamentals rows for the same range are consistent with those prices. L3 check `mon_ticker_reuse_still_present` asserts this quirk is still there — if it disappears, investigate before updating the doc.
2. **FB ticker reuse**: 5 fundamental rows 2024-Q4 → 2025-Q4 under `FB` are a new company, not Meta (which renamed in 2022). Do NOT blanket-alias `FB → META`; time-gate any such alias at 2022-06-09 if it's ever needed.
3. **5 merged-out tickers lack prices**: `BXLT, PCL, RHT, SNI, TWC`. FMP doesn't retain prices for tickers fully absorbed post-merger. Filter at query time via `INNER JOIN` on price_data.
4. **y_return / adj_close_q drift in the DB**: 54 of 57 columns in `fundamental_data` diverge from what `get_fundamental_data` would now compute. This is post-processing by `src/data/fix_adj_close.py` and `src/data/fill_recent_yreturn.py` (yfinance-sourced recompute) — NOT a refactor regression. See `scripts/verify_fundamentals_db_crosscheck.py` docstring for details.
5. **`close` is split-adjusted everywhere**: no unadjusted close is stored. Not dividend-adjusted either. `close == adj_close` for all rows (since `adj_close` defaults to `close` when FMP's stable endpoint doesn't return `adjClose` separately). See 07_data_quality.md for details.
6. **Volume is NOT adjusted for splits**: `close * volume` (dollar volume) crosses an adjustment boundary. Avoid or compute dollar volume with separate adjusted-volume logic.
7. **Short interest unavailable**: 16 URL variants tried on the stable API, all 404. Not worth trying again unless FMP adds a new endpoint.
8. **Ad-hoc `yfinance` calls in 5 existing files** (`ml_bucket_selection.py`, `dashboard.py`, `fill_recent_yreturn.py`, `fix_adj_close.py`, `adaptive_rotation/data_preprocessor.py`) — pre-date `macro.py`. Migration to the canonical module is a future cleanup, deliberately not done in Component 1.

## Reference map

If you need more detail than this doc provides:

| You want | Read |
|---|---|
| Why this fork exists + reading order | `docs/migration/README.md` |
| The Sharpe 1.96 system being ported | `docs/migration/01_validated_baseline.md` |
| Architecture vision (macro agent, Stage 1 classifier, RL) | `docs/migration/03_architecture_vision.md` |
| Data decisions (FMP tier, universe choice, FRED + Yahoo) | `docs/migration/06_data_decisions.md` |
| Weaknesses #1-#3 investigation + fixes | `docs/migration/07_data_quality.md` |
| FMP stable endpoint schemas + volume estimates | `docs/migration/08_step4_endpoint_inventory.md` |
| Step 4 overall plan (9 modules, 3 fetch classes, priority order) | `docs/migration/09_step4_build_plan.md` |
| Current component plan (when in plan mode) | `~/.claude/plans/*.md` |
| Git history of actual changes | `git log --oneline docs/migration/ src/data/fetcher/ scripts/` |

## How to resume work on Component 8 (`transcripts.py`)

1. Read this doc (you're here).
2. Skim `docs/migration/08_step4_endpoint_inventory.md` row 3 (transcripts) + 3a (transcript date index) — the **headliner of Step 4** in volume.
3. Ensure L3 + fixture suites pass (`22/22` and `26/26`).
4. Trigger plan mode; Claude will run Phase 1 exploration then draft a new plan file in `~/.claude/plans/`.
5. Review + approve plan, then execute. Expected scope: ~42K transcripts, 60-100 MB of text, 12-24h overnight pull. Two-step fetch: pre-index `/earning-call-transcript-dates?symbol=X` for the (year, quarter) pairs per ticker (~715 calls cheap), then `/earning-call-transcript?symbol=X&year=Y&quarter=Q` per pair (~42K calls slow).
