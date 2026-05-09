# 07 — Strategy implementations: how each one would actually work

[06_strategies_catalog.md](06_strategies_catalog.md) is the catalog. This
doc is the *implementation* companion: for each strategy, what signal it
uses, what parameters it needs, how it plugs into the existing
TA / Stop-loss / Executor split, what's the failure mode it solves vs
creates, and where the decision points are.

If you're going to actually build any of these, this is the doc that
should drive the design. The current code is a single TA implementation
(`MeanBandTechnicalAnalysis`); everything below assumes you'll generalize
that into a strategy-id-dispatched layer.

---

## 0. What's missing from the current architecture (foundation)

Before any new strategy can land cleanly, three foundation pieces are
needed. The current code has hardcoded assumptions that make multiple
strategies awkward:

### 0a. Strategy registry

Today: `strategy_id: mean_band` is a single class. A registry would let
each session pick a strategy and wire it the same way.

```python
# fxagent/trading/strategies/__init__.py
class TechnicalAnalysisBase(Protocol):
    def evaluate(self, prepared: PreparedTradeEvaluation,
                 ctx: TechnicalAnalysisContext) -> list[TradeActionSchema]: ...

STRATEGIES: dict[str, type[TechnicalAnalysisBase]] = {
    "mean_band": MeanBandTechnicalAnalysis,
    "simple_oscillator": SimpleOscillatorTA,
    "rsi_oscillator": RSIOscillatorTA,
    "range_swing": RangeSwingTA,
    "breakout": BreakoutTA,
    "vol_compression": VolCompressionTA,
    "finrl_ensemble": FinRLEnsembleTA,
}
```

### 0b. Closes-window feature pipeline

Today: closes are loaded fresh each tick and fed straight to SMA + std.
A strategy like RSI needs a feature dataframe (close, ema_short,
ema_long, rsi, atr, swing_high, swing_low, etc.).

Proposal: `ClosesWindow` becomes a wrapper around a pandas DataFrame
that lazily computes features on demand and caches them per-tick:

```python
class ClosesWindow:
    def closes(self) -> pd.Series: ...
    def sma(self, n: int) -> float: ...
    def ema(self, n: int) -> float: ...
    def rsi(self, n: int = 14) -> float: ...
    def atr(self, n: int = 14) -> float: ...           # needs OHLC, not just closes
    def swings(self, lookback: int) -> tuple[list[float], list[float]]: ...  # (highs, lows)
    def volatility_regime(self) -> Literal["compressing","stable","expanding"]: ...
```

