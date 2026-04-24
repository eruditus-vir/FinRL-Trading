"""Probe FMP stable-API endpoints to confirm exact URL paths + response shapes.

Hits each endpoint with AAPL (or SPY for ETF-specific ones), saves raw JSON
to tests/probes/<name>.json, prints a summary table. Feeds into the Step 4
build plan — one row of the inventory per endpoint.

For endpoints where I'm unsure of the URL, I try 2-3 plausible variants and
pick whichever the server accepts.

Use: python scripts/probe_fmp_endpoints.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_DIR = REPO_ROOT / "tests" / "probes"
PROBE_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(REPO_ROOT / ".env")
API_KEY = os.environ.get("FMP_API_KEY")
if not API_KEY:
    print("ERROR: FMP_API_KEY not found in .env", file=sys.stderr)
    sys.exit(2)

BASE = "https://financialmodelingprep.com/stable"
TIMEOUT = 30
socket.setdefaulttimeout(TIMEOUT)


# Each entry:  (label, [(path, params_dict), (alt1, alt1_params), ...])
# First working URL (non-empty + 200) wins; we keep trying variants until one succeeds
# or they all fail.
PROBES: List[Tuple[str, List[Tuple[str, dict]]]] = [
    ("earnings_calendar_global", [
        ("earnings-calendar", {"from": "2024-06-01", "to": "2024-06-07"}),
    ]),
    ("earnings_calendar_per_ticker", [
        ("earnings", {"symbol": "AAPL", "limit": 4}),
        ("historical-earnings-calendar", {"symbol": "AAPL", "limit": 4}),
    ]),
    ("earnings_transcript", [
        ("earning-call-transcript", {"symbol": "AAPL", "year": "2024", "quarter": "2"}),
        ("earnings-call-transcripts", {"symbol": "AAPL", "year": "2024", "quarter": "2"}),
        ("earnings-transcript-latest", {"symbol": "AAPL"}),
    ]),
    ("transcripts_list_per_ticker", [
        ("earning-call-transcripts-list", {"symbol": "AAPL"}),
        ("earnings-transcript-list", {"symbol": "AAPL"}),
    ]),
    ("dividends_historical", [
        ("dividends", {"symbol": "AAPL"}),
        ("dividends-company", {"symbol": "AAPL"}),
        ("historical-dividends", {"symbol": "AAPL"}),
    ]),
    ("splits_historical", [
        ("splits", {"symbol": "AAPL"}),
        ("stock-splits", {"symbol": "AAPL"}),
        ("historical-stock-splits", {"symbol": "AAPL"}),
    ]),
    ("grades_historical", [
        ("grades", {"symbol": "AAPL"}),
        ("historical-grades", {"symbol": "AAPL"}),
        ("grades-historical", {"symbol": "AAPL"}),
    ]),
    ("price_target_consensus", [
        ("price-target-consensus", {"symbol": "AAPL"}),
        ("price-target", {"symbol": "AAPL"}),
    ]),
    ("analyst_estimates", [
        ("analyst-estimates", {"symbol": "AAPL", "period": "quarter", "limit": 4}),
        ("financial-estimates", {"symbol": "AAPL", "period": "quarter", "limit": 4}),
    ]),
    ("insider_trading", [
        ("insider-trading", {"symbol": "AAPL", "page": "0", "limit": 10}),
        ("insider-trades", {"symbol": "AAPL"}),
    ]),
    ("short_interest", [
        ("short-interest", {"symbol": "AAPL"}),
        ("historical-short-interest", {"symbol": "AAPL"}),
        ("fail-to-deliver", {"symbol": "AAPL"}),
    ]),
    ("shares_float", [
        ("shares-float", {"symbol": "AAPL"}),
        ("shares-float-historical", {"symbol": "AAPL"}),
    ]),
    ("thirteen_f_holders_of_symbol", [
        # Who holds AAPL?
        ("institutional-ownership/extract", {"symbol": "AAPL"}),
        ("institutional-holder", {"symbol": "AAPL"}),
        ("institutional-ownership", {"symbol": "AAPL"}),
    ]),
    ("thirteen_f_holdings_of_fund", [
        # What does an institution hold? Berkshire Hathaway CIK = 0001067983
        ("institutional-ownership/extract", {"cik": "0001067983", "year": "2024", "quarter": "2"}),
        ("form-thirteen", {"cik": "0001067983"}),
    ]),
    ("etf_holdings", [
        ("etf-holdings", {"symbol": "SPY"}),
        ("etf-holder", {"symbol": "SPY"}),
        ("etf/holdings", {"symbol": "SPY"}),
    ]),
    ("sec_filings_latest", [
        ("sec-filings-search/symbol", {"symbol": "AAPL"}),
        ("sec-filings", {"symbol": "AAPL"}),
        ("financials-latest", {"symbol": "AAPL"}),
    ]),
]


def probe_one(path: str, params: dict) -> Tuple[Optional[int], Optional[object], Optional[str]]:
    """Return (status_code, parsed_json, error_message)."""
    url = f"{BASE}/{path}"
    q = dict(params)
    q["apikey"] = API_KEY
    try:
        r = requests.get(url, params=q, timeout=TIMEOUT)
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"
    try:
        data = r.json()
    except Exception:
        return r.status_code, None, f"non-json body: {r.text[:120]}"
    return r.status_code, data, None


def is_usable(data) -> bool:
    """Heuristic: response is usable if it's a non-empty list, OR a dict
    that isn't an error payload."""
    if data is None:
        return False
    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        if "Error Message" in data:
            return False
        if "Message" in data and "legacy" in str(data.get("Message", "")).lower():
            return False
        return bool(data)
    return False


