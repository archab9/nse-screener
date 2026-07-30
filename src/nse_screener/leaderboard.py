"""Which sectors and subsectors are leading the market over the last six months.

Ranked on the MEDIAN six-month share price return of a group's constituents. Median
rather than mean deliberately: one multi-bagger should not carry an otherwise flat
sector, and the question here is whether the group as a whole is moving.

Groups with too few constituents are excluded from the ranking entirely rather than
being allowed to win on two or three names.

Gold, silver and bronze go to the top three of each level. Medals are computed at display
time from the current export, never frozen into a saved run - a medal that silently
described last quarter's market would be worse than no medal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from .config import settings
from .sector_reference import SectorReference

GOLD, SILVER, BRONZE = "🥇", "🥈", "🥉"
MEDALS = (GOLD, SILVER, BRONZE)
TROPHY = "🏆"

SECTOR, SUBSECTOR = "sector", "subsector"


@dataclass
class GroupPerformance:
    name: str
    level: str
    median_return_6m: float
    constituents: int
    rank: int = 0
    medal: str = ""

    @property
    def display(self) -> str:
        prefix = f"{self.medal} " if self.medal else ""
        return f"{prefix}{self.name}  ({self.median_return_6m:+.1f}% 6m, {self.constituents})"


@dataclass
class Leaderboards:
    sectors: list[GroupPerformance] = field(default_factory=list)
    subsectors: list[GroupPerformance] = field(default_factory=list)
    skipped_thin: int = 0
    available: bool = False

    def _lookup(self, groups: list[GroupPerformance], name: str) -> GroupPerformance | None:
        return next((g for g in groups if g.name and g.name == name), None)

    def sector(self, name: str) -> GroupPerformance | None:
        return self._lookup(self.sectors, name)

    def subsector(self, name: str) -> GroupPerformance | None:
        return self._lookup(self.subsectors, name)

    def medal_for(self, level: str, name: str) -> str:
        group = self.sector(name) if level == SECTOR else self.subsector(name)
        return group.medal if group else ""

    def label(self, level: str, name: str) -> str:
        """Name prefixed with its medal, e.g. '🥇 Aerospace & Defense'."""
        medal = self.medal_for(level, name)
        return f"{medal} {name}".strip() if name else ""

    def summary(self) -> str:
        if not self.available:
            return "No sector reference data loaded, or it has no 6-month return column."
        top_sectors = ", ".join(g.display for g in self.sectors[:3]) or "none"
        top_subs = ", ".join(g.display for g in self.subsectors[:3]) or "none"
        return (
            f"Sectors leading (6m median return): {top_sectors}\n"
            f"Subsectors leading: {top_subs}\n"
            f"{len(self.sectors)} sector(s) and {len(self.subsectors)} subsector(s) ranked; "
            f"{self.skipped_thin} group(s) skipped for having too few constituents."
        )


def _rank_groups(buckets: dict[str, list[float]], level: str, minimum: int
                 ) -> tuple[list[GroupPerformance], int]:
    ranked: list[GroupPerformance] = []
    skipped = 0
    for name, values in buckets.items():
        if not name:
            continue
        if len(values) < minimum:
            skipped += 1
            continue
        ranked.append(
            GroupPerformance(
                name=name, level=level,
                median_return_6m=float(median(values)), constituents=len(values),
            )
        )

    ranked.sort(key=lambda g: (-g.median_return_6m, g.name))
    for position, group in enumerate(ranked, start=1):
        group.rank = position
        if position <= len(MEDALS):
            group.medal = MEDALS[position - 1]
    return ranked, skipped


def build_leaderboards(reference: SectorReference | None) -> Leaderboards:
    """Rank every sector and subsector by median six-month return."""
    board = Leaderboards()
    if reference is None:
        return board

    minimum = int(settings().get("leaderboard", {}).get("min_constituents", 5))
    sector_buckets: dict[str, list[float]] = {}
    subsector_buckets: dict[str, list[float]] = {}

    for row in reference.rows:
        if row.return_6m is None:
            continue
        if row.sector:
            sector_buckets.setdefault(row.sector, []).append(row.return_6m)
        if row.subsector:
            subsector_buckets.setdefault(row.subsector, []).append(row.return_6m)

    board.sectors, skipped_a = _rank_groups(sector_buckets, SECTOR, minimum)
    board.subsectors, skipped_b = _rank_groups(subsector_buckets, SUBSECTOR, minimum)
    board.skipped_thin = skipped_a + skipped_b
    board.available = bool(board.sectors or board.subsectors)
    return board


# ----------------------------------------------------------------------- trophy

def trophy_reasons(snap, board: Leaderboards) -> tuple[bool, list[str]]:
    """(has trophy, reasons) - every condition, met or not, so the UI can explain itself.

    A trophy needs all four:
      1. every active parameter passed outright (YES, not PARTIAL)
      2. ranked #1 in its subsector on the primary peer metric
      3. its subsector holds a medal
      4. its sector holds a medal

    Deliberately strict. A trophy that appeared often would say nothing.
    """
    reasons: list[str] = []

    evaluated = snap.yes_count + snap.partial_count + snap.no_count + snap.unknown_count
    all_passed = evaluated > 0 and snap.yes_count == evaluated
    reasons.append(
        f"{'PASS' if all_passed else 'no  '}  all {evaluated} active parameters passed "
        f"(has {snap.yes_count})"
    )

    primary = (snap.peer_ranks or {}).get("Price return 1y") or {}
    is_first = primary.get("sub_rank") == 1
    rank_text = (
        f"#{primary['sub_rank']} of {primary['sub_total']}" if primary.get("sub_rank") else "unranked"
    )
    reasons.append(f"{'PASS' if is_first else 'no  '}  #1 in subsector on price return ({rank_text})")

    sub_medal = board.medal_for(SUBSECTOR, snap.peer_subsector)
    reasons.append(
        f"{'PASS' if sub_medal else 'no  '}  subsector is a top-3 6m performer "
        f"({sub_medal or 'no medal'}: {snap.peer_subsector or 'unknown'})"
    )

    sector_medal = board.medal_for(SECTOR, snap.peer_sector)
    reasons.append(
        f"{'PASS' if sector_medal else 'no  '}  sector is a top-3 6m performer "
        f"({sector_medal or 'no medal'}: {snap.peer_sector or 'unknown'})"
    )

    return (all_passed and is_first and bool(sub_medal) and bool(sector_medal)), reasons


def has_trophy(snap, board: Leaderboards) -> bool:
    return trophy_reasons(snap, board)[0]