This is also a Phase-4 TODO ("graph change to candlebar for correct
technical analysis") — the same OHLC change unblocks ATR/range/breakout
strategies.

### 0c. Multi-strategy capital allocation

Today: one session = one TA = full risk budget. If two strategies run
simultaneously, who gets the capital?

Three options (decision below):

| Option | How it works | Pros | Cons |
|---|---|---|---|
| **One-at-a-time** | Regime detector picks one strategy per tick. | No capital split. Strategies can use full risk budget. | Switching cost on regime flips. Hysteresis needed. |
| **Fixed split** | e.g. 50% mean-band, 50% breakout. | Simple. Diversifies. | Wasted capital when one strategy has no signal. |
| **Dynamic weight** | Weights from rolling-Sharpe of each strategy. | Capital follows performance. | Over-fits to recent regime; cold-start problem. |

**Recommendation:** start with **one-at-a-time** + a regime detector.
Move to dynamic weighting only after 2+ strategies have proven Sharpe in
paper trading.

---

## 1. Simple Oscillator (the lower-band flavor of mean-band)

### Concept

Trade only with the AI's forecast direction. Use a fixed σ-multiplier
band (typically -1σ / +1σ) instead of mean-band's tighter k=0.5.

### Signals & parameters

| Param | Default suggestion | Role |
|---|---|---|
| `sigma_entry` | 1.0 | σ multiplier for entry |
| `sigma_exit` | 0.0 | σ multiplier for exit (0 = revert to SMA) |
| `lookback_hours` | inherit from `time_range_hours` | window for SMA + σ |

### Algorithm

```
Each tick, per leg:
  load closes window
  compute SMA, σ
  upper = SMA + sigma_entry * σ
  lower = SMA - sigma_entry * σ
  mid_target = SMA + sigma_exit * σ   # for longs; symmetric for shorts

  if AI label = appreciation AND held = 0:
    arm long if buy_rate <= lower
  if held > 0 AND sell_rate >= mid_target:
    close long

  (mirror for shorts on TFEX with depreciation)
```

### Plug-in points

- New `SimpleOscillatorTA` class implementing the same `evaluate`
  contract.
- Reuses everything in the row-selection (`_ta_prediction_row_for_leg`)
  and TFEX 0.01-tick gate.
- Sizing tiers identical to mean-band.

### Decision points

| Decision | Pro | Con |
|---|---|---|
| Use σ-multiplier (not k·σ floor) | Wider bands = fewer false touches | Fewer entries → smaller trade count → harder to validate stat-sig |
| Exit at SMA vs at +1σ | Faster exit, more trade count | Misses bigger reversion moves |
| Reuse mean-band's regime-flatten | Consistent TFEX behavior | Couples strategies; regime logic was tuned for tighter bands |

### What it solves vs creates

- **Solves:** mean-band's noise-trade problem when bands are tight.
- **Creates:** much sparser signal. May go days without a touch in
  trending or quiet markets. Need to validate sample size before
  trusting backtest results.

---

## 2. Enhanced Oscillator with RSI confirmation

### Concept

Layer momentum confirmation on top of strategy 1: only enter when AI
direction AND RSI extreme agree. Scale into the position as RSI gets
more extreme.

### Signals & parameters

| Param | Default | Role |
|---|---|---|
| `rsi_period` | 14 bars | RSI window |
| `rsi_oversold` | 30 (entry threshold for longs) | |
| `rsi_oversold_strong` | 20 | scale-in threshold |
| `rsi_overbought` | 70 (exit threshold for longs) | |
| `rsi_overbought_strong` | 80 | full-exit threshold |
| `scale_in_steps` | 3 | number of accumulation tranches |
| `scale_in_size_pct` | per-tier × 1/scale_in_steps | |

### Algorithm

```
each tick, per leg:
  rsi = closes_window.rsi(rsi_period)

  if AI = appreciation:
    if held = 0 AND rsi <= rsi_oversold:
      open long with first tranche
    elif held > 0 AND rsi <= rsi_oversold_strong AND tranches_used < scale_in_steps:
      add next tranche
    elif held > 0 AND rsi >= rsi_overbought:
      reduce 50%
    elif held > 0 AND rsi >= rsi_overbought_strong:
      close all
```

### Plug-in points

- Needs `ClosesWindow.rsi()` (foundation 0b).
- Needs **tranche tracking state** in `TradeConfig` or session state:
  `tranches_used` per leg per session. Today the code tracks `held` and
  average cost but not tranche count.
- Sizing math diverges: tier × `1/scale_in_steps` per tranche, not
  tier × full.

### TFEX-specific notes

- Scale-in must respect `tfex_max_open_contracts` per tranche, not
  cumulatively. Consider rounding: e.g. 5 contracts cap with 3 tranches
  ⇒ `[2, 2, 1]`, not `[1.67, 1.67, 1.67]`.
- Each scale-in is a separate `place_order` call; the executor's
  duplicate-close / working-order guards apply.

### Decision points

| Decision | Pro | Con |
|---|---|---|
| Scale-in vs all-or-nothing | Better avg cost, smoother equity | Fees stack; partial fills harder to manage; complexity |
| RSI as gate (block trades) vs filter (size adjuster) | Gate: cleaner logic | Filter: more trades, potentially better expectancy |
| Use raw RSI vs Stochastic RSI | RSI: simpler, well-known | Stoch RSI: more sensitive to recent extremes |

### What it solves vs creates

- **Solves:** mean-band's "buy at the lower band right before it breaks
  through" failure mode — RSI confirms exhaustion before entry.
- **Creates:** RSI lags. In sharp moves you miss the reversal entirely.
  Also: AI confidence and RSI signal can be uncorrelated, so the gate
  may filter out high-quality entries.

---

## 3. Range trading (swing-based, AI-optional)

### Concept

Identify a market range using prior swing highs/lows. Trade the range:
buy near the lower boundary, sell near the upper. Optionally gated by
AI; can run AI-less in compressed sideways markets where mean reversion
dominates.

### Signals & parameters

| Param | Default | Role |
|---|---|---|
| `swing_lookback_hours` | 168 (1 week) | window for swing detection |
| `swing_min_separation_bars` | 5 | minimum bars between swing pivots |
| `swing_n_pivots` | 4 | min pivots to confirm a range |
| `range_tolerance_pct` | 0.1% of range height | bands around boundary |
| `range_break_pct` | 0.5% beyond boundary | breakout / stop trigger |
| `ai_required` | False | if False, run on geometry alone |

### Algorithm

```
each tick:
  pivots_high, pivots_low = swings(swing_lookback)
  if len(pivots_high) < swing_n_pivots/2:
    no range → skip
  range_high = top quartile of pivots_high
  range_low  = bottom quartile of pivots_low
  height = range_high - range_low
  if height / SMA < min_range_pct:
    range too tight → skip

  upper_buy_zone = range_low * (1 + range_tolerance_pct)
  lower_sell_zone = range_high * (1 - range_tolerance_pct)

  if (NOT ai_required OR AI = appreciation) AND held = 0 AND buy_rate <= upper_buy_zone:
    open long
  if held > 0 AND sell_rate >= lower_sell_zone:
    close long
  if buy_rate < range_low * (1 - range_break_pct):
    range broken → close, no new entries until new range confirmed
```

### Plug-in points

- Needs **swing detection**. Simplest: ZigZag with fixed % threshold.
  Better: ATR-based pivots. `ClosesWindow.swings(lookback)` returns
  `(highs, lows)`.
- Needs **range memory** as session state — current range bounds + the
  bar index when they were last confirmed. Today's session state has no
  such field.
- AI-optional means the predictor row may be `not_confident` and the
  strategy still trades — that's a deliberate departure from
  mean-band's row-selection rules.

### Decision points

| Decision | Pro | Con |
|---|---|---|
| ZigZag with % threshold | Simple, deterministic | One parameter, sensitive to choice |
| ATR-based pivots | Adapts to volatility | More moving parts |
| AI-optional | Captures pure mean reversion | Trades into news shocks the predictor would have flagged |
| Quartile boundary vs extremes | Robust to single outlier wicks | Misses very tight ranges with few pivots |
| Fixed-% range break vs ATR-multiple | Predictable | Not regime-aware |

### What it solves vs creates

- **Solves:** sideways-market opportunities that mean-band's tight bands
  miss because std is tiny.
- **Creates:** range-breakdown losses. Strategy 4 (breakout) is the
  natural complement — together they form a "range vs trend" pair.

---

## 4. Breakout

### Concept

The complement to mean-reversion. When the market trends out of the
range AND the AI agrees, ride the move instead of fading it.

### Signals & parameters

| Param | Default | Role |
|---|---|---|
| `range_source` | swing pivots OR Donchian channel (N-bar high/low) | |
| `donchian_period_hours` | 48 | for Donchian variant |
| `confirmation_bars` | 2 | bars price must hold beyond range |
| `breakout_buffer_pct` | 0.1% | required margin past range |
| `momentum_min` | rate-of-change ≥ 0.5% over last `n_bars` | optional |
| `stop_inside_range_pct` | 0.25% inside prior range | stop placement |

### Algorithm

```
each tick:
  range_high, range_low = donchian(donchian_period) OR last_confirmed_range()
  if AI != appreciation: bail (or run mirror for short on TFEX)

  if held = 0:
    if buy_rate > range_high * (1 + breakout_buffer_pct) for >= confirmation_bars:
      open long with stop at range_high * (1 - stop_inside_range_pct)

  if held > 0:
    if buy_rate falls back into prior range:
      close (failed breakout)
    if momentum < momentum_min:
      tighten stop
    if AI flips to depreciation:
      close
```

### Plug-in points

- Donchian can be computed from closes window directly.
- Breakout *must* track:
  - The last confirmed range,
  - Bars since breakout,
  - Stop level at entry (≠ a band-touch trigger; this is a real stop
    that becomes the L1 SL output).
- `stop_loss_limit` must be persisted with the position record so the
  Stop-loss component (Layer 1) can enforce it independently — exactly
  as the v0.4 architecture requires.

### TFEX-specific notes

- **Critical**: breakout stops will trigger inside session-end buffers.
  The current rule "no new opens last 5 min, only SL closes" is fine —
  SL closes are allowed.
- Failed breakouts close at a *loss* — that's by design, not a bug.
  Make sure SL Layer 2 (per-position max-loss threshold) is generous
  enough to accommodate the natural breakout-failure stop, otherwise L2
  will trigger before L1 every time.

### Decision points

| Decision | Pro | Con |
|---|---|---|
| Donchian vs swing-based range | Donchian: deterministic, no swing detection needed | Donchian: every new high creates a "breakout" with no real structure |
| Confirmation bars (2 vs 0) | Filters whipsaws | Slower entry → worse fill |
| Hard stop vs trailing stop | Predictable risk | Hard: gives back gains; trailing: stops out early on noise |
| AI-required vs AI-bias | Required: aligns with predictor | AI-bias: more independent from predictor errors |
| Run alongside mean-band vs replace it | Diversification | Capital split (see §0c) |

### What it solves vs creates

- **Solves:** mean-band's failure mode in trending markets — instead of
  buying-the-dip into a downtrend, ride the trend.
- **Creates:** false breakouts are common. Win rate is typically <50%;
  profits come from skewed expectancy. If the win amount isn't ≥1.5×
  the loss amount, the strategy bleeds. Backtest must measure
  expectancy, not just hit rate.

---

## 5. Volatility Compression Accumulation

### Concept

When volatility contracts (tight bands, narrow ATR, sideways price),
slowly accumulate in the AI's direction. Profit when volatility
expands.

### Signals & parameters

| Param | Default | Role |
|---|---|---|
| `compression_metric` | rolling band-width OR ATR percentile | |
| `compression_threshold` | bottom 25% of last 30-day band-width values | |
| `accumulate_step_bars` | every 4 bars during compression | |
| `accumulate_step_size_pct` | 1% of risk budget per step | |
| `max_accumulated_pct` | 20% of risk budget total | |
| `expansion_exit_threshold` | top 25% of last 30-day ATR percentile | |

### Algorithm

```
each tick:
  vol_pct = compression_metric percentile vs trailing window
  if vol_pct <= compression_threshold AND AI = appreciation:
    if bars_since_last_step >= accumulate_step_bars AND total_pct < max_accumulated_pct:
      add `accumulate_step_size_pct` long
  elif vol_pct >= expansion_exit_threshold AND held > 0:
    if price moving in profit direction: take profit
    else: exit (compression broke wrong way)
```

### Plug-in points

- The codebase already has the half-width floor (`SMA_BAND_MIN_HALF_WIDTH
  = 0.01`) — that floor *fires* exactly during compression. So a
  trivial compression detector is "is the algorithm currently using the
  floor instead of `k * std`?"
- Like RSI strategy: needs **tranche tracking** in session state.
- Needs a **trailing volatility window** longer than the SMA window
  (e.g. 30-day percentile of band-width on a 1-week SMA).

### Decision points

| Decision | Pro | Con |
|---|---|---|
| Compression metric: band-width vs ATR vs Bollinger %B | Each captures different vol dimensions | Pick one; ensemble is overkill at this stage |
| Accumulate during compression vs at compression *end* | Better avg price; rides the squeeze | Risk: compression can break wrong way; you're already loaded |
| Exit on expansion vs hold for AI horizon | Expansion = thesis confirmed → take profit | Cuts winners short |
| Hard cap on accumulated size | Caps blow-up risk | Misses outsized opportunities |

### What it solves vs creates

- **Solves:** the boredom problem. In quiet markets, mean-band makes
  small profits; compression accumulation makes larger profits when the
  squeeze breaks.
- **Creates:** position drawdown during compression itself. You're
  building exposure while volatility is low, but if the predictor is
  wrong about direction and the squeeze breaks down, you've stacked the
  loss. The 2x stop-loss rule from v0.2 (DOCX) would fit here.

---

## 6. ML / FinRL ensemble

### Concept

Reinforcement learning agents learn entry/exit timing as a policy.
Ensemble multiple agents (PPO, A2C, DDPG, SAC, DPQ) and combine via
voting or weighted Q-values.

### What this requires that the others don't

| Requirement | Status |
|---|---|
| Defined state space | TBD — would include closes window, predictor labels, position, time-of-session |
| Defined action space | discrete (long/short/flat × tier) or continuous (target position size) |
| Reward function | realized PnL? Sharpe? drawdown-penalized PnL? — choice dominates behavior |
| Training data | minimum 2 years of TFEX (data we don't yet have at intraday resolution) OR Bitkub spot |
| Compute | training takes hours per agent; ensemble = N× |
| Validation framework | walk-forward OOS; the paper's reported results are on US stocks 2009–2020 — not transferable |
| Production inference | latency budget; PyTorch/TF model loaded into the trading-tick path |

### Plug-in points

- Big architectural addition: a `Policy` interface separate from `TA`.
  Today's TA emits "open/close geometry"; an RL policy emits "target
  position vector." These are different abstractions.
- Inference path: need GPU/CPU model loaded once at session start, not
  per-tick.
- Reward calculation needs the same realized-PnL machinery the
  dashboard uses.

### Decision points

| Decision | Pro | Con |
|---|---|---|
| Use FinRL framework as-is | Fast start; benchmarks paper | FinRL is research code, not prod-grade |
| Reimplement under our infra | Cleaner, testable, our reward fn | Months of work |
| Train per-pair vs cross-pair | Per-pair: tuned, less data | Cross-pair: more data, regime-robust |
| Use predictor output as state input | RL learns to weight LLM signal | If predictor is unreliable, RL may learn to ignore it (good or bad?) |
| Reward: PnL vs Sharpe vs DD-penalized | PnL: simple | Sharpe/DD: encourages stable equity curves |
| Single agent vs ensemble | Single: simpler debug | Ensemble: paper reports better results |
| Action: discrete tier vs continuous size | Discrete: easier to train, matches existing sizing tiers | Continuous: finer control |

### Realistic stance

The parent CLAUDE.md notes the existing alpaca-trade-ideas system has
**Sharpe 1.96**. That's the bar.

- A poorly-tuned RL agent will Sharpe < 1.0 for months and convince
  nobody.
- A well-tuned RL agent on insufficient data will overfit and look
  great in backtest, fail in production.
- ML is high-effort, high-variance. Treat it as **R&D**, not a
  near-term replacement for mean-band.

**Sequencing recommendation:**

1. Get strategies 4 (breakout) and 5 (vol-compression) working as
   deterministic complements to mean-band.
2. Build the regime detector to switch between them.
3. *Then* try RL — initially as a *strategy selector* (which of
   {mean-band, breakout, vol-compression} should run *now*), not as a
   trader. Selector is a much smaller learning problem.
4. Only after the selector beats hand-coded rules: try RL as a full
   trader.

---

## 7. Regime detection (the meta-strategy)

If you build any of strategies 1–5 alongside mean-band, you'll need a
regime layer that picks which to run. Two ways:

### 7a. Hand-coded regime detector

| Regime | Condition | Strategy |
|---|---|---|
| Compressing | band-width < 25th percentile of 30-day window | vol-compression accumulate |
| Mean-reverting | ATR stable, no breakout in N bars, AI confidence medium | mean-band / simple oscillator |
| Trending up | Donchian-N high broken, momentum > threshold, AI = appreciation | breakout |
| Trending down | mirror, TFEX-only | breakout (short) |
| Range-bound | swing pivots clustered, std stable | range trading |

Add hysteresis: don't switch regimes on a single bar; require N bars of
the new regime before flipping.

### 7b. Learned regime classifier

A small classifier (logistic regression, gradient boosting, or a tiny
NN) trained on features like (band-width percentile, ATR percentile,
ADX, return autocorrelation) labels each bar with a regime. Strategy
chosen = highest-prob regime.

Decision: hand-coded first, learned later. Hand-coded is debuggable and
gives you ground-truth labels to train the classifier on.

---

## 8. Multi-strategy backtesting concerns

| Concern | Mitigation |
|---|---|
| **Data leakage** between regime detector and strategies | Walk-forward only; never train detector on the same window strategies use |
| **Look-ahead bias** in swing pivots | Use only confirmed pivots (pivot bar + N bars after) |
| **Survivorship bias** in cross-pair training | Include pairs that were delisted / had margin changes |
| **Regime change after fit** | Reserve final 20% of data for true OOS — never look at it during dev |
| **Strategy churn cost** | Charge realistic Settrade fees + Bitkub fees + a slippage model (one-tick aggressive nudge already in code) |
| **Single-pair overfit** | Validate on at least one other pair (e.g. EUR/THB) even if production only trades USD/THB |

---

## 9. What I'd actually build first (opinionated)

In priority order, with rough effort estimates:

1. **Foundation 0a + 0b** (strategy registry + ClosesWindow features) —
   small, unblocks everything. ~1 week.
2. **Strategy 4: Breakout**, run *alongside* mean-band gated by a
   trivial trend filter (e.g. "Donchian-48 high broken in last 4
   bars"). The complement that solves mean-band's worst failure mode.
   ~2 weeks incl. backtest.
3. **Hand-coded regime detector** to allocate capital between
   mean-band and breakout. ~1 week.
4. **Strategy 5: Vol-compression** — leverages existing band-width
   floor logic. ~1 week.
5. **Strategy 2: RSI oscillator** — the cheapest way to add momentum
   confirmation. ~1 week.
6. **Strategy 3: Range trading** — biggest implementation surface area
   (swing detection, range memory). Defer unless 1–5 don't deliver. ~3
   weeks.
7. **ML / FinRL** — start as strategy *selector*, not trader. ~2 months
   minimum to first paper-trade.

Skip strategy 1 (Simple Oscillator) — mean-band already covers this
flavor; it's not enough of a delta.

---

## 10. Things to verify against current code before building

- The PDF references `MeanBandTechnicalAnalysis` class behaviors. Read
  `fxagent.trading.mean_band_ta` directly to confirm:
  - What's actually a class member vs computed per-call.
  - How `TradeActionSchema` carries `stop_loss_limit` (needed for
    breakout).
  - Whether session state already has any tranche-tracking fields.
- The DOCX references a `predictor_result` row schema; confirm it
  against the live SQLAlchemy model — fields may have evolved.
- Check whether the ClosesWindow today is a list, a series, or just
  raw rows. The feature pipeline (0b) depends on this.

These are all in the active codebase, not in this docs/ folder, so
this doc deliberately stops at the design level.
