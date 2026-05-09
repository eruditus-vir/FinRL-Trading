# 04 — TFEX & the Settrade executor

This doc consolidates everything venue-specific. Sources: the "Future and
TFEX" section of the DOCX, the "Executor logic" section of the DOCX, the
TFEX-only mechanics in the PDF, and the Phase-4 summary.

## 1. TFEX USD/THB futures — the contract

| Property | Value |
|---|---|
| Underlying | USD/THB exchange rate |
| Contract size | 1,000 USD per contract |
| Tick size | 0.01 THB |
| Tick value | 10 THB per contract per 0.01 move |
| Initial margin | 700 THB per contract |
| Maintenance margin | 492 THB per contract (must top back up to 700) |
| Tradable contract codes | **H, M, U, Z** (Mar/Jun/Sep/Dec) — others are illiquid |
| Settlement | Cash-settled |

Reference spec:
[TFEX_All_Currency_Futures_Contract_Specification.pdf](https://media.tfex.co.th/tfex/Documents/2024/Oct/TFEX_All_Currency_Futures_Contract_Specification.pdf)

**Why the contract month restriction matters for code:** off-quarter codes
have low liquidity and the price gaps a lot, which breaks the band-touch
geometry the TA relies on.

**Why margin matters for sizing:** every 0.01 against your position drains
10 THB per contract. From 700 → 492 (the maintenance floor) is only **20.8
ticks**. With a 32 THB rate, that's a 0.65% adverse move before margin call.
This is why you must size with `tfex_max_open_contracts` cap, not "as many
as the balance supports."

## 2. TFEX trading sessions

Raw exchange hours:

| Session | Pre-open | Trading |
|---|---|---|
| Morning | 09:15–09:45 | 09:45–12:30 |
| Afternoon | 13:15–13:45 | 13:45–16:55 |
| Night | 18:45–18:50 | 18:50–03:00 (T+1) |

**Effective Bangkok windows the executor uses** (trimmed open/close to
avoid auctions):

- ~09:46–12:29
- ~13:46–16:54
- ~18:51–02:59

Last ~5 minutes of each segment: **no new TA opens** (gate function
`is_tfex_near_session_end_no_new_opens`). In that buffer, **only stop-loss
may close**; other TA closes are rejected.

Holidays: Bank of Thailand calendar; TFEX is closed on Thai national
holidays. Phase 4 TODO is to wire BOT API for this. Today,
`market_status` from InfoV3 (the same feed as prices) acts as the kill
switch when the exchange is closed.

## 3. The 0.01 tick co-linearity gate

This is the single most TFEX-specific mechanic in the algorithm.

Before arming TFEX legs, the algorithm snaps:

- `buy_rate`, `bottom`, `SMA` (lower side), or
- `sell_rate`, `top`, `SMA` (upper side)

… to **two decimal places**. If they all collapse to the same tick, the leg
**does not emit** until a tick separates them.

Why: with a 0.01 tick size, the SMA, the band edge, and the executable
quote can mathematically fall on the same price level when std is tiny.
Emitting an order in that state produces fragile limits or duplicate ticks.

This is `apply_settrade_tick_gating` in code.

## 4. Regime tracking & flatten-on-flip (TFEX only)

State the session keeps: `_tfex_last_prediction_regime` =
`appreciation` | `depreciation` | None.

When the regime *flips*:

1. A **flatten leg** may fire first to clear the wrong side:
    - flip → `depreciation`: close long
    - flip → `appreciation`: cover short
2. Opposite-side band geometry can arm normally on the *next* pass.

Confirmation rules:

- Running tier score must have the right sign: `> 0` when entering
  `appreciation`; `< 0` when entering `depreciation` (window: Bangkok TFEX
  segment bounds for `created_at`, sum of signed tier magnitudes clamped to
  ±2).
- `None` score ⇒ flip **deferred**.
- SMA-tick guard blocks some flips when the working quote sits exactly on
  the SMA tick in the adverse direction.

## 5. Force-flat on forecast disagreement

Triggered when:

- Held position contradicts current regime (short under appreciation, or
  long under depreciation), AND
- No covering band leg fired this pass, AND
- Running tier score magnitude confirms (`≥ 0.5` or `≤ -0.5`).

If the score is `None`, force-close does NOT fire.

## 6. Settrade derivatives executor (DOCX § "Executor logic")

This section is dense; treat it as a checklist of behaviors to verify
against current code.

### 6a. Sessions & opens

- All opens must be inside the trimmed Bangkok windows above.
- Last ~5 min of each segment: no new TA opens.
- Closes in that buffer: only stop-loss may close.

### 6b. Sizing

```
desired_contracts = round(invest_pct * tfex_max_open_contracts)
                  = at least 1 if opening
```

Volume is clamped to:

- Remaining room vs `tfex_max_open_contracts`.
- Signed position direction (cannot exceed long cap when going long, etc.).

### 6c. Broker-as-truth

Prefer `get_portfolios()` net-position over the local DB when the SDK is
live; the executor logs drift vs `session._get_current_units` for diagnosis.

### 6d. O001 / "flip" handling

If the request is **long** but broker net is **short** (or vice versa):

- On real / UAT+SDK: `close_position` first (full opposite leg), then
  re-check TFEX open preflight on wall-clock Bangkok, refresh symbol/net,
  THEN open.
- Sandbox dry-run cannot flip ⇒ preflight error unless
  `SETTRADE_SANDBOX_USE_SDK=1`.

### 6e. Working orders

- Open is **blocked** if a `tfex_working_order` close leg is still working
  (real tier).
- Orphan broker opens (`get_orders()` shows working opens not matching the
  single tracked local open) ⇒ skip (transient; `ok=True`, 0 units).
- Close: if broker is already flat ⇒ success, no duplicate. If broker shows
  a working close ⇒ skip (wait), not a hard failure event.

### 6f. Market / holiday

`market_status` from InfoV3 (same feed as prices) can block opens AND
closes when the exchange is closed.

### 6g. GWD-01 / 0.01 tick

- Limit prices quantized to 2dp.
- If the limit and the cached last are the same tick, the executor may
  nudge **one tick** in the aggressive direction (buy +0.01, sell −0.01).
- If still invalid ⇒ skip the open/close (no order placed).

### 6h. Aggregate allocation

`tfex_aggregate_allocation_check_ok` runs before any real `place_order`
call — live-paper TFEX budgets vs equity.

### 6i. Limit lifecycle (real tier)

- `change_order` increases volume only if same symbol AND stored Settrade
  side matches the request; opposite side ⇒ fail fast.
- After submit: MQTT wait for full match, capped by:
    - ~1 hour, or
    - 15 minutes before segment effective end, or
    - optional `SETTRADE_LIMIT_FILL_TIMEOUT_SEC`.
- Optional `SETTRADE_LIMIT_MOVE_CANCEL_PCT` triggers drift cancel.
- `SETTRADE_LIMIT_FILL_IGNORE_SEGMENT_END` relaxes the segment-end
  shortening.

### 6j. Sandbox

- Default: no `place_order` calls; notifications only.
- `SETTRADE_SANDBOX_USE_SDK=1` ⇒ sends UAT orders.
- No blocking full-fill wait in sandbox.

### 6k. Stop-loss closes

- Use **MP-MKT** (market order on derivatives channel) per module doc.
- Session-end rules still gate non-SL closes; SL is the exception.

### 6l. Closes (TA, non-SL)

- Must be in TFEX trading window.
- Same tick-quantization and one-tick nudge logic as opens.

### 6m. Notifications / audit

- SNS + `broker_order_event` on real outcomes.
- Some preflight "skip" paths do NOT persist events (e.g. orphan broker
  opens, transient mismatches). Be aware when auditing.

## 7. Phase-4 integration (Settrade-side highlights)

From the Phase 4 Summary in the DOCX:

- **Portfolio & working orders** treated as truth: `get_portfolios` net,
  duplicate-close guards, terminal-order guards, MQTT recovery.
- **Execution quality**: quantized limits for GWD-01, aggression/buffer
  tweaks, symmetric mean-band, tier/regime-aware band logic.
- **MQTT lifecycle** including reconnect around session boundaries.
- **Bangkok-schedule alignment** for holidays and predictor windows.
- **Realized P&L scaling/labeling** for THB and dashboard.
- **Live-paper trade_config** editable via PATCH.
- **DB timestamps** standardized as naive UTC; UI/API normalize to Bangkok
  on display. Watch out: this required Alembic merges.

## 8. What can still bite you

Based on the DOCX TODOs and "Review notes":

- BOT holiday API not yet wired — manual oversight on Thai holidays.
- Discord notifications still being migrated from email.
- "Don't use closing price, use live price" for TA — high-priority TODO.
  The current `mean_band` SMA uses closes; live executable quotes are used
  only for the band-touch decision.
- Candle-bar (OHLC) display vs line-close graphs: another high-priority
  visualization fix that doesn't change algorithm output but does change
  what humans see.
- Lint workflow was removed in Phase 4 — restore in a follow-up.
