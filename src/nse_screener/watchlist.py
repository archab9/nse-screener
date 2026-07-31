"""Persistent watchlist.

Three states per symbol, matching the three actions offered on each result row:
  WATCHING  - explicitly added
  REMOVED   - explicitly dismissed, so it can be filtered out of future runs
  NONE      - no decision recorded (the default)

REMOVED is kept as a distinct state rather than deleting the entry, so "I looked at this
and said no" survives the next run instead of resurfacing as if it were new.

Stored under the user's local app data, not in the repo - it is personal state, and the
repo is shared.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path


class WatchState(Enum):
    NONE = "none"
    WATCHING = "watching"
    REMOVED = "removed"

    @property
    def label(self) -> str:
        return {
            WatchState.NONE: "No action",
            WatchState.WATCHING: "In watchlist",
            WatchState.REMOVED: "Removed",
        }[self]


def default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "nse-screener" / "watchlist.json"


@dataclass
class WatchEntry:
    symbol: str
    state: WatchState = WatchState.NONE
    note: str = ""
    updated: str = ""
    # Snapshot of why it was added, so the watchlist still means something later.
    tier_when_added: str = ""
    score_when_added: str = ""


@dataclass
class Watchlist:
    path: Path = field(default_factory=default_path)
    entries: dict[str, WatchEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Watchlist:
        target = Path(path) if path else default_path()
        watchlist = cls(path=target)
        if not target.exists():
            return watchlist
        try:
            with target.open(encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Corrupt file must not block a run; start clean rather than crash.
            return watchlist

        for symbol, payload in (raw.get("entries") or {}).items():
            try:
                state = WatchState(payload.get("state", "none"))
            except ValueError:
                state = WatchState.NONE
            watchlist.entries[symbol.upper()] = WatchEntry(
                symbol=symbol.upper(),
                state=state,
                note=payload.get("note", ""),
                updated=payload.get("updated", ""),
                tier_when_added=payload.get("tier_when_added", ""),
                score_when_added=payload.get("score_when_added", ""),
            )
        return watchlist

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": {
                symbol: {
                    "state": entry.state.value,
                    "note": entry.note,
                    "updated": entry.updated,
                    "tier_when_added": entry.tier_when_added,
                    "score_when_added": entry.score_when_added,
                }
                for symbol, entry in self.entries.items()
                if entry.state is not WatchState.NONE or entry.note
            }
        }
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    def state_of(self, symbol: str) -> WatchState:
        entry = self.entries.get(symbol.upper())
        return entry.state if entry else WatchState.NONE

    def set_state(
        self,
        symbol: str,
        state: WatchState,
        tier: str = "",
        score: str = "",
    ) -> None:
        symbol = symbol.upper()
        entry = self.entries.setdefault(symbol, WatchEntry(symbol=symbol))
        entry.state = state
        entry.updated = date.today().isoformat()
        if state is WatchState.WATCHING:
            entry.tier_when_added = tier or entry.tier_when_added
            entry.score_when_added = score or entry.score_when_added

    def set_note(self, symbol: str, note: str) -> None:
        symbol = symbol.upper()
        entry = self.entries.setdefault(symbol, WatchEntry(symbol=symbol))
        entry.note = note

    def watching(self) -> list[WatchEntry]:
        return sorted(
            (e for e in self.entries.values() if e.state is WatchState.WATCHING),
            key=lambda e: e.symbol,
        )

    def removed_symbols(self) -> set[str]:
        return {e.symbol for e in self.entries.values() if e.state is WatchState.REMOVED}
