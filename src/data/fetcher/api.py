"""Module-level `fetch_*` and `get_*` convenience wrappers.

Relocated from src/data/data_fetcher.py on 2026-04-23 (Step 2 — mechanical move).
Dead-import purge on 2026-04-23 (Step 3.2): removed unused imports and replaced
the original `project_root` sys.path-global (dropped during Step 2, making
get_sp500_members_at_date / get_all_historical_sp500_tickers raise NameError
when called) with a file-relative Path-based resolution of the CSV default.

These are the public API that strategies / web / backtest import from.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests

from src.data.fetcher.manager import DataSourceManager, get_data_manager
from src.data.fetcher.fmp import FMPFetcher

logger = logging.getLogger(__name__)

# Repo root is 3 levels above this file: src/data/fetcher/api.py → repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def fetch_sp500_tickers(output_path: str = "./data/sp500_tickers.csv", preferred_source='FMP') -> pd.DataFrame:
    """Fetch S&P 500 tickers and save to file."""
    manager = get_data_manager(preferred_source=preferred_source)
    components = manager.get_sp500_components()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    components.to_csv(output_path, index=False)

    logger.info(f"Saved {len(components)} tickers to {output_path}")
    return components


def fetch_nasdaq100_tickers(preferred_source='FMP') -> pd.DataFrame:
    """Fetch NASDAQ 100 tickers from FMP."""
    manager = get_data_manager(preferred_source=preferred_source)
    fetcher = manager.current_source

    url = f"{fetcher.base_url}/nasdaq-constituent?apikey={fetcher.api_key}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()

    df = pd.DataFrame({
        'tickers': [item['symbol'] for item in data],
        'sectors': [item.get('sector', '') for item in data],
        'dateFirstAdded': [item.get('dateFirstAdded') or '' for item in data],
    })
    logger.info(f"Fetched {len(df)} NASDAQ 100 tickers")
    return df


def get_sp500_members_at_date(target_date: str,
                              csv_path: str = None) -> set:
    """Return the set of SP500 tickers as of *target_date*.

    Uses the historical constituents CSV (one row per snapshot date).
    Picks the latest snapshot whose date <= target_date.
    """
    if csv_path is None:
        csv_path = os.path.join(str(_REPO_ROOT), "data", "sp500_historical_constituents.csv")
    df = pd.read_csv(csv_path)
    df['date'] = pd.to_datetime(df['date'])
    target = pd.to_datetime(target_date)
    valid = df[df['date'] <= target]
    if valid.empty:
        return set()
    row = valid.iloc[-1]
    return set(row['tickers'].split(','))


def get_all_historical_sp500_tickers(csv_path: str = None,
                                     start_date: str = "2015-01-01") -> set:
    """Return every ticker that has ever been in SP500 since *start_date*."""
    if csv_path is None:
        csv_path = os.path.join(str(_REPO_ROOT), "data", "sp500_historical_constituents.csv")
    df = pd.read_csv(csv_path)
    df['date'] = pd.to_datetime(df['date'])
    df = df[df['date'] >= pd.to_datetime(start_date)]
    all_tickers: set = set()
    for tickers_str in df['tickers']:
        all_tickers.update(tickers_str.split(','))
    return all_tickers


def fetch_fundamental_data(tickers: List[str] | pd.DataFrame, start_date: str, end_date: str,
                          align_quarter_dates: bool = False, preferred_source='FMP') -> pd.DataFrame:
    """
    Fetch fundamental data for tickers.
    
    All data is automatically stored in and retrieved from the database.
    No CSV files are created.
    
    Args:
        tickers: List of ticker symbols or DataFrame with tickers and sectors
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        align_quarter_dates: Whether to align quarter dates to Mar/Jun/Sep/Dec 1st
        preferred_source: Preferred data source ('FMP')
        
    Returns:
        DataFrame with fundamental data from database
    """
    manager = get_data_manager(preferred_source=preferred_source)
    if isinstance(tickers, list):
        tickers = pd.DataFrame({'tickers': tickers, 'sectors': [None] * len(tickers), 'dateFirstAdded': [None] * len(tickers)})
    df = manager.get_fundamental_data(tickers, start_date, end_date, align_quarter_dates)
    
    logger.info(f"Retrieved {len(df)} fundamental records from database")
    return df


def fetch_price_data(tickers: List[str] | pd.DataFrame, start_date: str, end_date: str,
                    preferred_source='FMP') -> pd.DataFrame:
    """
    Fetch price data for tickers.
    
    All data is automatically stored in and retrieved from the database.
    No CSV files are created.
    
    Args:
        tickers: List of ticker symbols or DataFrame with tickers and sectors
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        preferred_source: Preferred data source ('FMP')
        
    Returns:
        DataFrame with price data from database
    """
    manager = get_data_manager(preferred_source=preferred_source)
    if isinstance(tickers, list):
        tickers = pd.DataFrame({'tickers': tickers, 'sectors': [None] * len(tickers), 'dateFirstAdded': [None] * len(tickers)})
    df = manager.get_price_data(tickers, start_date, end_date)
    
    logger.info(f"Retrieved {len(df)} price records from database")
    return df


def fetch_news(ticker: str, start_date: str, end_date: str,
               analyze_sentiment: bool = False,
               sentiment_model: Optional[str] = None,
               force_refresh: bool = False,
               preferred_source='FMP') -> pd.DataFrame:
    """
    Fetch news for a ticker with optional GPT情绪分析.

    Args:
        ticker: 股票代码
        start_date: 起始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        analyze_sentiment: 是否调用 GPT 进行情绪分析
        sentiment_model: 覆盖默认 GPT 模型
        force_refresh: 是否忽略缓存强制重新抓取
        preferred_source: 指定数据源

    Returns:
        DataFrame of news articles with sentiment metadata.
    """
    manager = get_data_manager(preferred_source=preferred_source)
    return manager.get_news(
        ticker,
        start_date,
        end_date,
        analyze_sentiment=analyze_sentiment,
        sentiment_model=sentiment_model,
        force_refresh=force_refresh
    )

