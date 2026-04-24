# Build Plan

## Current Phase: Week 1 — Migration + Validation Parity

**Goal:** Reproduce Sharpe 1.96 on META + NFLX 2025 holdout using FMP-sourced data inside this fork. If this passes, expand. If not, debug data parity before adding new features.

### Day 1: Environment + endpoint verification

- [ ] Subscribe to FMP Ultimate ($99/mo)
- [ ] Copy `.env.example` to `.env`
- [ ] Set `FMP_API_KEY` in `.env`
- [ ] Verify these endpoints return data via `src/data/data_fetcher.py`:
  - [ ] `fetch_sp500_tickers()` — historical S&P 500 constituents
  - [ ] `fetch_fundamental_data(['META'], '2024-01-01', '2024-12-31')` — quarterly fundamentals
  - [ ] `fetch_price_data(['META'], '2024-01-01', '2024-12-31')` — OHLCV
  - [ ] `fetch_news('META', '2024-01-01', '2024-12-31')` — news articles

### Day 2: Bulk download for META + NFLX

- [ ] Run bulk download for META + NFLX, 2021-01-01 to 2025-12-31:
  - prices, fundamentals (quarterly), news, S&P 500 constituents
- [ ] Verify data quality:
  - Prices: no missing trading days, splits/dividends look right
  - Fundamentals: 4 quarters × 5 years = ~20 records per stock
  - News: reasonable article counts
- [ ] Add `fetch_earnings_calendar` extension method to `data_fetcher.py` (~50 lines, follows existing pattern)

### Day 3: Port trigger system

- [ ] Create `src/strategies/oversold_bounce.py` with the trigger logic
- [ ] Port feature builder logic from `alpaca-trade-ideas/src/analysis_engine/feature_builder.py`
- [ ] Wire to read prices from FinRL data store
- [ ] Compute features for META + NFLX 2021-2025

### Day 4: Backtest reproduction

- [ ] Run trigger detection on META + NFLX 2025
- [ ] Compute trade list
- [ ] Compute Sharpe, Calmar, MDD
- [ ] Compare against `alpaca-trade-ideas` notebook 12 / 15 results

### Day 5: VALIDATION GATE

**Pass criterion:** Sharpe within ±0.1 of 1.96 on META + NFLX 2025 holdout.

If pass → proceed to Week 2 (macro layer).  
If fail → diagnose data parity issue. Most likely:
- Split/dividend adjustment differences (FMP vs Alpaca historical)
- Fundamental period-end conventions (FMP vs SimFin)
- News timestamp timezone differences
- Trading calendar differences

DO NOT proceed to new features until this gate passes.

---

## Week 2: Macro Layer

After Week 1 passes:

- Day 1-2: FRED + Yahoo data fetchers (`src/macro_analysis/data/`)
- Day 3: Cross-asset features + regime classifier (port FinRL pattern from `src/strategies/adaptive_rotation/market_regime.py`)
- Day 4: Economic event calendar (hardcoded CSV of FOMC/CPI/NFP dates)
- Day 5: Macro LLM agent (Claude Sonnet, 1 call/day, backfill 2021-2025)

Validation gate Week 2: macro agent qualitative output for 2022 should consistently classify as "tightening regime, low bounce friendliness." If it shows neutral or bullish during 2022, the agent prompt is broken — fix before integration.

## Week 3: Integration on META + NFLX

- Add macro features to feature builder
- Add event guards to portfolio sim (no entry within 2 days of earnings, no entry day before FOMC/CPI/NFP)
- Backtest 2022 + 2025 on META + NFLX with vs without macro layer
- **Validation gate:** 2022 MDD on both stocks improves materially (target: -35.6% → -20% or better) while 2025 Sharpe preserved (≥1.96)

## Week 4: Universe expansion (only if Week 3 passes)

- Bulk download S&P 500 historical data via FMP (~500 stocks × 5 years, ~30 min API time)
- Re-run Stage 1 classifier with macro features on full universe
- Validate on 2025 holdout
- Compare Sharpe vs prior 169-stock baseline

## Out of scope (defer until earlier gates pass)

- Earnings call transcripts (Ultimate-tier feature, build only after v4 LLM validates)
- 13F institutional holdings
- ETF holdings / sector exposure features
- RL allocation layer
- Continuous monitoring platform (multi-agent debate, real-time, etc.)
- Migration of remaining alpaca-trade-ideas notebooks beyond what's needed for parity

These were all considered and pushed to "later" deliberately. See `02_failed_approaches.md` for context on why premature complexity has been a recurring pitfall.