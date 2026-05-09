# 06 — Strategies catalog

The DOCX lists five "simple strategies" that the trader is supposed to
support eventually, plus the current Bollinger-style mean-band, plus a
pointer to FinRL/ML. This doc inventories them with the actionable bits.

> **Implementation companion:** [07_strategy_implementations.md](07_strategy_implementations.md)
> goes per-strategy into how each would actually plug into the current
> codebase — parameters, algorithm pseudocode, decision points,
> failure modes, and a recommended build order.

## Inputs the TA layer expects

- **Prices** for the asset pair (closes window for SMA; live quotes for
  band touches).
- **Analyst prediction**: high/medium/low × appreciation/depreciation, or
  `not_confident`.
- **Time range** — how long the position/prediction is valid for
  (`time_range_hours`).
- Constants (k, thresholds, sizing percentages).

## Current production: Bollinger-style mean-band (strategy 0)

See [03_mean_band_algorithm.md](03_mean_band_algorithm.md). Summary form
for catalog parity:

- **Bands:** SMA ± max(k·population_std, 0.01) over the closes window.
- **Direction:** dictated by predictor label substring.
- **Entries:** band-touch — buy/cover at lower edge if forecast says
  appreciation; sell/short at upper edge if depreciation.
- **TFEX extras:** 0.01 tick co-linearity gate, regime flatten-on-flip,
  force-flat on disagreement.
- **Sizing:** tier × (high_conf 20% / med_conf 10% / low_conf 5%).

## Strategy 1: Simple Oscillator

> Trade only in the direction of the AI's forecast.

- Bullish forecast ⇒ long only when price retraces to lower bound (e.g.
  -1σ).
- Exit at target OR when price reverts to mid / +1σ.

Effectively the lower-band-only flavor of mean-band. Closest to today's
spot behavior (Bitkub: long-only).

## Strategy 2: Enhanced Oscillator with RSI confirmation

Layer momentum confirmation on top of the oscillator:

- AI buy + RSI ≤ 20 (oversold) ⇒ scale into longs.
- Scale out when RSI ≥ 80 (overbought).

Not implemented yet. Would require an RSI feature in the closes window
and a tier-mapping that combines AI confidence with RSI extremes.

## Strategy 3: Range trading

Identify a market range using prior swing highs/lows + tolerance bands.

- AI bullish ⇒ buy near lower boundary, sell near upper boundary.
- Optional: works *without* AI in compressed/sideways markets where mean
  reversion dominates.
- If price breaks out: cut losses or lock in profits depending on
  positioning.

This is the "trade the range" cousin of mean-band; the difference is
range bounds come from swing structure, not SMA±k·σ.

## Strategy 4: Breakout

The complement to mean-reversion: when AI is right and the market is
trending, *go with* the move instead of fading it.

- Price breaks above range AND AI is bullish ⇒ enter long for momentum.
- **Exit if**: price falls back into prior range OR momentum weakens.

This solves the "what happens when the market trends out of the band"
failure mode of mean-band.

## Strategy 5: Volatility compression accumulation

When volatility contracts, accumulate slowly in the AI's direction.

- AI bullish + ATR / band-width contracting + price compressed ⇒ slowly
  build longs in small increments.
- Exit when volatility expands sharply in profit direction OR breakout
  hits target OR compression breaks against AI.

This is the "buy quietly while the market is sleeping" play. Codebase
has the volatility primitives (the band itself collapses in low-σ
regimes), but no compression-accumulation logic yet.

## ML / FinRL path

Repo: <https://github.com/AI4Finance-Foundation/FinRL>
Reference paper: <https://arxiv.org/pdf/2501.10709>

- Paper uses **ensemble** of RL agents.
- Tested on US stock-market old data — not directly transferable.
- **PPO** looks best but takes risky decisions.
- **DPQ** suits crypto markets.
- Ensemble diversifies decision risk.

To use here: train on our own data first; do not assume the paper's
results transfer to FX/TFEX.

The parent repo CLAUDE.md frames this whole FinRL-Trading project as
"the production platform for a swing trading system that previously lived
in alpaca-trade-ideas (Sharpe 1.96)." So the ML strategies are competing
with a human-tuned strategy that already works — the bar is high.

## Decision matrix: which strategy fits which regime

| Regime | Best strategy | Worst strategy |
| --- | --- | --- |
| Mean-reverting / sideways | mean-band, range trading, simple oscillator | breakout |
| Trending strongly | breakout | mean-band, range trading (will get steamrolled) |
| Low-vol / compressing | vol-compression accumulation | breakout (false signals) |
| High-vol / news-driven | none of these alone — need wider stops + AI gating | range trading |
| AI-confident + supportive technicals | enhanced oscillator (RSI) | none clearly bad |

The DOCX explicitly notes the system is "currently Bollinger Breakout" but
positioned as mean-reversion-style — meaning the algorithm is good at
sideways/mean-reverting regimes and exposed during strong trends. The
TFEX regime-flatten-on-flip mechanism partially mitigates this by
recognizing when the predictor is signaling a trend change.

## What's missing (vs the catalog)

| Strategy | Status |
| --- | --- |
| 0 — Mean-band Bollinger | **production** |
| 1 — Simple oscillator | covered by mean-band |
| 2 — RSI-confirmed oscillator | not built |
| 3 — Range trading on swings | not built |
| 4 — Breakout | not built |
| 5 — Volatility compression | not built |
| ML / FinRL ensemble | exploration only — needs FinRL port + training |

Phase 4 TODO: "Implement all the 5 basic strategies; fix current one to
correspond to those names."
