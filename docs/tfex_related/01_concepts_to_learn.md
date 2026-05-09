# 01 — Concepts you need to learn

This is the curriculum. If a term shows up in the other docs and you don't
recognize it, it should be defined here. Items are tagged by category so you
can decide what to deep-dive vs skim.

Categories: **[FX]** foreign exchange basics · **[FUT]** futures / TFEX
mechanics · **[TA]** technical analysis · **[SYS]** codebase-specific names ·
**[AI]** the LLM / agent stack · **[STAT]** statistics or finance math.

---

## A. FX fundamentals (read first if you're new to currency)

- **[FX] Currency pair X/Y** — `X` is the **base**, `Y` is the **quote**. The
  rate is "how many Y per one X". USD/THB = 32 means 1 USD costs 32 THB.
- **[FX] Appreciation vs depreciation** — refers to the **base X** in this
  codebase. Rate up ⇒ X strengthens ⇒ label family `*_appreciation`. Rate
  down ⇒ X weakens ⇒ `*_depreciation`. Label substring matching is how the
  trader maps predictions to actions, so this convention is load-bearing.
- **[FX] Spot vs forward vs futures** — spot = settle now; forward = OTC
  contract for future delivery; futures = exchange-listed standardized
  forward. TFEX USD/THB futures are the *third* category.
- **[FX] FX swap / basis** — futures price ≠ spot price; the gap is driven by
  the interest-rate differential between the two currencies. The codebase
  computes a *Bitkub-basis proxy* to align TFEX marks where the venue feed
  has gaps.
- **[FX] DXY** — dollar index, weighted basket; USD/THB tracks DXY moves.
  Mentioned as a correlated input for the qualitative agent.
- **[FX] Cross-pair correlation** — JPY/THB, SGD/THB, CNH/THB tend to move
  with USD/THB. The agent uses this as context.

## B. Futures & TFEX mechanics (must-know before touching TFEX code)

- **[FUT] Long vs short** — long profits when price rises; short profits when
  price falls. With futures, opening a short does not require borrowing.
- **[FUT] Open / close / cover** — open long, open short, close long (sell),
  close short (cover/buy). The codebase distinguishes these as separate
  "legs" with separate gating rules.
- **[FUT] Contract codes H, M, U, Z** — Mar/Jun/Sep/Dec quarterly expiries.
  The codebase only trades these four — other monthly codes are illiquid.
- **[FUT] Expiry & roll** — must close ≥1 day before expiry; price gets
  unstable on expiry day. Rolling = closing the front contract and opening
  the next one.
- **[FUT] Initial margin vs maintenance margin** — initial = posted on open
  (700 THB per contract per the doc); maintenance = floor below which broker
  forces a top-up (492 THB). Below maintenance ⇒ margin call ⇒ contract is
  closed if not topped up.
- **[FUT] Tick value** — for TFEX USD/THB: each 0.01 THB move = 10 THB P&L per
  contract. Each contract = 1,000 USD notional. This is why aggressive
  position sizing can blow through maintenance margin fast.
- **[FUT] TFEX trading sessions** —
    - Pre-open 09:15–09:45, Morning 09:45–12:30
    - Pre-open 13:15–13:45, Afternoon 13:45–16:55
    - Pre-open 18:45–18:50, Night 18:50–03:00 (T+1)
  Code uses **trimmed effective windows** (~09:46–12:29, 13:46–16:54,
  18:51–02:59) so the executor doesn't try to hit the open/close auctions.
- **[FUT] No new opens in the last ~5 min of each segment** — codebase rule
  via `is_tfex_near_session_end_no_new_opens`. Stop-losses can still close.
- **[FUT] National holidays** — Bank of Thailand calendar; TFEX is closed.
  The TODO mentions wiring the BOT API for this.

## C. Technical analysis primitives

