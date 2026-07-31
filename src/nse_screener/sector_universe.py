"""The complete NSE sector / subsector taxonomy and its constituents.

This is the authoritative list of what EXISTS. It answers "which sectors and subsectors
are there", independently of whether any of them happen to have return data this run.

That separation is the point. Previously the Sector Ranks tab was built only from the
bulk Screener export, so a sector with no usable rows simply did not appear - which made
the tab look truncated to a handful of groups. Now the universe defines the rows and the
export fills in returns; a group with no return data is listed with blanks, never dropped.

Input: an .xlsx or .csv with columns Sector, Subsector, Stock Name, Market Cap Category.
Extra columns are ignored, so a richer export still loads.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .config import resolve_path
from .symbol_utils import normalise_symbol

COLUMN_ALIASES = {
    "sector": ("sector",),
    "subsector": ("subsector", "sub sector", "basic industry", "industry"),
    "name": ("stock name", "name", "company", "company name", "stock"),
    "cap": ("market cap category", "market cap", "cap", "category"),
    "symbol": ("symbol", "nse code", "ticker", "code"),
}

# The workbook writes these; normalised so "Microcap" and "Micro Cap" agree.
CAP_CANONICAL = {
    "largecap": "Large cap",
    "midcap": "Mid cap",
    "smallcap": "Small cap",
    "microcap": "Micro cap",
}


@dataclass
class Constituent:
    name: str
    sector: str
    subsector: str
    cap_category: str = ""
    symbol: str = ""


@dataclass
class SectorUniverse:
    constituents: list[Constituent] = field(default_factory=list)
    source: Path | None = None

    @property
    def available(self) -> bool:
        return bool(self.constituents)

    @property
    def named_constituents(self) -> list[Constituent]:
        return [c for c in self.constituents if c.name or c.symbol]

    @property
    def sectors(self) -> list[str]:
        return sorted({c.sector for c in self.constituents if c.sector})

    @property
    def subsectors(self) -> list[str]:
        return sorted({c.subsector for c in self.constituents if c.subsector})

    def subsectors_of(self, sector: str) -> list[str]:
        return sorted({c.subsector for c in self.constituents
                       if c.sector == sector and c.subsector})

    def parent_of(self, subsector: str) -> str:
        return next((c.sector for c in self.constituents if c.subsector == subsector), "")

    def members(self, level: str, name: str) -> list[Constituent]:
        key = "sector" if level == "sector" else "subsector"
        return [c for c in self.constituents if getattr(c, key) == name]

    def size(self, level: str, name: str) -> int:
        """Companies in the group. A taxonomy row that names an empty industry carries no
        stock, and must not be counted as a constituent of it."""
        return sum(1 for c in self.members(level, name) if c.name or c.symbol)

    def cap_for(self, name_or_symbol: str) -> str:
        """Cap category straight from the classification file, when it lists the stock."""
        target = (name_or_symbol or "").strip().lower()
        symbol = normalise_symbol(name_or_symbol)
        for c in self.constituents:
            if (c.symbol and c.symbol == symbol) or c.name.strip().lower() == target:
                return c.cap_category
        return ""

    def summary(self) -> str:
        if not self.available:
            return "No sector universe file loaded."
        return (
            f"{len(self.sectors)} sectors, {len(self.subsectors)} subsectors, "
            f"{len(self.named_constituents)} constituents"
            + (f" from {self.source.name}" if self.source else "")
        )


class SectorUniverseError(RuntimeError):
    pass


def _key(header: str) -> str:
    return " ".join(str(header or "").strip().lower().replace("-", " ").split())


def _map_headers(headers: list[str]) -> dict[str, int]:
    lowered = {_key(h): i for i, h in enumerate(headers) if h}
    mapping: dict[str, int] = {}
    for field_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                mapping[field_name] = lowered[alias]
                break
    return mapping


def _canonical_cap(raw: str) -> str:
    squashed = "".join(str(raw or "").lower().split())
    return CAP_CANONICAL.get(squashed, str(raw or "").strip())


def _rows_from_xlsx(path: Path) -> list[list]:
    try:
        import openpyxl
    except ImportError as exc:
        raise SectorUniverseError(
            "openpyxl is needed to read .xlsx - run: pip install openpyxl"
        ) from exc

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    return [list(row) for row in sheet.iter_rows(values_only=True) if any(row)]


def _rows_from_csv(path: Path) -> list[list]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [row for row in csv.reader(fh) if any(row)]


def load_sector_universe(path: Path | str | None = None) -> SectorUniverse:
    """Load the classification file. A missing file yields an empty universe, not an error:
    the rest of the app still works, the Sector Ranks tab just falls back to whatever the
    bulk export happens to contain."""
    if path is None:
        try:
            directory = resolve_path("sector_universe_dir")
        except KeyError:
            return SectorUniverse()
        candidates = sorted(
            [p for p in directory.glob("*.xlsx") if not p.name.startswith("~")]
            + list(directory.glob("*.csv"))
        ) if directory.exists() else []
        if not candidates:
            return SectorUniverse()
        # More than one classification file can sit here - a hand-made example alongside
        # the harvested full taxonomy. Take the one with the most rows, so the complete
        # NSE universe wins over a sample without anyone having to tidy the folder.
        def row_count(path: Path) -> int:
            try:
                return len(_rows_from_xlsx(path) if path.suffix.lower() == ".xlsx"
                           else _rows_from_csv(path))
            except Exception:
                return 0

        target = max(candidates, key=row_count)
    else:
        target = Path(path)
        if not target.exists():
            raise SectorUniverseError(f"Sector universe file not found: {target}")

    rows = _rows_from_xlsx(target) if target.suffix.lower() == ".xlsx" else _rows_from_csv(target)
    if not rows:
        return SectorUniverse(source=target)

    mapping = _map_headers([str(c) if c is not None else "" for c in rows[0]])
    for required in ("sector", "subsector"):
        if required not in mapping:
            raise SectorUniverseError(
                f"{target.name} has no '{required}' column. Found: {rows[0]}"
            )

    universe = SectorUniverse(source=target)
    for row in rows[1:]:
        def cell(field_name: str) -> str:
            index = mapping.get(field_name)
            if index is None or index >= len(row) or row[index] is None:
                return ""
            return str(row[index]).strip()

        sector, subsector = cell("sector"), cell("subsector")
        if not sector and not subsector:
            continue
        universe.constituents.append(
            Constituent(
                name=cell("name"),
                sector=sector,
                subsector=subsector,
                cap_category=_canonical_cap(cell("cap")),
                symbol=normalise_symbol(cell("symbol")),
            )
        )
    return universe
