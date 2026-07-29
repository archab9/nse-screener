"""Rolling 30-day history of screening runs.

Every press of Generate Results appends a run. Runs older than the retention window are
pruned on save, so the file stays bounded without anyone having to tidy it.

Stored under LOCALAPPDATA alongside the watchlist - it is personal output, not project
config, and the repo is shared.

"Parameters hit" means parameters that returned YES. That is the sort key the history list
uses, and it is deliberately NOT the core score: a stock with four YES and three NO (8
points) has demonstrated more individual criteria than one with seven PARTIAL (7 points),
and the question this list answers is "how many tests did it actually pass".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import settings
from .models import P8_ID, PARAM_IDS, ScoredStock
from .watchlist import default_path as _watchlist_dir

DEFAULT_RETENTION_DAYS = 30


def default_path() -> Path:
    return _watchlist_dir().parent / "run_history.json"


def _jsonable(value):
    """Evidence values are mostly numbers, lists and dicts; anything else becomes text."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


@dataclass
class ParamSnapshot:
    verdict: str = "N/A"
    detail: str = ""
    evidence: dict = field(default_factory=dict)


@dataclass
class StockSnapshot:
    symbol: str
    name: str = ""
    industry: str = ""
    core_score: int = 0
    active_max: int = 0
    pct_of_max: float = 0.0
    tier: str = ""
    yes_count: int = 0
    partial_count: int = 0
    no_count: int = 0
    unknown_count: int = 0
    pegy: float | None = None
    pb: float | None = None
    sector_tailwind: bool = False
    tailwind_sector: str = ""
    flags: list[str] = field(default_factory=list)
    params: dict[str, ParamSnapshot] = field(default_factory=dict)

    @property
    def hit_display(self) -> str:
        return f"{self.yes_count} of {self.yes_count + self.partial_count + self.no_count + self.unknown_count}"

    @property
    def score_display(self) -> str:
        return f"{self.core_score}/{self.active_max}"


@dataclass
class RunRecord:
    run_id: str
    run_at: str
    stage1_count: int = 0
    scored_count: int = 0
    active_params: list[str] = field(default_factory=list)
    data_source: str = ""
    stocks: list[StockSnapshot] = field(default_factory=list)

    @property
    def run_date(self) -> date:
        try:
            return datetime.fromisoformat(self.run_at).date()
        except ValueError:
            return date.today()

    @property
    def label(self) -> str:
        try:
            stamp = datetime.fromisoformat(self.run_at)
        except ValueError:
            return self.run_id
        return f"{stamp:%d %b %Y  %H:%M}  -  {self.scored_count} scored"


def snapshot_stock(stock: ScoredStock, toggles: dict[str, bool] | None = None) -> StockSnapshot:
    from .scoring import normalise_toggles

    active = normalise_toggles(toggles)
    counts = {"YES": 0, "PARTIAL": 0, "NO": 0, "N/A": 0}
    params: dict[str, ParamSnapshot] = {}

    for pid in list(PARAM_IDS) + [P8_ID]:
        result = stock.results.get(pid)
        if result is None:
            continue
        params[pid] = ParamSnapshot(
            verdict=result.verdict.label,
            detail=result.detail,
            evidence={str(k): _jsonable(v) for k, v in result.evidence.items()},
        )
        # P8 is never scored, so it must not count toward "parameters hit" either.
        if pid in PARAM_IDS and active.get(pid, True):
            counts[result.verdict.label] = counts.get(result.verdict.label, 0) + 1

    return StockSnapshot(
        symbol=stock.symbol,
        name=stock.name,
        industry=stock.industry,
        core_score=stock.core_score,
        active_max=stock.active_max,
        pct_of_max=round(stock.pct_of_max, 1),
        tier=stock.tier.value,
        yes_count=counts["YES"],
        partial_count=counts["PARTIAL"],
        no_count=counts["NO"],
        unknown_count=counts["N/A"],
        pegy=stock.pegy,
        pb=stock.pb,
        sector_tailwind=stock.sector_tailwind,
        tailwind_sector=stock.tailwind_sector,
        flags=[str(f) for f in stock.all_flags],
        params=params,
    )


def sort_key(snapshot: StockSnapshot) -> tuple:
    """Most parameters hit first; ties broken by score, then percentage, then symbol."""
    return (-snapshot.yes_count, -snapshot.core_score, -snapshot.pct_of_max, snapshot.symbol)


