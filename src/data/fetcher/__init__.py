"""Data-source layer for FinRL-Trading.

Preferred import path for new code:

    from src.data.fetcher import (
        fetch_price_data, fetch_fundamental_data, fetch_news,
        fetch_sp500_tickers, get_sp500_members_at_date,
        FMPClient, FMPFetcher, DataSourceManager,
    )

Legacy path `from src.data.data_fetcher import ...` continues to work via
the facade at src/data/data_fetcher.py.

Module layout (post Step 3 refactor, 2026-04-23):

- base           `DataSource` protocol + `BaseDataFetcher` abstract base
- client         Shared `FMPClient` — HTTP + timeout + retry + cache hook
- fmp            `FMPFetcher` — composes the topic modules
- manager        `DataSourceManager` + `get_data_manager` entry point

Topic modules (each takes `client: FMPClient` as its first argument):

- universes      S&P 500 / Nasdaq 100 constituents
- prices         Historical OHLCV via /historical-price-eod/full
- fundamentals   Quarterly income/balance/cashflow/ratios + derived factors
- news           /news/stock + optional GPT sentiment helpers
- realtime       /actively-trading-list + /quote + /batch-quote

Module-level convenience wrappers (used by strategy / backtest / web):

- api            `fetch_*` and `get_*` public functions
"""

from src.data.fetcher.base import DataSource, BaseDataFetcher
from src.data.fetcher.client import FMPClient
from src.data.fetcher.fmp import FMPFetcher
from src.data.fetcher.manager import DataSourceManager, get_data_manager
from src.data.fetcher.api import (
    fetch_sp500_tickers,
    fetch_nasdaq100_tickers,
    get_sp500_members_at_date,
    get_all_historical_sp500_tickers,
    fetch_fundamental_data,
    fetch_price_data,
    fetch_news,
)
from src.data.fetcher.macro import (
    fetch_fred_series,
    fetch_yahoo_series,
    fetch_macro_data,
    FRED_SERIES,
    YAHOO_SYMBOLS,
)
from src.data.fetcher.earnings import (
    fetch_earnings_per_ticker,
    fetch_earnings_calendar,
    fetch_all_earnings,
    PER_TICKER_SOURCE as EARNINGS_PER_TICKER_SOURCE,
    CALENDAR_SOURCE as EARNINGS_CALENDAR_SOURCE,
)
from src.data.fetcher.ownership import (
    fetch_insider_trading,
    fetch_insider_trading_page,
    fetch_shares_float,
    fetch_all_insider_trading,
    fetch_all_shares_float,
)
from src.data.fetcher.corporate_actions import (
    fetch_dividends,
    fetch_splits,
    fetch_all_dividends,
    fetch_all_splits,
)
from src.data.fetcher.etf import (
    fetch_etf_holdings,
    fetch_all_etf_holdings,
    ETF_UNIVERSE,
)
from src.data.fetcher.analyst import (
    fetch_analyst_grades,
    fetch_price_target_consensus,
    fetch_analyst_estimates,
    fetch_all_analyst_grades,
    fetch_all_price_targets,
    fetch_all_analyst_estimates,
    ESTIMATE_PERIODS,
)
from src.data.fetcher.universes import get_universe_tickers

__all__ = [
    # base
    "DataSource",
    "BaseDataFetcher",
    # shared client
    "FMPClient",
    # composed fetcher + manager
    "FMPFetcher",
    "DataSourceManager",
    "get_data_manager",
    # public API
    "fetch_sp500_tickers",
    "fetch_nasdaq100_tickers",
    "get_sp500_members_at_date",
    "get_all_historical_sp500_tickers",
    "fetch_fundamental_data",
    "fetch_price_data",
    "fetch_news",
    # macro (Step 4 Component 1)
    "fetch_fred_series",
    "fetch_yahoo_series",
    "fetch_macro_data",
    "FRED_SERIES",
    "YAHOO_SYMBOLS",
    # earnings (Step 4 Component 2)
    "fetch_earnings_per_ticker",
    "fetch_earnings_calendar",
    "fetch_all_earnings",
    "EARNINGS_PER_TICKER_SOURCE",
    "EARNINGS_CALENDAR_SOURCE",
    # ownership (Step 4 Component 3)
    "fetch_insider_trading",
    "fetch_insider_trading_page",
    "fetch_shares_float",
    "fetch_all_insider_trading",
    "fetch_all_shares_float",
    "get_universe_tickers",
    # corporate actions (Step 4 Component 4)
    "fetch_dividends",
    "fetch_splits",
    "fetch_all_dividends",
    "fetch_all_splits",
    # ETFs (Step 4 Component 5)
    "fetch_etf_holdings",
    "fetch_all_etf_holdings",
    "ETF_UNIVERSE",
    # analyst (Step 4 Component 6)
    "fetch_analyst_grades",
    "fetch_price_target_consensus",
    "fetch_analyst_estimates",
    "fetch_all_analyst_grades",
    "fetch_all_price_targets",
    "fetch_all_analyst_estimates",
    "ESTIMATE_PERIODS",
]
