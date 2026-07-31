"""Sector and subsector performance across four horizons.

Every sector and subsector in the bulk export is ranked on the MEDIAN share price return
of its constituents over 6 months, 1, 3 and 5 years, plus an overall strength score.

Median rather than mean deliberately: one multi-bagger should not carry an otherwise flat
sector, and the question is whether the group as a whole is moving.

EVERY group is listed. Groups with fewer than min_constituents companies are marked thin
and barred from medals - a median over two names is not a sector view - but they are never
hidden. Dropping them silently removed a large share of the subsectors in a real export.

Strength score is the mean of the group's percentile position across the horizons it
reports, on a 0-100 scale where 100 is the best group in the market. Horizons are equally
weighted - a deliberately plain rule, since any weighting of recent-versus-long would be
an unbacked judgement dressed up as arithmetic.

Medals (gold, silver, bronze) go to the top three of each level on the SIX-MONTH horizon,
which is what was asked for; the tab sorts on strength.
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

# (attribute, short label). Order is display order and drives the strength average.
HORIZONS: tuple[tuple[str, str], ...] = (
    ("return_6m", "6m"),
    ("return_1y", "1y"),
    ("return_3y", "3y"),
    ("return_5y", "5y"),
)
MEDAL_HORIZON = "return_6m"


@dataclass
class HorizonResult:
    median_return: float | None = None
    rank: int | None = None
    total: int = 0

    @property
    def known(self) -> bool:
        return self.rank is not None and self.total > 0

    @property
    def display(self) -> str:
        if self.median_return is None:
            return "-"
        rank = f"#{self.rank}" if self.rank else "-"
        return f"{rank}  {self.median_return:+.1f}%"

    @property
    def percentile_score(self) -> float | None:
        """100 = best group on this horizon, 0 = worst."""
        if not self.known or self.total < 2:
            return None
        return (1 - (self.rank - 1) / (self.total - 1)) * 100.0


@dataclass
class GroupPerformance:
    name: str
    level: str
    constituents: int = 0
    horizons: dict[str, HorizonResult] = field(default_factory=dict)
    strength: float | None = None
    rank: int = 0
    medal: str = ""
    thin: bool = False      # too few constituents for the median to mean much

    def horizon(self, key: str) -> HorizonResult:
        return self.horizons.get(key, HorizonResult())

    @property
    def median_return_6m(self) -> float | None:
        return self.horizon(MEDAL_HORIZON).median_return

    @property
    def strength_display(self) -> str:
        return "-" if self.strength is None else f"{self.strength:.1f}"

    @property
    def display(self) -> str:
        prefix = f"{self.medal} " if self.medal else ""
        six = self.median_return_6m
        tail = f"  ({six:+.1f}% 6m, {self.constituents})" if six is not None else ""
        return f"{prefix}{self.name}{tail}"


@dataclass
class Leaderboards:
    sectors: list[GroupPerformance] = field(default_factory=list)
    subsectors: list[GroupPerformance] = field(default_factory=list)
    skipped_thin: int = 0
    available: bool = False

    def all_groups(self) -> list[GroupPerformance]:
        return self.sectors + self.subsectors

    def _lookup(self, groups, name):
        return next((g for g in groups if g.name and g.name == name), None)

    def sector(self, name: str) -> GroupPerformance | None:
        return self._lookup(self.sectors, name)

    def subsector(self, name: str) -> GroupPerformance | None:
        return self._lookup(self.subsectors, name)

    def medal_for(self, level: str, name: str) -> str:
        group = self.sector(name) if level == SECTOR else self.subsector(name)
        return group.medal if group else ""

    def label(self, level: str, name: str) -> str:
        medal = self.medal_for(level, name)
        return f"{medal} {name}".strip() if name else ""

    def summary(self) -> str:
        if not self.available:
            return (
                "No sector reference data loaded, or it has no share price return columns. "
                "Add Return over 6months / 1year / 3years / 5years to the bulk export."
            )
        top_sectors = ", ".join(g.display for g in self.sectors[:3]) or "none"
        top_subs = ", ".join(g.display for g in self.subsectors[:3]) or "none"
        return (
            f"Sectors leading (6m median return): {top_sectors}\n"
            f"Subsectors leading: {top_subs}\n"
            f"{len(self.sectors)} sector(s) and {len(self.subsectors)} subsector(s) listed; "
            f"{self.skipped_thin} marked thin (too few constituents for a medal)."
        )


def _build_level(buckets: dict[str, dict[str, list[float]]], level: str, minimum: int
                 ) -> tuple[list[GroupPerformance], int]:
    groups: list[GroupPerformance] = []
    skipped = 0

    for name, by_horizon in buckets.items():
        if not name:
            continue
        size = max((len(v) for v in by_horizon.values()), default=0)
        if size == 0:
            # No return figure on any horizon: there is nothing to rank, as distinct from
            # a group that reports few but real numbers.
            continue
        thin = size < minimum
        skipped += thin
        group = GroupPerformance(name=name, level=level, constituents=size, thin=thin)
        for key, _label in HORIZONS:
            values = by_horizon.get(key) or []
            if values:
                group.horizons[key] = HorizonResult(median_return=float(median(values)))
            else:
                group.horizons[key] = HorizonResult()
        groups.append(group)

    # Rank within each horizon before scoring, so strength is a position, not a raw number:
    # a +40% median means very different things over six months and over five years.
    for key, _label in HORIZONS:
        scored = [g for g in groups if g.horizon(key).median_return is not None]
        scored.sort(key=lambda g: (-g.horizon(key).median_return, g.name))

        # Standard competition ranking: equal returns share a rank. Ordering ties by name
        # would hand a better position to whichever group happens to start with an earlier
        # letter, which is not a market fact.
        previous_value = None
        previous_rank = 0
        for position, group in enumerate(scored, start=1):
            result = group.horizons[key]
            value = result.median_return
            rank = previous_rank if value == previous_value else position
            previous_value, previous_rank = value, rank
            result.rank, result.total = rank, len(scored)
        # Medals rank only the groups big enough to carry a meaningful median, so a
        # two-company subsector cannot take gold on one lucky name.
        if key == MEDAL_HORIZON:
            for position, group in enumerate([g for g in scored if not g.thin], start=1):
                if position <= len(MEDALS):
                    group.medal = MEDALS[position - 1]

    for group in groups:
        scores = [
            group.horizon(key).percentile_score
            for key, _label in HORIZONS
            if group.horizon(key).percentile_score is not None
        ]
        group.strength = sum(scores) / len(scores) if scores else None

    groups.sort(key=lambda g: (g.strength is None, -(g.strength or 0), g.name))
    for position, group in enumerate(groups, start=1):
        group.rank = position
    return groups, skipped


def build_leaderboards(reference: SectorReference | None) -> Leaderboards:
    board = Leaderboards()
    if reference is None:
        return board

    minimum = int(settings().get("leaderboard", {}).get("min_constituents", 5))
    sector_buckets: dict[str, dict[str, list[float]]] = {}
    subsector_buckets: dict[str, dict[str, list[float]]] = {}

    for row in reference.rows:
        for bucket, name in ((sector_buckets, row.sector), (subsector_buckets, row.subsector)):
            if not name:
                continue
            slot = bucket.setdefault(name, {})
            for key, _label in HORIZONS:
                value = getattr(row, key, None)
                if value is not None:
                    slot.setdefault(key, []).append(value)

    board.sectors, skipped_a = _build_level(sector_buckets, SECTOR, minimum)
    board.subsectors, skipped_b = _build_level(subsector_buckets, SUBSECTOR, minimum)
    board.skipped_thin = skipped_a + skipped_b
    board.available = bool(board.sectors or board.subsectors)
    return board


# ----------------------------------------------------------------------- trophy

def trophy_reasons(snap, board: Leaderboards) -> tuple[bool, list[str]]:
    """(has trophy, reasons) - every condition, met or not, so the UI can explain itself.

    Needs all four: every active parameter passed outright, ranked #1 in its subsector on
    one-year price return, and both its subsector and sector holding a six-month medal.
    Deliberately strict; a trophy that appeared often would say nothing.
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
