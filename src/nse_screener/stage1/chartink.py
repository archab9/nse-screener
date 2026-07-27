"""Scripted mode - POST the scan clause to Chartink's public process endpoint.

No authentication anywhere in this module, by design (spec sections 0, 2 and the build
prompt in section 8). The scan clause is a filter definition, not private account data;
running it needs only a CSRF token from the public scan page, exactly as an anonymous
visitor's browser does.

The clause below is RECONSTRUCTED from the seven conditions listed in spec section 2.
Chartink's own syntax view is the authoritative version - paste it over SCAN_CLAUSE once
you have copied it from the saved scan in your account, per section 2's instruction to
confirm the exact text before relying on it.
"""

from __future__ import annotations

import re
from datetime import date

import requests

from ..models import Stage1Hit

PROCESS_URL = "https://chartink.com/screener/process"
SCAN_PAGE_URL = "https://chartink.com/screener/"

# Reconstructed from spec section 2. Conditions, in order:
#   volume > SMA(volume,50) * 3
#   close > 30
#   % change >= 6.5
#   SMA(volume,50) >= 25000
#   volume > 50000
#   quarterly promoter+group holding >= 50
#   yearly debt/equity <= 1.25
SCAN_CLAUSE = (
    "( {cash} ( "
    "latest volume > latest sma( latest volume , 50 ) * 3 and "
    "latest close > 30 and "
    "latest per_chg >= 6.5 and "
    "latest sma( latest volume , 50 ) >= 25000 and "
    "latest volume > 50000 and "
    "quarterly promotergroupholding >= 50 and "
    "yearly debtoequity <= 1.25 "
    ") )"
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://chartink.com",
    "Referer": SCAN_PAGE_URL,
}

_CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')


class ChartinkError(RuntimeError):
    pass


def _fetch_csrf(session: requests.Session, timeout: int) -> str:
    response = session.get(SCAN_PAGE_URL, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    match = _CSRF_RE.search(response.text)
    if not match:
        raise ChartinkError(
            "Could not find a CSRF token on the Chartink scan page. The page layout may "
            "have changed - fall back to manual CSV mode."
        )
    return match.group(1)


def run_scripted_scan(
    scan_clause: str = SCAN_CLAUSE, timeout: int = 30
) -> list[Stage1Hit]:
    """Run the scan and return today's hits.

    An empty result list is returned as-is; a quiet market day legitimately produces zero
    names (spec section 7).
    """
    session = requests.Session()
    try:
        token = _fetch_csrf(session, timeout)
        headers = {**DEFAULT_HEADERS, "X-CSRF-TOKEN": token}
        response = session.post(
            PROCESS_URL,
            headers=headers,
            data={"scan_clause": scan_clause},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ChartinkError(f"Chartink request failed: {exc}") from exc
    except ValueError as exc:
        raise ChartinkError(f"Chartink returned a non-JSON response: {exc}") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise ChartinkError(f"Chartink rejected the scan clause: {payload['error']}")

    rows = payload.get("data", []) if isinstance(payload, dict) else []
    today = date.today()

    hits: list[Stage1Hit] = []
    for row in rows:
        symbol = str(row.get("nsecode") or row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        hits.append(
            Stage1Hit(
                symbol=symbol,
                name=str(row.get("name") or "").strip(),
                close=_maybe_float(row.get("close")),
                pct_change=_maybe_float(row.get("per_chg")),
                volume=_maybe_int(row.get("volume")),
                scan_date=today,
                price_backfilled=False,
            )
        )
    return hits


def _maybe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _maybe_int(value: object) -> int | None:
    result = _maybe_float(value)
    return int(result) if result is not None else None
