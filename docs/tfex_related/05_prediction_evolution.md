# 05 — Prediction stack: how it got here

The DOCX is essentially a logbook of how the prediction layer was built and
re-tuned. This doc reorganizes it as a chronological narrative with the
*decisions* and the *evidence* that drove them. Use it to understand why
the system has the shape it has — not as a reference for current code.

## Phase 0: POC1 ([deprecated])

**Goal:** Predict whether a currency pair will appreciate / depreciate /
no_change next day/week/month using economic indicators + news.

**Architecture:** Single LangGraph agent with these node roles:

- Fetch quantitative data (BOT API, IMF data, FRED).
- Crawl qualitative data (SerpAPI / Serper Google news, top-10 results).
- (Future) Fetch full article markdown via Firecrawl, summarize via LLM.
- Technical analysis: convert raw quant data to ratios (inflation rate,
  interest-rate differential, SMA, trade balance, current account).
- **Evaluator:** an LLM thinking model that decides whether it has enough
  data or needs to fetch more.

**Output:** `appreciation` / `depreciation` / `no_change`, plus brief
reasoning. Low confidence ⇒ falls back to `no_change`.

### POC1 backtest results (24-month run, 2023-09 to 2025-08)

| Configuration | Correct/24 | Correct/12 (from 2024-09) |
|---|---|---|
| SMA baseline | 10/24 | 6/12 |
| EMA baseline | 10/24 | 6/12 |
| Agent (GPT-4o, ex-rate only) | 12/24 | 6/12 |
| Agent (GPT-4o, ex-rate + headlines) | 13/24 | **9/12** |
| Agent (GPT-4o, ex-rate + headlines + IMF) | 13/24 | **9/12** |

### POC1 conclusions

- IMF indicators added **zero** lift — dropped.
- LLM has potential but confidence stuck at "medium" — needed better
  signal differentiation.
- Old headlines were unreliable (sources got deleted) — needed Perplexity
  or similar live-search.
- Maybe a separate LLM should evaluate which headlines actually matter.

**Why deprecated:** monolithic agent couldn't scale and didn't separate the
research, analysis, and trading concerns enough to debug.

---

## Phase 1: MLP Beta-v0.1 — the 3-agent split ([deprecated])

**Decision:** Split into three agents.

| Agent | Job |
|---|---|
| **Researcher** | Fetch sentiment from the internet, summarize into structured rows in DB. |
| **Analyst (a.k.a. Predictor)** | Predict appreciation / depreciation given prior rates + research. |
| **Trader** | Decide actual buy/sell/hold via ensemble of researcher + analyst output. |

### Why split (and what it bought)

- Different models excel at different tasks → flexibility on price/perf.
- Context-window limits → forced compression at each boundary.
- Debugging → "which agent made us lose money?" becomes answerable.
- Scalability → future N parallel researchers/analysts feeding one trader.
- Cost: more code complexity, weaker decoupling initially.

### Researcher benchmarks

- **Perplexity sonar-pro:** good. Produces well-formatted output.
- **Grok 4.1:** poor — failed to return in the requested format.
- Future: test Gemini 3 against sonar-pro; tune sonar-pro
  include/exclude lists.

### Analyst benchmarks (target: THB/USD next-day direction; tier ignored)

| Model | Window | Accuracy |
|---|---|---|
| gpt-5.2 | 2025-11 | 0.50 |
| gpt-5.2 | 2026-01 | 0.87 |
| gemini-3-pro-preview | 2025-11 | 0.83 |
| gemini-3-pro-preview | 2026-01 | 0.645 |
| gemini-2.5-pro | 2026-01 | 0.48 |
| deepseek-v3.2-speciale | 2025-11 | 0.38 |
| claude-sonnet-4-5 | 2025-11 | 0.33 |
| **claude-opus-4-6** | 2025-11 | **0.90** |
| **claude-opus-4-6** | 2026-01 | **0.87** |
| xiaomi/mimo-v2-flash | 2026-01 | 0.36 |

### Pricing

| Model | Input $/Mtok | Output $/Mtok |
|---|---|---|
| claude-opus-4-6 | $5 | $25 |
| gemini-3-pro-preview | $2 | $12 |
| gpt-5.2 | $1.75 | $14 |

