"""Bulk sector reference data - manual CSV export from Screener.in.

This is a market-wide dataset, separate from the tracked stock universe, needed purely to
compute breadth. It is CSV-only by design: breadth needs the FULL denominator (every
covered company in an Industry), which no per-company fetch and no amount of manual typing
can supply for ~2,000 companies.

How to produce it:
  1. Run one Screener.in query with a trivially broad condition, e.g.
     `Market Capitalization > 100`, so the screen returns the whole covered universe.
  2. Edit Columns and add: Sector, Industry, Basic Industry, Sales growth 3Years,
     Profit growth 3Years, OPM, OPM last year, ROCE, Debt to equity,
     Market Capitalization, the NSE code, and - for peer ranking -
     Return over 6months, Return over 1year, Return over 3years,
     Return over 5years.
  3. Export to CSV. If the export truncates, split by market-cap band into 2-3 files and
     drop them all in the configured directory - they are merged on load.

Screener's CSV header text does not always match the on-screen column label, so every
field is matched against a list of aliases and normalised (case, punctuation and spacing
are ignored). load_sector_reference reports exactly which columns it could not find
instead of silently producing a table of Nones.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import resolve_path
from .symbol_utils import normalise_symbol

# Aliases are compared after _key(): lowercased, alphanumerics only.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("nsecode", "nsesymbol", "symbol", "code", "ticker", "nse"),
    "name": ("name", "companyname", "company"),
    "sector": ("sector",),
    "industry": ("industry",),
    "basic_industry": ("basicindustry", "basicind"),
    "sales_growth_3y": ("salesgrowth3years", "salesgrowth3yrs", "salesgrowth3y", "salesvar3yrs"),
    "profit_growth_3y": (
        "profitgrowth3years", "profitgrowth3yrs", "profitgrowth3y", "profitvar3yrs",
    ),
    "opm_current": ("opm", "opmcurrent", "opmlatestquarter", "opmlatest", "operatingprofitmargin"),
    "opm_preceding": (
        "opmprecedingyear", "opmlastyear", "opmpreviousyear", "opmly", "opmprecedingyr",
    ),
    "roce": ("roce", "rocepercentage", "roce3yr", "rocelatest"),
    "debt_to_equity": ("debttoequity", "debtequity", "de"),
    "market_cap": ("marketcapitalization", "marketcap", "mcap", "marcap"),
    # Peer ranking metrics - SHARE PRICE return, not accounting return. Add these in
    # Screener's Edit Columns as "Return over 1year" / "3years" / "5years".
    "return_6m": ("returnover6months", "return6months", "6mreturn", "returnover6month"),
    "return_1y": ("returnover1year", "return1year", "1yrreturn", "priceerturn1y", "returnover1yr"),
    "return_3y": ("returnover3years", "return3years", "3yrsreturn", "returnover3yrs"),
    "return_5y": ("returnover5years", "return5years", "5yrsreturn", "returnover5yrs"),
}

REQUIRED = ("symbol", "industry")
# Peer ranking degrades gracefully without these, so their absence is not a problem
# worth reporting as a missing column.
OPTIONAL = ("return_6m", "return_1y", "return_3y", "return_5y")


def _key(header: str) -> str:
    return "".join(ch for ch in (header or "").lower() if ch.isalnum())


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = str(raw).strip().replace(",", "").replace("%", "").replace("₹", "")
    if cleaned in ("", "-", "NA", "N/A", "na", "nan", "None"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


@dataclass
class CompanyRow:
    symbol: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    basic_industry: str = ""
    sales_growth_3y: float | None = None
    profit_growth_3y: float | None = None
    opm_current: float | None = None
    opm_preceding: float | None = None
    roce: float | None = None
    debt_to_equity: float | None = None
    market_cap: float | None = None
    return_6m: float | None = None
    return_1y: float | None = None
    return_3y: float | None = None
    return_5y: float | None = None

    @property
    def subsector(self) -> str:
        """Most specific classification available."""
        return self.basic_industry or self.industry


@dataclass
class SectorReference:
    rows: list[CompanyRow] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    loaded_from: date | None = None
    missing_columns: list[str] = field(default_factory=list)
    unresolved_rows: int = 0

    @property
    def symbols(self) -> dict[str, CompanyRow]:
        return {row.symbol: row for row in self.rows if row.symbol}

    @property
    def industries(self) -> set[str]:
        return {row.industry for row in self.rows if row.industry}

    def age_days(self, today: date | None = None) -> int | None:
        if self.loaded_from is None:
            return None
        return ((today or date.today()) - self.loaded_from).days

    def lookup(self, symbol: str) -> CompanyRow | None:
        return self.symbols.get(normalise_symbol(symbol))


class SectorReferenceError(RuntimeError):
    pass


def _map_headers(fieldnames: list[str]) -> tuple[dict[str, str], list[str]]:
    """(field -> actual header) plus the list of fields that could not be located.

    Two passes, with headers claimed exclusively. Exact alias matches go first so a
    specific column wins its own name; only then does the substring fallback run, and
    only over headers nothing has claimed.

    Without the claiming step, a missing 'Industry' column silently matched
    'Basic Industry' - the substring is right there in the name - and every company got
    classified one level too deep with no error. Suppressing that is the entire point of
    reporting missing columns.
    """
    available = {_key(h): h for h in fieldnames if h}
    all_aliases = {alias for aliases in FIELD_ALIASES.values() for alias in aliases}

    mapping: dict[str, str] = {}
    claimed: set[str] = set()

    for field_name, aliases in FIELD_ALIASES.items():
        exact = next((a for a in aliases if a in available and a not in claimed), None)
        if exact:
            mapping[field_name] = available[exact]
            claimed.add(exact)

    for field_name, aliases in FIELD_ALIASES.items():
        if field_name in mapping:
            continue
        # 'Sales growth 3Years %' and similar: extra words around a known alias. Skip any
        # header that is itself another field's exact alias.
        found = next(
            (
                key
                for key, _header in available.items()
                if key not in claimed
                and key not in all_aliases
                and any(alias in key for alias in aliases)
            ),
            None,
        )
        if found:
            mapping[field_name] = available[found]
            claimed.add(found)

    missing = [f for f in FIELD_ALIASES if f not in mapping]
    return mapping, missing


def load_sector_reference(directory: Path | str | None = None) -> SectorReference:
    """Load and merge every CSV in the sector reference directory.

    Multiple files are supported so a truncated export can be split by market-cap band.
    Later files win on duplicate symbols.
    """
    base = Path(directory) if directory else resolve_path("sector_reference_dir")
    if not base.exists():
        raise SectorReferenceError(
            f"Sector reference directory not found: {base}. Export the market-wide screen "
            f"from Screener.in and place the CSV there."
        )

    files = sorted(p for p in base.glob("*.csv") if p.is_file())
    if not files:
        raise SectorReferenceError(f"No CSV files in {base}.")

    reference = SectorReference(files=files)
    merged: dict[str, CompanyRow] = {}
    missing_union: set[str] = set()

    for path in files:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                continue
            mapping, missing = _map_headers(list(reader.fieldnames))
            missing_union.update(missing)

            absent_required = [f for f in REQUIRED if f not in mapping]
            if absent_required:
                raise SectorReferenceError(
                    f"{path.name} is missing required column(s) {absent_required}. "
                    f"Found headers: {list(reader.fieldnames)[:12]}"
                )

            for raw in reader:
                def value(field_name: str) -> str | None:
                    header = mapping.get(field_name)
                    return raw.get(header) if header else None

                symbol = normalise_symbol(value("symbol"))
                if not symbol:
                    reference.unresolved_rows += 1
                    continue

                merged[symbol] = CompanyRow(
                    symbol=symbol,
                    name=(value("name") or "").strip(),
                    sector=(value("sector") or "").strip(),
                    industry=(value("industry") or "").strip(),
                    basic_industry=(value("basic_industry") or "").strip(),
                    sales_growth_3y=_num(value("sales_growth_3y")),
                    profit_growth_3y=_num(value("profit_growth_3y")),
                    opm_current=_num(value("opm_current")),
                    opm_preceding=_num(value("opm_preceding")),
                    roce=_num(value("roce")),
                    debt_to_equity=_num(value("debt_to_equity")),
                    market_cap=_num(value("market_cap")),
                    return_6m=_num(value("return_6m")),
                    return_1y=_num(value("return_1y")),
                    return_3y=_num(value("return_3y")),
                    return_5y=_num(value("return_5y")),
                )

    reference.rows = list(merged.values())
    reference.missing_columns = sorted(m for m in missing_union if m not in OPTIONAL)
    timestamps = [p.stat().st_mtime for p in files]
    reference.loaded_from = datetime.fromtimestamp(max(timestamps)).date()
    return reference
