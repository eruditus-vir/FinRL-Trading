# 03 — The `mean_band` algorithm

This is a structured re-statement of [ALGORITHM.pdf](ALGORITHM.pdf). The PDF
is the source of truth; if you spot a conflict, trust the PDF and fix this
doc.

`strategy_id: mean_band`, implemented in
`fxagent.trading.mean_band_ta:MeanBandTechnicalAnalysis`.

The algorithm runs once per tick per leg and outputs zero or more
`TradeActionSchema` rows that the executor will then size and submit.

## 1. Inputs the TA needs

Per leg, the TA receives:

- **Predictor rows** (newest-first), filtered to *active at* `market_as_of`:
  `created_at <= as_of <= predicted_ts`. With a **stale fallback** to older
  rows when none are active.
- **Closes window** for the SMA (see §3 for source resolution).
- **Market context**: `buy_rate` (ask side), `sell_rate` (bid side),
  `max_*_units`.
- **TA context**: `held` (current units, signed), `balance`, `short_ok`,
  optional `settrade_symbol` (presence ⇒ TFEX mode).

## 2. Picking the effective row (`_ta_prediction_row_for_leg`)

Of the rows active at `as_of`, ordered newest-first, exactly **one** row
becomes the "effective" row per leg:

| Case | Head row state | Decision |
|---|---|---|
| 1 | head is **directional** | use it. |
| 2 | head is `not_confident` AND still inside its horizon | scan **newer→older** siblings; pick the **first directional** still in play. |
| 3 | head is `not_confident` AND **past its horizon** | **no row** ⇒ TA skips the leg (stale pause). |
| 4 | every candidate in the active window is `not_confident` | **no row** ⇒ TA does nothing. |

**Important nuance:** `not_confident` does NOT always idle the strategy. An
older directional forecast can still arm bands until *its* horizon expires.

**Sizing nuance:** `invest_pct_from_predictions` reads the **head row's**
text, not the effective row's. So if the head is `not_confident`, sizing
returns 0% even if TA later resolved a directional sibling for *geometry*.
This means: a stale directional forecast can still tell you *which way* to
arm, but won't tell you *how big* to go — that requires a fresh head.

## 3. Closes window and price source

`range_delta` (the SMA window length) is chosen in this order:

1. `session.fixed_time_range_hours` (if set) — overrides predictor horizons.
2. `time_range` from the effective predictor row (if valid).
3. The latest **older** row for the same leg/models with non-null
   `time_range`.
4. Fallback **1 day**.

Price source by venue:

- **Bitkub spot:** `get_bitkub_closes_in_range` for the mapped symbol.
- **TFEX (Settrade):** `get_tfex_mean_band_closes_with_bitkub_basis_proxy`
  — TFEX marks aligned with Bitkub basis where the TFEX feed has gaps. Live
  marks may splice in once caches are wired.
  (See `mean_band_ta._resolve_tfex_mean_band_closes`.)

Bands are evaluated against **executable quotes from market context**:
`buy_rate` (ask) vs the **lower edge**; `sell_rate` (bid) vs the **upper
edge**. Falls back to the latest close when the book is absent.

## 4. Band math

Given closes in the window:

```
SMA          = arithmetic mean of closes
std          = population std (ddof=0)
half_offset  = max(k * std, SMA_BAND_MIN_HALF_WIDTH)   # min = 0.01 rate units
top          = SMA + half_offset
bottom       = SMA - half_offset
```

Defaults: `k = 0.5` (`TradeConfig.k`). The min half-width prevents the band
from collapsing when variance is tiny or zero.

Plain-English: it's a Bollinger band with a small floor on width.

Geometry triggers:

- `bottom` — buy / cover geometry **arms** when `buy_rate <= bottom` (after
  TFEX tick-gate checks below).
- `top` — sell / short geometry **arms** when `sell_rate >= top`.

## 5. Label gates (substring match on prediction text)

`mean_band_actions_for_leg` lower-cases the effective row's prediction and
tests substrings `appreciation` / `depreciation`. The flag
`require_label_gates` is **True on Bitkub spot** (no `settrade_symbol`) and
**False on TFEX**.

This flag only affects **risk closes** (cover / close long), NOT new opens.
**New opens on both venues always require the matching label substring.**

| Mean-band leg | Geometry condition | New-open label rule | Close / cover label rule |
|---|---|---|---|
| **Open long** (held=0, balance>0) | `buy_rate <= bottom` | must contain `appreciation` (spot AND TFEX) | — |
| **Cover short** (held<0) | `buy_rate <= bottom` | — | **Spot:** must contain `appreciation`. **TFEX:** NOT label-gated — band geometry only. |
| **Close long** (held>0) | `sell_rate >= top` | — | **Spot:** must contain `depreciation`. **TFEX:** NOT label-gated — geometry only. |
| **Open short** (held≥0, short_ok, TFEX-only) | `sell_rate >= top` | must contain `depreciation` | — |

Two important corollaries:

- **Already net long:** the *lower* band never *adds* long size — it only
  opens-from-flat or covers-shorts. Pyramiding a winning long requires an
  upper-band exit or a regime pass.
- **TFEX flatten reminder:** opens still always require the matching
  directional substring. Derivatives mode only relaxes label checks on
  *flattening* legs, so positions are not trapped by a stale headline.

## 6. TFEX-only mechanics (when `session.settrade_symbol` is set)

### 6a. 0.01 tick co-linearity gate (`apply_settrade_tick_gating`)

