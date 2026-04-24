"""Price fetcher — historical OHLCV via FMP /historical-price-eod/full.

Step 3.5 of the 2026-04-23 refactor: relocated from FMPFetcher.get_price_data
with no behavior changes. Network call goes through FMPClient for uniform
timeout+retry. Gap-detection and caching in data_store are unchanged.

`_standardize_price_data` is moved here from BaseDataFetcher — it's the only
caller, and the function doesn't use `self`, so it belongs with the price code.
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import pandas_market_calendars as mcal
from tqdm import tqdm

from src.data.fetcher.client import FMPClient

logger = logging.getLogger(__name__)


def _standardize_price_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize incoming OHLCV to the price_data row shape.

    Accepts either FMP-shaped records (already carrying the canonical names we
    use below) or yfinance-shaped records (Title-Case). Returns columns in the
    order:  gvkey, datadate, tic, prccd, prcod, prchd, prcld, cshtrd, adj_close.

    Identical to the previous BaseDataFetcher._standardize_price_data.
    """
    df = df.copy()

    column_mapping = {
        'Open': 'prcod',
        'High': 'prchd',
        'Low': 'prcld',
        'Close': 'prccd',
        'Adj Close': 'adj_close',
        'Volume': 'cshtrd',
    }
    df = df.rename(columns=column_mapping)

    required_columns = ['datadate', 'prccd', 'prcod', 'prchd', 'prcld', 'cshtrd', 'adj_close']
    for col in required_columns:
        if col not in df.columns:
            if col == 'datadate':
                df['datadate'] = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)
            elif col == 'prccd':
                df['prccd'] = df.get('Close', df.get('close', 100))
            elif col == 'prcod':
                df['prcod'] = df.get('Open', df.get('open', df['prccd']))
            elif col == 'prchd':
                df['prchd'] = df.get('High', df.get('high', df['prccd']))
            elif col == 'prcld':
                df['prcld'] = df.get('Low', df.get('low', df['prccd']))
            elif col == 'cshtrd':
                df['cshtrd'] = df.get('Volume', df.get('volume', 1000000))
            elif col == 'adj_close':
                df['adj_close'] = df.get('Adj Close', df.get('adj_close', df['prccd']))

    if 'gvkey' not in df.columns:
        df['gvkey'] = df['tic'] if 'tic' in df.columns else 'UNKNOWN'
    if 'tic' not in df.columns:
        df['tic'] = df['gvkey'] if 'gvkey' in df.columns else 'UNKNOWN'

    return df[['gvkey', 'datadate', 'tic', 'prccd', 'prcod', 'prchd', 'prcld', 'cshtrd', 'adj_close']]


