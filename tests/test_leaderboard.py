"""Tests for the 6-month sector/subsector leaderboard, medals and the trophy."""

from __future__ import annotations

import csv

import pytest

from nse_screener import config
from nse_screener.leaderboard import (
    BRONZE,
    GOLD,
    SILVER,
    build_leaderboards,
    has_trophy,
    trophy_reasons,
)
from nse_screener.sector_reference import load_sector_reference

HEADERS = [
    "NSE Code", "Name", "Sector", "Industry", "Basic Industry",
    "Sales growth 3Years", "Profit growth 3Years", "OPM", "OPM last year",
    "ROCE", "Debt to equity", "Market Capitalization",
    "Return over 6months", "Return over 1year", "Return over 3years", "Return over 5years",
]


def row(symbol, sector, sub, ret6m, ret1y=None):
    return [
        symbol, f"{symbol} Ltd", sector, sub, sub,
        10.0, 10.0, 15.0, 14.0, 15.0, 0.4, 1000,
        ret6m, ret1y if ret1y is not None else ret6m, 100.0, 200.0,
    ]


def build(tmp_path, rows):
    directory = tmp_path / "ref"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "e.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        writer.writerows(rows)
    return load_sector_reference(directory)


def group(sector, sub, count, ret6m, start=0):
    return [row(f"{sub[:3]}{start + i}", sector, sub, ret6m) for i in range(count)]


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_THRESHOLDS", tmp_path / "over.json")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


class TestLeaderboard:
    def test_medals_go_to_the_top_three(self, tmp_path):
        rows = (group("A", "Aaa", 6, 40.0) + group("B", "Bbb", 6, 30.0, 10)
                + group("C", "Ccc", 6, 20.0, 20) + group("D", "Ddd", 6, 10.0, 30))
        board = build_leaderboards(build(tmp_path, rows))
        assert [g.medal for g in board.sectors[:4]] == [GOLD, SILVER, BRONZE, ""]
        assert board.sectors[0].name == "A"

    def test_ranked_by_median_not_mean(self, tmp_path):
        """One runaway winner must not carry an otherwise flat sector."""
        flat = group("Steady", "Steady", 6, 20.0)
        skewed = group("Skewed", "Skewed", 5, 5.0, 50) + [row("MOON", "Skewed", "Skewed", 900.0)]
        board = build_leaderboards(build(tmp_path, flat + skewed))
        assert board.sectors[0].name == "Steady"

    def test_thin_groups_are_excluded(self, tmp_path):
        rows = group("Big", "Big", 6, 10.0) + group("Tiny", "Tiny", 2, 500.0, 90)
        board = build_leaderboards(build(tmp_path, rows))
        assert [g.name for g in board.sectors] == ["Big"]
        assert board.skipped_thin >= 1

    def test_minimum_is_configurable(self, tmp_path):
        rows = group("Big", "Big", 6, 10.0) + group("Tiny", "Tiny", 2, 500.0, 90)
        config.save_overrides({"leaderboard": {"min_constituents": 2}})
        board = build_leaderboards(build(tmp_path, rows))
        assert board.sectors[0].name == "Tiny"

    def test_sectors_and_subsectors_rank_separately(self, tmp_path):
        rows = group("Ind", "Fast", 6, 40.0) + group("Ind", "Slow", 6, 5.0, 10)
        board = build_leaderboards(build(tmp_path, rows))
        assert len(board.sectors) == 1
        assert [g.name for g in board.subsectors] == ["Fast", "Slow"]
        assert board.subsectors[0].medal == GOLD

    def test_label_prefixes_the_medal(self, tmp_path):
        board = build_leaderboards(build(tmp_path, group("Ind", "Fast", 6, 40.0)))
        assert board.label("sector", "Ind") == f"{GOLD} Ind"
        assert board.label("sector", "Unknown") == "Unknown"

    def test_no_reference_is_safe(self):
        board = build_leaderboards(None)
        assert board.available is False
        assert board.medal_for("sector", "X") == ""
        assert "No sector reference" in board.summary()

    def test_missing_six_month_column_yields_nothing(self, tmp_path):
        rows = group("Ind", "Fast", 6, 40.0)
        for r in rows:
            r[12] = ""
        assert build_leaderboards(build(tmp_path, rows)).available is False


class Snap:
    """Minimal stand-in for a StockSnapshot."""

    def __init__(self, yes=7, partial=0, no=0, unknown=0, sub_rank=1,
                 sector="Ind", subsector="Fast"):
        self.yes_count, self.partial_count = yes, partial
        self.no_count, self.unknown_count = no, unknown
        self.peer_sector, self.peer_subsector = sector, subsector
        self.peer_ranks = {"Price return 1y": {"sub_rank": sub_rank, "sub_total": 12}}


@pytest.fixture
def board(tmp_path):
    rows = (group("Ind", "Fast", 6, 40.0) + group("Other", "Slow", 6, 5.0, 20)
            + group("Third", "Mid", 6, 20.0, 40))
    return build_leaderboards(build(tmp_path, rows))


class TestTrophy:
    def test_awarded_when_all_four_conditions_hold(self, board):
        assert has_trophy(Snap(), board) is True

    def test_denied_when_a_parameter_is_only_partial(self, board):
        assert has_trophy(Snap(yes=6, partial=1), board) is False

    def test_denied_when_not_first_in_subsector(self, board):
        assert has_trophy(Snap(sub_rank=2), board) is False

    def test_denied_when_subsector_has_no_medal(self, board):
        assert has_trophy(Snap(subsector="Nowhere"), board) is False

    def test_denied_when_sector_has_no_medal(self, board):
        assert has_trophy(Snap(sector="Nowhere"), board) is False

    def test_reasons_list_every_condition_either_way(self, board):
        _won, reasons = trophy_reasons(Snap(sub_rank=4), board)
        assert len(reasons) == 4
        assert sum(r.startswith("PASS") for r in reasons) == 3
        assert any(r.startswith("no") and "#1 in subsector" in r for r in reasons)

    def test_unranked_stock_gets_no_trophy(self, board):
        snap = Snap()
        snap.peer_ranks = {}
        assert has_trophy(snap, board) is False

    def test_nothing_evaluated_gets_no_trophy(self, board):
        assert has_trophy(Snap(yes=0), board) is False
