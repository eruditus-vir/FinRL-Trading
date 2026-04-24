# Data Decisions

## FMP Subscription Tier: Ultimate ($99/mo)

### Why Ultimate over Premium ($49)

Premium has everything essential (quarterly fundamentals, news, prices, earnings calendar). Ultimate adds:

| Feature | Status | Value |
|---|---|---|
| Earnings Call Transcripts | **Highest value addition** | Far richer signal than headlines for LLM analysis |
| 3000 calls/min (vs 750) | Immediate | 4x faster bulk download |
| Bulk and Batch Delivery | Immediate | Fewer API calls during backfill |
| 13F Institutional Holdings | Defer | "Smart money" tracking, slow signal |
| ETF & Mutual Fund Holdings | Defer | Sector exposure features |
| Global coverage | Skip | US-focused |
| 1-min intraday | Skip | Irrelevant for swing trading |

The decision logic: if we're paying for FMP at all, the $50/mo delta to Ultimate is worth it for transcripts alone (when we eventually consume them). Removes a future upgrade decision.

### What's already automated in FinRL's data layer

```python
from src.data.data_fetcher import (
    fetch_sp500_tickers,
    fetch_fundamental_data,
    fetch_price_data,
    fetch_news,  # has optional GPT sentiment built in
)
```

Just set `FMP_API_KEY` in `.env`. These functions handle:
- Auth, caching (24hr TTL), database storage, rate limits
- Multi-source fallback (FMP > WRDS > Yahoo)

### What's NOT in FinRL's data layer (need to add)

| Endpoint | When to add |
|---|---|
| `fetch_earnings_calendar` | Week 1 Day 2 (needed for event guards) |
| `fetch_earnings_transcripts` | Defer until v4 LLM micro analysis validates |
| `fetch_13f_holdings` | Defer indefinitely — unproven value |
| `fetch_etf_holdings` | Defer indefinitely — unproven value |

Each is ~30-50 lines following the existing pattern (URL → JSON → DataFrame → cache).

## Universe: S&P 500 with historical constituents

Switched from 169 hand-picked stocks (alpaca-trade-ideas) to S&P 500. Reasons:

1. FMP has built-in S&P 500 constituent endpoint — no custom universe maintenance
2. **Historical constituents are critical for backtest honesty** — survivorship bias inflates results otherwise. A stock kicked out in 2022 must be in the 2022 backtest universe.
3. Standard benchmark — easier to compare results vs published research
4. More candidates per day for trigger system

Cost: Stage 1 classifier and macro layer must be re-validated on the larger universe. Don't expand to S&P 500 until META + NFLX 2-stock validation passes (Week 1 + Week 3 gates).

## Macro Data: FRED + Yahoo (free)

| Series | Source | Symbol/Code |
|---|---|---|
| 10-year Treasury | FRED | `DGS10` |
| 2-year Treasury | FRED | `DGS2` |
| Fed funds rate | FRED | `DFF` |
| HY credit spread | FRED | `BAMLH0A0HYM2` |
| IG credit spread | FRED | `BAMLC0A0CM` |
| VIX | Yahoo | `^VIX` |
| S&P 500 | Yahoo | `^GSPC` |
| NASDAQ | Yahoo | `^IXIC` |
| Dollar Index | Yahoo | `DX-Y.NYB` |
| Gold | Yahoo | `GC=F` |
| Oil | Yahoo | `CL=F` |
| Copper | Yahoo | `HG=F` |

**FRED API key:** Free, register at https://fred.stlouisfed.org/docs/api/api_key.html. Use `fredapi` Python package.

**Yahoo:** Free, no key needed. Use `yfinance` package. FinRL already depends on this.

## Economic event calendar

Hardcoded CSV of FOMC / CPI / NFP / GDP dates 2021-2025. Reasons:
- Schedules are published years in advance
- ~30 minutes of data entry beats library dependency churn (`investpy` etc. tend to break)
- Trivially updatable each year

Location: `data/macro/economic_events.csv`

## Storage

| Data type | Storage | Reason |
|---|---|---|
| Stock prices, fundamentals, news | FinRL's existing SQLite + parquet cache | Already built and works |
| Macro data (FRED + Yahoo) | Parquet, wide format with date index | Time-series natural fit, fast pandas read |
| LLM agent outputs (macro + micro) | Parquet | Time-series, append-only |

## Schema discipline

Before bulk download, lock these decisions:

| Decision | Choice |
|---|---|
| Time zone | UTC throughout |
| Symbol format | Plain ticker, no exchange prefix (e.g. `META`, not `NASDAQ:META`) |
| Quarterly fundamentals as-of date | Use `publish_date` (filing date), not fiscal period end |
| News primary key | Hash of (headline + first 200 chars of content) — robust to URL variations |
| Trading calendar | Use FinRL's existing calendar (probably NYSE) |

Schema changes after bulk download require re-pulling data. Lock these upfront.