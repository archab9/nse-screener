"""Tests for the 15-day ranking-refresh cadence."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from nse_screener.analysis_state import AnalysisState


@pytest.fixture
def state(tmp_path):
    return AnalysisState(path=tmp_path / "analysis_state.json")


class Board:
    def __init__(self, sectors=3, subsectors=5, priced=4):
        self.sectors = list(range(sectors))
        self.subsectors = list(range(subsectors))
        self._priced = priced

    def all_groups(self):
        class Group:
            def __init__(self, has):
                self.has_returns = has

        total = len(self.sectors) + len(self.subsectors)
        return [Group(i < self._priced) for i in range(total)]


class TestDueLogic:
    def test_never_run_is_due(self, state):
        assert state.is_due() is True
        assert state.age_days() is None
        assert "never been run" in state.status()

    def test_fresh_run_is_not_due(self, state):
        state.record(Board())
        assert state.is_due() is False
        assert state.days_until_due() == 15

    def test_due_again_after_the_interval(self, state):
        state.record(Board(), when=datetime.now() - timedelta(days=15))
        assert state.is_due() is True
        assert "DUE NOW" in state.status()

    def test_not_due_one_day_early(self, state):
        state.record(Board(), when=datetime.now() - timedelta(days=14))
        assert state.is_due() is False
        assert state.days_until_due() == 1

    def test_interval_is_fifteen_days(self):
        assert AnalysisState.interval_days() == 15

    def test_age_counts_calendar_days(self, state):
        state.record(Board(), when=datetime.now() - timedelta(days=3))
        assert state.age_days() == 3


class TestPersistence:
    def test_round_trip(self, state):
        state.record(Board(sectors=22, subsectors=188, priced=9))
        state.save()

        reloaded = AnalysisState.load(state.path)
        assert reloaded.sectors == 22 and reloaded.subsectors == 188
        assert reloaded.priced == 9
        assert reloaded.last_run is not None
        assert reloaded.is_due() is False

    def test_missing_file_reads_as_never_run(self, tmp_path):
        assert AnalysisState.load(tmp_path / "absent.json").is_due() is True

    def test_corrupt_file_reads_as_never_run(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text("{broken", encoding="utf-8")
        assert AnalysisState.load(path).last_run is None

    def test_status_reports_coverage(self, state):
        state.record(Board(sectors=22, subsectors=188, priced=9))
        assert "9 of 210 groups had return data" in state.status()

    def test_due_state_never_blocks(self, state):
        """Being due is a prompt. The previous result must survive it."""
        state.record(Board(priced=4), when=datetime.now() - timedelta(days=40))
        assert state.is_due() is True
        assert state.priced == 4, "the last run's numbers are still there"
