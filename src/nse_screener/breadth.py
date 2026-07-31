"""Sector breadth - how widely fundamentals are improving inside each Industry.

What this measures, precisely: the share of companies in an Industry whose financials
ALREADY show simultaneous improvement. It is the earliest confirmable evidence of a
re-rating that is under way. It is not a forecast, and nothing here may be described as
one - see STATUS_LABELS, which the UI is required to use verbatim.

Hard design rule: this never touches the P1-P7 core score or any stock's tier. It is an
overlay, exactly as P8 is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import settings
from .sector_reference import CompanyRow, SectorReference

CONDITION_KEYS = ("sales_growth_3y", "profit_growth_3y", "opm_expanding", "roce")

CONDITION_LABELS = {
    "sales_growth_3y": "3-year sales growth above threshold",
    "profit_growth_3y": "3-year profit growth above threshold",
    "opm_expanding": "Operating margin higher than the preceding year",
    "roce": "ROCE above threshold",
}


class Status(Enum):
    LEADERSHIP_ALIGNED = "leadership_aligned"
    WATCH = "watch"
    NOT_FLAGGED = "not_flagged"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


# Wording is deliberate. "Leadership-aligned (early signal)" describes a measurement of
# what is already in the financials; anything implying a prediction is wrong here.
STATUS_LABELS = {
    Status.LEADERSHIP_ALIGNED: "Leadership-aligned (early signal)",
    Status.WATCH: "Watch",
    Status.NOT_FLAGGED: "Not flagged",
    Status.INSUFFICIENT_SAMPLE: "Insufficient sample",
}


class Override(Enum):
    AUTO = "auto"
    FORCE_YES = "force_yes"
    FORCE_NO = "force_no"

    @property
    def label(self) -> str:
        return {Override.AUTO: "Auto", Override.FORCE_YES: "Force Yes", Override.FORCE_NO: "Force No"}[self]


class Trend(Enum):
    RISING = "rising"
    FALLING = "falling"
    FLAT = "flat"
    INSUFFICIENT_HISTORY = "insufficient_history"

    @property
    def label(self) -> str:
        return {
            Trend.RISING: "rising",
            Trend.FALLING: "falling",
            Trend.FLAT: "flat",
            Trend.INSUFFICIENT_HISTORY: "insufficient history",
        }[self]


@dataclass
class IndustryBreadth:
    sector: str
    industry: str
    numerator: int = 0
    denominator: int = 0
    breadth_pct: float | None = None
    status: Status = Status.NOT_FLAGGED
    computed_status: Status = Status.NOT_FLAGGED
    override: Override = Override.AUTO
    trend: Trend = Trend.INSUFFICIENT_HISTORY
    previous_pct: float | None = None
    rising_into_leadership: bool = False

    @property
    def status_label(self) -> str:
        base = STATUS_LABELS[self.status]
        return f"{base} (manual)" if self.override is not Override.AUTO else base

    @property
    def is_leadership_aligned(self) -> bool:
        return self.status is Status.LEADERSHIP_ALIGNED

    @property
    def breadth_display(self) -> str:
        return "-" if self.breadth_pct is None else f"{self.breadth_pct:.0f}%"


def condition_settings() -> dict:
    return settings()["sector_leadership"]["conditions"]


def enabled_conditions() -> list[str]:
    cfg = condition_settings()
    return [k for k in CONDITION_KEYS if cfg.get(k, {}).get("enabled", True)]


def evaluate_conditions(row: CompanyRow) -> dict[str, bool | None]:
    """Per-condition outcome. None means the data needed was absent.

    A missing input is NOT treated as a pass - a company with no ROCE figure has not
    demonstrated ROCE above the bar.
    """
    cfg = condition_settings()
    out: dict[str, bool | None] = {}

    sales = row.sales_growth_3y
    out["sales_growth_3y"] = None if sales is None else sales > cfg["sales_growth_3y"]["min"]

    profit = row.profit_growth_3y
    out["profit_growth_3y"] = None if profit is None else profit > cfg["profit_growth_3y"]["min"]

    if row.opm_current is None or row.opm_preceding is None:
        out["opm_expanding"] = None
    else:
        out["opm_expanding"] = row.opm_current > row.opm_preceding

    roce = row.roce
    out["roce"] = None if roce is None else roce > cfg["roce"]["min"]
    return out


def company_passes(row: CompanyRow, enabled: list[str] | None = None) -> bool:
    """True when every ENABLED condition passes. Disabled conditions are ignored entirely."""
    active = enabled if enabled is not None else enabled_conditions()
    if not active:
        return False  # no conditions enabled means nothing has been demonstrated
    results = evaluate_conditions(row)
    return all(results.get(key) is True for key in active)


def compute_breadth(
    reference: SectorReference,
    overrides: dict[str, Override] | None = None,
    enabled: list[str] | None = None,
) -> list[IndustryBreadth]:
    """Breadth per Industry, gated on a minimum company count."""
    cfg = settings()["sector_leadership"]
    min_companies = int(cfg["min_companies"])
    active = enabled if enabled is not None else enabled_conditions()
    overrides = overrides or {}

    grouped: dict[str, list[CompanyRow]] = {}
    for row in reference.rows:
        if row.industry:
            grouped.setdefault(row.industry, []).append(row)

    results: list[IndustryBreadth] = []
    for industry, rows in grouped.items():
        sector = next((r.sector for r in rows if r.sector), "")
        denominator = len(rows)
        numerator = sum(1 for r in rows if company_passes(r, active))

        entry = IndustryBreadth(
            sector=sector, industry=industry, numerator=numerator, denominator=denominator
        )

        if denominator < min_companies:
            # Thin industries are shown as insufficient, never silently dropped and never
            # given a percentage that a handful of companies cannot support.
            entry.computed_status = Status.INSUFFICIENT_SAMPLE
            entry.breadth_pct = None
        else:
            entry.breadth_pct = numerator / denominator * 100.0
            entry.computed_status = _status_for(entry.breadth_pct, cfg)

        entry.override = overrides.get(industry, Override.AUTO)
        entry.status = _apply_override(entry.computed_status, entry.override)
        results.append(entry)

    results.sort(key=lambda e: (e.breadth_pct is None, -(e.breadth_pct or 0), e.industry))
    return results


def _status_for(pct: float, cfg: dict) -> Status:
    if pct >= cfg["leadership_aligned_min_pct"]:
        return Status.LEADERSHIP_ALIGNED
    if pct >= cfg["watch_min_pct"]:
        return Status.WATCH
    return Status.NOT_FLAGGED


def _apply_override(computed: Status, override: Override) -> Status:
    if override is Override.FORCE_YES:
        return Status.LEADERSHIP_ALIGNED
    if override is Override.FORCE_NO:
        return Status.NOT_FLAGGED
    return computed


def apply_trend(
    entries: list[IndustryBreadth], previous: dict[str, float]
) -> list[IndustryBreadth]:
    """Attach quarter-on-quarter movement from a prior snapshot."""
    cfg = settings()["sector_leadership"]
    for entry in entries:
        prior = previous.get(entry.industry)
        entry.previous_pct = prior
        if prior is None or entry.breadth_pct is None:
            entry.trend = Trend.INSUFFICIENT_HISTORY
            continue

        delta = entry.breadth_pct - prior
        entry.trend = Trend.RISING if delta > 1 else Trend.FALLING if delta < -1 else Trend.FLAT
        # "Rising" in the spec's sense: crossed Watch into Leadership-aligned.
        entry.rising_into_leadership = (
            entry.status is Status.LEADERSHIP_ALIGNED
            and prior < cfg["leadership_aligned_min_pct"]
            and prior >= cfg["watch_min_pct"]
        )
    return entries


def leadership_industries(entries: list[IndustryBreadth]) -> set[str]:
    return {e.industry for e in entries if e.is_leadership_aligned}
