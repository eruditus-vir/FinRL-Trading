"""Realtime FMP endpoints — active trading list + single/batch quote.

Step 3.8 of the 2026-04-23 refactor: relocated from FMPFetcher. Per the user's
explicit call, these are promoted (not deleted) despite having zero callers in
the current codebase — they'll be available for future live-trading work.
They now go through FMPClient for uniform timeout + retry; the original code
used raw `requests.get(url)` with no timeout, which is exactly the shape that
caused the 17-minute hang earlier in Step 3.

Return shape is preserved verbatim (raw JSON from FMP, not a DataFrame) —
the original code's `pd.DataFrame` return annotation was misleading.
"""

from __future__ import annotations

import logging
from typing import Any, List

from src.data.fetcher.client import FMPClient

logger = logging.getLogger(__name__)


def get_active_trading_list(client: FMPClient) -> Any:
    """FMP /actively-trading-list — returns the list of currently-tradable symbols."""
    if not client.api_key:
        raise ValueError("FMP API key not found")
    return client.get_json("actively-trading-list")


def get_realtime_single_price(client: FMPClient, ticker: str) -> Any:
    """FMP /quote?symbol= — latest quote for one ticker."""
    if not client.api_key:
        raise ValueError("FMP API key not found")
    return client.get_json("quote", symbol=ticker)


def get_realtime_batch_prices(client: FMPClient, tickers: List[str]) -> Any:
    """FMP /batch-quote?symbols= — latest quote for multiple tickers (comma-joined)."""
    if not client.api_key:
        raise ValueError("FMP API key not found")
    return client.get_json("batch-quote", symbols=",".join(tickers))
