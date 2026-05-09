# 02 — System architecture

The system is two cooperating pipelines plus a venue executor. The PDF's
Figure 1 is the canonical picture; this doc explains it in prose with the
"why" pulled in from the DOCX.

## 1. Forecasting pipeline (produces `predictor_result` rows)

```
Event source ──► Trigger gate (optional) ──► Researcher (LLM) ──► Predictor (LLM) ──► Persist row ──► [enqueue eval]
   RSS / RSS-only today      TradeTriggerProcessor       LLMSentimentResearcher        predictor_result
   horizon expiry            evaluate_trade_trigger      get_context +                  rows + research
   TFEX pre-open scanners    is_new_trade?               collect_research_sentiment     items
```

Two ways a row gets created:

- **Event-driven (RSS today):** Google RSS hits the trigger gate. If
  `is_new_trade`, `TradingSession.create_prediction_with_trigger` ⇒
  `create_predictions_for_timestamp` ⇒ research → predictor → persist. Live
  paths then enqueue a worker to run the trading evaluation.
- **Scheduled:** horizon-expiry sweeps and TFEX pre-open scanners call
  `create_predictions_for_timestamp` directly — **the trigger LLM is
  skipped**.

Outputs that get persisted to `predictor_result`:

- `prediction` — text label, must contain `appreciation` / `depreciation` /
  `no_change` substring or be `not_confident`.
- `time_range_hours` — integer horizon clamped to **[1, 168]** (1 hour to 1
  week). Missing or invalid ⇒ row is logged but **not written**.
- `predicted_ts = created_at + time_range` — wall-clock from the run, not
  midnight + hours.
- `asset_a` = base ISO (e.g. USD), `asset_b` = quote ISO (e.g. THB).
- `context.prediction_session_date` — Bangkok date (YYYY-MM-DD) for
  joins and caching.

### Why this is split into Researcher + Predictor (DOCX rationale)

| Reason | What it buys |
|---|---|
| Different models excel at different tasks | sonar-pro is a great researcher but a mediocre analyst; opus is the reverse. Splitting lets you pick per-step. |
| Context-window budget | One-shot prompts blow context with raw research. Splitting compresses research into structured sentiment first. |
| Debuggability | You can see exactly which step caused a wrong prediction. |
| Scalability | Future plan: parallel researchers + analysts feeding one trader. |

## 2. Trading tick (consumes rows, never re-runs predictor)

```
run_trade_evaluation
  └─ exclusive session lock
     └─ run_trading_pipeline
         ├─ prepare_trade_evaluation
         │   • create_if_missing = False  ← does NOT run the predictor
         │   • build prediction list
         │   • build market_context (buy_rate, sell_rate, max units)
         │   • assemble PreparedTradeEvaluation
         ├─ Stop-loss FIRST  (StopLossMonitor.check)
         │   • portfolio latch, warn, FIRST close via executor
         │   • can flip emergency-stop latch that suppresses TA signals
         ├─ Technical analysis  (MeanBandTechnicalAnalysis.evaluate)
         │   • produces trade_actions (List[TradeActionSchema])
         └─ Executor  (Bitkub paper / Settrade TFEX)
             • execute_planned_trade_actions
             • execute_thx_trade_actions  (TFEX-specific path)
```

The order is **fixed** in `fxagent.trading.pipeline`: prepare → SL → TA →
executor. TA signals are suppressed while emergency-stop is latched.

### Why stop-loss runs *before* TA

Because TA may *want* to add to a losing position; SL needs the chance to
say "no, we're flat now" first. Also, if SL triggers an emergency-stop
latch, TA must not emit any opens this tick.

## 3. Trader v0.4 decomposition (DOCX, "Redesigning the trader")

The DOCX explains the architectural reason the pipeline above is shaped this
way: an earlier monolithic trader was inflexible and had no reliable stop
loss. v0.4 splits the trader into three components — and that split is
exactly the SL → TA → Executor sequence above.

| Component | Responsibility | Triggered on |
|---|---|---|
| **TA** | Decide *whether* and *what kind* of position to take, at what price/limit. Outputs `(side, open_price, stop_loss_limit)`. Recompute on every price change and on new predictions. | Every tick / new prediction |
| **Stop-loss** | Three layers: **L1** = honor TA-emitted limit; **L2** = hard per-position max-loss threshold (emergency stop, no new trades); **L3** = aggregate-loss notification for manual intervention. | Every price change; failures must notify |
| **Executor** | Translate TA decision into broker actions, subject to venue rules: session windows, tick size, working orders, flips, holiday calendar. Records new positions for SL. Per-venue: Settrade sandbox, InnovestX (TradingView), Bitkub. | New price / open / before close |

Important architectural rule: SL outputs from TA must be **persisted with the
position record** so SL is independent of TA being alive. (See "Record new
open position into the db for SL" in the DOCX.)

## 4. Venue split

| Venue | Status | Settrade-symbol set? | Behaviors |
|---|---|---|---|
| Bitkub spot (paper or live) | working | No | Long-only or flat. Sells without inventory are ignored. Label gates on closes (`require_label_gates=True`). |
| TFEX (Settrade) | Phase 4 integration | Yes | Real shorts via Settrade derivatives executor. 0.01 tick gate, regime tracking, force-flat-on-flip. Closes gated by geometry only (no label substring required). |
| InnovestX (TradingView) | Roadmap | n/a | Future second TFEX broker for redundancy. |

## 5. Persistence boundaries

- Naive UTC in DB (per Phase 4 normalization), Bangkok time on UI/API output.
  Several Phase-4 commits standardize this — earlier code had hours of
  drift.
- Alembic migrations need to run in order; check that heads are merged.
- Realized P&L is what the dashboard shows. Net base units come from the
  *last trade row's* `current_units`, not a separate position table.

## 6. Concurrency

- Per-session **exclusive lock** wraps `run_trade_evaluation` so two ticks
  can't race.
- Redis-based **predictor pair locks** with skip-when-busy semantics so a
  slow LLM call doesn't pile up duplicates for the same pair.
- Phase 4 added MQTT lifecycle handling around session boundaries (Settrade
  market-data feed reconnects).

## 7. What this architecture optimizes for

- **Predictability**: forecasting and execution are decoupled; replays are
  deterministic if rows + closes are fixed.
- **Cost**: predictor (expensive) runs at most once per (session, pair) per
  trigger; trading tick (cheap) runs frequently and just consumes rows.
- **Recoverability**: trading tick can survive without the LLM stack alive
  (it loads with `create_if_missing=False`).
- **Auditability**: every decision flows through `predictor_result` rows
  and trade rows, so a backtest replays the same code path as live.
