"""Quarterly breadth snapshots, for trend detection.

Storage note: the spec asked for "whatever persistence layer the app already uses for
score history / the backtest module". There isn't one - the backtest is pure pandas over
frames passed in, and nothing writes score history. The only established on-disk pattern
is a JSON file under LOCALAPPDATA (watchlist.py), so this follows that rather than
introducing SQLite for a table that gains four rows per industry per year.

Fields per record: sector, industry, quarter_label, breadth_pct, company_count,
computed_at - plus the enabled condition set, because a breadth number is only comparable
to another computed under the same conditions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .breadth import IndustryBreadth, Override
from .watchlist import default_path as _watchlist_dir


def default_path() -> Path:
    return _watchlist_dir().parent / "sector_breadth_history.json"


def quarter_label(day: date | None = None) -> str:
    """Indian FY quarter ending: Jun/Sep/Dec/Mar. 'FY2027-Q1' for Apr-Jun 2026."""
    day = day or date.today()
    month, year = day.month, day.year
    if month <= 3:
        return f"FY{year}-Q4"
    fy = year + 1
    quarter = 1 if month <= 6 else 2 if month <= 9 else 3
    return f"FY{fy}-Q{quarter}"


@dataclass
class BreadthRecord:
    sector: str
    industry: str
    quarter_label: str
    breadth_pct: float | None
    company_count: int
    computed_at: str
    conditions: list[str] = field(default_factory=list)


@dataclass
class SectorHistory:
    path: Path = field(default_factory=default_path)
    records: list[BreadthRecord] = field(default_factory=list)
    overrides: dict[str, Override] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> SectorHistory:
        history = cls(path=Path(path) if path else default_path())
        if not history.path.exists():
            return history
        try:
            raw = json.loads(history.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return history

        for item in raw.get("records", []):
            try:
                history.records.append(BreadthRecord(**item))
            except TypeError:
                continue  # schema drift: skip the row rather than lose the whole file
        for industry, value in (raw.get("overrides") or {}).items():
            try:
                history.overrides[industry] = Override(value)
            except ValueError:
                continue
        return history

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": [asdict(r) for r in self.records],
            "overrides": {k: v.value for k, v in self.overrides.items()},
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ snapshots

    def quarters(self) -> list[str]:
        return sorted({r.quarter_label for r in self.records})

    def record_snapshot(
        self,
        entries: list[IndustryBreadth],
        conditions: list[str],
        label: str | None = None,
        today: date | None = None,
    ) -> str:
        """Store this run's breadth under a quarter label, replacing that quarter's rows.

        Re-running in the same quarter overwrites rather than appends, so a threshold tweak
        does not create two competing records for one period.
        """
        label = label or quarter_label(today)
        self.records = [r for r in self.records if r.quarter_label != label]
        computed = (today or date.today()).isoformat()

        for entry in entries:
            self.records.append(
                BreadthRecord(
                    sector=entry.sector,
                    industry=entry.industry,
                    quarter_label=label,
                    breadth_pct=entry.breadth_pct,
                    company_count=entry.denominator,
                    computed_at=computed,
                    conditions=list(conditions),
                )
            )
        return label

    def previous_breadth(self, before_label: str) -> dict[str, float]:
        """industry -> breadth % from the most recent quarter before `before_label`."""
        earlier = [q for q in self.quarters() if q < before_label]
        if not earlier:
            return {}
        prior = earlier[-1]
        return {
            r.industry: r.breadth_pct
            for r in self.records
            if r.quarter_label == prior and r.breadth_pct is not None
        }

    def has_history(self, before_label: str) -> bool:
        return bool([q for q in self.quarters() if q < before_label])

    # ------------------------------------------------------------------ overrides

    def set_override(self, industry: str, override: Override) -> None:
        if override is Override.AUTO:
            self.overrides.pop(industry, None)
        else:
            self.overrides[industry] = override

    def override_for(self, industry: str) -> Override:
        return self.overrides.get(industry, Override.AUTO)
