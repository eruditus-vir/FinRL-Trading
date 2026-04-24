# Data Quality Notes

Living record of data semantics and known issues in [data/cache/finrl_trading.db](../../data/cache/finrl_trading.db). Read before writing any code that reads from `price_data` or `fundamental_data`.

## Price adjustment semantics

**Both `close` and `adj_close` columns hold the split-adjusted (but not dividend-adjusted) close price.** They are identical across all rows after the weakness-#2 SPY/QQQ re-fetch. There is no raw/unadjusted close in this DB, and there is no dividend-reinvested total-return series.

Why: the FMP `stable/historical-price-eod/full` endpoint returns a single `close` field that is already split-adjusted. The fetch code at [src/data/data_fetcher.py:1132](../../src/data/data_fetcher.py#L1132) does `item.get('adjClose', item['close'])` — since `adjClose` is absent from the stable response, `adj_close` falls through to the same value as `close`.

### What this means for new code

- **Use either column; prefer `adj_close`** for consistency with the backtest engine and data_processor.
- Returns, SMAs, RSI, ATR, and any multiplicative indicator computed over either column are correct.
- **Volume is NOT split-adjusted.** Any dollar-volume or VWAP computation crosses an adjustment boundary: multiplying split-adjusted close by raw volume under-counts dollar flow for pre-split dates. Mostly matters for liquidity filters and execution modeling.
- If you need raw unadjusted close (e.g., to join against external data that references nominal prices, or to validate against printed broker statements), the current endpoint does not provide it. See "Revisiting" below.

### Residual code inconsistency (latent, harmless today)

The codebase has two conventions: [src/backtest/backtest_engine.py](../../src/backtest/backtest_engine.py) and [src/data/data_processor.py](../../src/data/data_processor.py) read `adj_close`; [src/strategies/run_adaptive_rotation_strategy.py](../../src/strategies/run_adaptive_rotation_strategy.py) and [src/strategies/adaptive_rotation/](../../src/strategies/adaptive_rotation/) read `close`. Currently harmless because the two columns are identical across every row. Becomes a real bug if we ever backfill true raw close into `close` and move adjusted prices to `adj_close`. Standardize new code on `adj_close`.

## Fundamental price fields

`fundamental_data.adj_close_q` is the ticker's price snapshot on the fiscal quarter-end date (or nearest trading day). Separate from `price_data.adj_close`; already split-adjusted by construction. Used by [ml_strategy.py](../../src/strategies/ml_strategy.py) and [ml_bucket_selection.py](../../src/strategies/ml_bucket_selection.py) for min-variance weighting.

`fundamental_data.trade_price` is the price on `actual_tradedate` (the realized trading day ~2 months after quarter-end, when the report is available). This is the price the strategy executes at. Verified to match `price_data.close` on the same date within 0.5% for 99.94% of rows.

## Verified clean (2026-04-22)

- No duplicate rows on either table's unique key
- No NULL/zero close, adj_close, or volume
- Split adjustments correct on AAPL 4:1 (2020), AMZN 20:1 (2022), TSLA 3:1 (2022), NVDA 10:1 (2024)
- Public benchmark closes match: AAPL 2022-01-03 = $182.01; TSLA 2020-01-02 = $28.68
- `y_return = ln(next_trade_price / this_trade_price)` exactly matches on all 21,306 computable rows
- `y_return` distribution: mean +1.45%, std 16%, p1/p99 ±40% — healthy
- 703 of 712 tickers have zero gaps in daily coverage within their active window
- Fundamentals↔prices alignment on `(ticker, actual_tradedate)`: 99.94% match within 0.5%

## Known issues to account for

| # | Issue | Scope | Mitigation |
| --- | --- | --- | --- |
| 1 | `close` and `adj_close` are redundant | All rows | Use `adj_close`; see residual-code-inconsistency note above |
| 2 | Volume is not split-adjusted | All rows | Avoid dollar-volume / VWAP that spans split events |
| 3 | No dividend-reinvested total-return series | All rows | Fine for short-horizon price-return backtests; model dividends separately if long-horizon total return matters |
| 4 | No sector ETFs (XLK, XLF, XLE, ...) | Regime / sector-flow features blocked | Backfill ~11 tickers (weakness #3) |
| 5 | MON ticker-reuse | 356 price rows 2021-03 → 2022-12 AND 9 fundamental rows 2020-Q3 → 2022-Q3 are a different company, not Monsanto (acquired 2018) | Quarantine at query time; don't assume MON = Monsanto |
| 6 | FB ticker-reuse | 5 fundamental rows 2024-Q4 → 2025-Q4 are a new company, not Facebook/Meta | Drop or alias explicitly; do not alias `FB → META` unconditionally |
| 7 | Merged-out tickers have no FMP prices | `BXLT, PCL, RHT, SNI, TWC` — fundamentals present, prices absent | Filter via `INNER JOIN` on price_data |
| 8 | 8 orphan fundamental rows | `(AET, CDAY, HBI, HCP, IPG, MON, VIAC, WBA)` with `actual_tradedate` after ticker retired / renamed | Filter by `close IS NOT NULL` on the join |
| 9 | Dead-code `ajexdi` fallback | [data_fetcher.py:100-101](../../src/data/data_fetcher.py#L100-L101) references a Compustat field FMP never provides | Harmless; leave until refactoring |

## Resolved

- **2026-04-22** — SPY/QQQ `close != adj_close` on 2,411 rows + coverage starting only 2020-12-01. Fixed by `DELETE` + re-fetch 2015-01-01 → today via stable endpoint. Both tickers now 2,841 rows each, full range, `close == adj_close` on every row.

## Revisiting the close/adj_close redundancy

Re-fetch with a second endpoint returning raw unadjusted close, so `close` holds raw and `adj_close` holds adjusted. ~50 min of API time, ~double disk on `price_data`. Trigger conditions:

- A feature requires nominal price (corporate action filings, news-text matching, broker-statement reconciliation)
- External data source keyed on nominal price needs joining
- A dollar-volume or VWAP computation genuinely needs cross-split accuracy

Until then, the redundancy is noise, not signal.
