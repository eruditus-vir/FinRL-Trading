# Step 4 — Build the full data layer

Draft plan (2026-04-23). Ready to convert to formal plan-mode artifact when you trigger it; until then, this is the living project document.

## Context

Step 3 left us with a clean 7-module fetcher in `src/data/fetcher/` backed by a shared `FMPClient` (timeout + retry), a green L3 safety net, and byte-identical fixture equivalence. The data layer currently covers: prices, fundamentals, news, sp500 constituents, realtime, nasdaq-100.

Step 4 adds the 12 remaining FMP endpoints plus FRED/Yahoo macro + bulk-pulls for everything we haven't yet pulled. User's explicit calls: pull **all** 715 tickers for news, pull **all** ~100 seed CIKs for 13F, **resume-at-ticker-N** for failure recovery, **no GPT sentiment** on the news bulk-pull (text only).

The inventory from Phase 1 probes is at [docs/migration/08_step4_endpoint_inventory.md](./08_step4_endpoint_inventory.md) with sample responses in [tests/probes/](../../tests/probes/). Short interest confirmed unavailable on our tier — dropped from scope.

## Scope

**IN:**
- 12 new FMP endpoint modules (earnings calendar + transcripts, dividends, splits, analyst grades/targets/estimates, insider trades, shares float, 13F, ETF holdings, SEC filings)
- FRED + Yahoo macro series (11 FRED + 8 Yahoo + economic events CSV)
- Full bulk-pull: 2015→now for all 715 fundamental tickers + 100 seed CIKs + 46 ETFs
- Resumable fetch scripts (per-ticker checkpoint)
- 16 new DB tables (12 data + 4 fetch-log/progress)
- L3 suite extensions (one check per new table) + smoke-test fixtures