@dataclass
class RunHistory:
    path: Path = field(default_factory=default_path)
    runs: list[RunRecord] = field(default_factory=list)

    # ------------------------------------------------------------------ persistence

    @classmethod
    def load(cls, path: Path | None = None) -> RunHistory:
        history = cls(path=Path(path) if path else default_path())
        if not history.path.exists():
            return history
        try:
            raw = json.loads(history.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return history  # a corrupt file must not block a run

        for item in raw.get("runs", []):
            try:
                stocks = [
                    StockSnapshot(
                        **{
                            **s,
                            "params": {
                                k: ParamSnapshot(**v) for k, v in (s.get("params") or {}).items()
                            },
                        }
                    )
                    for s in item.get("stocks", [])
                ]
                history.runs.append(
                    RunRecord(
                        run_id=item["run_id"],
                        run_at=item["run_at"],
                        stage1_count=item.get("stage1_count", 0),
                        scored_count=item.get("scored_count", 0),
                        active_params=item.get("active_params", []),
                        data_source=item.get("data_source", ""),
                        stocks=stocks,
                    )
                )
            except (KeyError, TypeError):
                continue  # skip a malformed run rather than lose the whole history
        return history

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "retention_days": self.retention_days(),
            "runs": [
                {
                    **asdict(run),
                    "stocks": [
                        {**asdict(s), "params": {k: asdict(v) for k, v in s.params.items()}}
                        for s in run.stocks
                    ],
                }
                for run in self.runs
            ],
        }
        self.path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------------- recording

    @staticmethod
    def retention_days() -> int:
        return int(settings().get("history", {}).get("retention_days", DEFAULT_RETENTION_DAYS))

    def record(
        self,
        stocks: list[ScoredStock],
        toggles: dict[str, bool] | None = None,
        stage1_count: int = 0,
        data_source: str = "",
        when: datetime | None = None,
    ) -> RunRecord:
        from .scoring import active_params

        stamp = when or datetime.now()
        run = RunRecord(
            run_id=stamp.strftime("%Y%m%d-%H%M%S"),
            run_at=stamp.isoformat(timespec="seconds"),
            stage1_count=stage1_count,
            scored_count=len(stocks),
            active_params=active_params(toggles),
            data_source=data_source,
            stocks=sorted((snapshot_stock(s, toggles) for s in stocks), key=sort_key),
        )
        self.runs.append(run)
        # Prune against the real calendar, NOT against the timestamp being inserted.
        # Using the inserted run's date meant a back-dated run moved the cutoff back with
        # it and pruning silently stopped happening.
        self.prune()
        return run

    def prune(self, today: date | None = None) -> int:
        """Drop runs outside the retention window. Returns how many were removed."""
        cutoff = (today or date.today()) - timedelta(days=self.retention_days())
        before = len(self.runs)
        self.runs = [r for r in self.runs if r.run_date > cutoff]
        self.runs.sort(key=lambda r: r.run_at)
        return before - len(self.runs)

    # ------------------------------------------------------------------- queries

    @property
    def latest(self) -> RunRecord | None:
        return self.runs[-1] if self.runs else None

    def recent_runs(self) -> list[RunRecord]:
        """Newest first, for the run selector."""
        return sorted(self.runs, key=lambda r: r.run_at, reverse=True)

    def run_by_id(self, run_id: str) -> RunRecord | None:
        return next((r for r in self.runs if r.run_id == run_id), None)

    def aggregate(self) -> list[StockSnapshot]:
        """Best appearance per symbol across the window, sorted by parameters hit.

        A stock is represented by its strongest run, so the list answers "what is the best
        this name has looked in the last 30 days" rather than being skewed by a single
        weak day.
        """
        best: dict[str, StockSnapshot] = {}
        for run in self.runs:
            for snapshot in run.stocks:
                current = best.get(snapshot.symbol)
                if current is None or sort_key(snapshot) < sort_key(current):
                    best[snapshot.symbol] = snapshot
        return sorted(best.values(), key=sort_key)

    def appearances(self, symbol: str) -> list[tuple[RunRecord, StockSnapshot]]:
        """Every run this symbol appeared in, newest first."""
        out = []
        for run in sorted(self.runs, key=lambda r: r.run_at, reverse=True):
            for snapshot in run.stocks:
                if snapshot.symbol == symbol:
                    out.append((run, snapshot))
                    break
        return out
