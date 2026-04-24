"""Facade for backwards compatibility.

All implementation now lives in `src/data/fetcher/`. Re-exported here so the
5 existing consumers (web/app, ml_strategy, group_selection_by_gics,
performance_analyzer, backtest_engine) keep working without import changes.

Prefer importing directly from `src.data.fetcher` in new code.
"""

from src.data.fetcher import (
    DataSource,
    BaseDataFetcher,
    FMPClient,
    FMPFetcher,
    DataSourceManager,
    get_data_manager,
    fetch_sp500_tickers,
    fetch_nasdaq100_tickers,
    get_sp500_members_at_date,
    get_all_historical_sp500_tickers,
    fetch_fundamental_data,
    fetch_price_data,
    fetch_news,
    fetch_fred_series,
    fetch_yahoo_series,
    fetch_macro_data,
    FRED_SERIES,
    YAHOO_SYMBOLS,
    fetch_earnings_per_ticker,
    fetch_earnings_calendar,
    fetch_all_earnings,
    EARNINGS_PER_TICKER_SOURCE,
    EARNINGS_CALENDAR_SOURCE,
)

__all__ = [
    "DataSource",
    "BaseDataFetcher",
    "FMPClient",
    "FMPFetcher",
    "DataSourceManager",
    "get_data_manager",
    "fetch_sp500_tickers",
    "fetch_nasdaq100_tickers",
    "get_sp500_members_at_date",
    "get_all_historical_sp500_tickers",
    "fetch_fundamental_data",
    "fetch_price_data",
    "fetch_news",
    "fetch_fred_series",
    "fetch_yahoo_series",
    "fetch_macro_data",
    "FRED_SERIES",
    "YAHOO_SYMBOLS",
    "fetch_earnings_per_ticker",
    "fetch_earnings_calendar",
    "fetch_all_earnings",
    "EARNINGS_PER_TICKER_SOURCE",
    "EARNINGS_CALENDAR_SOURCE",
]