def describe_shape(data) -> str:
    if isinstance(data, list):
        if not data:
            return "empty list"
        first = data[0]
        if isinstance(first, dict):
            keys = list(first.keys())[:8]
            return f"list[{len(data)}] of dict; sample keys: {keys}"
        return f"list[{len(data)}] of {type(first).__name__}"
    if isinstance(data, dict):
        keys = list(data.keys())[:8]
        return f"dict; keys: {keys}"
    return f"{type(data).__name__}: {str(data)[:60]}"


def main():
    print(f"Probing {len(PROBES)} endpoints at {BASE}\n")
    results = []

    for label, variants in PROBES:
        chosen_url = None
        chosen_data = None
        chosen_status = None
        chosen_err = None
        for (path, params) in variants:
            status, data, err = probe_one(path, params)
            if is_usable(data):
                chosen_url = path
                chosen_data = data
                chosen_status = status
                break
            chosen_url = chosen_url or path
            chosen_status = status
            chosen_err = err or (data if isinstance(data, dict) else None)

        # Save raw response (whichever we landed on, even if unusable — useful for debugging).
        if chosen_data is not None:
            out_path = PROBE_DIR / f"{label}.json"
            with open(out_path, "w") as f:
                json.dump(chosen_data, f, indent=2, default=str)

        shape = describe_shape(chosen_data) if chosen_data is not None else "no data"
        ok = is_usable(chosen_data)
        status_str = f"HTTP {chosen_status}" if chosen_status else "n/a"
        mark = "OK " if ok else "XX "
        print(f"[{mark}] {label:<35}  {status_str}  via '{chosen_url}'")
        if not ok:
            err_preview = str(chosen_err)[:100] if chosen_err else "empty response"
            print(f"          failure: {err_preview}")
        else:
            print(f"          shape: {shape}")
        results.append((label, ok, chosen_url, chosen_status, shape, chosen_err))

    print("\nSaved raw payloads to tests/probes/ for further inspection.")

    # Final summary table
    print("\n---- summary ----")
    print(f"  {len(PROBES)} probes, {sum(1 for r in results if r[1])} usable, "
          f"{sum(1 for r in results if not r[1])} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
