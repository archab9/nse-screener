"""NSE trading-day awareness (spec section 10).

A non-trading day is never a blocker. The app shows the most recent trading day's data
behind an explicit "as of" banner instead of either refusing to run or quietly presenting
old numbers as current.

The 2026 holiday list is NOT hard-coded here. The spec states there are 19 but does not
enumerate them, and a wrong exchange calendar silently mis-dates every result, so an
unfilled list degrades to weekend-only detection plus a loud warning.
"""

from __future__ import annotations

from datetime import date, timedelta

from .config import holiday_calendar


def _holiday_set(year: int) -> set[date]:
    data = holiday_calendar(year)
    out: set[date] = set()
    for raw in data.get("holidays", []):
        try:
            out.add(date.fromisoformat(str(raw).strip()))
        except ValueError:
            continue
    return out


def calendar_is_verified(year: int = 2026) -> bool:
    data = holiday_calendar(year)
    holidays = data.get("holidays", [])
    expected = data.get("expected_count")
    if not data.get("verified") or not holidays:
        return False
    return expected is None or len(holidays) == expected


def calendar_status(year: int = 2026) -> str:
    """Human-readable state of the holiday list, for the banner."""
    data = holiday_calendar(year)
    holidays = data.get("holidays", [])
    expected = data.get("expected_count")
    if not holidays:
        return (
            f"NSE {year} holiday list is empty - only weekends are being detected. "
            f"Fill config/nse_holidays_{year}.json to make trading-day detection correct."
        )
    if expected and len(holidays) != expected:
        return f"NSE {year} holiday list has {len(holidays)} entries, expected {expected}."
    if not data.get("verified"):
        return f"NSE {year} holiday list is present but not marked verified."
    return f"NSE {year} holiday calendar verified ({len(holidays)} holidays)."


def is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:  # Saturday, Sunday
        return False
    return day not in _holiday_set(day.year)


def last_trading_day(reference: date | None = None) -> date:
    """Most recent trading day on or before `reference`."""
    day = reference or date.today()
    for _ in range(15):  # a 15-day walk-back covers any realistic holiday cluster
        if is_trading_day(day):
            return day
        day -= timedelta(days=1)
    return day


def next_trading_day(reference: date | None = None) -> date:
    day = (reference or date.today()) + timedelta(days=1)
    for _ in range(15):
        if is_trading_day(day):
            return day
        day += timedelta(days=1)
    return day
