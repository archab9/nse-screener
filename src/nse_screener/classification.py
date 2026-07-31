"""Resolve every tracked stock to a Sector / Industry / Basic Industry.

Runs for ALL ingestion paths, not just some. The Chartink CSV carries only
`Sr., Stock Name, Symbol` - no classification - so a CSV-imported stock needs resolving
exactly as much as a typed one does.

An unmatched symbol becomes UNRESOLVED and is never guessed into a Sector. Unresolved
stocks are excluded from breadth flagging and from the row highlight by definition, and
the UI shows that as its own state so a missing classification can never be mistaken for
"evaluated and did not qualify".

Manual per-stock overrides use the same Auto/Force shape as the Industry-level override.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .sector_reference import SectorReference
from .symbol_utils import normalise_symbol
from .watchlist import default_path as _watchlist_dir


class Source(Enum):
    REFERENCE = "reference"     # matched against the sector reference export
    MANUAL = "manual"           # classified by hand
    UNRESOLVED = "unresolved"   # no match and no override

    @property
    def label(self) -> str:
        return {
            Source.REFERENCE: "from reference data",
            Source.MANUAL: "set manually",
            Source.UNRESOLVED: "Unresolved",
        }[self]


@dataclass
class Classification:
    symbol: str
    sector: str = ""
    industry: str = ""
    basic_industry: str = ""
    source: Source = Source.UNRESOLVED

    @property
    def resolved(self) -> bool:
        return self.source is not Source.UNRESOLVED and bool(self.industry)


def default_path() -> Path:
    return _watchlist_dir().parent / "classification_overrides.json"


@dataclass
class ClassificationStore:
    """Resolved classifications plus any manual overrides.

    Overrides persist to their own JSON file, following the watchlist pattern - the app
    has no database, and the backtest module keeps nothing on disk, so there was no
    existing store to reuse.
    """

    path: Path = field(default_factory=default_path)
    overrides: dict[str, Classification] = field(default_factory=dict)
    resolved: dict[str, Classification] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> ClassificationStore:
        store = cls(path=Path(path) if path else default_path())
        if not store.path.exists():
            return store
        try:
            raw = json.loads(store.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return store

        for symbol, payload in (raw.get("overrides") or {}).items():
            key = normalise_symbol(symbol)
            store.overrides[key] = Classification(
                symbol=key,
                sector=payload.get("sector", ""),
                industry=payload.get("industry", ""),
                basic_industry=payload.get("basic_industry", ""),
                source=Source.MANUAL,
            )
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "overrides": {
                symbol: {
                    "sector": c.sector,
                    "industry": c.industry,
                    "basic_industry": c.basic_industry,
                }
                for symbol, c in self.overrides.items()
            }
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def set_override(
        self, symbol: str, sector: str, industry: str, basic_industry: str = ""
    ) -> None:
        key = normalise_symbol(symbol)
        self.overrides[key] = Classification(
            symbol=key,
            sector=sector.strip(),
            industry=industry.strip(),
            basic_industry=basic_industry.strip(),
            source=Source.MANUAL,
        )
        self.resolved[key] = self.overrides[key]

    def clear_override(self, symbol: str) -> None:
        self.overrides.pop(normalise_symbol(symbol), None)

    def resolve(self, symbols: list[str], reference: SectorReference | None) -> None:
        """(Re)resolve a set of symbols. Call on universe change AND on reference refresh."""
        for raw in symbols:
            symbol = normalise_symbol(raw)
            if not symbol:
                continue

            override = self.overrides.get(symbol)
            if override:
                self.resolved[symbol] = override
                continue

            row = reference.lookup(symbol) if reference else None
            if row and row.industry:
                self.resolved[symbol] = Classification(
                    symbol=symbol,
                    sector=row.sector,
                    industry=row.industry,
                    basic_industry=row.basic_industry,
                    source=Source.REFERENCE,
                )
            else:
                self.resolved[symbol] = Classification(symbol=symbol, source=Source.UNRESOLVED)

    def get(self, symbol: str) -> Classification:
        key = normalise_symbol(symbol)
        return self.resolved.get(key) or self.overrides.get(key) or Classification(symbol=key)

    def industry_of(self, symbol: str) -> str:
        return self.get(symbol).industry

    def unresolved_symbols(self) -> list[str]:
        return sorted(s for s, c in self.resolved.items() if not c.resolved)


def rank_within_industry(stocks: list, classification: ClassificationStore) -> dict[str, tuple[int, int]]:
    """symbol -> (rank, total) by core score among TRACKED stocks sharing an Industry.

    Deliberately scoped to the stocks currently scored in this app, not all of NSE: the
    core score only exists for stocks that have been through stage 2.
    """
    grouped: dict[str, list] = {}
    for stock in stocks:
        industry = classification.industry_of(stock.symbol)
        if industry:
            grouped.setdefault(industry, []).append(stock)

    ranks: dict[str, tuple[int, int]] = {}
    for industry, members in grouped.items():
        ordered = sorted(members, key=lambda s: (-s.core_score, s.symbol))
        for position, stock in enumerate(ordered, start=1):
            ranks[stock.symbol] = (position, len(ordered))
    return ranks