**Decision:** opus-4-6 wins accuracy but is 5×–14× more expensive on
output. Picked **gemini-3-pro-preview** as best price/perf. Re-run the
benchmark whenever a cheaper or stronger model lands.

### Trader benchmarks (1-month sim, 1M THB starting balance, runs daily)

| Model | Max gain (THB) | Notes |
|---|---|---|
| gpt-5.2 | -1,933 | |
| **gemini-3-flash-preview** | **+1,336** | best |
| deepseek-chat (v3.2) | -7,156 | needed shorter lookback for context |
| xiaomi/mimo-v2-flash | -2,100 | failed to understand sell condition / balance |

**Decision:** trader = `gemini-3-flash-preview`. Pro / Opus hit rate-limits.

### MLP-Beta conclusion

Foundation works. Move to Phase 2 priorities:

1. Backtest on Bitkub data.
2. Backtest with prior news triggers (simulated real-time).
3. Trade visualization for prompt tuning.
4. Real-time paper trading on Bitkub or FSB (target: early March).
5. Prompt + parameter tuning.

---

## Phase 2: "Improving prediction" — the horizon insight

**Discovery while building the naive trader:** day-over-day rate moves are
tiny (≤ 0.2%). On 1M THB you make/lose ~200 THB per day — less than the
interest rate (and the interest rate is *annualized*, so it's even worse
per-day). At 50% prediction accuracy, you're at break-even before fees.

**Reframing:** instead of "next-day direction", use a **1-week window**.
A prediction is correct if any future rate within 7 days from prediction
moves in the predicted direction by at least:

- High → ≥ 1%
- Medium → ≥ 0.5%
- Low → ≥ 0.25%

### Result: peaks at 74% (Dec) / floor at 33% (Oct), avg ~50% over months

So even with a 1-week horizon, the predictor is roughly coin-flip on
average. Pattern: over-prediction of appreciation. Direction is okay,
*timing and rate* are off.

### Citation analysis revealed weak research sources

The researcher was citing low-trust sites:

- exchangerates.org.uk
- businesstoday.com.my
- banque-france.fr
- keycurrency.co.uk
- danamon.co.id

**Action:** switch to more reputable sources + add Thai local sources.
Also: feed the analyst the last **15 days of past predictions** so it can
self-correct.

### After switching researcher to gemini-3-flash-preview

New thresholds (data-driven):

- High → 0.5%
- Medium → 0.25%
- Low → 0.1%

### gemini-3-pro-preview as predictor

- Without checking tier ⇒ >60% every month.
- With tier checks ⇒ Nov is bad; other months ≥ 50%.
- 2-week window improves all months by ~10% except November.
- November may be an outlier (consider data quality issue or regime
  break).

This is the version of the predictor that the **production `mean_band`
algorithm consumes**. The 1-week horizon insight is why
`time_range_hours` is clamped to [1, 168] — 168h = exactly 1 week.

---

## Phase 3: Trader implementations

### 3.1 LLM-as-trader (abandoned)

- Each model behaved very differently (gemini-flash chunks small; Kimi /
  GLM5 dump it all in one trade).
- Token cost too high for daily/multi-daily trade decisions.
- Speed: by the time the LLM decides, the price has moved.
- Verdict: abandoned for trading; LLM kept for research + prediction only.

### 3.2 Naive rule-based v0.1 (also deprecated, but informative)

The structure that the current `mean_band` evolved from. Driven entirely
by the predictor output + fixed thresholds.

| Param | Default | Role |
|---|---|---|
| `naive_high_pct` | 2.5% | Min price move % to act on high-conf prediction |
| `naive_low_pct` | 1.0% | Min price move % to act on low-conf prediction |
| `naive_high_conf` | 50% | Share of balance/position used at high conf |
| `naive_low_conf` | 25% | Share of balance/position used at low conf |
| medium | average of high/low | |

Algorithm sketch:

1. Get prediction string, current price, last-trade rate, held units,
   max-buyable.
2. `not_confident` ⇒ hold.
3. Map `high_/medium_/low_` to (size %, price threshold %).
4. **Appreciation** branch: if `held ≤ 0` ⇒ hold. Else if rise since last
   trade < threshold ⇒ hold. Else sell `buy_pct` of position.
