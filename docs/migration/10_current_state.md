# Current State — read this first after context reset

Last updated: **2026-04-24**. This is the resumption document. Any fresh session (human or Claude) should read this before touching code.

If it conflicts with an earlier migration doc (01-09), this doc is authoritative.

## One-screen snapshot

- **Data layer**: refactored; FMP topic modules + FRED/Yahoo macro module + earnings module; single shared HTTP client with timeout + retry.
- **DB**: SQLite at `data/cache/finrl_trading.db` (482 MB). Contains 1.82M price rows, 22.9K quarterly fundamentals, 51.3K macro observations, 29.4K earnings rows. News table exists but essentially empty. Transcripts / insider / SEC / 13F tables do not exist yet.
- **Test infrastructure**: two regression suites (L3 data quality + fixture equivalence). Both green: 17/17 and 13/13.
- **Step 4 build progress**: **2 of 10 components shipped** (`macro.py`, `earnings.py`). Next: `ownership.py`.
- **Known blockers**: none. Known pre-existing quirks: listed in "Gotchas" below.

## DB state as of 2026-04-24

| Table | Rows | Distinct keys | Date range | Status |
|---|---|---|---|---|
| `price_data` | 1,816,414 | 712 tickers | 2015-01-02 → 2026-04-21 | ✓ Healthy |
| `fundamental_data` | 22,909 | 715 tickers | 2015-06-30 → 2026-03-31 | ✓ Healthy |
| `macro_series` | 51,269 | 32 series (24 FRED + 8 Yahoo) | 2015-01-01 → 2026-04-23 | ✓ **Component 1 output** |
| `earnings_calendar` | 29,424 | 685 tickers (135 rows FMP_CALENDAR, rest FMP_EARNINGS) | 2015-01-03 → 2027-01-27 | ✓ **Component 2 output** |
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
    └── earnings.py (~230)        — FMP /earnings + /earnings-calendar (Step 4 Component 2)
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
| `probe_fmp_endpoints.py` | 214 | Live probes for FMP endpoints (used to build the Step 4 inventory in 08_step4_endpoint_inventory.md) | As needed when adding new FMP endpoints |
| `verify_fundamentals_db_crosscheck.py` | 87 | **Diagnostic only** (not a regression check) — compares fetcher output vs DB rows for AAPL/XOM/JPM. See its docstring for why they diverge. | Rarely — only if debugging post-processor drift |

Golden fixtures live in `tests/fixtures/` (10 `.pkl` files, ~500 KB total). Probe sample JSONs live in `tests/probes/`.

### Expected suite output

```bash
python scripts/verify_data_quality.py        # → 17/17 passed
python scripts/verify_fixture_equivalence.py # → 13/13 passed
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
- **Design note on hybrid sources**: per-ticker endpoint is authoritative for historical (2015→now, estimates populated, clean S&P-500 scope). Global-calendar is for forward watch only; running it with `--from today` avoids overwriting historical per-ticker rows via the `UNIQUE(ticker, date)` constraint.
- **Design note on incremental**: `fetch_earnings_per_ticker` does NOT skip based on DB freshness — it always makes the 1 API call and upserts. Rationale: upcoming-earnings rows need their actuals updated post-announcement, and FMP occasionally revises historical EPS; skipping creates a freshness gap for a zero-call saving.

## Step 4 remaining — 8 components

Per the component-by-component execution agreed with the user (1 component → review → next). Overview lives in [09_step4_build_plan.md](./09_step4_build_plan.md). Detailed per-endpoint schemas live in [08_step4_endpoint_inventory.md](./08_step4_endpoint_inventory.md).

| Order | Component | Status | Notes |
|---|---|---|---|
| 1 | `macro.py` (FRED + Yahoo) | ✅ Done | — |
| 2 | `earnings.py` (calendar + per-ticker) | ✅ Done | Hybrid source strategy: per-ticker for history, global for forward window |
| 3 | `ownership.py` (insider + shares_float) | Next | paginated — `insider-trading/search?page=N&limit=100` terminates when page returns <100 |
| 4 | `corporate_actions.py` (dividends + splits) | — | trivial Class B (one call per ticker, all-history) |
| 5 | `etf.py` (holdings) + ETF price backfill | — | unblocks sector-rotation features |
| 6 | `analyst.py` (grades + targets + estimates) | — | three endpoints in one module |
| 7 | `filings.py` (SEC) | — | paginated; requires `from`/`to` date params |
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

## How to resume work on Component 3 (`ownership.py`)

1. Read this doc (you're here).
2. Skim `docs/migration/08_step4_endpoint_inventory.md` rows 9-10 for insider-trading + shares-float endpoint schemas.
3. Ensure L3 + fixture suites pass (`17/17` and `13/13`).
4. Trigger plan mode; Claude will run Phase 1 exploration then draft a new plan file in `~/.claude/plans/`.
5. Review + approve plan, then execute.
