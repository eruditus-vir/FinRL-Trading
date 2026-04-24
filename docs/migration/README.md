# Migration Context

This folder documents why this fork exists, what's being ported into it, and how decisions were made. Read in order if you're new to the project.

## Why this fork exists

Two prior efforts converged here:

1. **`~/Work/alpaca/alpaca-trade-ideas`** — research repo that built and validated a swing trading system. Reached Sharpe 1.96 on 2025 holdout via RSI<35 + 10% drop oversold mean reversion. 17 notebooks of validated research. Lost SimFin and Alpaca subscriptions, so cannot run live going forward.

2. **AI4Finance-Foundation/FinRL-Trading** — mature FMP-based platform with backtest engine, S&P 500 universe management, RL framework, Alpaca trading executor. Has ~60% of what's needed for production.

Decision: migrate INTO FinRL (this fork) rather than build alongside. FinRL provides the production foundation; we add the validated edge (trigger system + LLM analysis + macro layer) as new modules.

## Reading order

| File | What it covers |
|---|---|
| `01_validated_baseline.md` | The Sharpe 1.96 system. What works. The thing being ported. **DO NOT regress this.** |
| `02_failed_approaches.md` | What's already been tried and failed. Don't repeat. |
| `03_architecture_vision.md` | Where this is heading: macro + micro agents → Stage 1 classifier → eventual RL |
| `04_build_plan.md` | Current week's work, validation gates, go/no-go criteria |
| `05_what_to_port.md` | Specific files to migrate from alpaca-trade-ideas |
| `06_data_decisions.md` | FMP Ultimate tier, S&P 500 universe, FRED for macro |
| `07_data_quality.md` | DB semantics (close/adj_close), verified checks, known data issues + mitigations |

## Current phase

**Week 1: Migration + bulk download.** Goal: reproduce Sharpe 1.96 on META + NFLX 2025 using FMP-sourced data. If validation passes, expand to S&P 500 and add macro layer in subsequent weeks.

See `04_build_plan.md` for current week's specific tasks.

## Two repos, two purposes

| | `alpaca-trade-ideas` | This fork (`FinRL`) |
|---|---|---|
| Status | **FROZEN** — research archive | **ACTIVE** — production platform |
| Data source | SimFin + Alpaca (cached only) | FMP Ultimate |
| Universe | 169 hand-picked stocks | S&P 500 (with historical constituents) |
| New code goes here? | No | Yes |
| Validation runs here? | Reference only | Yes |

Don't try to bring the old repo back to life. It served its purpose. New work happens here.