- **[TA] SMA** — simple moving average over a window of closes.
- **[TA] EMA** — exponential weighting; recent prices count more.
- **[TA] Standard deviation, population (ddof=0)** — codebase uses population
  std, not sample std. Matters for reproducibility.
- **[TA] Bollinger bands** — SMA ± k·σ. The codebase's `mean_band` is
  effectively Bollinger with `k=0.5` default (very tight) and a hard
  `0.01`-rate-unit floor on half-width to prevent collapse.
- **[TA] RSI** — Relative Strength Index, momentum oscillator 0–100. Mentioned
  as a future enhancement (strategy #2 in the catalog).
- **[TA] ATR** — Average True Range, used in the volatility-compression
  strategy idea.
- **[TA] Mean reversion vs breakout vs momentum** — three trading philosophies
  the strategy catalog enumerates.

## D. Codebase-specific terms (the ones that are NOT obvious)

These are the names you'll see in code and docs that you cannot derive from
general knowledge:

- **[SYS] `mean_band` (a.k.a. `strategy_id: mean_band`)** — current production
  TA. Implemented in `fxagent.trading.mean_band_ta:MeanBandTechnicalAnalysis`.
- **[SYS] Forecasting pipeline** — RSS trigger → researcher (sentiment fetch)
  → predictor (LLM) → `predictor_result` row. Live paths often enqueue an
  evaluation worker after a new row.
- **[SYS] Trading tick / `run_trading_pipeline`** — periodic loop that
  *consumes* prediction rows but never re-runs the predictor (it loads with
  `create_if_missing=False`). Stop-loss runs before TA.
- **[SYS] `PredictorResultModel`** — DB row holding a forecast. Key fields:
  `prediction` (text label), `time_range_hours` (horizon, clamped to
  [1, 168]), `predicted_ts = created_at + time_range`, `asset_a = base ISO`,
  `asset_b = quote ISO`.
- **[SYS] `PreparedTradeEvaluation`** — bundle the trading tick assembles
  per-leg before TA: effective prediction row + closes window + market
  context (buy_rate / sell_rate / max units).
- **[SYS] Effective row / head row** — `_ta_prediction_row_for_leg`
  selects exactly one row per leg from rows ordered newest-first within
  `market_as_of`. See [03_mean_band_algorithm.md](03_mean_band_algorithm.md)
  for the four cases.
- **[SYS] `not_confident`** — directional label = "no view"; sizing is 0%, but
  if it's the head row and still inside its horizon, TA scans newer→older
  for the first directional sibling.
- **[SYS] Stale fallback** — if no row is active at `market_as_of`, fall back
  to the most recent older row.
- **[SYS] Tier magnitudes** — `high_*` ⇒ 1.0, `medium_*` / `med_*` ⇒ 0.5,
  `low_*` ⇒ 0.25. Used to compute the **running tier score** for regime
  flatten and force-close decisions on TFEX.
- **[SYS] Position sizing tiers (separate from tier magnitudes!)** —
  `high_conf` 20% (default), `med_conf` 10%, `low_conf` 5% of starting
  balance / risk anchor.
- **[SYS] `invest_pct`** — fraction of starting balance / contract cap that
  the executor will deploy. TA emits this as a hint via `units`.
- **[SYS] `tfex_max_open_contracts`** — venue-side hard cap on open contracts.
  Sizing: `desired_contracts = round(invest_pct × tfex_max_open_contracts)`,
  min 1 if opening.
- **[SYS] Label gates / `require_label_gates`** — substring check on the
  prediction text for "appreciation" / "depreciation". `True` on Bitkub
  spot, `False` on TFEX (TFEX gates closes by geometry only). New-opens are
  ALWAYS gated by label, on both venues.
- **[SYS] Geometry trigger** — band edges arm legs:
  `buy_rate <= bottom` arms buy/cover; `sell_rate >= top` arms sell/short.
- **[SYS] 0.01 tick co-linearity gate** (`apply_settrade_tick_gating`) —
  before arming TFEX legs, snap quote/band/SMA to 2dp; if they collapse to
  the same tick, the leg does NOT emit until a tick separates them. Avoids
  duplicate orders / fragile limits.
- **[SYS] `_tfex_last_prediction_regime`** — session state recording the most
  recent appreciation/depreciation regime (substring-derived).
- **[SYS] Regime flatten** — when the regime *flips* and tier-score sign
  confirms it, fire a flatten leg (close long on flip-to-depreciation, cover
  short on flip-to-appreciation) so the opposite side can arm next pass.
- **[SYS] Force-flat on disagreement** — TA forces flat if held position
  contradicts current regime AND no covering band leg fired AND tier score
  confirms (|score| ≥ 0.5).
- **[SYS] Sign confirmation / running tier score** — sum of signed tier
  magnitudes across the active forecast window for the leg, clamped to ±2.
  If `None` (e.g. only `not_confident` rows), regime flips are *deferred*.
- **[SYS] Trader v0.4 decomposition** — split into TA + Stop-loss + Executor.
  TA chooses geometry & limit; SL enforces 3-layer loss limits; Executor
  hits the broker subject to venue rules.
- **[SYS] L1 / L2 / L3 stop-loss layers** —
  L1 = TA-emitted limit; L2 = hard threshold per position (emergency stop,
  no new trades); L3 = aggregate loss → manual-intervention notification.

## E. The LLM / agent stack

- **[AI] LangGraph** — graph-based orchestration framework for LLM agents
  (used to wire Researcher → Analyst → Trader nodes).
- **[AI] MCP (Model Context Protocol)** — protocol for giving LLMs tool
  access to external data sources. The DOCX mentions building MCPs for
  quantitative data, search, and a "math" MCP because LLMs are bad at math.
- **[AI] Researcher / Analyst (Predictor) / Trader split** — three agents,
  three different model tiers (cheap research, expensive analysis, decent
  trader). Rationale: prompt-length budget, debugging, parallelization.
- **[AI] Sonar-pro (Perplexity) / Grok 4.1 / GPT-5.2 / Gemini 3 Pro / Claude
  Opus 4.6** — models benchmarked. Current picks: sonar-pro for research,
  gemini-3-pro-preview for analyst (best price/perf), gemini-3-flash-preview
  for trader.
- **[AI] SerpAPI / Serper / Firecrawl** — Google news / web fetch tools. Used
  in the qualitative-data nodes.

## F. Statistics & finance math you'll bump into

- **[STAT] Population vs sample standard deviation** — codebase uses
  population (ddof=0); sample is ddof=1. Different denominators.
- **[STAT] Realized vs unrealized P&L** — the dashboard tracks realized only.
  Average-cost long exits and average-entry short covers realize P&L;
  opening a short does NOT realize anything yet.
- **[STAT] Signal accuracy thresholds** — predictor accuracy is measured
  against a `rate_change_threshold` (default 0.01 rate units vs Bitkub
  closes). |move| ≤ threshold counted correct only if label contains
  `no_change`; etc.
- **[STAT] Sharpe ratio** — referenced in the parent CLAUDE.md as the
  baseline metric (Sharpe 1.96) the FinRL port must not break parity with.
  Not directly in the FX docs but adjacent.

---

## Suggested study order

1. FX basics (A) → so the appreciation/depreciation convention stops being
   load-bearing surprise.
2. Futures + TFEX (B) → so margin calls and tick value stop being abstract.
3. Codebase terms (D) → because half the docs are unreadable without them.
4. TA primitives (C) → quick refresher.
5. Agent stack (E) → only if you're going to touch the predictor/researcher.
6. Stats (F) → reference as needed.

## Things explicitly *not* in scope of these docs

- The actual Python source under `fxagent.trading.*` — read the PDF, then
  read the code. The PDF is the spec; nothing in this folder reproduces it.
- Backtester implementation details — only the metrics it reports.
- Settrade SDK internals — only the executor's contract with it.
