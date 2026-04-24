"""DataSourceManager and the `get_data_manager` entry point.

Relocated from src/data/data_fetcher.py on 2026-04-23 (Step 2 — mechanical move).
Dead-import purge on 2026-04-23 (Step 3.2).

Preserved intentionally even though only FMP is wired in today — kept so that
future data sources (WRDS, Polygon, ...) can be added without touching callers.
"""

import logging
from typing import Any, Dict, Optional

import pandas as pd

from src.data.fetcher.fmp import FMPFetcher

logger = logging.getLogger(__name__)


class DataSourceManager:
    """Manager for multiple data sources with automatic fallback."""

    def __init__(self, cache_dir: str = "./data/cache", preferred_source: Optional[str] = None):
        """
        Initialize DataSourceManager.
        
        Args:
            cache_dir: Directory for caching data
            preferred_source: Preferred data source name ('FMP'), 
                            None for automatic selection
        """
        self.cache_dir = cache_dir
        self.preferred_source = preferred_source

        # Initialize data sources in priority order
        self.data_sources = [
            ('FMP', FMPFetcher(cache_dir))
        ]

        # Determine best available source
        self._select_best_source()

    def _select_best_source(self):
        """Select the best available data source."""
        # If a preferred source is specified, try to use it first
        if self.preferred_source:
            preferred_source_upper = self.preferred_source.upper()
            for name, source in self.data_sources:
                if name.upper() == preferred_source_upper:
                    if source.is_available():
                        self.current_source = source
                        self.current_source_name = name
                        logger.info(f"Using preferred data source: {name}")
                        return
                    else:
                        logger.warning(f"Preferred data source '{name}' is not available, falling back to automatic selection")
                        break
        
        # Automatic selection (priority order)
        for name, source in self.data_sources:
            if source.is_available():
                self.current_source = source
                self.current_source_name = name
                logger.info(f"Selected data source: {name}")
                break

    def get_sp500_components(self, date: str = None) -> pd.DataFrame:
        """Get S&P 500 components using best available source."""
        return self.current_source.get_sp500_components(date)

    def get_fundamental_data(self, tickers: pd.DataFrame,
                           start_date: str, end_date: str, align_quarter_dates: bool = False) -> pd.DataFrame:
        """Get fundamental data using best available source."""
        return self.current_source.get_fundamental_data(tickers, start_date, end_date, align_quarter_dates)

    def get_price_data(self, tickers: pd.DataFrame,
                      start_date: str, end_date: str) -> pd.DataFrame:
        """Get price data using best available source."""
        return self.current_source.get_price_data(tickers, start_date, end_date)

    def get_news(self, ticker: str, from_date: str, to_date: str,
                 analyze_sentiment: bool = False,
                 sentiment_model: Optional[str] = None,
                 force_refresh: bool = False) -> pd.DataFrame:
        """Get news data using best available source."""
        fetcher = getattr(self.current_source, 'get_news', None)
        if not callable(fetcher):
            raise NotImplementedError(f"{self.current_source_name} does not support news retrieval")
        return fetcher(
            ticker,
            from_date,
            to_date,
            analyze_sentiment=analyze_sentiment,
            sentiment_model=sentiment_model,
            force_refresh=force_refresh
        )

    def get_source_info(self) -> Dict[str, Any]:
        """Get information about current data source."""
        return {
            'current_source': self.current_source_name,
            'available_sources': [name for name, source in self.data_sources if source.is_available()],
            'cache_dir': self.cache_dir
        }


# Global data source manager instance
_data_manager = None
_data_manager_config = {}

def get_data_manager(cache_dir: str = "./data/cache", preferred_source: Optional[str] = None) -> DataSourceManager:
    """
    Get global data source manager instance.
    
    Args:
        cache_dir: Directory for caching data
        preferred_source: Preferred data source name ('FMP'), 
                        None for automatic selection
    
    Returns:
        DataSourceManager instance
        
    Examples:
        # Automatic selection (default)
        manager = get_data_manager()
        
        # Force use FMP (if API key is configured)
        manager = get_data_manager(preferred_source='FMP')
    """
    global _data_manager, _data_manager_config
    
    # Check if we need to recreate the manager
    current_config = {'cache_dir': cache_dir, 'preferred_source': preferred_source}
    
    if _data_manager is None or _data_manager_config != current_config:
        _data_manager = DataSourceManager(cache_dir, preferred_source)
        _data_manager_config = current_config
        
    return _data_manager


