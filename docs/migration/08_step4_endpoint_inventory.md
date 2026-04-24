# Step 4 — Endpoint Inventory (probe results)

Live-probe results from `scripts/probe_fmp_endpoints.py` against FMP Ultimate
on 2026-04-23. Sample responses saved to `tests/probes/*.json`. This feeds the
Step 4 build plan.

## Confirmed FMP stable endpoints (14 working)

| # | Purpose | Stable path | Key params | Sample keys | Per-unit volume | Bulk estimate (2015→now, 715 tickers) |
|---|---|---|---|---|---|---|
| 1 | Earnings calendar (global) | `earnings-calendar` | `from`, `to` | symbol, date, epsActual, epsEstimated, revenueActual, revenueEstimated, lastUpdated | ~100/week | 1 call per date-range; full history ~52K rows |
| 2 | Earnings (per ticker) | `earnings` | `symbol`, `limit` | same as #1 | ~40 quarters/ticker | 715 calls; ~28K rows total |
| 3 | Earnings call transcripts | `earning-call-transcript` | `symbol`, `year`, `quarter` | symbol, period, year, date, content | ~1-2KB text/transcript | **42,529 transcripts** across our 715 tickers → ~60-100MB |
| 3a | Per-ticker transcript date index | `earning-call-transcript-dates` | `symbol` | quarter, fiscalYear, date | ~60-80 rows/ticker | 715 calls (pre-step, cheap) |
| 3b | Global transcript inventory | `earnings-transcript-list` | (none) | symbol, companyName, noOfTranscripts | 10,792 tickers | 1 call total |
| 4 | Historical dividends | `dividends` | `symbol` | symbol, date, recordDate, paymentDate, declarationDate, adjDividend, dividend, yield, frequency | ~90 rows for AAPL lifetime | 715 calls; ~40K rows |
| 5 | Historical splits | `splits` | `symbol` | symbol, date, numerator, denominator, splitType | ~5 rows/ticker lifetime | 715 calls; ~3K rows |
| 6 | Analyst grades (upgrades/downgrades) | `grades` | `symbol` | symbol, date, gradingCompany, previousGrade, newGrade, action | ~2,000 rows for AAPL | 715 calls; ~1M rows (varies widely) |
| 7 | Price target consensus | `price-target-consensus` | `symbol` | targetHigh, targetLow, targetConsensus, targetMedian | 1 row (current snapshot) | 715 calls; ~715 rows |
| 8 | Analyst estimates | `analyst-estimates` | `symbol`, `period`, `limit` | rev/ebitda/ebit/netIncome/sga/eps Low/High/Avg + analyst counts | ~4 per call | 715 × [quarter, annual] × ~10yr = ~14K rows |
| 9 | Insider trading | `insider-trading/search` | `symbol`, `page`, `limit` | symbol, filingDate, transactionDate, reportingCik, companyCik, transactionType, securitiesOwned, reportingName, typeOfOwner, acquisitionOrDisposition, directOrIndirect, formType, securitiesTransacted, price, securityName, url | ~100 filings per request (paginated) | 715 × ~5-50 pages = ~50-200K rows |
| 10 | Shares float (current snapshot) | `shares-float` | `symbol` | date, freeFloat, floatShares, outstandingShares, source | 1 row/ticker | 715 calls; ~715 rows |
| 11 | 13F holdings by fund | `institutional-ownership/extract` | `cik`, `year`, `quarter` | date, filingDate, cik, securityCusip, symbol, nameOfIssuer, shares, titleOfClass, sharesType, putCallShare, value, link | ~40-10,000 rows per fund per quarter | Depends on seed CIK list — see §"13F strategy" below |
| 11a | 13F industry summary | `institutional-ownership/industry-summary` | `year`, `quarter` | industryTitle, industryValue, date | ~400 industries | 4 calls/yr × 10 yr = 40 rows |
| 11b | Holder performance summary | `institutional-ownership/holder-performance-summary` | `cik`, `year` | date, cik, investorName, portfolioSize, securitiesAdded, securitiesRemoved, marketValue | ~quarterly rows | Small (per-CIK) |
| 12 | ETF holdings | `etf/holdings` | `symbol` | symbol, asset, name, isin, securityCusip, sharesNumber, weightPercentage, marketValue, updatedAt | 500+ rows for SPY | ~60 ETFs (sector ETFs + broad) × 1 call; ~30K rows |
| 13 | SEC filings | `sec-filings-search/symbol` | `symbol`, `from`, `to` | symbol, cik, filingDate, acceptedDate, formType, link, finalLink | ~100 filings per call (paginated) | 715 × ~5-20 pages = ~50-200K rows |

## Unavailable on our subscription (dropped from scope)

| Endpoint | 16 variants tried | Decision |
|---|---|---|
| **Short interest / short sales volume** | `short-interest`, `short-volume`, `historical-short-interest`, `fail-to-deliver`, `fail-to-deliver/search`, `short-sale-volume`, `finra-short-sale-volume`, `shorted-stocks`, `short-interest/latest`, `short-interest-by-symbol`, etc. — all 404 | **Drop from Step 4.** Not exposed on the stable API for our tier. Document as unavailable; revisit only if FMP adds it later. |

