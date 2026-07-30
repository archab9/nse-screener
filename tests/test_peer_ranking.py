"""Tests for peer ranking and market-cap classification."""

from __future__ import annotations

import csv

import pytest

from nse_screener import config
from nse_screener.peer_ranking import METRICS, PRIMARY_METRIC, cap_category, rank_symbol
from nse_screener.sector_reference import load_sector_reference

HEADERS = [
    "NSE Code", "Name", "Sector", "Industry", "Basic Industry",
    "Sales growth 3Years", "Profit growth 3Years", "OPM", "OPM last year",
    "ROCE", "Debt to equity", "Market Capitalization",
    "Return over 1year", "Return over 3years", "Return over 5years",
]


def build(tmp_path, rows):
    directory = tmp_path / "ref"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "export.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        writer.writerows(rows)
    return load_sector_reference(directory)


def row(symbol, sector, sub, ret1, ret3=None, ret5=None, cap=1000, roce=None):
    return [
        symbol, f"{symbol} Ltd", sector, sub.split(" - ")[0], sub,
        10.0, 10.0, 15.0, 14.0, roce if roce is not None else ret1, 0.4, cap,
        ret1, ret3 if ret3 is not None else ret1, ret5 if ret5 is not None else ret1,
    ]


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_THRESHOLDS", tmp_path / "over.json")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


class TestRanking:
    def test_best_value_ranks_first(self, tmp_path):
        ref = build(tmp_path, [
            row("A", "Ind", "Defence", 30.0),
            row("B", "Ind", "Defence", 20.0),
            row("C", "Ind", "Defence", 10.0),
        ])
        assert rank_symbol(ref, "A").metric("return_1y").subsector.rank == 1
        assert rank_symbol(ref, "C").metric("return_1y").subsector.rank == 3

    def test_subsector_and_sector_are_separate_groups(self, tmp_path):
        ref = build(tmp_path, [
            row("A", "Ind", "Defence", 30.0),
            row("B", "Ind", "Defence", 40.0),
            row("C", "Ind", "Cement", 50.0),
        ])
        metric = rank_symbol(ref, "A").metric("return_1y")
        assert (metric.subsector.rank, metric.subsector.total) == (2, 2)
        assert (metric.sector.rank, metric.sector.total) == (3, 3)

    def test_every_metric_is_ranked(self, tmp_path):
        ref = build(tmp_path, [row("A", "Ind", "Defence", 30.0), row("B", "Ind", "Defence", 10.0)])
        ranking = rank_symbol(ref, "A")
        assert [m.key for m in ranking.metrics] == [k for k, _ in METRICS]
        assert all(m.subsector.known for m in ranking.metrics)

    def test_1y_3y_5y_rank_independently(self, tmp_path):
        """The whole point: a stock can lead on one horizon and lag on another."""
        ref = build(tmp_path, [
            row("A", "Ind", "Defence", ret1=30.0, ret3=5.0, ret5=5.0),
            row("B", "Ind", "Defence", ret1=10.0, ret3=40.0, ret5=40.0),
        ])
        ranking = rank_symbol(ref, "A")
        assert ranking.metric("return_1y").subsector.rank == 1
        assert ranking.metric("return_3y").subsector.rank == 2
        assert ranking.metric("return_5y").subsector.rank == 2

    def test_companies_missing_the_metric_leave_the_denominator(self, tmp_path):
        rows = [row("A", "Ind", "Defence", 30.0), row("B", "Ind", "Defence", 20.0)]
        rows.append(row("C", "Ind", "Defence", 10.0))
        rows[2][12] = ""      # C reports no 1-year return
        ref = build(tmp_path, rows)
        metric = rank_symbol(ref, "A").metric("return_1y")
        assert metric.subsector.total == 2, "unreported is not the same as last"

    def test_unknown_symbol_yields_nothing(self, tmp_path):
        ref = build(tmp_path, [row("A", "Ind", "Defence", 30.0)])
        assert rank_symbol(ref, "ZZZ").available is False

    def test_no_reference_yields_nothing(self):
        assert rank_symbol(None, "A").available is False

    def test_percentile_and_summary(self, tmp_path):
        ref = build(tmp_path, [row(f"S{i}", "Ind", "Defence", float(100 - i)) for i in range(10)])
        ranking = rank_symbol(ref, "S0")
        assert ranking.metric(PRIMARY_METRIC).subsector.percentile == 0.0
        assert "#1/10 sub" in ranking.summary()

    def test_table_lists_every_metric(self, tmp_path):
        ref = build(tmp_path, [row("A", "Ind", "Defence", 30.0), row("B", "Ind", "Defence", 10.0)])
        text = "\n".join(rank_symbol(ref, "A").table())
        for _key, label in METRICS:
            assert label in text


class TestMarketCap:
    def test_threshold_fallback_without_a_reference(self):
        assert cap_category(60000) == "Large cap"
        assert cap_category(9000) == "Mid cap"
        assert cap_category(1200) == "Small cap"
        assert cap_category(120) == "Micro cap"

    def test_boundaries(self):
        assert cap_category(20000) == "Large cap"
        assert cap_category(19999) == "Mid cap"
        assert cap_category(5000) == "Mid cap"
        assert cap_category(4999) == "Small cap"
        assert cap_category(500) == "Small cap"
        assert cap_category(499) == "Micro cap"

    def test_missing_market_cap(self):
        assert cap_category(None) == ""

    def test_thresholds_are_configurable(self):
        config.save_overrides({"market_cap": {"large_cap_cr": 1000}})
        assert cap_category(1500) == "Large cap"

    def test_rank_based_when_a_full_universe_is_loaded(self, tmp_path):
        """SEBI classifies by rank, not rupees. With the bulk export loaded the true rank
        is used, so a 9,000cr company can be large cap in a small universe."""
        rows = [row(f"S{i}", "Ind", "Defence", 10.0, cap=100000 - i * 100) for i in range(300)]
        ref = build(tmp_path, rows)
        assert cap_category(100000, ref) == "Large cap"     # rank 1
        assert cap_category(100000 - 150 * 100, ref) == "Mid cap"    # rank ~151
        assert cap_category(100000 - 280 * 100, ref) == "Small cap"  # rank ~281

    def test_small_universe_falls_back_to_thresholds(self, tmp_path):
        ref = build(tmp_path, [row("A", "Ind", "Defence", 10.0, cap=9000)])
        assert cap_category(9000, ref) == "Mid cap", "too few rows to rank meaningfully"
