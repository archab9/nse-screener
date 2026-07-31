"""Weighted scoring and tiering (spec section 4).

Three rules this module exists to enforce, all of which the spec calls out explicitly:

1. Toggling a parameter off removes it from the numerator AND the denominator.
   It is not zeroed - a switched-off parameter must not drag the score down.
2. Tiers are a percentage of the ACTIVE maximum, never a fixed point total, precisely
   because the denominator moves when toggles change.
3. There is no AND-gate. A stock failing an active parameter still ranks; stage 1 is
   already strict enough that gating would return an empty list most days.
"""

from __future__ import annotations

from .config import settings
from .models import PARAM_IDS, ScoredStock, Tier, Verdict

# P6 is binary per spec section 3 - either the promoter holding is stable or it isn't.
BINARY_PARAMS = frozenset({"P6"})

POINTS_PER_PARAM = 2


def normalise_toggles(toggles: dict[str, bool] | None) -> dict[str, bool]:
    """Default every core parameter to ON (spec section 10)."""
    if toggles is None:
        return {pid: True for pid in PARAM_IDS}
    return {pid: bool(toggles.get(pid, True)) for pid in PARAM_IDS}


def active_params(toggles: dict[str, bool] | None) -> list[str]:
    resolved = normalise_toggles(toggles)
    return [pid for pid in PARAM_IDS if resolved[pid]]


def active_maximum(toggles: dict[str, bool] | None) -> int:
    return len(active_params(toggles)) * POINTS_PER_PARAM


def tier_for(pct: float) -> Tier:
    cfg = settings()["tiers"]
    if pct >= cfg["elite_compounder_min_pct"]:
        return Tier.ELITE_COMPOUNDER
    if pct >= cfg["quality_grower_min_pct"]:
        return Tier.QUALITY_GROWER
    if pct >= cfg["watchlist_min_pct"]:
        return Tier.WATCHLIST
    return Tier.EXCLUDED


def score_stock(stock: ScoredStock, toggles: dict[str, bool] | None = None) -> ScoredStock:
    """Recompute core score, active max, percentage and tier for one stock.

    Mutates and returns the stock so the GUI can re-score a list in place on every toggle
    change without rebuilding parameter results (which are toggle-independent).
    """
    active = active_params(toggles)

    total = 0
    for pid in active:
        result = stock.results.get(pid)
        if result is None:
            # No result computed for an active parameter: contributes 0 points but still
            # counts toward the denominator, so the score honestly reflects the gap.
            continue
        verdict = result.verdict
        if pid in BINARY_PARAMS and verdict is Verdict.PARTIAL:
            # Defensive: a binary parameter should never emit PARTIAL. If a rule change
            # ever does, treat it as NO rather than silently granting half credit.
            continue
        total += verdict.points

    stock.core_score = total
    stock.active_max = len(active) * POINTS_PER_PARAM
    stock.pct_of_max = (total / stock.active_max * 100.0) if stock.active_max else 0.0
    stock.tier = tier_for(stock.pct_of_max) if stock.active_max else Tier.EXCLUDED
    return stock


def score_all(
    stocks: list[ScoredStock], toggles: dict[str, bool] | None = None
) -> list[ScoredStock]:
    """Score and rank. Sorted by core score desc, then PEGY asc (spec section 6)."""
    for stock in stocks:
        score_stock(stock, toggles)
    return rank(stocks)


def rank(stocks: list[ScoredStock]) -> list[ScoredStock]:
    def sort_key(s: ScoredStock) -> tuple[float, float, str]:
        # PEGY ascending, but missing PEGY must sort last rather than first.
        pegy = s.pegy if s.pegy is not None else float("inf")
        return (-s.pct_of_max, pegy, s.symbol)

    return sorted(stocks, key=sort_key)


def summary_line(toggles: dict[str, bool] | None = None) -> str:
    """e.g. 'Score: x/12 - 6 of 7 active' (spec section 10 - never ambiguous about the denominator)."""
    active = active_params(toggles)
    return f"{len(active)} of {len(PARAM_IDS)} parameters active - max score {len(active) * POINTS_PER_PARAM}"
