"""Core data types shared across stage 1, stage 2, scoring and rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Verdict(Enum):
    """Per-parameter outcome. Points are fixed by spec section 3: YES=2, PARTIAL=1, NO=0."""

    YES = 2
    PARTIAL = 1
    NO = 0
    # Distinct from NO: the underlying data was missing, so the parameter could not be
    # evaluated at all. Scored as 0 but rendered differently, because "we don't know"
    # and "we checked and it failed" are not the same claim.
    UNKNOWN = -1

    @property
    def points(self) -> int:
        return max(self.value, 0)

    @property
    def label(self) -> str:
        return "N/A" if self is Verdict.UNKNOWN else self.name


class Tier(Enum):
    ELITE_COMPOUNDER = "ELITE COMPOUNDER"
    QUALITY_GROWER = "QUALITY GROWER"
    WATCHLIST = "WATCHLIST"
    EXCLUDED = "EXCLUDED"

    @property
    def rank(self) -> int:
        order = {
            Tier.ELITE_COMPOUNDER: 3,
            Tier.QUALITY_GROWER: 2,
            Tier.WATCHLIST: 1,
            Tier.EXCLUDED: 0,
        }
        return order[self]


# Parameter registry. P8 is present but never contributes points (spec section 4).
PARAM_IDS = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")
P8_ID = "P8"

PARAM_NAMES = {
    "P1": "Record financials",
    "P2": "Capital efficiency (ROCE/ROE)",
    "P3": "Free cash flow quality",
    "P4": "Earnings quality (CFO/PAT)",
    "P5": "Institutional conviction",
    "P6": "Promoter stability",
    "P7": "Valuation safety margin",
    "P8": "Sector tailwind",
}


@dataclass
class Flag:
    """A named risk or informational marker raised while evaluating a parameter."""

    param_id: str
    code: str
    message: str
    severity: str = "info"  # info | risk | positive

    def __str__(self) -> str:
        return f"[{self.param_id}] {self.message}"


@dataclass
class ParamResult:
    param_id: str
    verdict: Verdict
    detail: str = ""
    # Underlying numbers behind the verdict, surfaced on the detail card (spec section 6).
    evidence: dict[str, object] = field(default_factory=dict)
    flags: list[Flag] = field(default_factory=list)

    @property
    def name(self) -> str:
        return PARAM_NAMES.get(self.param_id, self.param_id)


@dataclass
class Stage1Hit:
    """One ticker surviving the Chartink technical screen (spec section 2)."""

    symbol: str
    name: str = ""
    close: float | None = None
    pct_change: float | None = None
    volume: int | None = None
    scan_date: date | None = None
    # True when close/pct_change/volume were backfilled from Kite rather than
    # supplied by Chartink. The manual CSV export carries symbol and name only.
    price_backfilled: bool = False


@dataclass
class ScoredStock:
    symbol: str
    name: str
    industry: str = ""
    results: dict[str, ParamResult] = field(default_factory=dict)

    # Computed by the scoring engine against the currently-active toggles.
    core_score: int = 0
    active_max: int = 0
    pct_of_max: float = 0.0
    tier: Tier = Tier.EXCLUDED

    # P8 rides alongside the score, never inside it.
    sector_tailwind: bool = False
    tailwind_sector: str = ""

    pegy: float | None = None
    pb: float | None = None
    stage1: Stage1Hit | None = None
    live_price: float | None = None

    @property
    def all_flags(self) -> list[Flag]:
        out: list[Flag] = []
        for pid in list(PARAM_IDS) + [P8_ID]:
            result = self.results.get(pid)
            if result:
                out.extend(result.flags)
        return out

    @property
    def score_display(self) -> str:
        return f"{self.core_score}/{self.active_max}"


@dataclass
class RunWarning:
    """Surfaced as a banner. Never swallowed - spec section 10 forbids silent stale data."""

    code: str
    message: str
    severity: str = "warning"  # warning | error | info


@dataclass
class RunContext:
    """Everything the GUI needs to describe the run it is showing."""

    run_at: date
    as_of_trading_day: date | None = None
    is_trading_day: bool = True
    warnings: list[RunWarning] = field(default_factory=list)
    stage1_count: int = 0
    fundamentals_age_days: int | None = None

    def warn(self, code: str, message: str, severity: str = "warning") -> None:
        self.warnings.append(RunWarning(code=code, message=message, severity=severity))
