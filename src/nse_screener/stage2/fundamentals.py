"""Load the manually-prepared fundamentals dataset.

This is the STUB loader called for in spec section 0 / section 8: a documented CSV schema
that the scoring engine can be built and tested against before the live Screener.in
export format is confirmed. Swapping in the real export means writing a new adapter that
produces the same CompanyFundamentals objects - no change to params.py or scoring.py.

Nothing here ever logs into Screener.in. It reads files already saved to disk by a human
who exported them while logged in (spec section 0, deliberate and permanent).

Schema - four CSVs in config paths.fundamentals_dir:

  company.csv       symbol, name, industry, market_cap_cr, pe, pb, eps_cagr_pct,
                    dividend_yield_pct, is_psu
  quarterly.csv     symbol, quarter (YYYY-Qn), net_sales, net_profit
  annual.csv        symbol, fy (YYYY), roce_pct, roe_pct, cfo, capex, pat,
                    receivable_days
  shareholding.csv  symbol, quarter (YYYY-Qn), promoter_pct, pledge_pct, fii_pct,
                    dii_pct, public_pct

Money columns share whatever unit the export uses (Rs. crore by convention). Only ratios
of like-for-like columns are taken, so the unit cancels - except FCF yield, which needs
market_cap_cr in the same unit as cfo/capex.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..config import resolve_path


@dataclass
class QuarterPoint:
    quarter: str
    net_sales: float | None = None
    net_profit: float | None = None

    @property
    def sort_key(self) -> tuple[int, int]:
        return _quarter_sort_key(self.quarter)


@dataclass
class AnnualPoint:
    fy: int
    roce_pct: float | None = None
    roe_pct: float | None = None
    cfo: float | None = None
    capex: float | None = None
    pat: float | None = None
    receivable_days: float | None = None

    @property
    def fcf(self) -> float | None:
        if self.cfo is None or self.capex is None:
            return None
        return self.cfo - self.capex

    @property
    def cfo_pat(self) -> float | None:
        if self.cfo is None or self.pat in (None, 0):
            return None
        return self.cfo / self.pat


@dataclass
class ShareholdingPoint:
    quarter: str
    promoter_pct: float | None = None
    pledge_pct: float | None = None
    fii_pct: float | None = None
    dii_pct: float | None = None
    public_pct: float | None = None

    @property
    def sort_key(self) -> tuple[int, int]:
        return _quarter_sort_key(self.quarter)


@dataclass
class CompanyFundamentals:
    symbol: str
    name: str = ""
    industry: str = ""
    market_cap_cr: float | None = None
    pe: float | None = None
    pb: float | None = None
    eps_cagr_pct: float | None = None
    dividend_yield_pct: float | None = None
    is_psu: bool = False

    quarterly: list[QuarterPoint] = field(default_factory=list)
    annual: list[AnnualPoint] = field(default_factory=list)
    shareholding: list[ShareholdingPoint] = field(default_factory=list)

    def sorted_quarterly(self) -> list[QuarterPoint]:
        return sorted(self.quarterly, key=lambda q: q.sort_key)

    def sorted_annual(self) -> list[AnnualPoint]:
        return sorted(self.annual, key=lambda a: a.fy)

    def sorted_shareholding(self) -> list[ShareholdingPoint]:
        return sorted(self.shareholding, key=lambda s: s.sort_key)


@dataclass
class FundamentalsStore:
    companies: dict[str, CompanyFundamentals] = field(default_factory=dict)
    source_dir: Path | None = None
    newest_file_date: date | None = None

    def get(self, symbol: str) -> CompanyFundamentals | None:
        return self.companies.get(symbol.strip().upper())

    def age_days(self, today: date | None = None) -> int | None:
        if self.newest_file_date is None:
            return None
        return ((today or date.today()) - self.newest_file_date).days

    @property
    def symbols(self) -> list[str]:
        return sorted(self.companies)


class FundamentalsError(RuntimeError):
    pass


# Ordered most-specific first: 'YYYY-Qn', then 'Qn YYYY', then a bare year.
_QUARTER_PATTERNS = (
    (re.compile(r"^(\d{4})[-_\s]?Q([1-4])$"), ("year", "quarter")),
    (re.compile(r"^Q([1-4])[-_\s]?(\d{4})$"), ("quarter", "year")),
    (re.compile(r"^(\d{4})$"), ("year",)),
)

_MONTH_TO_QUARTER = {
    "MAR": 4, "JUN": 1, "SEP": 2, "DEC": 3,  # Indian FY: Apr-Mar, so Jun is Q1
}
_MONTH_YEAR_RE = re.compile(r"^([A-Z]{3})[-_\s]?(\d{4})$")


def _quarter_sort_key(label: str) -> tuple[int, int]:
    """Parse a quarter label into a sortable (year, quarter).

    Handles 'YYYY-Qn', 'YYYYQn', 'Qn YYYY', a bare 'YYYY', and Screener.in's
    'Mmm YYYY' column headers. Unparseable labels sort first as (0, 0) rather than
    producing a nonsense key that would silently reorder a company's history.
    """
    text = (label or "").strip().upper()
    for pattern, fields in _QUARTER_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        values = dict(zip(fields, (int(g) for g in match.groups())))
        return (values.get("year", 0), values.get("quarter", 4))

    month_match = _MONTH_YEAR_RE.match(text)
    if month_match:
        month, year = month_match.group(1), int(month_match.group(2))
        if month in _MONTH_TO_QUARTER:
            quarter = _MONTH_TO_QUARTER[month]
            # Mar closes the FY that began the previous April.
            return (year, quarter)
    return (0, 0)


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace(",", "").replace("%", "").replace("₹", "")
    if cleaned in ("", "-", "NA", "N/A", "na", "nan"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _flag(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "y")


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [
            {(k or "").strip().lower(): (v or "") for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]


def load_fundamentals(directory: Path | str | None = None) -> FundamentalsStore:
    """Read the four CSVs into a keyed store.

    A missing directory is an error the caller turns into a banner - it must never be
    silently treated as "no stocks qualify" (spec section 10).
    """
    base = Path(directory) if directory else resolve_path("fundamentals_dir")
    if not base.exists():
        raise FundamentalsError(
            f"Fundamentals directory not found: {base}. Export from Screener.in and place "
            f"company.csv / quarterly.csv / annual.csv / shareholding.csv there."
        )

    store = FundamentalsStore(source_dir=base)

    for row in _read_rows(base / "company.csv"):
        symbol = row.get("symbol", "").strip().upper()
        if not symbol:
            continue
        store.companies[symbol] = CompanyFundamentals(
            symbol=symbol,
            name=row.get("name", "").strip(),
            industry=row.get("industry", "").strip(),
            market_cap_cr=_num(row.get("market_cap_cr")),
            pe=_num(row.get("pe")),
            pb=_num(row.get("pb")),
            eps_cagr_pct=_num(row.get("eps_cagr_pct")),
            dividend_yield_pct=_num(row.get("dividend_yield_pct")),
            is_psu=_flag(row.get("is_psu")),
        )

    for row in _read_rows(base / "quarterly.csv"):
        company = store.get(row.get("symbol", ""))
        if company is None:
            continue
        company.quarterly.append(
            QuarterPoint(
                quarter=row.get("quarter", "").strip(),
                net_sales=_num(row.get("net_sales")),
                net_profit=_num(row.get("net_profit")),
            )
        )

    for row in _read_rows(base / "annual.csv"):
        company = store.get(row.get("symbol", ""))
        if company is None:
            continue
        fy = _num(row.get("fy"))
        if fy is None:
            continue
        company.annual.append(
            AnnualPoint(
                fy=int(fy),
                roce_pct=_num(row.get("roce_pct")),
                roe_pct=_num(row.get("roe_pct")),
                cfo=_num(row.get("cfo")),
                capex=_num(row.get("capex")),
                pat=_num(row.get("pat")),
                receivable_days=_num(row.get("receivable_days")),
            )
        )

    for row in _read_rows(base / "shareholding.csv"):
        company = store.get(row.get("symbol", ""))
        if company is None:
            continue
        company.shareholding.append(
            ShareholdingPoint(
                quarter=row.get("quarter", "").strip(),
                promoter_pct=_num(row.get("promoter_pct")),
                pledge_pct=_num(row.get("pledge_pct")),
                fii_pct=_num(row.get("fii_pct")),
                dii_pct=_num(row.get("dii_pct")),
                public_pct=_num(row.get("public_pct")),
            )
        )

    timestamps = [p.stat().st_mtime for p in base.glob("*.csv") if p.is_file()]
    if timestamps:
        store.newest_file_date = datetime.fromtimestamp(max(timestamps)).date()

    return store


def load_sector_index_valuations(path: Path | str | None = None) -> dict[str, dict[str, float]]:
    """NSE sectoral index PE/PB, used as P7's sector benchmark (spec section 3 P7).

    Schema: index_name, pe, pb, dividend_yield. Missing file returns {} - P7 then reports
    the PB half as unavailable rather than inventing a benchmark.
    """
    target = Path(path) if path else resolve_path("sector_index_csv")
    out: dict[str, dict[str, float]] = {}
    for row in _read_rows(target):
        name = row.get("index_name", "").strip().upper()
        if not name:
            continue
        out[name] = {
            "pe": _num(row.get("pe")) or 0.0,
            "pb": _num(row.get("pb")) or 0.0,
            "dividend_yield": _num(row.get("dividend_yield")) or 0.0,
        }
    return out
