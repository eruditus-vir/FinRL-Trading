"""DataSource protocol and BaseDataFetcher abstract base.

Relocated from src/data/data_fetcher.py on 2026-04-23 (Step 2 — mechanical move).
Dead code purged on 2026-04-23 (Step 3.2): removed unused imports (yfinance, mcal,
tqdm, concurrent.futures), removed dead _standardize_fundamental_data method
(never called on the FMP path; its ajexdi division relied on a Compustat field
FMP does not provide).

2026-04-23 (Step 3.5): `_standardize_price_data` moved into
src/data/fetcher/prices.py as a module-private helper — it was called from
exactly one place (get_price_data) and did not use `self`.
"""

import logging
from abc import ABC
from typing import List, Optional, Protocol

import pandas as pd

logger = logging.getLogger(__name__)


class DataSource(Protocol):
    """Protocol for data source implementations."""

    def get_sp500_components(self, date: str = None) -> pd.DataFrame:
        """Get S&P 500 components."""
        ...

    def get_fundamental_data(self, tickers: List[str],
                           start_date: str, end_date: str, align_quarter_dates: bool = False) -> pd.DataFrame:
        """Get fundamental data for tickers."""
        ...

    def get_price_data(self, tickers: pd.DataFrame,
                      start_date: str, end_date: str) -> pd.DataFrame:
        """Get price data for tickers."""
        ...

    def is_available(self) -> bool:
        """Check if data source is available."""
        ...

    def get_news(self, ticker: str, from_date: str, to_date: str,
                 analyze_sentiment: bool = False,
                 sentiment_model: Optional[str] = None,
                 force_refresh: bool = False) -> pd.DataFrame:
        """Get news articles for a ticker."""
        ...


class BaseDataFetcher(ABC):
    """Base class for data fetchers with common functionality."""

    def __init__(self, cache_dir: str = None):
        """
        Initialize base data fetcher.
        
        Args:
            cache_dir: Deprecated, kept for backward compatibility. Uses DATA_BASE_DIR env var instead.
        """
        # Import here to avoid circular dependency
        from src.data.data_store import get_data_store
        self.data_store = get_data_store(base_dir=cache_dir)