## Partial coverage — 13F "who holds ticker X?"

FMP's stable API provides 13F filings **indexed by fund CIK**, not by the held symbol. There is no direct "who holds AAPL this quarter" endpoint.

**Derivative strategy**: bulk-pull 13F extracts for a seed list of ~50-100 top institutional investors (Berkshire Hathaway, Vanguard, BlackRock, State Street, Fidelity, CapRe, etc.) across all quarters 2015–now. Store in a single `form_thirteen_f_holdings` table. Then "who holds AAPL" = `SELECT ... WHERE symbol='AAPL'`.

- Seed list size: ~100 CIKs
- Quarters: 44 (2015-Q1 through 2026-Q1)
- Calls: 100 × 44 = 4,400
- Wall time at 1.5s: ~2 hours
- Rows: 100 CIKs × 44 quarters × avg 500 holdings = ~2.2M rows

## Free / non-FMP sources (from existing data-decisions plan)

| Source | Use | Library | Notes |
|---|---|---|---|
| FRED | 10Y/2Y yields, Fed funds, HY/IG spreads, CPI, unemployment, etc. | `fredapi` | Free API key; `.get_series("DGS10", start="2015-01-01")` returns pandas Series |
| Yahoo Finance | VIX (^VIX), S&P 500 (^GSPC), NASDAQ (^IXIC), Russell 2000 (^RUT), DXY (DX-Y.NYB), gold (GC=F), oil (CL=F), copper (HG=F) | `yfinance` | No key needed; well-tested |
| Economic events CSV | FOMC / CPI / NFP / GDP dates | hardcoded CSV | ~30 min of data entry per year |

Sector ETFs (XLK, XLF, XLE, XLI, XLY, XLP, XLV, XLU, XLB, XLRE, XLC), broad-market (IWM, DIA, VTI), style factors (IWD, IWF, MTUM, QUAL, VLUE, USMV, SIZE), bond ETFs (TLT, IEF, SHY, HYG, LQD, AGG, TIP), commodities (GLD, SLV, USO, DBC), international (EFA, EEM), volatility (VXX), currency (UUP), industry sub-sectors (SOXX, SMH, XBI, KRE, XHB, XOP, XRT, XPH, XME) — **all use existing `prices.get_price_data` pipeline**; no new endpoint code needed. ~46 tickers × 2s = ~90 seconds of API time.

## Volume + wall-time headline numbers

Starting from current DB state, Step 4 adds roughly:

| Data type | Expected rows/size | API-time estimate (3000/min budget) |
|---|---|---|
| ETFs (46 new tickers, prices 2015→now) | ~360K rows | ~90s |
| Earnings calendar (global daily) | ~52K rows | ~30s |
| Earnings per-ticker | ~28K rows | ~5 min (715 calls) |
| Dividends | ~40K rows | ~5 min (715 calls) |
| Splits | ~3K rows | ~5 min (715 calls) |
| Analyst grades | ~1M rows | ~5 min (715 calls, paginated) |
| Price target consensus | ~715 rows | ~5 min (715 calls) |
| Analyst estimates (quarterly + annual) | ~14K rows | ~10 min (715 × 2 calls) |
| Insider trading | ~50-200K rows | ~15-60 min (pagination matters) |
| Shares float | ~715 rows | ~5 min |
| **13F holdings per seed CIK** | ~2.2M rows | ~2 hours (100 × 44 = 4,400 calls) |
| ETF holdings | ~30K rows | ~2 min (60 ETFs) |
| SEC filings index | ~50-200K rows | ~15-60 min (pagination) |
| Earnings transcripts (the big one) | **42K transcripts, ~60-100 MB text** | **~12-24 hours wall, ~15 min pure API** |
| News backfill (no sentiment) | unknown (ticker × date-range) | **potentially hours** |
| FRED macro (11 series) | ~30K rows | ~30s |
| Yahoo macro (8 series) | ~30K rows | ~30s |
| **Total net addition** | **~4-5 GB, mostly transcripts + 13F** | **~1-2 days of bulk pulls** |

## What remains for formal plan mode

1. **Dependency ordering** — earnings-calendar + shares-float unblock Week 1 event-guards; everything else is parallelizable.
2. **Module structure** — mirror Step 3's shape: one file per domain (`earnings.py`, `corporate_actions.py`, `analyst.py`, `ownership.py`, `institutional.py`, `filings.py`, `macro.py`).
3. **DB schema** — ~12 new tables. Keys, indexes, upsert semantics.
4. **Seed CIK list for 13F** — curate the top ~100 institutional investors.
5. **Bulk-pull strategy** — background tasks, checkpoint/resume, progress logging (learned from Step 3's 17-minute hang that run_in_background + file-based logs work).
6. **Verification** — extend L3 suite with per-endpoint checks; smoke tests for each.
7. **Risks** — FMP rate-limit headroom, storage provisioning, news-backfill volume (unknown until probed).
