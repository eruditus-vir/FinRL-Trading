# Architecture Vision

The system is built in layers. Each layer is independently validatable and adds complexity only after the previous layer's edge is confirmed.

## Layer Cake

```
┌────────────────────────────────────────────────────────────────┐
│ Layer 5: RL allocation (optional, far future)                  │
│   - Sizes positions within Stage 1 high-conviction candidates  │
│   - Learns timing, exit, regime adaptation                     │
│   - Only built if Stage 1 hits its IC gate                     │
└────────────────────────────────────────────────────────────────┘
                              ↑
┌────────────────────────────────────────────────────────────────┐
│ Layer 4: Stage 1 unified ML classifier                         │
│   - Binary: P(fwd_5d > 1%) on all stock-days                   │
│   - Features: technical + fundamental + macro + LLM bounce_prob│
│   - Single model learning interactions across feature types    │
│   - Replaces the multi-model "compose-trained-in-isolation"    │
│     approach that failed in notebook 17                        │
└────────────────────────────────────────────────────────────────┘
                              ↑                            ↑
┌──────────────────────────────────────┐  ┌────────────────────┐
│ Layer 3a: Macro Agent (LLM, daily)   │  │ Layer 3b: Micro    │
│   - Universal cross-asset signals    │  │ Agent (LLM,        │
│   - FRED + Yahoo: 10Y, VIX, DXY,     │  │ per-trigger)       │
│     credit spreads, gold, oil        │  │   - Stock-specific │
│   - LLM synthesis (Claude/GPT-4)     │  │     news + multi-  │
│   - 1 call/day, broadcast to all     │  │     quarter        │
│     stocks                           │  │     trajectory     │
│   - Output: regime, bounce_friend-   │  │   - Local Gemma 4  │
│     liness, tail_risk                │  │   - Output:        │
│                                      │  │     bounce_prob    │
└──────────────────────────────────────┘  └────────────────────┘
                              ↑
┌────────────────────────────────────────────────────────────────┐
│ Layer 2: Trigger detection (validated baseline)                │
│   - RSI<35 AND pre_return_10d<-5%                              │
│   - Pre-filters universe to 5-20 candidates per day            │
│   - This is the validated edge — DO NOT replace                │
└────────────────────────────────────────────────────────────────┘
                              ↑
┌────────────────────────────────────────────────────────────────┐
│ Layer 1: Data infrastructure (FMP via FinRL data layer)        │
│   - S&P 500 universe with historical constituents              │
│   - Quarterly fundamentals, daily prices, news, earnings cal   │
│   - Macro data: FRED + Yahoo (free)                            │
└────────────────────────────────────────────────────────────────┘
```

## Why this order

**Layer 2 (trigger) is validated.** Sharpe 1.96. Don't touch.

**Layer 3a (macro agent) is the next hypothesis test.** The 2022 -35.6% MDD is a regime problem — the trigger fires correctly but the bounces don't materialize because the macro environment is hostile. Macro layer should detect this and reduce/avoid trades during such regimes.

**Layer 3b (micro agent) is also next.** Multi-quarter fundamental trajectory in the LLM prompt addresses the NFLX/INTC trap (companies in structural decline). v4 prompt is built (in alpaca-trade-ideas) but never validated end-to-end.

**Layer 4 (Stage 1 classifier) unifies everything.** Trains on ALL features simultaneously so it can learn interactions like "RSI<30 + bull regime + improving fundamentals = high bounce probability" vs "RSI<30 + tightening regime + declining fundamentals = falling knife." This is what notebook 17 tried to do with a separate fundamental model and failed.

**Layer 5 (RL) is far future.** Only build if Layer 4 produces strong enough signal that adding RL sizing/timing on top makes sense.

## What "macro agent" actually is

NOT: A continuous multi-agent platform with per-company analysis.  
IS: One LLM call per day on universal cross-asset data + macro headlines, producing structured features that all stocks share.

Reasoning for the simpler version is in the conversation history but boils down to:
- Macro is universal (same signal for every stock) — compute once, broadcast
- Daily granularity matches the swing horizon (no need for real-time)
- One strong cloud LLM call/day beats five small local LLM calls reasoning in parallel
- Per-company macro can be added later if universal version works but isn't enough

## What "micro agent" actually is

The existing v4 LLM prompt (`trigger_filter_v4.py` in alpaca-trade-ideas) running on local Gemma 4 27B per trigger event. Outputs `bounce_probability` 0-1 plus catalyst type, deterioration flag, sentiment.

Per-trigger frequency = ~5-20 calls/day depending on universe and trigger sensitivity. Local Gemma keeps this free.

## Validation gates between layers

| Adding | Pass criterion |
|---|---|
| Layer 2 ported to this fork | Sharpe ~1.96 reproduced on META + NFLX 2025 (±0.1) |
| Layer 3a macro agent | Qualitative: outputs sensible regime classifications during 2022 |
| Layer 3a integration | 2022 MDD on META + NFLX drops materially while 2025 Sharpe preserved |
| Layer 3b micro agent | Spearman IC of bounce_probability vs actual fwd_5d > 0.05 |
| Layer 4 unified classifier | PR-AUC > baseline by >0.02 with all features vs technical-only |
| Layer 5 RL | Sharpe improves by >0.5 over Layer 4 alone |

If any gate fails, stop and diagnose — don't pile on more layers hoping they cancel out the failure.