Before arming lower or upper band legs, snap quote/band/SMA to **two decimal
places**. If `buy_rate`, `bottom`, and `SMA` (lower side) collapse to the
same tick — or `sell_rate`, `top`, and `SMA` (upper side) collapse — the
signal **does NOT emit** until a distinct tick separates them. Avoids
duplicate ticks / fragile limits.

### 6b. Regime tracking and flatten-on-flip

The session keeps `_tfex_last_prediction_regime` (last seen
appreciation/depreciation, derived from substring match).

When the regime flips (e.g. last pass was depreciation, this pass head is
appreciation):

- A **flatten leg** may fire first to clear the wrong-side position:
    - flip → depreciation: close long
    - flip → appreciation: cover short
- Then the opposite side can arm normally on a *later* pass.

Confirmation logic:

- **Running tier score** = signed sum of tier magnitudes across the row
  filter window (Bangkok TFEX segment bounds for `created_at`), clamped to
  ±2.
  - `high_*` = ±1.0, `medium_* / med_*` = ±0.5, `low_*` = ±0.25,
  - other directional text without a tier prefix = **0.5** (signed).
  - Sign: `+` for appreciation, `−` for depreciation.
- **Sign confirmation:** flatten requires running tier score `> 0` when
  entering appreciation, `< 0` when entering depreciation.
- **`None` score** (e.g. only `not_confident` rows in the window) ⇒ flip is
  **deferred** (treated as lacking confirmation).
- **SMA-tick guard:** an extra rule blocks some flips when the working quote
  sits exactly on the SMA tick in the adverse direction (long holder
  flipping out on depreciation vs bid-at-SMA symmetry — see
  `MeanBandTechnicalAnalysis.evaluate`).

### 6c. Force-close on forecast disagreement

If still **short** under an `appreciation` regime, or still **long** under a
`depreciation` regime, AND no covering band leg fired this pass:

- TA may **force flat** — but ONLY if running tier score confirms magnitude:
    - `≥ 0.5` (appreciation side, for shorts) or
    - `≤ -0.5` (depreciation side, for longs).
- If score is `None`, force-close does **not** fire.

### 6d. Spot note on the same machinery

The same `_predictions_within_regime_running_score_window` helper supports a
rolling 6-hour naive-UTC filter for non-TFEX rows for analytics consistency,
but **regime-flatten and force-close behavior fire only on derivatives
sessions**.

## 7. Position sizing

Defaults from `TradeConfig` (each session may override percentages):

- `high_*` ⇒ `high_conf` of starting balance/risk budget — default **20%**.
- `low_*` ⇒ `low_conf` — default **5%**.
- Any other directional text without `high_` / `low_` prefix ⇒ `med_conf`
  (default **10%**) — includes `medium_*`.

Executor-specific risk budget:

- **Bitkub paper:** `invest_pct * starting_balance`, converted through the
  limit rate.
- **TFEX:** `invest_pct * tfex_max_open_contracts` (whole contracts),
  executor-normalized.

TA emits `units` as **capacity hints** on opens (`max_buy_units` /
`max_sell_units`); closes carry **exact held sizes** subject to caps.

## 8. Entry sketch (combined behavior, all legs)

- **Lower band, flat, balance > 0:** buy long — requires `appreciation` in
  the effective prediction text.
- **Lower band, short:** buy to cover — spot requires `appreciation`; TFEX
  covers on geometry once the tick gate passes.
- **Upper band, long:** sell to flat — spot requires `depreciation`; TFEX
  closes on geometry only.
- **Upper band, flat / long-slot for shorts:** sell to open short — only if
  `short_ok` AND text contains `depreciation`.
- **Upper band while net short:** does NOT manufacture extra covers —
  flatten path is via lower band / regime logic.

## 9. Execution semantics

- **Separation of concerns:** TA picks **threshold geometry and limit
  references**; the executor decides **filled size** from `invest_pct` and
  venue caps.
- **TFEX limits:** prices in 0.01 ticks (2dp); the executor typically
  applies a **one-tick aggressive nudge** on limits for fill reliability
  (defaults via `TradeActionSchema`).
- **Lower-band buys:** limit basis uses `buy_rate` snapped to TFEX decimals
  on derivatives.
- **Spot buy budget cap:** persisted paths clamp notionally against
  `min(total_balance, starting_balance)` so simulated spot books don't
  pyramid purely on profits.

## 10. P&L semantics

- Quote cash moves by `±units * rate` on fills.
- **Realized** P&L follows average-cost long exits and average-entry short
  covers. Opening a short does *not* realize anything immediately.
- Backtest dashboard shows **realized only** — no unrealized MTM snapshot.
- Net base units come from the **last trade row's** `current_units` for
  that session.

## 11. How prediction accuracy is measured (separate from PnL)

`session.rate_change_threshold` (default **0.01 rate units** vs Bitkub
closes when available):

- `|move| ≤ threshold` ⇒ counted correct **only** when the prediction text
  contains `no_change` (flat expectation).
- `move > threshold` ⇒ correct when prediction contains `appreciation`.
- `move < -threshold` ⇒ correct when prediction contains `depreciation`.

This is how "accuracy" numbers in the DOCX model-comparison tables were
computed.

## 12. One-line summary (PDF's own wording)

> Forecasting (trigger → researcher → predictor, or schedules) persists
> labeled horizons; each tick runs `run_trading_pipeline`: stop-loss, then
> mean-band TA (SMA ± max(k * population_std, 0.01) bands, bid/ask touches,
> spot-only label gates on closes), then the executor, with TFEX-only
> tick/regime/force-flat extras — sized by `high_*` / `med_*` / `low_*`
> fractions of the session risk anchor.
