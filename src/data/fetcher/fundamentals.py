"""Fundamental fetcher — the 5-phase pipeline producing quarterly factor records.

Step 3.7 of the 2026-04-23 refactor: relocated intact from
FMPFetcher.get_fundamental_data. Per the user's explicit call, decomposition
is MINIMAL — only the two local closures (`_align_to_mjsd_first`,
`_index_by_date`) are hoisted to module scope; the 5-phase body stays in
the public function to minimize bug surface. The 444-row equivalence test
must hold exactly (atol=1e-9) after this move.

The function's signature takes `client` + `data_store` explicitly rather than
carrying a `self` reference — purity makes the helpers easier to reason about
and aligns with how `universes`, `prices`, `news` are now shaped.

Network calls that were `self._fetch_fmp_data(...)` now go through
`client.fetch_cached(...)`; price-data lookup goes through
`prices.get_price_data(client, data_store, ...)`. Behavior is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.data.fetcher.client import FMPClient
from src.data.fetcher import prices as prices_mod

logger = logging.getLogger(__name__)


# ── hoisted local closures (Step 3.7 second half) ──────────────────────────


def _align_to_mjsd_first(d: pd.Timestamp) -> pd.Timestamp:
    """Map a quarter-end date to the 1st day of the month two months later.

    Q1 (Mar) → Jun 1, Q2 (Jun) → Sep 1, Q3 (Sep) → Dec 1, Q4 (Dec) → Mar 1 (next year).
    Was a local closure inside get_fundamental_data. Pure function, no `self`.
    """
    month = int(d.month)
    year = int(d.year)
    if month in (12, 1, 2):
        if month == 12:
            year = year + 1
        return pd.Timestamp(year=year, month=3, day=1)
    if month in (3, 4, 5):
        return pd.Timestamp(year=year, month=6, day=1)
    if month in (6, 7, 8):
        return pd.Timestamp(year=year, month=9, day=1)
    # 9,10,11
    return pd.Timestamp(year=year, month=12, day=1)


def _index_by_date(items: List[Dict[str, Any]]) -> Dict[pd.Timestamp, Dict[str, Any]]:
    """Build {quarter-end-timestamp → item} from an FMP quarterly payload.

    Precedence (preserved EXACTLY — the top-3 Step 3 risks list flagged this):
    1. Use `calendarYear` + `period` (Q1/Q2/Q3/Q4/FY→Q4) → fixed month/day.
    2. Else fall back to `date` field, map to its calendar-quarter end.

    Was a local closure inside get_fundamental_data. Pure function.
    """
    out: Dict[pd.Timestamp, Dict[str, Any]] = {}
    for it in items or []:
        key_ts = None
        try:
            year = it.get('calendarYear')
            period_raw = it.get('period')
            period = str(period_raw).upper() if period_raw is not None else None
            if year is not None and period:
                q_map = {
                    'Q1': (3, 31),
                    'Q2': (6, 30),
                    'Q3': (9, 30),
                    'Q4': (12, 31),
                    'FY': (12, 31),  # treat annual FY payloads as Q4-ending
                }
                md = q_map.get(period)
                if md:
                    key_ts = pd.Timestamp(int(year), md[0], md[1])
            if key_ts is None and 'date' in it and it.get('date'):
                d = pd.to_datetime(it['date'], errors='coerce')
                if pd.notna(d):
                    q = ((int(d.month) - 1) // 3) + 1
                    end_month = q * 3
                    end_day = 31 if end_month in (3, 12) else 30
                    key_ts = pd.Timestamp(int(d.year), end_month, end_day)
        except Exception:
            key_ts = None
        if key_ts is not None:
            out[key_ts] = it
    return out


# ── public entry ───────────────────────────────────────────────────────────


def get_fundamental_data(client, data_store, tickers: pd.DataFrame, start_date: str, end_date: str, align_quarter_dates: bool = False) -> pd.DataFrame:
    """Get fundamental data from FMP with extended fields and forward y_return and incremental updates.
    Adds one next quarter to compute the last forward return, then drops that extra row, and drops rows with missing y_return.
    If align_quarter_dates is True, align each quarter to Mar/Jun/Sep/Dec 1st and compute prices and y_return based on these aligned dates."""

    # # Step 1: Check database for existing data
    # existing_data = self.data_store.get_fundamental_data(tickers, start_date, end_date)
    # logger.info(f"Found {len(existing_data)} existing fundamental records in database")

    # Step 2: Decide tickers to handle (local-first inside _fetch_fmp_data avoids extra API calls)
    tickers_to_fetch = list(tickers['tickers'])

    # Step 3: Fetch missing data from API
    all_records: List[Dict[str, Any]] = []
    if tickers_to_fetch:
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        # extend both ends to ensure previous/next quarter prices available
        extended_start_dt = (start_dt - pd.DateOffset(months=4))
        # Ensure extended_end_dt does not exceed today
        today = pd.Timestamp.today().normalize()
        candidate_end_dt = (end_dt + pd.DateOffset(months=3))
        extended_end_dt = min(candidate_end_dt, today)
        extended_start_date = extended_start_dt.strftime('%Y-%m-%d')
        extended_end_date = extended_end_dt.strftime('%Y-%m-%d')
        
    prices = prices_mod.get_price_data(client, data_store, tickers, extended_start_date, extended_end_date)

    for ticker in tickers_to_fetch if tickers_to_fetch else []:
        try:
            # Local-first: helper will use DB in offline mode
            income_data = client.fetch_cached(ticker, 'income-statement', 'quarter', start_date, end_date)
            balance_data = client.fetch_cached(ticker, 'balance-sheet-statement', 'quarter', start_date, end_date)
            cashflow_data = client.fetch_cached(ticker, 'cash-flow-statement', 'quarter', start_date, end_date)

            # ratios
            # Ratios via helper
            ratios_data = client.fetch_cached(ticker, 'ratios', 'quarter', start_date, end_date) or []

            # profile for sector
            # Profile via helper
            gsector = None
            try:
                gsector = tickers[tickers['tickers'] == ticker]['sectors'].values[0]
                # prof_json = client.fetch_cached(ticker, 'profile', 'na', start_date, end_date)
                # if isinstance(prof_json, list) and prof_json:
                #     gsector = prof_json[0].get('sector')
            except Exception as e:
                logger.warning(f"Failed to fetch profile for {ticker}: {e}")

            # price data for quarter adj_close (need next quarter too)
            
            prices_t = prices[prices['tic'] == ticker].copy() if not prices.empty else pd.DataFrame()
            if not prices_t.empty and 'datadate' in prices_t.columns:
                prices_t['datadate'] = pd.to_datetime(prices_t['datadate'], errors='coerce')
                prices_t = prices_t.dropna(subset=['datadate']).sort_values('datadate', ascending=True)

            income_by_date = _index_by_date(income_data)
            balance_by_date = _index_by_date(balance_data)
            cashflow_by_date = _index_by_date(cashflow_data)
            ratios_by_date = _index_by_date(ratios_data)

            # in-range quarter dates
            # TODO: ratios and cashflow dates are different in NVDA, need to adapt to this case
            all_quarter_dates = sorted(set(income_by_date.keys()) | set(balance_by_date.keys()) | set(cashflow_by_date.keys()))
            inrange_quarters = [d for d in all_quarter_dates if start_dt <= d <= end_dt]

            # If aligning, create a mapping from original qd -> aligned date (仅用于计算 y_return 的价格)
            if align_quarter_dates:
                aligned_dates = {qd: _align_to_mjsd_first(qd) for qd in inrange_quarters}
            else:
                aligned_dates = {qd: qd for qd in inrange_quarters}

            for qd, aligned_date in aligned_dates.items():
                # if aligned_date > today: 
                #     continue
                income_q = income_by_date.get(qd, {})
                balance_q = balance_by_date.get(qd, {})
                cash_q = cashflow_by_date.get(qd, {})
                ratio_q = ratios_by_date.get(qd, {})

                # shares outstanding
                shares_out = balance_q.get('commonStockSharesOutstanding') or income_q.get('weightedAverageShsOutDil') or income_q.get('weightedAverageShsOut')
                try:
                    shares_out = float(shares_out) if shares_out is not None else None
                except Exception:
                    shares_out = None

                # equity and income
                equity = balance_q.get('totalStockholdersEquity', balance_q.get('totalStockholdersEquity', 0)) or 0
                try:
                    equity = float(equity)
                except Exception:
                    equity = 0.0

                # net_income_ratio: prefer ratio endpoint's netProfitMargin
                net_income_ratio = ratio_q.get('netProfitMargin')
                if net_income_ratio is None:
                    net_income_ratio = income_q.get('netIncomeRatio')
                try:
                    net_income_ratio = float(net_income_ratio) if net_income_ratio is not None else np.nan
                except (TypeError, ValueError):
                    net_income_ratio = np.nan

                revenue = income_q.get('revenue', income_q.get('sales', 0))
                try:
                    revenue = float(revenue)
                except Exception:
                    revenue = 0.0

                # 价格：区分原始季度日与对齐日
                prccd_orig = np.nan
                adj_close_orig = np.nan
                prccd_aligned = np.nan
                adj_close_aligned = np.nan
                if not prices_t.empty and 'datadate' in prices_t.columns:
                    # 原始季度日价格：优先取季度日(含)之后最近交易日，若无则回退到之前最近交易日
                    price_row_orig = prices_t[prices_t['datadate'] >= qd].head(1)
                    if price_row_orig.empty:
                        price_row_orig = prices_t[prices_t['datadate'] < qd].tail(1)
                    if not price_row_orig.empty:
                        prccd_orig = float(price_row_orig.iloc[0].get('prccd', np.nan))
                        ac_o = price_row_orig.iloc[0].get('adj_close', np.nan)
                        if pd.notna(ac_o):
                            adj_close_orig = float(ac_o)

                    # 对齐日价格：仅当开启对齐时计算，用于 y_return
                    if align_quarter_dates:
                        max_days_forward = 10
                        price_row_aln = pd.DataFrame()
                        for days_offset in range(max_days_forward + 1):
                            search_date = aligned_date + pd.Timedelta(days=days_offset)
                            price_row_aln = prices_t[prices_t['datadate'] == search_date]
                            if not price_row_aln.empty:
                                break
                        if price_row_aln.empty:
                            price_row_aln = prices_t[prices_t['datadate'] <= aligned_date].tail(1)
                        if not price_row_aln.empty:
                            prccd_aligned = float(price_row_aln.iloc[0].get('prccd', np.nan))
                            ac_a = price_row_aln.iloc[0].get('adj_close', np.nan)
                            if pd.notna(ac_a):
                                adj_close_aligned = float(ac_a)

                # EPS, BPS, DPS
                eps = income_q.get('eps')
                net_income_raw = income_q.get('netIncome')
                try:
                    net_income = float(net_income_raw) if net_income_raw is not None else np.nan
                except Exception:
                    net_income = np.nan
                if eps is None and shares_out:
                    eps = (net_income / shares_out) if (pd.notna(net_income) and shares_out) else np.nan

                try:
                    eps = float(eps) if eps is not None else np.nan
                except Exception:
                    eps = np.nan

                bps = (equity / shares_out) if shares_out else np.nan

                # DPS: prefer ratio endpoint, fallback to cashflow
                dps_raw = ratio_q.get('dividendPerShare')
                try:
                    dps = float(dps_raw) if dps_raw is not None else np.nan
                except (TypeError, ValueError):
                    dps = np.nan
                if pd.isna(dps):
                    dividends_paid = cash_q.get('commonDividendsPaid') or cash_q.get('dividendsPaid')
                    try:
                        dps = (abs(float(dividends_paid)) / shares_out) if (dividends_paid is not None and shares_out) else np.nan
                    except Exception:
                        dps = np.nan

                # ratios (prefer API fields, else compute)
                cur_ratio = ratio_q.get('currentRatio')
                if cur_ratio is None:
                    ca = balance_q.get('totalCurrentAssets')
                    cl = balance_q.get('totalCurrentLiabilities')
                    try:
                        cur_ratio = (float(ca) / float(cl)) if (ca and cl and float(cl) != 0) else np.nan
                    except Exception:
                        cur_ratio = np.nan

                quick_ratio = ratio_q.get('quickRatio')
                if quick_ratio is None:
                    ca = balance_q.get('totalCurrentAssets')
                    inv = balance_q.get('inventory') or balance_q.get('inventoryAndOtherCurrentAssets')
                    cl = balance_q.get('totalCurrentLiabilities')
                    try:
                        quick_ratio = ((float(ca) - float(inv)) / float(cl)) if (ca and cl and float(cl) != 0 and inv is not None) else np.nan
                    except Exception:
                        quick_ratio = np.nan

                cash_ratio = ratio_q.get('cashRatio')
                if cash_ratio is None:
                    cash_st = balance_q.get('cashAndShortTermInvestments') or balance_q.get('cashAndShortTermInvestments', None)
                    cl = balance_q.get('totalCurrentLiabilities')
                    try:
                        cash_ratio = (float(cash_st) / float(cl)) if (cash_st and cl and float(cl) != 0) else np.nan
                    except Exception:
                        cash_ratio = np.nan

                acc_rec_turnover = ratio_q.get('receivablesTurnover') or ratio_q.get('accountsReceivableTurnover')

                debt_ratio = ratio_q.get('debtRatio')
                if debt_ratio is None:
                    liabilities = balance_q.get('totalLiabilities')
                    assets = balance_q.get('totalAssets')
                    try:
                        debt_ratio = (float(liabilities) / float(assets)) if (liabilities and assets and float(assets) != 0) else np.nan
                    except Exception:
                        debt_ratio = np.nan

                debt_to_equity = ratio_q.get('debtEquityRatio') or ratio_q.get('debtToEquity')
                if debt_to_equity is None:
                    liabilities = balance_q.get('totalLiabilities')
                    try:
                        debt_to_equity = (float(liabilities) / float(equity)) if (liabilities and equity and float(equity) != 0) else np.nan
                    except Exception:
                        debt_to_equity = np.nan

                # price multiples
                pe = ratio_q.get('priceEarningsRatio')
                if pe is None:
                    try:
                        pe = (prccd_orig / eps) if (pd.notna(prccd_orig) and eps and eps != 0) else np.nan
                    except Exception:
                        pe = np.nan

                ps = ratio_q.get('priceToSalesRatio')
                pb = ratio_q.get('priceToBookRatio')
                if pb is None:
                    try:
                        pb = (prccd_orig / bps) if (pd.notna(prccd_orig) and bps and bps != 0) else np.nan
                    except Exception:
                        pb = np.nan

                roe = (net_income / equity) if (pd.notna(net_income) and equity and float(equity) != 0.0) else np.nan

                # --- Extract all additional ratio fields ---
                RATIO_FIELD_MAP = {
                    # Profitability
                    'grossProfitMargin': 'gross_margin',
                    'operatingProfitMargin': 'operating_margin',
                    'ebitdaMargin': 'ebitda_margin',
                    'pretaxProfitMargin': 'pretax_margin',
                    'effectiveTaxRate': 'effective_tax_rate',
                    'ebtPerEbit': 'ebt_per_ebit',
                    # netIncomePerEBT removed (= 1 - effective_tax_rate)
                    # Efficiency
                    'assetTurnover': 'asset_turnover',
                    'fixedAssetTurnover': 'fixed_asset_turnover',
                    'inventoryTurnover': 'inventory_turnover',
                    'payablesTurnover': 'payables_turnover',
                    'workingCapitalTurnoverRatio': 'wc_turnover',
                    # Leverage
                    'debtToAssetsRatio': 'debt_to_assets',
                    'debtToCapitalRatio': 'debt_to_capital',
                    'longTermDebtToCapitalRatio': 'lt_debt_to_capital',
                    # financialLeverageRatio removed (= debt_to_equity + 1)
                    'interestCoverageRatio': 'interest_coverage',
                    'debtServiceCoverageRatio': 'debt_service_coverage',
                    'debtToMarketCap': 'debt_to_mktcap',
                    # Cash Flow
                    'freeCashFlowPerShare': 'fcf_per_share',
                    'operatingCashFlowPerShare': 'ocf_per_share',
                    'cashPerShare': 'cash_per_share',
                    'capexPerShare': 'capex_per_share',
                    'freeCashFlowOperatingCashFlowRatio': 'fcf_to_ocf',
                    'operatingCashFlowRatio': 'ocf_ratio',
                    'operatingCashFlowSalesRatio': 'ocf_to_sales',
                    'operatingCashFlowCoverageRatio': 'ocf_coverage',
                    'shortTermOperatingCashFlowCoverageRatio': 'st_ocf_coverage',
                    'capitalExpenditureCoverageRatio': 'capex_coverage',
                    # Per-Share
                    'revenuePerShare': 'revenue_per_share',
                    'tangibleBookValuePerShare': 'tangible_bvps',
                    'interestDebtPerShare': 'interest_debt_per_share',
                    # Valuation
                    'priceToEarningsGrowthRatio': 'peg',
                    'priceToFreeCashFlowRatio': 'price_to_fcf',
                    'priceToOperatingCashFlowRatio': 'price_to_ocf',
                    # priceToFairValue removed (= pb)
                    'enterpriseValueMultiple': 'ev_multiple',
                    # Dividend
                    'dividendPayoutRatio': 'dividend_payout',
                    'dividendYield': 'dividend_yield',
                    'dividendPaidAndCapexCoverageRatio': 'div_capex_coverage',
                    # Solvency
                    'solvencyRatio': 'solvency_ratio',
                }

                extra_ratios = {}
                for fmp_key, db_col in RATIO_FIELD_MAP.items():
                    v = ratio_q.get(fmp_key)
                    try:
                        extra_ratios[db_col] = float(v) if v is not None else np.nan
                    except (TypeError, ValueError):
                        extra_ratios[db_col] = np.nan

                # Earnings release dates from income statement
                filing_date = income_q.get('filingDate') or ''
                accepted_date = (income_q.get('acceptedDate') or '')[:10]  # trim time part

                record = {
                    'gvkey': ticker,
                    'datadate': aligned_date.strftime('%Y-%m-%d') if align_quarter_dates else qd.strftime('%Y-%m-%d'),
                    'tic': ticker,
                    'gsector': gsector,
                    'filing_date': filing_date,
                    'accepted_date': accepted_date,
                    'adj_close_q': (adj_close_aligned if align_quarter_dates and pd.notna(adj_close_aligned) else adj_close_orig) if pd.notna(adj_close_orig) or pd.notna(adj_close_aligned) else np.nan,
                    'EPS': eps if eps is not None else np.nan,
                    'BPS': bps if bps is not None else np.nan,
                    'DPS': dps if pd.notna(dps) else np.nan,
                    'cur_ratio': cur_ratio if cur_ratio is not None else np.nan,
                    'quick_ratio': quick_ratio if quick_ratio is not None else np.nan,
                    'cash_ratio': cash_ratio if cash_ratio is not None else np.nan,
                    'acc_rec_turnover': acc_rec_turnover if acc_rec_turnover is not None else np.nan,
                    'debt_ratio': debt_ratio if debt_ratio is not None else np.nan,
                    'debt_to_equity': debt_to_equity if debt_to_equity is not None else np.nan,
                    'pe': pe if pe is not None else np.nan,
                    'ps': ps if ps is not None else np.nan,
                    'pb': pb if pb is not None else np.nan,
                    'roe': roe if pd.notna(roe) else np.nan,
                    'net_income_ratio': net_income_ratio,
                    **extra_ratios,
                }
                all_records.append(record)
            
        except Exception as e:
            logger.error(f"Error fetching fundamentals for {ticker}: {e}")
            continue
    
    # Step 4: Process and combine data
    df = pd.DataFrame()
    if all_records:
        df = pd.DataFrame(all_records)
        try:
            df = df.sort_values(['tic', 'datadate'])
            # forward return: current quarter vs last quarter
            # df['y_return'] = np.log(df['adj_close_q'].shift(-1) / df['adj_close_q'])
            df['y_return'] = df.groupby('tic')['adj_close_q'].transform(lambda s: np.log(s.shift( -1) / s))

            # Fill missing y_return for the last quarter of each ticker
            # using price data (e.g., Q4 2025 y_return needs Q1 2026 price which is available even without Q1 2026 fundamentals)
            if not prices.empty and 'datadate' in prices.columns:
                prices_dt = prices.copy()
                prices_dt['datadate'] = pd.to_datetime(prices_dt['datadate'], errors='coerce')
                for tic_name, grp in df.groupby('tic'):
                    last_idx = grp.index[-1]
                    if pd.isna(df.loc[last_idx, 'y_return']):
                        last_qd = pd.to_datetime(df.loc[last_idx, 'datadate'])
                        cur_price = df.loc[last_idx, 'adj_close_q']
                        if pd.isna(cur_price) or cur_price <= 0:
                            continue
                        # Determine next quarter-end date
                        next_q_end = last_qd + pd.offsets.QuarterEnd(1)
                        # Look up price near next quarter-end from price data
                        tic_prices = prices_dt[prices_dt['tic'] == tic_name].sort_values('datadate')
                        if tic_prices.empty:
                            continue
                        # Find closest trading day to next_q_end (within 7 days before)
                        mask_near = (tic_prices['datadate'] >= next_q_end - pd.Timedelta(days=7)) & (tic_prices['datadate'] <= next_q_end + pd.Timedelta(days=3))
                        near_prices = tic_prices[mask_near]
                        if near_prices.empty:
                            # Fallback: closest price before next_q_end
                            near_prices = tic_prices[tic_prices['datadate'] <= next_q_end + pd.Timedelta(days=3)].tail(1)
                        if not near_prices.empty:
                            # Pick the one closest to next_q_end
                            near_prices = near_prices.iloc[(near_prices['datadate'] - next_q_end).abs().argsort()[:1]]
                            next_price = near_prices.iloc[0].get('adj_close', np.nan)
                            if pd.notna(next_price) and float(next_price) > 0:
                                df.loc[last_idx, 'y_return'] = np.log(float(next_price) / cur_price)

            # keep only original in-range quarters
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            mask_in_range = pd.to_datetime(df['datadate']).between(start_dt, end_dt, inclusive='both')
            df = df[mask_in_range].reset_index(drop=True)
            # don't need to drop rows without y_return
            # df = df[df['y_return'].notna()].reset_index(drop=True)
        except Exception as e:
            logger.warning(f"Failed to compute forward y_return: {e}")

    # Step 5: Return combined data
    mode = 'offline' if client.offline_mode else 'online'
    logger.info(f"Returning {len(df)} total fundamental records ({mode} mode)")
    
    return df

