"""Sector and subsector performance across four horizons.

Every sector and subsector in the bulk export is ranked on the MEDIAN share price return
of its constituents over 6 months, 1, 3 and 5 years, plus an overall strength score.

Median rather than mean deliberately: one multi-bagger should not carry an otherwise flat
sector, and the question is whether the group as a whole is moving.

EVERY sector and subsector in the NSE classification is listed, always. The universe file
decides which rows exist; the bulk export only supplies returns. A group the export says
nothing about is shown with blank returns rather than vanishing - which is what previously
made this look like a five-row table.

Groups with fewer than min_constituents companies are marked thin and barred from medals -
a median over two names is not a sector view - but they are never hidden.

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
    thin: bool = False          # too few constituents with returns to mean much
    with_returns: int = 0       # how many constituents the export actually priced

    @property
    def has_returns(self) -> bool:
        return self.with_returns > 0

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
    priced: bool = False    # at least one group had return data

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


def _build_level(buckets: dict[str, dict[str, list[float]]], level: str, minimum: int,
                 universe_sizes: dict[str, int] | None = None
                 ) -> tuple[list[GroupPerformance], int]:
    groups: list[GroupPerformance] = []
    skipped = 0

    for name, by_horizon in buckets.items():
        if not name:
            continue
        with_returns = max((len(v) for v in by_horizon.values()), default=0)
        # Constituent count comes from the classification when we have it, so a group
        # still shows its true size even if the export covers none of its members.
        in_universe = name in (universe_sizes or {})
        if with_returns == 0 and not in_universe:
            # Nothing to rank and nothing saying the group exists. Distinct from a group
            # the classification lists but the export has not priced, which stays.
            continue
        size = (universe_sizes or {}).get(name, with_returns)
        thin = with_returns < minimum
        skipped += thin
        group = GroupPerformance(
            name=name, level=level, constituents=size, thin=thin, with_returns=with_returns
        )
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


def build_leaderboards(reference: SectorReference | None, universe=None) -> Leaderboards:
    """`universe` is the NSE classification: it decides which groups exist. `reference` is
    the bulk export: it supplies returns for whichever of them it covers."""
    board = Leaderboards()
    if reference is None and universe is None:
        return board

    minimum = int(settings().get("leaderboard", {}).get("min_constituents", 5))
    sector_buckets: dict[str, dict[str, list[float]]] = {}
    subsector_buckets: dict[str, dict[str, list[float]]] = {}

    # Seed every group the classification knows about, so none can be missing from the
    # table just because the export happens not to cover it.
    if universe is not None:
        for name in universe.sectors:
            sector_buckets.setdefault(name, {})
        for name in universe.subsectors:
            subsector_buckets.setdefault(name, {})

    for row in (reference.rows if reference is not None else []):
        for bucket, name in ((sector_buckets, row.sector), (subsector_buckets, row.subsector)):
            if not name:
                continue
            slot = bucket.setdefault(name, {})
            for key, _label in HORIZONS:
                value = getattr(row, key, None)
                if value is not None:
                    slot.setdefault(key, []).append(value)

    sizes = {}
    if universe is not None:
        sizes[SECTOR] = {n: universe.size(SECTOR, n) for n in universe.sectors}
        sizes[SUBSECTOR] = {n: universe.size(SUBSECTOR, n) for n in universe.subsectors}

    board.sectors, skipped_a = _build_level(
        sector_buckets, SECTOR, minimum, sizes.get(SECTOR, {})
    )
    board.subsectors, skipped_b = _build_level(
        subsector_buckets, SUBSECTOR, minimum, sizes.get(SUBSECTOR, {})
    )
    board.skipped_thin = skipped_a + skipped_b
    board.available = bool(board.sectors or board.subsectors)
    board.priced = any(g.has_returns for g in board.all_groups())
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
