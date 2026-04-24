# Validated Baseline (the thing being ported)

This is the system from `alpaca-trade-ideas` that this fork must reproduce before any new work begins.

## The Edge

**Oversold mean reversion with technical + fundamental + regime context.**

Trigger: `RSI(14) < 35 AND pre_return_10d < -5%`  
Hold: 5 trading days  
Universe: 169 hand-picked liquid US equities  
Position sizing: equal weight, max 10 concurrent positions  
Exit: fixed 5-day hold OR stop-loss

## Performance (2025 holdout, out-of-sample)

| Metric | Value |
|---|---|
| Sharpe | 1.96 |
| Win rate | 62.9% |
| Annualized return | (need to extract from notebook) |
| MDD (2025 holdout) | ~-12% |
| MDD (full train 2021-2024) | -35.6% (2022 bear-driven) |

## Statistical validation

- p < 0.0001 vs random entry on the same triggers
- Walk-forward validated across 6 rolling windows 2021-2024
- 2025 entirely held out, never touched during training

## What features matter (from notebook 12-15 feature analysis)

Top features (Spearman correlation with fwd_5d return):
1. `oversold_depth` (RSI distance below threshold)
2. `pre_return_10d` magnitude
3. `regime` (SPY vs SMA-50/200)
4. `sector_flow` (sector rotation signal)
5. `eigen_1_ratio` (correlation regime concentration)
6. `volume_ratio` (recent vs historical volume)

## Critical: this is the parity gate

When porting to this fork:
- Same trigger definition (RSI<35 + pre_return<-5%)
- Same 5-day hold
- Same universe (initially META + NFLX, then S&P 500)
- Same feature computations

The Week 1 validation gate: **reproduce Sharpe ~1.96 on META + NFLX 2025 within rounding (±0.1)**. If migrated system drifts significantly, debug data parity issues before adding any new features. Most likely culprits if it fails:
- FMP split/dividend adjustment differs from Alpaca
- Quarterly fundamental period-end conventions differ from SimFin
- News timestamp timezone differences
- Trading calendar half-day handling

## What this means for design

**The trigger system is the validated foundation.** Everything new (macro layer, Stage 1 classifier, RL allocation) is INCREMENTAL on top of this baseline. Don't replace; augment.

If a new addition reduces Sharpe below 1.96, it's a regression — not "different but valid." The 2025 holdout is the canonical benchmark.