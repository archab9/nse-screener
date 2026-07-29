"""Tests for the rolling 30-day run history."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nse_screener.models import PARAM_IDS, ParamResult, ScoredStock, Tier, Verdict
from nse_screener.run_history import RunHistory, snapshot_stock, sort_key


def stock(symbol: str, verdicts: dict[str, Verdict], score: int = 0, pegy=None) -> ScoredStock:
    s = ScoredStock(
        symbol=symbol,
        name=f"{symbol} Ltd",
        industry="Aerospace & Defense",
        results={pid: ParamResult(pid, v, f"{pid} detail", {"n": 1}) for pid, v in verdicts.items()},
        pegy=pegy,
    )
    s.core_score = score
    s.active_max = 14
    s.pct_of_max = score / 14 * 100
    s.tier = Tier.QUALITY_GROWER
    return s


def all_of(verdict: Verdict) -> dict[str, Verdict]:
    return {pid: verdict for pid in PARAM_IDS}


@pytest.fixture
def history(tmp_path):
    return RunHistory(path=tmp_path / "runs.json")


class TestSnapshotCounts:
    def test_counts_verdicts(self):
        verdicts = all_of(Verdict.NO) | {
            "P1": Verdict.YES, "P2": Verdict.YES, "P3": Verdict.PARTIAL, "P4": Verdict.UNKNOWN
        }
        snap = snapshot_stock(stock("X", verdicts))
        assert (snap.yes_count, snap.partial_count, snap.unknown_count) == (2, 1, 1)
        assert snap.no_count == 3

    def test_p8_never_counts_as_a_hit(self):
        """P8 is reported separately and never scored, so it cannot be a parameter hit."""
        verdicts = all_of(Verdict.NO) | {"P1": Verdict.YES}
        s = stock("X", verdicts)
        s.results["P8"] = ParamResult("P8", Verdict.YES)
        snap = snapshot_stock(s)
        assert snap.yes_count == 1
        assert "P8" in snap.params, "still recorded for display"

    def test_disabled_parameter_is_not_counted(self):
        toggles = {pid: pid != "P1" for pid in PARAM_IDS}
        snap = snapshot_stock(stock("X", all_of(Verdict.YES)), toggles)
        assert snap.yes_count == len(PARAM_IDS) - 1

    def test_evidence_is_preserved_for_the_detail_view(self):
        snap = snapshot_stock(stock("X", all_of(Verdict.YES)))
        assert snap.params["P1"].evidence == {"n": 1}
        assert snap.params["P1"].detail == "P1 detail"


class TestSorting:
    def test_most_parameters_hit_first(self, history):
        stocks = [
            stock("LOW", all_of(Verdict.NO) | {"P1": Verdict.YES}, score=2),
            stock("HIGH", all_of(Verdict.YES), score=14),
            stock("MID", all_of(Verdict.NO) | {p: Verdict.YES for p in ("P1", "P2", "P3")}, score=6),
        ]
        run = history.record(stocks)
        assert [s.symbol for s in run.stocks] == ["HIGH", "MID", "LOW"]

    def test_hits_outrank_raw_score(self):
        """4 YES + 3 NO is 8 points; 7 PARTIAL is 7 points. The one with more YES leads,
        even though a score sort would agree here - the point is hits come first."""
        many_hits = snapshot_stock(
            stock("HITS", all_of(Verdict.NO) | {p: Verdict.YES for p in ("P1", "P2", "P3", "P4")}, score=8)
        )
        all_partial = snapshot_stock(stock("PARTIAL", all_of(Verdict.PARTIAL), score=7))
        assert sort_key(many_hits) < sort_key(all_partial)

    def test_score_breaks_ties_on_equal_hits(self):
        a = snapshot_stock(stock("A", all_of(Verdict.NO) | {"P1": Verdict.YES}, score=2))
        b = snapshot_stock(stock("B", all_of(Verdict.NO) | {"P1": Verdict.YES}, score=6))
        assert sort_key(b) < sort_key(a)


class TestPersistence:
    def test_round_trip(self, history):
        history.record([stock("HAL", all_of(Verdict.YES), score=14)], stage1_count=21,
                       data_source="live")
        history.save()

        reloaded = RunHistory.load(history.path)
        assert len(reloaded.runs) == 1
        run = reloaded.runs[0]
        assert run.stage1_count == 21 and run.data_source == "live"
        assert run.stocks[0].symbol == "HAL"
        assert run.stocks[0].params["P1"].detail == "P1 detail"

    def test_corrupt_file_starts_clean(self, tmp_path):
        path = tmp_path / "runs.json"
        path.write_text("{not json", encoding="utf-8")
        assert RunHistory.load(path).runs == []

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert RunHistory.load(tmp_path / "absent.json").runs == []

    def test_malformed_run_is_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "runs.json"
        path.write_text('{"runs":[{"bad":1},{"run_id":"a","run_at":"2026-07-01T10:00:00"}]}',
                        encoding="utf-8")
        assert len(RunHistory.load(path).runs) == 1


class TestRetention:
    """Ages are relative to the real clock, because that is what pruning uses - a run
    dated 40 days ago is out of the window no matter when it was inserted."""

    def test_runs_older_than_the_window_are_pruned(self, history):
        now = datetime.now()
        for age in (0, 5, 29, 31, 60):
            history.record([stock("X", all_of(Verdict.YES))], when=now - timedelta(days=age))
        assert len(history.runs) == 3, [r.run_at for r in history.runs]

    def test_pruning_happens_on_record(self, history):
        history.record(
            [stock("X", all_of(Verdict.YES))], when=datetime.now() - timedelta(days=100)
        )
        assert len(history.runs) == 0

    def test_backdated_insert_does_not_move_the_cutoff(self, history):
        """Regression: pruning used the inserted run's date as 'today', so adding an old
        run pushed the window back with it and nothing was ever pruned."""
        now = datetime.now()
        history.record([stock("FRESH", all_of(Verdict.YES))], when=now)
        history.record([stock("STALE", all_of(Verdict.YES))], when=now - timedelta(days=90))
        assert [r.stocks[0].symbol for r in history.runs] == ["FRESH"]

    def test_retention_window_is_thirty_days_by_default(self, history):
        assert history.retention_days() == 30

    def test_runs_are_kept_in_chronological_order(self, history):
        now = datetime(2026, 7, 28, 10, 0)
        for age in (2, 10, 1):
            history.record([stock("X", all_of(Verdict.YES))], when=now - timedelta(days=age))
        assert [r.run_at for r in history.runs] == sorted(r.run_at for r in history.runs)


class TestQueries:
    def test_aggregate_keeps_the_best_appearance(self, history):
        now = datetime(2026, 7, 28, 10, 0)
        history.record([stock("HAL", all_of(Verdict.NO) | {"P1": Verdict.YES}, score=2)],
                       when=now - timedelta(days=3))
        history.record([stock("HAL", all_of(Verdict.YES), score=14)], when=now)

        rows = history.aggregate()
        assert len(rows) == 1
        assert rows[0].yes_count == len(PARAM_IDS), "best run should represent the stock"

    def test_appearances_newest_first(self, history):
        now = datetime(2026, 7, 28, 10, 0)
        history.record([stock("HAL", all_of(Verdict.YES))], when=now - timedelta(days=4))
        history.record([stock("HAL", all_of(Verdict.YES))], when=now)
        runs = history.appearances("HAL")
        assert len(runs) == 2
        assert runs[0][0].run_at > runs[1][0].run_at

    def test_appearances_of_unknown_symbol(self, history):
        history.record([stock("HAL", all_of(Verdict.YES))])
        assert history.appearances("NOSUCH") == []

    def test_run_lookup_by_id(self, history):
        run = history.record([stock("HAL", all_of(Verdict.YES))])
        assert history.run_by_id(run.run_id) is run
        assert history.run_by_id("nope") is None

    def test_latest_returns_the_newest_run(self, history):
        now = datetime(2026, 7, 28, 10, 0)
        history.record([stock("A", all_of(Verdict.YES))], when=now - timedelta(days=2))
        newest = history.record([stock("B", all_of(Verdict.YES))], when=now)
        assert history.latest is newest

    def test_empty_history_queries_are_safe(self, history):
        assert history.aggregate() == [] and history.latest is None
