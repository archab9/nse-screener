"""Tailwind sector view model (GUI 'Sectors' tab).

Important framing, and the reason this module is thin: the app does NOT derive which
sectors will lead over the next three years. That is a forward macro judgement, and the
build spec calls it "explicitly a moving target" to be re-researched quarterly.

What the app does do is verifiable:
  - report the sector list and rationale a human put in config/sector_map.json
  - say how stale that review is
  - check membership, and check top-3-by-market-cap rank against Screener.in's own
    industry tables

A stock is highlighted only when both verifiable conditions hold. Nothing here forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import sector_map


@dataclass
class SectorView:
    name: str
    rationale: str
    keywords: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)   # symbols from this run
    leaders: list[str] = field(default_factory=list)   # matched AND top-3 in industry


def review_status(today: date | None = None) -> tuple[str, bool]:
    """(message, is_stale) describing how current the hand-maintained list is."""
    meta = sector_map().get("_tailwind_review", {})
    raw = meta.get("last_reviewed")
    every = int(meta.get("review_every_days", 90))
    if not raw:
        return ("Sector list has no recorded review date.", True)
    try:
        reviewed = date.fromisoformat(str(raw))
    except ValueError:
        return (f"Sector list review date '{raw}' is unreadable.", True)

    age = ((today or date.today()) - reviewed).days
    stale = age > every
    message = (
        f"Sector list last reviewed {reviewed:%d %b %Y} ({age} days ago). "
        f"{'Overdue - re-research and update config/sector_map.json.' if stale else f'Next review due after {every} days.'}"
    )
    return (message, stale)


def excluded_sectors() -> dict[str, str]:
    return dict(sector_map().get("excluded_rationale", {}))


def build_sector_views(stocks=None) -> list[SectorView]:
    """One view per configured tailwind sector, annotated with this run's stocks."""
    smap = sector_map()
    rationale = smap.get("tailwind_rationale", {})

    views = [
        SectorView(name=name, rationale=rationale.get(name, ""), keywords=list(keywords))
        for name, keywords in smap.get("tailwind_sectors", {}).items()
    ]
    index = {view.name: view for view in views}

    for stock in stocks or []:
        sector = getattr(stock, "tailwind_sector", "")
        view = index.get(sector)
        if view is None:
            continue
        view.matched.append(stock.symbol)
        if is_sector_leader(stock):
            view.leaders.append(stock.symbol)
    return views


def is_sector_leader(stock) -> bool:
    """True only when BOTH verifiable conditions hold: the stock sits in a configured
    tailwind sector AND ranks top-3 by market cap in its Screener.in industry.

    This is exactly P8's YES condition, exposed separately so the GUI can highlight rows
    without reaching into parameter internals.
    """
    result = (getattr(stock, "results", {}) or {}).get("P8")
    return bool(result and result.verdict.name == "YES")


def leader_reason(stock) -> str:
    """Short explanation for the highlight, shown as a tooltip."""
    result = (getattr(stock, "results", {}) or {}).get("P8")
    if not result:
        return ""
    rank = result.evidence.get("Rank by market cap")
    total = result.evidence.get("Companies in industry")
    sector = result.evidence.get("Tailwind sector") or ""
    if rank and sector:
        of = f" of {total}" if total else ""
        return f"{sector} - #{rank}{of} by market cap in {result.evidence.get('Industry', 'its industry')}"
    return result.detail
