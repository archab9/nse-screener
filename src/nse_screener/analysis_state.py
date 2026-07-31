"""When the sector ranking analysis was last run, and whether it is due.

The ranking is built from a quarterly bulk export, so it does not need running often - but
it does need running. This records the last run so the Refresh button can say plainly how
old the numbers are rather than leaving a stale table looking current.

Due after refresh_every_days (default 15). Being due is a prompt, never a block: the last
result stays on screen, marked stale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import settings
from .watchlist import default_path as _watchlist_dir

DEFAULT_INTERVAL_DAYS = 15


def default_path() -> Path:
    return _watchlist_dir().parent / "analysis_state.json"


@dataclass
class AnalysisState:
    path: Path = field(default_factory=default_path)
    last_run: datetime | None = None
    sectors: int = 0
    subsectors: int = 0
    priced: int = 0

    # ------------------------------------------------------------------ storage

    @classmethod
    def load(cls, path: Path | None = None) -> AnalysisState:
        state = cls(path=Path(path) if path else default_path())
        if not state.path.exists():
            return state
        try:
            raw = json.loads(state.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return state
        try:
            state.last_run = datetime.fromisoformat(raw["last_run"])
        except (KeyError, TypeError, ValueError):
            state.last_run = None
        state.sectors = int(raw.get("sectors", 0))
        state.subsectors = int(raw.get("subsectors", 0))
        state.priced = int(raw.get("priced", 0))
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_run": self.last_run.isoformat(timespec="seconds") if self.last_run else None,
            "sectors": self.sectors,
            "subsectors": self.subsectors,
            "priced": self.priced,
            "refresh_every_days": self.interval_days(),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ queries

    @staticmethod
    def interval_days() -> int:
        return int(
            settings().get("leaderboard", {}).get("refresh_every_days", DEFAULT_INTERVAL_DAYS)
        )

    def age_days(self, today: date | None = None) -> int | None:
        if self.last_run is None:
            return None
        return ((today or date.today()) - self.last_run.date()).days

    def is_due(self, today: date | None = None) -> bool:
        age = self.age_days(today)
        return age is None or age >= self.interval_days()

    def days_until_due(self, today: date | None = None) -> int | None:
        age = self.age_days(today)
        return None if age is None else max(self.interval_days() - age, 0)

    def record(self, board, when: datetime | None = None) -> None:
        self.last_run = when or datetime.now()
        self.sectors = len(getattr(board, "sectors", []) or [])
        self.subsectors = len(getattr(board, "subsectors", []) or [])
        self.priced = sum(1 for g in getattr(board, "all_groups", list)() if g.has_returns)

    def status(self, today: date | None = None) -> str:
        interval = self.interval_days()
        if self.last_run is None:
            return f"Ranking analysis has never been run. Due now (every {interval} days)."
        age = self.age_days(today)
        when = f"{self.last_run:%d %b %Y %H:%M}"
        covered = f"{self.priced} of {self.sectors + self.subsectors} groups had return data"
        if self.is_due(today):
            return f"Last run {when} - {age} days ago, DUE NOW (every {interval}). {covered}."
        return (
            f"Last run {when} - {age} day(s) ago. Next due in "
            f"{self.days_until_due(today)} day(s). {covered}."
        )