5. **Depreciation** branch: if drop since last trade < threshold ⇒ hold.
   Else buy with `buy_pct` of balance, capped by `max_buy_units` and
   `current_balance / rate`.
6. Anything else ⇒ hold.

**Why dropped:** performed badly enough that the doc explicitly says
"omitted due to how badly this performs not worth analyzing." Replaced
with v0.2 below.

### 3.3 New rule-based v0.2 (slot-based)

Built on the 1-week horizon insight: we can't time the move within the
week, but we know it'll happen with ~60% confidence within 7 days.

**Entry strategy**

- Open ONLY when model predicts **depreciation**.
- Daily decision at midnight Bangkok. **One new slot per day** max.

**Exit strategy (take-profit)**

- Sell when price hits the predicted depreciation threshold.
- Only sell during "appreciating slots" (upward intraday moves).
- **Batching**: all units in a slot must close together; if you hold 4
  positions, you need 4 available exit slots to clear them.

**Risk management (stop-loss)**

- **2x rule**: if price moves *opposite* by ≥ 2× the target threshold,
  exit immediately.
- No time limit otherwise — hold until target or stop hits.

**Glance card**

| Rule | Requirement |
|---|---|
| Buy timing | once/day at midnight Bangkok |
| Sell timing | anytime if threshold met AND appreciating block open |
| Capacity | one slot per buy; need equal exit slots to sell |
| Priority | profitability > greed; exit when math works |

**Known weakness:** the predictor's threshold isn't dialed in yet, and
not reinvesting all profits limits compounding. Goal: 5–10% per month
backtest, 2.5–5% per month paper-trade. Slot-based hit ~30k peak in Oct
(~1.5% / month avg); not enough.

This v0.2 is conceptually the bridge to today's `mean_band`: replaced
"slot at midnight" with "band-touch on each tick" and "depreciation only"
with both directions on TFEX.

---

## Phase 3.5: Action plan (operational, not algorithmic)

Selected items that shaped the system:

- Refactor for full parameter adjustment incl. date range.
- New UI for scenario creation with adjustable params (k, gradient, time
  range — overrides AI's default).
- Hardware: Mac Mini at Klod's house.
- Account / billing: separated, dedicated credit card.
- **InnovestX (SCBS) API** discussion for TFEX execution.
- Klod teaches Worapol futures basics.
- **Trader split into 3 components** (TA + Stop-loss + Executor) — see
  [02_system_architecture.md](02_system_architecture.md) §3.
- Settrade open-API integration for sandbox.

### Main timeline (DOCX)

- April: sandbox trading via Settrade, exploration of InnovestX TradingView.
- May/June: real trading with limited contracts (= Phase 4 entry).
- Beyond: keep iterating the algorithm.

---

## Phase 4: Integration branch

Highlights that affected the prediction stack specifically:

- **Reasoning summaries** persisted on each prediction.
- **Stricter scheduling**: explicit triggers + daily window vs ad-hoc.
- **Confidence/horizon honored downstream**: executor/TA skips on low-conf
  paths; overlays tied to `time_range_hours`; `not_confident` shows up as
  "skip" on the chart.
- Researcher → predictor sequencing tightened.
- Discord intel: window, headlines, optional per-row sentiment.

---

## Lessons that survived (apply these when extending the system)

1. **Don't cram everything in one prompt.** The 3-agent split is what made
   debugging and per-step model selection possible.
2. **Match horizon to signal.** Day-by-day moves are noise; week-window is
   the smallest unit where the signal beats coin-flip on average.
3. **LLMs are lousy at math and timing.** Use them for *direction*; let
   deterministic code handle thresholds and execution.
4. **Source quality dominates predictor quality.** Switching to reputable
   news sources moved the needle more than swapping the analyst model.
5. **Always re-benchmark.** Models change quarterly; the price/perf winner
   today probably isn't the winner in 3 months.
6. **Confidence isn't free.** A model that returns mostly "medium" gives
   you no actionable tier separation; that's a model problem, not a
   threshold problem.
7. **Paper-trade gain target is half the backtest target.** Real frictions
   (fills, slippage, latency, holidays) eat ~half the edge.
