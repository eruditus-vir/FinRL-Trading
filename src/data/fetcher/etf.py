"""ETF holdings fetcher — FMP `/etf/holdings`.

Step 4 Component 5 (2026-05-07). One call per ETF returns its full constituent
list as of FMP's most recent refresh. Stored as accumulating snapshot history
(UNIQUE(etf_symbol, asset, snapshot_date)) so weekly re-runs build a
constituent-change timeline.

The 46-ETF universe is curated and module-scoped (matches `macro.py`'s
FRED_SERIES catalog pattern). Edit `ETF_UNIVERSE` below to add/remove ETFs.

Public API:
- `fetch_etf_holdings(etf_symbol, client, data_store)`
- `fetch_all_etf_holdings(etfs, client, data_store)`
- `ETF_UNIVERSE` — 46-ETF dict (symbol → description)
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── ETF universe (the editable source of truth) ───────────────────────────

ETF_UNIVERSE: dict[str, str] = {
    # Sector ETFs (11)
    "XLK":  "Technology Select Sector SPDR",
    "XLF":  "Financial Select Sector SPDR",
    "XLE":  "Energy Select Sector SPDR",
    "XLI":  "Industrial Select Sector SPDR",
    "XLY":  "Consumer Discretionary Select Sector SPDR",
    "XLP":  "Consumer Staples Select Sector SPDR",
    "XLV":  "Health Care Select Sector SPDR",
    "XLU":  "Utilities Select Sector SPDR",
    "XLB":  "Materials Select Sector SPDR",
    "XLRE": "Real Estate Select Sector SPDR",
    "XLC":  "Communication Services Select Sector SPDR",
    # Broad market (3)
    "IWM":  "iShares Russell 2000",
    "DIA":  "SPDR Dow Jones Industrial Average",
    "VTI":  "Vanguard Total Stock Market",
    # Style factors (7)
    "IWD":  "iShares Russell 1000 Value",
    "IWF":  "iShares Russell 1000 Growth",
    "MTUM": "iShares MSCI USA Momentum Factor",
    "QUAL": "iShares MSCI USA Quality Factor",
    "VLUE": "iShares MSCI USA Value Factor",
    "USMV": "iShares MSCI USA Min Vol Factor",
    "SIZE": "iShares MSCI USA Size Factor",
    # Bond ETFs (7)
    "TLT":  "iShares 20+ Year Treasury Bond",
    "IEF":  "iShares 7-10 Year Treasury Bond",
    "SHY":  "iShares 1-3 Year Treasury Bond",
    "HYG":  "iShares iBoxx High Yield Corporate Bond",
    "LQD":  "iShares iBoxx Investment Grade Corporate Bond",
    "AGG":  "iShares Core US Aggregate Bond",
    "TIP":  "iShares TIPS Bond",
    # Commodities (4)
    "GLD":  "SPDR Gold Trust",
    "SLV":  "iShares Silver Trust",
    "USO":  "United States Oil Fund",
    "DBC":  "Invesco DB Commodity Index Tracking",
    # International (2)
    "EFA":  "iShares MSCI EAFE",
    "EEM":  "iShares MSCI Emerging Markets",
    # Volatility (1)
    "VXX":  "iPath Series B S&P 500 VIX Short-Term Futures",
    # Currency (1)
    "UUP":  "Invesco DB US Dollar Index Bullish Fund",
    # Industry sub-sectors (10)
    "SOXX": "iShares Semiconductor",
    "SMH":  "VanEck Semiconductor",
    "XBI":  "SPDR S&P Biotech",
    "KRE":  "SPDR S&P Regional Banking",
    "XHB":  "SPDR S&P Homebuilders",
    "XOP":  "SPDR S&P Oil & Gas Exploration & Production",
    "XRT":  "SPDR S&P Retail",
    "XPH":  "SPDR S&P Pharmaceuticals",
    "XME":  "SPDR S&P Metals & Mining",
    "SPY":  "SPDR S&P 500 (broad benchmark)",
}  # 46 ETFs

ETF_HOLDINGS_COLUMNS = [
    "etf_symbol", "asset", "snapshot_date",
    "name", "isin", "security_cusip",
    "shares_number", "weight_percentage", "market_value", "updated_at",
]


# ── Helpers ────────────────────────────────────────────────────────────────


def _empty_holdings() -> pd.DataFrame:
    return pd.DataFrame(columns=ETF_HOLDINGS_COLUMNS)


def _get_data_store(data_store=None):
    if data_store is not None:
        return data_store
    from src.data.data_store import get_data_store
    return get_data_store()


def _get_client(client=None, data_store=None):
    if client is not None:
        return client
    from src.data.fetcher.client import FMPClient
    from src.config.settings import get_config
    cfg = get_config()
    api_key = cfg.fmp.api_key.get_secret_value() if cfg.fmp.api_key else None
    return FMPClient(api_key, _get_data_store(data_store))


def _normalize_etf_holdings(records: Iterable[dict]) -> pd.DataFrame:
    """Turn FMP /etf/holdings dicts into our column shape. Derives
    `snapshot_date` from `updatedAt[:10]` and stores the full timestamp as
    `updated_at` for audit. Drops rows missing symbol/asset/updatedAt."""
    out = []
    for r in records or []:
        etf = r.get("symbol")
        asset = r.get("asset")
        updated = r.get("updatedAt")
        if not etf or not asset or not updated:
            continue
        out.append({
            "etf_symbol": etf,
            "asset": asset,
            "snapshot_date": str(updated)[:10],
            "name": r.get("name"),
            "isin": r.get("isin"),
            "security_cusip": r.get("securityCusip"),
            "shares_number": r.get("sharesNumber"),
            "weight_percentage": r.get("weightPercentage"),
            "market_value": r.get("marketValue"),
            "updated_at": str(updated),
        })
    if not out:
        return _empty_holdings()
    df = pd.DataFrame(out)
    for col in ("shares_number", "weight_percentage", "market_value"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ── Public API ─────────────────────────────────────────────────────────────


def fetch_etf_holdings(
    etf_symbol: str,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Fetch one ETF's current holdings via /etf/holdings?symbol=X.
    UNIQUE(etf_symbol, asset, snapshot_date) handles re-runs idempotently.
    Returns the rows as stored in DB after the save."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if c.offline_mode:
        logger.info(f"Offline mode: skip /etf/holdings fetch for {etf_symbol}")
        return ds.get_etf_holdings(etf_symbol=etf_symbol)

    try:
        raw = c.get_json("etf/holdings", symbol=etf_symbol)
    except Exception as exc:
        logger.warning(f"/etf/holdings {etf_symbol} failed: {exc}")
        return _empty_holdings()

    df = _normalize_etf_holdings(raw)
    if df.empty:
        logger.info(f"/etf/holdings {etf_symbol}: empty response")
        return _empty_holdings()

    ds.save_etf_holdings(df)
    snap = df["snapshot_date"].iloc[0]
    return ds.get_etf_holdings(etf_symbol=etf_symbol, start_date=snap, end_date=snap)


def fetch_all_etf_holdings(
    etfs: Optional[List[str]] = None,
    client: Any = None,
    data_store: Any = None,
) -> pd.DataFrame:
    """Pull holdings for the universe (or a subset). Per-ETF failures log a
    warning; batch continues. Returns concatenation of all newly-saved rows."""
    ds = _get_data_store(data_store)
    c = _get_client(client, ds)

    if etfs is None:
        etfs = list(ETF_UNIVERSE.keys())

    frames = []
    for etf in etfs:
        try:
            df = fetch_etf_holdings(etf, client=c, data_store=ds)
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            logger.warning(f"fetch_all_etf_holdings: {etf} failed: {exc}")

    if not frames:
        return _empty_holdings()
    return pd.concat(frames, ignore_index=True)
