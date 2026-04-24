"""Universe fetchers — S&P 500 constituents via FMP.

Step 3.4 of the 2026-04-23 refactor: relocated intact from FMPFetcher.get_sp500_components
with no behavior changes. The one externally-visible difference is that
the network call now goes through FMPClient (uniform timeout + retry); the
fallback-to-cache-on-network-failure and offline-mode paths are byte-identical.

NOTE on ticker-case naming (`dateFirstAdded`): FMP returns camelCase JSON keys
and the data_store's save/load API is keyed on the same column name. Renaming
here would ripple into the DB schema — preserved as-is for Step 3 scope.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from src.data.fetcher.client import FMPClient

logger = logging.getLogger(__name__)


def get_sp500_components(client: FMPClient, data_store, date: Optional[str] = None) -> pd.DataFrame:
    """Return S&P 500 constituents as a DataFrame with columns
    (tickers, sectors, dateFirstAdded).

    Cache behavior, in order:
    1. If `date` is cached in `data_store.sp500_components_details`, return that row.
    2. Offline mode → return latest cached row (any date) or empty DataFrame.
    3. Hit FMP `/sp500-constituent`; on success, persist via
       `data_store.save_sp500_components` and return.
    4. On any network error, fall back to latest cached row or empty DataFrame.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    # 1. Exact-date cache hit.
    cached_tickers, cached_sectors, cached_date_first_added = (
        data_store.get_sp500_components(date)
    )
    if cached_tickers:
        logger.info(f"Loading S&P 500 components from database for {date}")
        return pd.DataFrame({
            "tickers": cached_tickers.split(","),
            "sectors": cached_sectors.split(","),
            "dateFirstAdded": cached_date_first_added.split(","),
        })

    # 2. Offline mode — latest cached snapshot or empty.
    if client.offline_mode:
        latest, latest_sectors, latest_date_first_added = data_store.get_sp500_components()
        if latest:
            logger.info("Offline mode: returning latest S&P 500 components from database")
            return pd.DataFrame({
                "tickers": latest.split(","),
                "sectors": latest_sectors.split(","),
                "dateFirstAdded": latest_date_first_added.split(","),
            })
        logger.warning("Offline mode: no S&P 500 components available in database")
        return (
            pd.DataFrame({"tickers": [], "sectors": [], "dateFirstAdded": []})
            .set_index(pd.Index([], name="date"))
        )

    # 3. Live fetch via FMPClient.
    try:
        if not client.api_key:
            raise ValueError("FMP API key not found")

        data = client.get_json("sp500-constituent")
        if not data:
            raise ValueError("empty response from FMP /sp500-constituent")

        tickers = [item["symbol"] for item in data]
        sectors = [item["sector"] for item in data]
        dates_added = [item["dateFirstAdded"] for item in data]

        df = pd.DataFrame({
            "tickers": tickers,
            "sectors": sectors,
            "dateFirstAdded": dates_added,
        })

        data_store.save_sp500_components(
            date, ",".join(tickers), ",".join(sectors), ",".join(dates_added)
        )
        logger.info(f"Saved S&P 500 components to database for {date}")
        return df

    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 components from FMP: {e}")
        # 4. Fallback to whatever is cached.
        latest, latest_sectors, latest_date_first_added = data_store.get_sp500_components()
        if latest:
            return pd.DataFrame({
                "tickers": latest.split(","),
                "sectors": latest_sectors.split(","),
                "dateFirstAdded": latest_date_first_added.split(","),
            })
        return pd.DataFrame({"tickers": [], "sectors": [], "dateFirstAdded": []})