**OUT:**
- Short interest (unavailable on stable API at our tier — documented as future-work)
- GPT sentiment on news backfill (user explicitly excluded — pure article text only)
- Direct "who holds AAPL" endpoint (doesn't exist; we derive via per-CIK 13F bulk-pull)
- Any refactor of existing Step 3 code

## Architecture — three fetch classes

Every new endpoint falls into one of three patterns. Pick the right pattern = right boilerplate.

| Class | Examples | Fetch shape | Progress tracking |
|---|---|---|---|
| **A. Singleton-per-ticker** | `shares_float`, `price_target_consensus` | 1 call per ticker → 1 row, run once | Row count / `max(created_at)` per table |
| **B. Quarterly per-ticker** | `earnings`, `transcripts`, `analyst_estimates` | N calls per ticker (one per quarter/year) | `raw_payloads` table via `get_raw_payload_latest_date` |
| **C. Paginated date-based** | `insider_trading`, `sec_filings`, `news`, `dividends` (all-history), `splits` (all-history), `analyst_grades` | Paginate `?page=N&limit=100` until empty result | Per-endpoint `<name>_fetch_log(ticker, last_page, last_date, fetched_at)` |

**13F special case**: iterate seed CIKs × quarters 2015-Q1 → 2026-Q1. Treat like Class B but keyed on `(cik, year, quarter)` instead of `(ticker, year, quarter)`.

## Module structure — 8 new files

Mirror Step 3's shape — one file per domain, each exposes module-level public functions that take `client: FMPClient` as first arg.

```
src/data/fetcher/
├── earnings.py             # NEW — earnings calendar + transcripts + per-ticker earnings
├── corporate_actions.py    # NEW — dividends + splits
├── analyst.py              # NEW — grades + price-target-consensus + analyst-estimates
├── ownership.py            # NEW — insider-trading + shares-float
├── institutional.py        # NEW — 13F per-CIK + industry-summary + holder-performance
├── etf.py                  # NEW — ETF constituent holdings
├── filings.py              # NEW — SEC filings index
└── macro.py                # NEW — FRED + Yahoo (no FMPClient; separate clients)
```

Plus updates:
- `fetcher/__init__.py` — re-export new public functions
- `fetcher/fmp.py` — new `FMPFetcher` delegates: `get_earnings_calendar`, `get_transcripts`, etc.
- `data_fetcher.py` (facade) — re-export the new symbols

**Common signature pattern** (every module):
```python
def get_<endpoint>(client: FMPClient, data_store, <key_args>, <optional_args>) -> pd.DataFrame:
    # 1. Read cache (if applicable)
    # 2. Check offline_mode, short-circuit if needed
    # 3. client.get_json(...) for the HTTP
    # 4. Upsert via data_store.save_<endpoint>(df)
    # 5. Return DataFrame from DB
```

## DB schema — 16 new tables

All declared inside `DataStore._init_database()` in `src/data/data_store.py`, following the existing `CREATE TABLE IF NOT EXISTS` + UNIQUE-for-upsert + `ALTER TABLE ADD COLUMN` migration idiom.

### Data tables (12)

```sql
-- Earnings calendar (global or per-ticker — one table)
CREATE TABLE IF NOT EXISTS earnings_calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    eps_actual REAL, eps_estimated REAL,
    revenue_actual REAL, revenue_estimated REAL,
    last_updated TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS earnings_transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    year INTEGER NOT NULL, quarter INTEGER NOT NULL,
    transcript_date TEXT, content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, year, quarter)
);

CREATE TABLE IF NOT EXISTS dividends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, date TEXT NOT NULL,
    record_date TEXT, payment_date TEXT, declaration_date TEXT,
    adj_dividend REAL, dividend REAL, yield REAL, frequency TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS stock_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, date TEXT NOT NULL,
    numerator INTEGER, denominator INTEGER, split_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS analyst_grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, date TEXT NOT NULL,
    grading_company TEXT, previous_grade TEXT, new_grade TEXT, action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, date, grading_company)
);

CREATE TABLE IF NOT EXISTS price_target_consensus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, snapshot_date TEXT NOT NULL,
    target_high REAL, target_low REAL, target_consensus REAL, target_median REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, snapshot_date)
);

CREATE TABLE IF NOT EXISTS analyst_estimates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, date TEXT NOT NULL, period TEXT NOT NULL,  -- 'quarter'|'annual'
    revenue_low REAL, revenue_high REAL, revenue_avg REAL,
    ebitda_low REAL, ebitda_high REAL, ebitda_avg REAL,
    ebit_low REAL, ebit_high REAL, ebit_avg REAL,
    net_income_low REAL, net_income_high REAL, net_income_avg REAL,
    sga_low REAL, sga_high REAL, sga_avg REAL,
    eps_low REAL, eps_high REAL, eps_avg REAL,
    num_analysts_revenue INTEGER, num_analysts_eps INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, date, period)
);

CREATE TABLE IF NOT EXISTS insider_trading (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, filing_date TEXT NOT NULL, transaction_date TEXT,
    reporting_cik TEXT, company_cik TEXT,
    transaction_type TEXT, securities_owned REAL, securities_transacted REAL, price REAL,
    reporting_name TEXT, type_of_owner TEXT,
    acquisition_or_disposition TEXT, direct_or_indirect TEXT,
    form_type TEXT, security_name TEXT, url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_cik, filing_date, reporting_cik, transaction_type, securities_transacted)
);

CREATE TABLE IF NOT EXISTS shares_float (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, snapshot_date TEXT NOT NULL,
    free_float REAL, float_shares REAL, outstanding_shares REAL, source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, snapshot_date)
);

CREATE TABLE IF NOT EXISTS form_thirteen_f (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cik TEXT NOT NULL, ticker TEXT, date TEXT NOT NULL,         -- date = quarter-end
    filing_date TEXT, accepted_date TEXT,
    security_cusip TEXT, name_of_issuer TEXT, title_of_class TEXT,
    shares REAL, shares_type TEXT, put_call_share TEXT, value REAL,
    link TEXT, final_link TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cik, security_cusip, date)
);

CREATE TABLE IF NOT EXISTS etf_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etf_symbol TEXT NOT NULL, asset TEXT NOT NULL, snapshot_date TEXT NOT NULL,
    name TEXT, isin TEXT, security_cusip TEXT,
    shares_number REAL, weight_percentage REAL, market_value REAL,
    updated_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(etf_symbol, asset, snapshot_date)
);

CREATE TABLE IF NOT EXISTS sec_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, cik TEXT,
    filing_date TEXT NOT NULL, accepted_date TEXT,
    form_type TEXT, link TEXT, final_link TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, filing_date, form_type, accepted_date)
);

CREATE TABLE IF NOT EXISTS macro_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id TEXT NOT NULL,      -- e.g. 'DGS10', '^VIX'
    source TEXT NOT NULL,          -- 'FRED' | 'YAHOO'
    date TEXT NOT NULL,
    value REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(series_id, source, date)
);
```

### Progress/fetch-log tables (3 — Class C only)

```sql
CREATE TABLE IF NOT EXISTS insider_trading_fetch_log (
    ticker TEXT PRIMARY KEY,
    last_page INTEGER DEFAULT 0,
    last_filing_date TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sec_filings_fetch_log (
    ticker TEXT PRIMARY KEY,
    last_page INTEGER DEFAULT 0,
    last_filing_date TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- news already has news_fetch_log from existing schema
```

### Bonus auxiliary tables (1)

```sql
-- 13F industry/holder-performance — low-volume but keep them for completeness
CREATE TABLE IF NOT EXISTS form_thirteen_f_industry_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL, quarter INTEGER NOT NULL,
    industry_title TEXT NOT NULL, industry_value REAL, date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(year, quarter, industry_title)
);
```

### Indexes — 13F is the only one big enough to warrant explicit ones

```sql
CREATE INDEX IF NOT EXISTS idx_13f_symbol_date ON form_thirteen_f(ticker, date);
CREATE INDEX IF NOT EXISTS idx_13f_cusip ON form_thirteen_f(security_cusip);
CREATE INDEX IF NOT EXISTS idx_insider_ticker_date ON insider_trading(ticker, filing_date);
CREATE INDEX IF NOT EXISTS idx_sec_ticker_date ON sec_filings(ticker, filing_date);
```

## Bulk-fetch scripts — 13 scripts with resumable skeleton

Every script follows the same template:

```python
# scripts/bulk_<endpoint>.py
def main():
    args = parse_args()  # --resume, --log-every N, --dry-run, --throttle-ms
    universe = get_universe()   # from fundamental_data OR seed CSV
    resume_from = find_resume_point(data_store, 'endpoint_name')

    with tqdm(total=len(universe), initial=resume_from) as pbar:
        for i, key in enumerate(universe[resume_from:], start=resume_from):
            try:
                n_saved = fetch_and_save(client, data_store, key)
                log_progress(data_store, 'endpoint_name', key, i, n_saved)
            except KeyboardInterrupt:
                log.warning(f'interrupted at idx={i} key={key}')
                raise
            except Exception as e:
                log.error(f'{key}: {e}', exc_info=False)
                continue
            pbar.update(1)
            if args.throttle_ms: time.sleep(args.throttle_ms / 1000)
```

**Resume point** = either a fetch_log entry, a `max(created_at)` sentinel, or for Class B the `get_raw_payload_latest_date` helper.

**Standard output**: append to `scripts/logs/bulk_<endpoint>_<timestamp>.log` + stdout. Running in background via `nohup python -u scripts/bulk_X.py > scripts/logs/X.out 2>&1 &`.

Scripts list:
1. `scripts/bulk_etf_prices.py` — 46 ETF tickers via existing `fetch_price_data` (~90s)
2. `scripts/bulk_earnings_calendar.py` — global fetch, monthly date windows
3. `scripts/bulk_earnings_per_ticker.py` — 715 calls
4. `scripts/bulk_transcripts.py` — 715 × ~60 quarters ≈ 42K calls (background, ~15 min API, ~12-24h wall)
5. `scripts/bulk_dividends.py` — 715 calls
6. `scripts/bulk_splits.py` — 715 calls
7. `scripts/bulk_analyst_grades.py` — 715 calls
8. `scripts/bulk_analyst_estimates.py` — 715 × 2 periods = 1,430 calls
9. `scripts/bulk_price_targets.py` — 715 calls (daily rerun needed — it's a snapshot)
10. `scripts/bulk_insider_trading.py` — 715 × N pages each (~10-100 pages per ticker)
11. `scripts/bulk_shares_float.py` — 715 calls
12. `scripts/bulk_form_13f.py` — 100 CIKs × 44 quarters = 4,400 calls, ~2h
13. `scripts/bulk_etf_holdings.py` — 46 ETFs × 1 call
14. `scripts/bulk_sec_filings.py` — 715 × N pages (~5-50 pages per ticker)
15. `scripts/bulk_news.py` — **the biggie** — 715 × monthly windows 2015→now ≈ 85K calls, ~12-24h wall
16. `scripts/bulk_macro.py` — FRED (11 series) + Yahoo (8 series) — ~1 min total

## Seed CIK list for 13F

Ship as `data/macro/top_13f_filers.csv` with columns `cik, investor_name, aum_usd_b` (approximate AUM for prioritization). Starter list (will curate + verify before running bulk):

Berkshire Hathaway, Vanguard Group, BlackRock, State Street, Fidelity/FMR, Capital Research, T Rowe Price, Wellington, Geode, Morgan Stanley, Bank of America, JPMorgan, Goldman Sachs, Renaissance Technologies, Two Sigma, Bridgewater, Citadel, Millennium, D.E. Shaw, AQR Capital, Tiger Global, Coatue, Viking, Point72, Pershing Square, Baupost, Third Point, Soros Fund, Elliott, Greenlight Capital, Dodge & Cox, Dimensional Fund Advisors, Invesco, Charles Schwab, TIAA, Northern Trust, U.S. Bancorp, Allianz/PIMCO, Legal & General, APG (Dutch), … (target: 100 total).

**Verification step before running bulk_form_13f**: query `institutional-ownership/holder-performance-summary?cik=X&year=2024` for each seed CIK — any that return empty list = bad CIK, flag before the bulk run.

## Execution order — dependency graph

```
Phase A — no-dep, fast (run immediately, concurrent):
  bulk_etf_prices ────────┐
  bulk_macro (FRED+Yahoo) ┤
  bulk_earnings_calendar ─┘

Phase B — per-ticker, ~5-10 min each (run sequentially or 2-at-a-time):
  bulk_earnings_per_ticker
  bulk_dividends
  bulk_splits
  bulk_shares_float
  bulk_price_targets
  bulk_analyst_grades
  bulk_analyst_estimates
  bulk_etf_holdings

Phase C — paginated, ~30 min – 2h each:
  bulk_insider_trading
  bulk_sec_filings
  bulk_form_13f  (after seed-CIK curation + verification)

Phase D — multi-hour, background:
  bulk_transcripts  (~12-24h wall)
  bulk_news         (~12-24h wall)
```

**Critical path for Week 1 backtest work**: Phase A + `bulk_earnings_calendar` + `bulk_shares_float` unblocks event-guards. Everything else can run in parallel.

## Per-endpoint spec — the 12 key ones

Brief form — full schemas in tables above.

### 1. earnings_calendar (Class A → global)
- URL: `GET /earnings-calendar?from=YYYY-MM-DD&to=YYYY-MM-DD`
- Strategy: paginate by month-sized date windows starting 2015-01
- Volume: ~52K rows total; ~5 min pull

### 2. earnings_per_ticker (Class B)
- URL: `GET /earnings?symbol=X&limit=100`
- Paginate until `limit` returns fewer; usually single call suffices
- Volume: ~28K rows

### 3. earnings_transcripts (Class B, big)
- Pre-step: `GET /earning-call-transcript-dates?symbol=X` — returns list of (quarter, fiscalYear, date) for the ticker
- Per (ticker, year, quarter): `GET /earning-call-transcript?symbol=X&year=Y&quarter=Q` — returns 1 transcript
- Volume: **42,529 transcripts across 654 tickers with transcripts** (61 of 715 have none)
- Wall time: ~12-24h (background)

### 4. dividends (Class B → lifetime)
- URL: `GET /dividends?symbol=X`
- 1 call, returns all-history
- Volume: ~40K rows

### 5. stock_splits (Class B → lifetime)
- URL: `GET /splits?symbol=X`
- 1 call, returns all-history
- Volume: ~3K rows

### 6. analyst_grades (Class C → paginated)
- URL: `GET /grades?symbol=X` — confirmed returns ~2K rows for AAPL in single call
- Appears non-paginated; if reply too large we'll learn when we probe in bulk
- Volume: ~1M rows

### 7. price_target_consensus (Class A)
- URL: `GET /price-target-consensus?symbol=X`
- 1 row per ticker, current snapshot
- Volume: ~715 rows

### 8. analyst_estimates (Class B × 2 periods)
- URL: `GET /analyst-estimates?symbol=X&period=quarter|annual&limit=40`
- Per ticker × 2 period values
- Volume: ~14K rows

### 9. insider_trading (Class C)
- URL: `GET /insider-trading/search?symbol=X&page=N&limit=100`
- Paginate N=0,1,2,… until page returns <100 rows
- Volume: ~50-200K rows

### 10. shares_float (Class A)
- URL: `GET /shares-float?symbol=X`
- 1 row, current snapshot (only)
- Volume: ~715 rows; re-run weekly for freshness

### 11. form_thirteen_f (special — per CIK)
- URL: `GET /institutional-ownership/extract?cik=CIK&year=Y&quarter=Q`
- Iterate seed CIKs × all quarters 2015-Q1 → 2026-Q1
- Volume: ~2.2M rows
- Wall time: ~2h

### 12. etf_holdings (Class A — by ETF)
- URL: `GET /etf/holdings?symbol=ETF`
- 1 call per ETF, ~500 constituents
- Volume: ~30K rows
- Run for 46 sector/broad ETFs

### 13. sec_filings (Class C)
- URL: `GET /sec-filings-search/symbol?symbol=X&from=&to=&page=N`
- Paginate by page, broad date range
- Volume: ~50-200K rows

### 14. macro_series (Class A — free APIs)
- FRED via `fredapi.Fred(api_key=...)` — 11 series (DGS10, DGS2, DFF, BAMLH0A0HYM2, BAMLC0A0CM, CPIAUCSL, UNRATE, INDPRO, GDP, M2SL, VIXCLS)
- Yahoo via `yfinance` — 8 series (^VIX, ^GSPC, ^IXIC, ^RUT, DX-Y.NYB, GC=F, CL=F, HG=F)
- Single DataFrame → single `macro_series` table
- Volume: ~50K rows, ~1 min

## Verification strategy

### L3 extensions (per endpoint)

Add one check per new table to `scripts/verify_data_quality.py`:

- `check_earnings_calendar_nonempty` — at least 30K rows for 2015-now
- `check_dividends_sanity` — no row with `dividend < 0 or dividend > 1000`
- `check_splits_sanity` — numerator/denominator > 0; no impossibly-large ratios
- `check_13f_coverage` — at least 50 CIKs have ≥30 quarterly filings
- `check_transcripts_content_nonempty` — no transcript with `content = ''`
- `check_insider_trading_monotonic` — filing_date descending within each ticker
- `check_macro_continuity` — DGS10 has ≤5 missing weekdays 2015-now (Treasury closures excluded)
- … (12 total checks)

### Smoke-test fixtures

Extend `scripts/capture_golden_fixtures.py` to capture a small sample per new endpoint (one ticker, narrow date range) so `verify_fixture_equivalence.py` can detect if a code-change silently breaks something.

Fixtures to add:
- `earnings_calendar_1week.pkl`
- `earnings_AAPL.pkl`
- `transcript_AAPL_2023Q2.pkl`
- `dividends_AAPL.pkl`
- `grades_AAPL.pkl`
- `insider_trading_AAPL_page0.pkl`
- `shares_float_AAPL.pkl`
- `form_13f_BRK_2024Q2.pkl`
- `etf_holdings_SPY.pkl`
- `sec_filings_AAPL_2024.pkl`
- `macro_DGS10.pkl`

## Risks + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| News bulk-pull takes 20-30h and blocks other work | High | High | Run in background (`nohup`), per-ticker checkpoint, OK to interrupt and resume. All other bulk-fetches can run in foreground in parallel. |
| FMP rate-limit (3000/min) breached under concurrent pulls | Medium | Medium | `FMPClient` already has 429 backoff. Also add `--throttle-ms` to each bulk script; default 0, bump to 50-100 if we see 429s. |
| Transcripts storage unexpectedly large | Low | Low | Estimate: 60-100 MB. Disk has headroom. |
| Seed-CIK list too small/stale | Medium | Medium | Verification step before bulk_form_13f; if <80 of 100 verify successfully, expand list from WhaleWisdom. |
| `insider-trading/search` pagination doesn't terminate cleanly | Low | Medium | Cap at 200 pages per ticker; log if hit. Most tickers << 100 pages. |
| DB disk pressure (~5 GB additions) | Medium | Low | Run `VACUUM` after all bulk loads. If SQLite hits 10+ GB, revisit whether transcripts should be in flat files keyed to DB rows. |
| Pre-existing DB divergence (Step 3's DB cross-check finding) causes new code to compute differently from old stored rows | Medium | Low | Already documented; affects only y_return/solvency_ratio path which is out of Step 4 scope. |
| Running news + transcripts + 13F concurrently over a single API key hits rate-limits | Medium | Medium | Run them sequentially overnight, not concurrently. `bulk_news.py` and `bulk_transcripts.py` each take ~12-24h — serialize as transcripts → news. |

## Execution timeline (realistic calendar)

Assuming 1-2 focused days of coding + overnight bulk runs:

- **Day 1**: Build all 8 fetcher modules + 16 tables + 14 bulk scripts (~1000 lines). Ship smoke tests. Run Phase A + B (fast pulls). Verify L3 still green.
- **Day 2 AM**: Run Phase C (paginated: insider, SEC filings, 13F). ~4-6h. Update L3 checks.
- **Day 2 PM**: Kick off `bulk_transcripts.py` in background. Estimated 12-24h to completion.
- **Day 3 AM**: After transcripts done, kick off `bulk_news.py` in background. 12-24h.
- **Day 4**: Final verification — L3 extensions, fixture captures, smoke tests across all 14 new endpoint groups.

Total: ~4 days calendar, much of it waiting for background pulls.

## What I need from you next

Before execution begins:
1. Confirm the module grouping (earnings vs earnings+transcripts vs transcripts standalone, etc.) — I'll default to the 8-module layout above unless you prefer finer granularity.
2. Seed CIK list — I'll hand-curate ~100 names from public sources; check if you want me to include anything specific.
3. Execution philosophy: build everything first then bulk-pull, or interleave (build earnings → pull earnings → build dividends → pull dividends → …)?

I'd recommend: **build everything first** (~8-10h coding), commit, then run the bulk phases. Less context-switching.

Once these are confirmed, I can enter formal plan mode to write `~/.claude/plans/step4-*.md` (which becomes the authoritative spec to execute against), or skip plan mode and start building directly from this document.
