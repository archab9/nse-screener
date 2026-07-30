"""Rank a stock against its sector and subsector peers.

Replaces the old top-3 sector-leader flag, which was binary and therefore blank for
almost every stock, and silently returned nothing for smaller companies that Screener's
industry table does not list.

Source of peers: the bulk sector reference CSV. That is the only dataset here covering the
whole universe, so a rank computed from it is a true cross-sectional position rather than
a position among whichever handful of names happened to be in the run. It also costs no
extra requests - the file is already loaded for the breadth tab.

Two levels are reported for every metric:
  subsector - Basic Industry, the most specific classification Screener publishes
  sector    - the broad Sector column

Ranking is on SHARE PRICE return over 1, 3 and 5 years - how the stock actually
performed against its peers - with ROCE and growth kept as supporting context.

Higher is better for every metric here, so rank 1 is the best value
in the group. Companies with no value for a metric are excluded from that metric's
denominator - being unreported is not the same as being last.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import settings
from .sector_reference import CompanyRow, SectorReference
from .symbol_utils import normalise_symbol

# (attribute, label). Order is display order.
METRICS: tuple[tuple[str, str], ...] = (
    ("return_1y", "Price return 1y"),
    ("return_3y", "Price return 3y"),
    ("return_5y", "Price return 5y"),
    ("roce", "ROCE"),
    ("profit_growth_3y", "Profit growth 3y"),
    ("sales_growth_3y", "Sales growth 3y"),
)

PRIMARY_METRIC = "return_1y"


@dataclass
class Rank:
    rank: int | None = None
    total: int = 0
    value: float | None = None

    @property
    def known(self) -> bool:
        return self.rank is not None and self.total > 0

    @property
    def display(self) -> str:
        return f"#{self.rank} of {self.total}" if self.known else "-"

    @property
    def percentile(self) -> float | None:
        """0 = best in group, 100 = worst."""
        if not self.known or self.total < 2:
            return None
        return (self.rank - 1) / (self.total - 1) * 100.0


@dataclass
class MetricRank:
    key: str
    label: str
    subsector: Rank = field(default_factory=Rank)
    sector: Rank = field(default_factory=Rank)


@dataclass
class PeerRanking:
    symbol: str
    sector: str = ""
    subsector: str = ""
    metrics: list[MetricRank] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return any(m.subsector.known or m.sector.known for m in self.metrics)

    def metric(self, key: str) -> MetricRank | None:
        return next((m for m in self.metrics if m.key == key), None)

    def summary(self, key: str = PRIMARY_METRIC) -> str:
        """Column-width form: 'ROE 1y #3/42 sub - #18/310 sec'."""
        metric = self.metric(key)
        if metric is None or not (metric.subsector.known or metric.sector.known):
            return ""
        parts = []
        if metric.subsector.known:
            parts.append(f"#{metric.subsector.rank}/{metric.subsector.total} sub")
        if metric.sector.known:
            parts.append(f"#{metric.sector.rank}/{metric.sector.total} sec")
        return f"{metric.label} " + " - ".join(parts)

    def table(self) -> list[str]:
        """Full matrix for the detail view."""
        lines = [
            f"    {'Metric':<18} {'Value':>9}   {'In subsector':<16} {'In sector':<16}",
            f"    {'-' * 18} {'-' * 9}   {'-' * 16} {'-' * 16}",
        ]
        for metric in self.metrics:
            value = metric.subsector.value if metric.subsector.value is not None else metric.sector.value
            lines.append(
                f"    {metric.label:<18} {('-' if value is None else f'{value:.1f}'):>9}   "
                f"{metric.subsector.display:<16} {metric.sector.display:<16}"
            )
        return lines


def _rank_in(rows: list[CompanyRow], symbol: str, attribute: str) -> Rank:
    """Rank by descending value; only companies reporting the metric are counted."""
    scored = [(r.symbol, getattr(r, attribute, None)) for r in rows]
    scored = [(s, v) for s, v in scored if v is not None]
    if not scored:
        return Rank()

    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    for position, (candidate, value) in enumerate(scored, start=1):
        if candidate == symbol:
            return Rank(rank=position, total=len(scored), value=value)
    return Rank(total=len(scored))


def rank_symbol(reference: SectorReference | None, symbol: str) -> PeerRanking:
    symbol = normalise_symbol(symbol)
    ranking = PeerRanking(symbol=symbol)
    if reference is None:
        return ranking

    row = reference.lookup(symbol)
    if row is None:
        return ranking

    ranking.sector = row.sector
    ranking.subsector = row.subsector

    subsector_rows = [r for r in reference.rows if r.subsector and r.subsector == row.subsector]
    sector_rows = [r for r in reference.rows if r.sector and r.sector == row.sector]

    for key, label in METRICS:
        ranking.metrics.append(
            MetricRank(
                key=key,
                label=label,
                subsector=_rank_in(subsector_rows, symbol, key),
                sector=_rank_in(sector_rows, symbol, key),
            )
        )
    return ranking


# ------------------------------------------------------------------ market cap

CAP_ORDER = ("Large cap", "Mid cap", "Small cap", "Micro cap")


def cap_category(market_cap_cr: float | None, reference: SectorReference | None = None) -> str:
    """Large / Mid / Small / Micro cap.

    SEBI defines these by RANK - top 100 by market cap are large, the next 150 mid, the
    rest small - not by an absolute rupee figure. When the bulk reference export is loaded
    the true rank is used. Without it the app falls back to the configured thresholds,
    which are an approximation of the same idea and will disagree at the boundaries.
    """
    if market_cap_cr is None:
        return ""

    cfg = settings().get("market_cap", {})
    if reference is not None and cfg.get("use_rank_when_available", True):
        caps = sorted(
            (r.market_cap for r in reference.rows if r.market_cap is not None), reverse=True
        )
        if len(caps) >= int(cfg.get("min_universe_for_rank", 250)):
            rank = sum(1 for c in caps if c > market_cap_cr) + 1
            if rank <= int(cfg.get("large_cap_rank", 100)):
                return "Large cap"
            if rank <= int(cfg.get("mid_cap_rank", 250)):
                return "Mid cap"
            return "Micro cap" if market_cap_cr < float(cfg.get("micro_cap_cr", 500)) else "Small cap"

    if market_cap_cr >= float(cfg.get("large_cap_cr", 20000)):
        return "Large cap"
    if market_cap_cr >= float(cfg.get("mid_cap_cr", 5000)):
        return "Mid cap"
    if market_cap_cr >= float(cfg.get("micro_cap_cr", 500)):
        return "Small cap"
    return "Micro cap"
