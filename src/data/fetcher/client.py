"""FMPClient — shared HTTP client for every FMP endpoint.

Replaces the inline `requests.get(url)` calls scattered across the original
`FMPFetcher`. Centralizes timeout (default 30s — fix for the 17-minute hang we
hit earlier when one call had none), retry-on-transient-failure with exponential
backoff, and the local-first caching pattern that fundamentals endpoints use.

Two public entry points:

- `fetch_cached(ticker, endpoint, period, start, end)`:
    For income/balance/cashflow/ratios/profile — the fundamentals fast-path.
    Consults `data_store.get_raw_payload` first; freshness-checks via
    `get_raw_payload_latest_date`; falls through to the API with cache-write
    on success. Offline mode returns `[]` without hitting the network.

- `get_json(endpoint, **params)`:
    Uncached raw GET used by prices / news / universes / realtime. Same
    retry + timeout behavior, no caching layer.

Retry policy (applied to both methods):
- `timeout` seconds per attempt (default 30)
- `max_retries` total attempts (default 3, so up to 3 tries)
- Backoff: `backoff_base * 2**attempt` seconds → 0.5s, 1s, 2s at defaults
- Retries on: requests.Timeout, ConnectionError, HTTP 500/502/503/504
- 429 (rate-limit) uses `backoff_base * 8 * 2**attempt` → 4s, 8s, 16s
- Any other 4xx returns an empty list (mirrors original failure semantics)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# Endpoints whose responses are cached via data_store raw_payloads table.
_CACHED_ENDPOINT_TO_PAYLOAD_KEY: Dict[str, str] = {
    "income-statement": "income",
    "balance-sheet-statement": "balance",
    "cash-flow-statement": "cashflow",
    "ratios": "ratios",
    "profile": "profile",
}

_RETRY_STATUS = {500, 502, 503, 504}
_RATE_LIMIT_STATUS = 429


class FMPClient:
    """HTTP client for the Financial Modeling Prep stable API."""

    def __init__(
        self,
        api_key: Optional[str],
        data_store,
        base_url: str = "https://financialmodelingprep.com/stable",
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self.data_store = data_store
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.offline_mode = not bool(api_key)

    # ── public API ──────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """FMP is considered available even offline — we can still serve
        cached payloads from the local store."""
        return True

    def fetch_cached(
        self,
        ticker: str,
        endpoint: str,
        period: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Local-first fetch for fundamentals endpoints.

        Mirrors the original `FMPFetcher._fetch_fmp_data` semantics exactly —
        same cache keys, same freshness threshold, same fallback behavior.
        """
        payload_key = _CACHED_ENDPOINT_TO_PAYLOAD_KEY.get(endpoint)

        # 1. Local-first: check cache (fundamentals endpoints only).
        if payload_key and start_date and end_date:
            try:
                stored = self.data_store.get_raw_payload(
                    ticker, payload_key, start_date, end_date, source="FMP"
                )
                if stored:
                    return stored
                if not self.offline_mode:
                    latest_str = self.data_store.get_raw_payload_latest_date(
                        ticker, payload_key, source="FMP"
                    )
                    if latest_str:
                        latest_dt = pd.to_datetime(latest_str)
                        today = pd.Timestamp.today().normalize()
                        req_end_dt = pd.to_datetime(end_date)
                        threshold_dt = min(
                            today - pd.DateOffset(months=3),
                            req_end_dt - pd.DateOffset(months=3),
                        )
                        if latest_dt >= threshold_dt:
                            stored2 = self.data_store.get_raw_payload(
                                ticker, payload_key, start_date, end_date, source="FMP"
                            )
                            if stored2 is not None:
                                return stored2
            except Exception:
                # Cache read failure must never block a live fetch.
                pass

        # 2. Offline mode: no network, empty result.
        if self.offline_mode:
            logger.info(
                f"Offline mode: skip remote fetch for {endpoint} {ticker}; "
                f"using local DB if available"
            )
            return []

        # 3. Build the URL — profile is the only endpoint without `period`.
        if endpoint == "profile":
            url = f"{self.base_url}/{endpoint}?symbol={ticker}&apikey={self.api_key}"
        else:
            url = (
                f"{self.base_url}/{endpoint}?symbol={ticker}&period={period}"
                f"&limit=40&apikey={self.api_key}"
            )
            if start_date:
                url += f"&from={start_date}"
            if end_date:
                url += f"&to={end_date}"

        # 4. HTTP with retry + timeout.
        data = self._get_with_retry(url, context=f"{endpoint} {ticker}")
        if data is None:
            return []

        # 5. Cache write for fundamentals endpoints.
        if payload_key and start_date and end_date:
            try:
                self.data_store._save_raw_payload(
                    "FMP", ticker, payload_key, start_date, end_date, data
                )
            except Exception as se:
                logger.debug(
                    f"Failed to save raw FMP payload {payload_key} for {ticker}: {se}"
                )
        return data

    def get_json(self, endpoint: str, **params: Any) -> Any:
        """Uncached GET — used by prices / news / universes / realtime.

        `params` is URL-encoded as query string. `apikey` is appended
        automatically. Returns parsed JSON (dict or list) on success,
        empty list on retry exhaustion / 4xx non-retryable.
        """
        endpoint = endpoint.lstrip("/")
        parts = [f"{k}={v}" for k, v in params.items() if v is not None]
        parts.append(f"apikey={self.api_key}")
        url = f"{self.base_url}/{endpoint}?{'&'.join(parts)}"
        data = self._get_with_retry(url, context=endpoint)
        return [] if data is None else data

    # ── internal ────────────────────────────────────────────────────────

    def _get_with_retry(self, url: str, context: str) -> Any:
        """Issue a GET with retry-on-transient-failure. Returns parsed JSON
        on success, None on exhaustion / non-retryable 4xx."""
        last_err: Optional[str] = None
        for attempt in range(self.max_retries):
            try:
                response = requests.get(url, timeout=self.timeout)
                status = response.status_code

                # Rate-limit: long backoff, retry
                if status == _RATE_LIMIT_STATUS:
                    if attempt == self.max_retries - 1:
                        logger.warning(
                            f"{context}: rate-limited (429), retries exhausted"
                        )
                        return None
                    delay = self.backoff_base * 8 * (2 ** attempt)
                    logger.info(
                        f"{context}: rate-limited (429), sleeping {delay:.1f}s "
                        f"before retry {attempt + 2}/{self.max_retries}"
                    )
                    time.sleep(delay)
                    continue

                # Server error: short backoff, retry
                if status in _RETRY_STATUS:
                    if attempt == self.max_retries - 1:
                        logger.warning(
                            f"{context}: HTTP {status}, retries exhausted"
                        )
                        return None
                    delay = self.backoff_base * (2 ** attempt)
                    logger.info(
                        f"{context}: HTTP {status}, sleeping {delay:.1f}s "
                        f"before retry {attempt + 2}/{self.max_retries}"
                    )
                    time.sleep(delay)
                    continue

                # Any other 4xx: non-retryable
                if 400 <= status < 500:
                    logger.warning(f"{context}: HTTP {status} (non-retryable)")
                    return None

                response.raise_for_status()
                return response.json()

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_err = f"{type(e).__name__}: {e}"
                if attempt == self.max_retries - 1:
                    logger.warning(f"{context}: {last_err}, retries exhausted")
                    return None
                delay = self.backoff_base * (2 ** attempt)
                logger.info(
                    f"{context}: {last_err}, sleeping {delay:.1f}s "
                    f"before retry {attempt + 2}/{self.max_retries}"
                )
                time.sleep(delay)
                continue

            except requests.exceptions.RequestException as e:
                # Unexpected request error — not retried
                logger.warning(f"{context}: {type(e).__name__}: {e}")
                return None

        return None
