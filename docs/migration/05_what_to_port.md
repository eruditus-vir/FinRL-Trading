# What to Port from alpaca-trade-ideas

Source repo: `~/Work/alpaca/alpaca-trade-ideas` (frozen, read-only going forward)

## Port (high priority — needed for Week 1 validation)

| Source | Target | Why |
|---|---|---|
| `src/analysis_engine/feature_builder.py` | `src/strategies/oversold_bounce_features.py` | Core feature engineering — RSI, SMA distances, ATR, regime, oversold_depth, etc. |
| `src/analysis_engine/statistics.py` | `src/utils/statistics.py` | Walk-forward eval, Sharpe/Calmar computation |
| `src/experiment/portfolio_sim.py` | `src/strategies/oversold_bounce_sim.py` | Portfolio simulator — equal weight, max 10 positions, 5-day hold. Compare to FinRL's backtest engine first; pick the better one. |
| `src/experiment/rolling_evaluator.py` | `src/utils/rolling_evaluator.py` | Walk-forward validation pattern |
| Trigger detection logic (in feature_builder + portfolio_sim) | `src/strategies/oversold_bounce.py` | The validated edge: RSI<35 + pre_return<-5% |

## Port (medium priority — needed for Week 2-3 macro layer)

| Source | Target | Why |
|---|---|---|
| `src/news_analysis/prompts/trigger_filter_v4.py` | `src/llm_analysis/prompts/trigger_filter_v4.py` | Latest micro agent prompt with trajectory context |
| `src/news_analysis/prompts/base.py` | `src/llm_analysis/prompts/base.py` | PromptTemplate base class |
| `src/news_analysis/context.py` | `src/llm_analysis/context.py` | Multi-quarter trajectory builder (already extended for v4) |
| `src/news_analysis/filters/openai_api.py` | `src/llm_analysis/clients/openai_client.py` | Cloud LLM client (for macro agent) |
| `src/news_analysis/filters/ollama.py` | `src/llm_analysis/clients/ollama_client.py` | Local LLM client (for micro agent on Gemma 4) |
| `src/stock_information_service/derived/indicators.py` | `src/data/derived/indicators.py` (or use FinRL equivalents) | RSI, SMA, ATR — check if FinRL already has these before porting |
| `src/stock_information_service/derived/sector_flow.py` | `src/data/derived/sector_flow.py` | Sector rotation feature |

## Port (lower priority — only if specific need arises)

| Source | Target | Why |
|---|---|---|
| `src/news_analysis/prompts/trigger_filter_v1.py`, `v2.py`, `v3.py` | Reference only | Historical context for prompt evolution — read for understanding, don't port unless needed |
| `src/research_engine/labeling/*` | Maybe later | LLM-based article labeling pipeline. Unclear if needed in new architecture. |
| `src/rl_trading/*` | Skip | Not validated, FinRL has its own RL framework |

## Don't port

| Source | Reason |
|---|---|
| `src/stock_information_service/adapters/simfin_*.py` | SimFin subscription gone, dead code |
| `src/stock_information_service/adapters/alpaca_*.py` | Alpaca subscription gone, dead code |
| `src/stock_information_service/storage/sqlite.py` | FinRL has its own data store |
| `src/news_analysis/parallel_runner.py` | Tied to old data pipeline, will be replaced by FMP-based equivalent |
| `exploration/notebooks/*` | Stay in alpaca-trade-ideas as research archive — re-run there for historical reference |
| `docs/llm_trigger_filter/*` | Stay in alpaca-trade-ideas as research archive |
| `data/raw/*`, `data/cached/*` | SimFin/Alpaca cached data, won't transfer to FMP world |

## Notebooks to re-create (NOT port)

These notebooks need fresh versions in this fork using FMP data. Don't try to port the ipynb files — re-run the analyses from scratch.

| Original notebook | New equivalent | Purpose |
|---|---|---|
| `12_model_experiments.ipynb` | `notebooks/migrated/12_baseline_reproduction.ipynb` | Reproduce Sharpe 1.96 on FMP data — Week 1 gate |
| `15_expanded_universe.ipynb` | `notebooks/migrated/15_universe_expansion.ipynb` | Re-validate on S&P 500 — Week 4 gate |
| `17_quarterly_screening.ipynb` | Skip — already failed, see `02_failed_approaches.md` | DON'T REPEAT |

## Migration checklist for each ported file

When porting any module:

1. Read the source file completely first
2. Identify dependencies on `alpaca-trade-ideas`-specific things (DataService, SimFin models, etc.) — these need adaptation
3. Replace data access with FinRL's data layer (fetch_*** functions)
4. Verify the port produces the same outputs as the original on a known input
5. Add to a notebook that proves equivalence before relying on it

Don't port and trust — port and verify.