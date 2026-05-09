# tfex_related — index & reading order

This folder contains two source artifacts and a set of derived summaries that
reorganize them for a reader trying to understand the FX / TFEX trading system
end-to-end.

## Source files

- [ALGORITHM.pdf](ALGORITHM.pdf) — production spec of the `mean_band` algorithm
  (forecasting pipeline, trading tick, band math, TFEX-only mechanics, sizing).
- [FX Agent System requirement.docx](FX%20Agent%20System%20requirement.docx) —
  product/architecture document covering the evolution from POC1 to Phase 4.
  Several sections are explicitly marked **[deprecated]** but were retained as
  historical context. Treat the DOCX as a *log of decisions*, not current spec.

When the two conflict, **the PDF wins for current behavior** of `mean_band`;
the DOCX wins for the *why* (motivation, lessons learned, what was tried and
discarded).

## Derived summaries (read in this order)

1. [01_concepts_to_learn.md](01_concepts_to_learn.md) — the curriculum. Every
   domain term, FX convention, TFEX rule, and codebase-specific name you need
   to know before the rest of the docs make sense.
2. [02_system_architecture.md](02_system_architecture.md) — the two pipelines
   (forecasting chain vs trading tick) and the v0.4 trader decomposition into
   TA / Stop-loss / Executor.
3. [03_mean_band_algorithm.md](03_mean_band_algorithm.md) — the actual
   `mean_band` algorithm: row selection, SMA±k·σ math, label gates, geometry
   triggers, sizing tiers. Mirrors the PDF closely but with explicit examples.
4. [04_tfex_executor.md](04_tfex_executor.md) — TFEX/Settrade-specific
   mechanics: contract codes, sessions, 0.01-tick co-linearity gate, regime
   tracking, force-flat-on-flip, and Settrade executor lifecycle.
5. [05_prediction_evolution.md](05_prediction_evolution.md) — how the
   predictor was built and tuned: POC1 baseline, 3-agent split, model
   benchmarks, the "1-week horizon" insight that reshaped thresholds.
6. [06_strategies_catalog.md](06_strategies_catalog.md) — the five "simple
   strategies" plus the current Bollinger-style mean-band, plus the FinRL/ML
   path forward.
7. [07_strategy_implementations.md](07_strategy_implementations.md) — per-strategy
   deep dive: foundation work needed, parameters, algorithm pseudocode,
   plug-in points, decision tables, failure modes, and an opinionated build
   order. Read alongside 06.

## Two-sentence elevator pitch of the whole system

A daily LLM pipeline (Researcher → Analyst/Predictor) emits directional
forecasts (`{high|med|low}_{appreciation|depreciation}` or `not_confident`)
with an explicit horizon, persisted as `predictor_result` rows. On every
trading tick, the trading-tick pipeline picks one effective forecast per leg,
runs stop-loss, then the `mean_band` TA which arms buy/sell geometry against
SMA±k·σ bands and emits sized actions to a venue executor (Bitkub spot /
Settrade TFEX) that translates them into orders subject to venue rules.
