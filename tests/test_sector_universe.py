"""Tests for the NSE sector universe - the list of what EXISTS, independent of returns."""

from __future__ import annotations

import csv

import pytest

from nse_screener.leaderboard import build_leaderboards
from nse_screener.sector_universe import (
    SectorUniverseError,
    load_sector_universe,
)

HEADERS = ["Sector", "Subsector", "Stock Name", "Market Cap Category"]

ROWS = [
    ["Automobiles & Auto Components", "2/3 Wheelers", "Bajaj Auto", "Large Cap"],
    ["Automobiles & Auto Components", "2/3 Wheelers", "TVS Motor", "Large Cap"],
    ["Automobiles & Auto Components", "Auto Components", "Bosch", "Large Cap"],
    ["Information Technology", "IT - Software", "Infosys", "Large Cap"],
    ["Information Technology", "IT - Software", "TCS", "Large Cap"],
    ["Fast Moving Consumer Goods (FMCG)", "Diversified FMCG", "ITC", "Large Cap"],
    ["Realty", "Realty", "DLF", "Mid Cap"],
    ["Textiles", "Other Textile Products", "Small Weaver", "Microcap"],
]


def write(tmp_path, rows=None, headers=None):
    directory = tmp_path / "universe"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "nse.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers or HEADERS)
        writer.writerows(rows if rows is not None else ROWS)
    return path


class TestLoading:
    def test_reads_every_sector_and_subsector(self, tmp_path):
        universe = load_sector_universe(write(tmp_path))
        assert len(universe.sectors) == 5
        assert len(universe.subsectors) == 6
        assert "Information Technology" in universe.sectors
        assert "Fast Moving Consumer Goods (FMCG)" in universe.sectors

    def test_constituent_counts(self, tmp_path):
        universe = load_sector_universe(write(tmp_path))
        assert universe.size("sector", "Automobiles & Auto Components") == 3
        assert universe.size("subsector", "2/3 Wheelers") == 2

    def test_parent_lookup(self, tmp_path):
        universe = load_sector_universe(write(tmp_path))
        assert universe.parent_of("IT - Software") == "Information Technology"
        assert universe.parent_of("Nonexistent") == ""

    def test_subsectors_of_a_sector(self, tmp_path):
        universe = load_sector_universe(write(tmp_path))
        assert universe.subsectors_of("Automobiles & Auto Components") == [
            "2/3 Wheelers", "Auto Components"
        ]

    def test_cap_category_comes_from_the_file(self, tmp_path):
        universe = load_sector_universe(write(tmp_path))
        assert universe.cap_for("Bajaj Auto") == "Large cap"
        assert universe.cap_for("Small Weaver") == "Micro cap", "Microcap normalises"
        assert universe.cap_for("Unlisted Co") == ""

    def test_missing_file_raises_when_named_explicitly(self, tmp_path):
        with pytest.raises(SectorUniverseError):
            load_sector_universe(tmp_path / "absent.csv")

    def test_missing_required_column(self, tmp_path):
        path = write(tmp_path, rows=[["a", "b"]], headers=["Sector", "Stock Name"])
        with pytest.raises(SectorUniverseError, match="subsector"):
            load_sector_universe(path)

    def test_empty_universe_is_safe(self):
        from nse_screener.sector_universe import SectorUniverse

        universe = SectorUniverse()
        assert universe.available is False
        assert universe.sectors == [] and universe.size("sector", "X") == 0


class TestEveryGroupIsListed:
    """The whole point: a sector with no return data must still appear."""

    def _reference(self, tmp_path, priced_sector):
        from nse_screener.sector_reference import load_sector_reference

        directory = tmp_path / "ref"
        directory.mkdir(parents=True, exist_ok=True)
        headers = [
            "NSE Code", "Name", "Sector", "Industry", "Basic Industry",
            "Sales growth 3Years", "Profit growth 3Years", "OPM", "OPM last year",
            "ROCE", "Debt to equity", "Market Capitalization",
            "Return over 6months", "Return over 1year", "Return over 3years",
            "Return over 5years",
        ]
        rows = [
            [f"S{i}", f"S{i} Ltd", priced_sector, "IT - Software", "IT - Software",
             10, 10, 15, 14, 15, 0.4, 1000, 20.0, 30.0, 60.0, 90.0]
            for i in range(6)
        ]
        with (directory / "e.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            writer.writerows(rows)
        return load_sector_reference(directory)

    def test_unpriced_sectors_still_appear(self, tmp_path):
        universe = load_sector_universe(write(tmp_path))
        reference = self._reference(tmp_path, "Information Technology")
        board = build_leaderboards(reference, universe)

        listed = {g.name for g in board.sectors}
        for sector in universe.sectors:
            assert sector in listed, f"{sector} vanished from the table"

    def test_unpriced_groups_are_marked_not_dropped(self, tmp_path):
        universe = load_sector_universe(write(tmp_path))
        board = build_leaderboards(self._reference(tmp_path, "Information Technology"), universe)

        realty = board.sector("Realty")
        assert realty is not None
        assert realty.has_returns is False
        assert realty.strength is None
        assert realty.medal == "", "a group with no returns cannot medal"

    def test_priced_group_still_ranks_normally(self, tmp_path):
        universe = load_sector_universe(write(tmp_path))
        board = build_leaderboards(self._reference(tmp_path, "Information Technology"), universe)
        it = board.sector("Information Technology")
        assert it.has_returns is True
        assert it.horizon("return_1y").median_return == 30.0

    def test_constituent_count_comes_from_the_universe(self, tmp_path):
        """A group shows its real size even when the export prices none of its members."""
        universe = load_sector_universe(write(tmp_path))
        board = build_leaderboards(self._reference(tmp_path, "Information Technology"), universe)
        autos = board.sector("Automobiles & Auto Components")
        assert autos.constituents == 3
        assert autos.with_returns == 0

    def test_universe_alone_still_lists_everything(self, tmp_path):
        """No bulk export at all: the table should still show the full taxonomy."""
        universe = load_sector_universe(write(tmp_path))
        board = build_leaderboards(None, universe)
        assert len(board.sectors) == len(universe.sectors)
        assert len(board.subsectors) == len(universe.subsectors)
        assert board.priced is False