def _adjust_end_date_for_market_close(end_date: str) -> str:
    """If end_date is today and US equity market has not yet closed, shift back
    one day to avoid fetching an incomplete trading day."""
    now_local = pd.Timestamp.now(tz='America/New_York')
    if now_local.date() > pd.to_datetime(end_date).date():
        return end_date
    try:
        schedule = mcal.get_calendar(name='NYSE').schedule(
            start_date=end_date, end_date=end_date, tz='America/New_York'
        )
        if not schedule.empty:
            close_time = schedule['market_close'].iloc[-1]
            if now_local.time() < close_time.time():
                return (now_local - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    except Exception:
        pass
    return end_date


def _identify_tickers_to_fetch(
    data_store,
    tickers: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> Dict[str, List[tuple]]:
    """Return a dict of ticker → list of (start, end) gap ranges that need fetching.

    Prefers the bulk query; falls back to per-ticker parallel gap detection
    that honors each ticker's `dateFirstAdded` if provided.
    """
    tickers_to_fetch: Dict[str, List[tuple]] = {}

    try:
        bulk_missing = data_store.get_missing_price_dates_bulk(
            tickers, start_date, end_date, exchange='NYSE'
        )
    except Exception as e:
        logger.debug(f"bulk missing ranges failed, fallback to per-ticker: {e}")
        bulk_missing = None

    if bulk_missing is not None:
        for t, ranges in (bulk_missing or {}).items():
            if ranges:
                tickers_to_fetch[t] = ranges
        return tickers_to_fetch

    # Fallback: per-ticker parallel detection.
    tickers_list = (
        tickers['tickers'].astype(str).tolist()
        if isinstance(tickers, pd.DataFrame) else list(tickers)
    )
    if isinstance(tickers, pd.DataFrame) and 'dateFirstAdded' in tickers.columns:
        dfa_map = {
            row['tickers']: row['dateFirstAdded']
            for _, row in tickers[['tickers', 'dateFirstAdded']].iterrows()
        }
    else:
        dfa_map = {t: None for t in tickers_list}

    def check_missing_ranges(ticker: str):
        dfa_raw = dfa_map.get(ticker)
        try:
            dfa = pd.to_datetime(dfa_raw, errors='coerce') if dfa_raw is not None else None
        except Exception:
            dfa = None
        eff_start_dt = (
            max(pd.to_datetime(start_date), dfa) if dfa is not None
            else pd.to_datetime(start_date)
        )
        eff_start_str = eff_start_dt.strftime('%Y-%m-%d')

        missing_ranges = data_store.get_missing_price_dates(
            ticker, eff_start_str, end_date, exchange='NYSE'
        )
        if missing_ranges:
            logger.info(f"Ticker {ticker}: Need to fetch price data")
            return (ticker, missing_ranges)
        return None

    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = list(executor.map(check_missing_ranges, tickers_list))

    for res in results:
        if res:
            ticker, missing_ranges = res
            tickers_to_fetch[ticker] = missing_ranges
    return tickers_to_fetch


def _fetch_ticker_prices(
    client: FMPClient,
    ticker: str,
    min_date: str,
    max_date: str,
) -> List[Dict[str, Any]]:
    """Fetch a single ticker's OHLCV range from FMP, return a list of records."""
    data = client.get_json(
        "historical-price-eod/full",
        symbol=ticker, **{"from": min_date, "to": max_date},
    )

    if isinstance(data, dict) and 'historical' in data:
        historical_rows = data.get('historical') or []
    elif isinstance(data, list):
        historical_rows = data
    else:
        logger.warning(f"No historical data key in response for {ticker} ({min_date} to {max_date})")
        historical_rows = []

    records = []
    for item in historical_rows:
        records.append({
            'gvkey': ticker,
            'datadate': item['date'],
            'tic': ticker,
            'prccd': item['close'],
            'prcod': item['open'],
            'prchd': item['high'],
            'prcld': item['low'],
            'cshtrd': item['volume'],
            'adj_close': item.get('adjClose', item['close']),
        })
    return records


def get_price_data(
    client: FMPClient,
    data_store,
    tickers: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Fetch OHLCV with incremental gap-filling against the local DB.

    Steps:
      1. Adjust end_date if asking for today and market still open.
      2. Load what's already cached.
      3. If offline, return cached.
      4. Identify per-ticker gaps.
      5. Fetch each ticker's gap range from FMP via FMPClient.
      6. Standardize + save the new rows.
      7. Re-query the DB for the final combined result.
    """
    end_date = _adjust_end_date_for_market_close(end_date)

    # Step 1: existing cache
    existing_data = data_store.get_price_data(tickers['tickers'], start_date, end_date)
    logger.info(f"Found {len(existing_data)} existing price records in database")

    if client.offline_mode:
        logger.info("Offline mode: returning existing price data from database and skipping remote fetch")
        return existing_data

    # Step 2: identify gaps
    tickers_to_fetch = _identify_tickers_to_fetch(data_store, tickers, start_date, end_date)

    # Step 3: fetch missing
    all_data: List[Dict[str, Any]] = []
    if tickers_to_fetch:
        logger.info(f"Fetching price data for {len(tickers_to_fetch)} tickers from FMP")

        for ticker, date_ranges in tqdm(tickers_to_fetch.items()):
            min_date = min(s for s, _ in date_ranges)
            max_date = max(e for _, e in date_ranges)
            try:
                ticker_records = _fetch_ticker_prices(client, ticker, min_date, max_date)
                if ticker_records:
                    all_data.extend(ticker_records)
                    logger.debug(f"Fetched {len(ticker_records)} records for {ticker} ({min_date} to {max_date})")
                else:
                    logger.warning(f"No historical data for {ticker} ({min_date} to {max_date})")
            except Exception as e:
                logger.warning(f"Failed to fetch price data for {ticker} ({min_date} to {max_date}): {e}")
    else:
        logger.warning("No price data to fetch")
        return existing_data

    # Step 4: save
    if all_data:
        df = pd.DataFrame(all_data)
        df = _standardize_price_data(df)
        rows_saved = data_store.save_price_data(df)
        logger.info(f"Saved {rows_saved} new price records to database")

    # Step 5: return combined from DB
    final_tickers = (
        tickers['tickers'].astype(str).tolist()
        if isinstance(tickers, pd.DataFrame) else list(tickers)
    )
    final_data = data_store.get_price_data(final_tickers, start_date, end_date)
    logger.info(f"Returning {len(final_data)} total price records")
    return final_data
