"""Tests for the Sector Leadership module, following the spec's own test checklist."""

from __future__ import annotations

import csv
from datetime import date

import pytest

from nse_screener import config
from nse_screener.breadth import (
    Override,
    Status,
    Trend,
    apply_trend,
    company_passes,
    compute_breadth,
    enabled_conditions,
    evaluate_conditions,
)
from nse_screener.classification import ClassificationStore, Source, rank_within_industry
from nse_screener.models import ScoredStock
from nse_screener.sector_history import SectorHistory, quarter_label
from nse_screener.sector_reference import (
    CompanyRow,
    SectorReferenceError,
    load_sector_reference,
)
from nse_screener.symbol_utils import normalise_symbol

HEADERS = [
    "NSE Code", "Name", "Sector", "Industry", "Basic Industry",
    "Sales growth 3Years", "Profit growth 3Years", "OPM", "OPM last year",
    "ROCE", "Debt to equity", "Market Capitalization",
]


def row(symbol, industry, sector="Industrials", passing=True, **over):
    values = {
        "sales": 18.0 if passing else 4.0,
        "profit": 22.0 if passing else 3.0,
        "opm": 19.0 if passing else 11.0,
        "opm_prev": 15.0 if passing else 12.0,
        "roce": 20.0 if passing else 8.0,
    }
    values.update(over)
    return [
        symbol, f"{symbol} Ltd", sector, industry, f"{industry} sub",
        values["sales"], values["profit"], values["opm"], values["opm_prev"],
        values["roce"], 0.4, 5000,
    ]


def write_reference(directory, rows, headers=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "export.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers or HEADERS)
        writer.writerows(rows)
    return path


@pytest.fixture(autouse=True)
def default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_THRESHOLDS", tmp_path / "over.json")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


# ------------------------------------------------------------------ import (step 1)

class TestReferenceImport:
    def test_screener_header_wording_is_matched_by_alias(self, tmp_path):
        write_reference(tmp_path / "ref", [row("AAA", "Aerospace & Defense")])
        reference = load_sector_reference(tmp_path / "ref")
        assert reference.missing_columns == []
        assert reference.rows[0].industry == "Aerospace & Defense"
        assert reference.rows[0].roce == 20.0

    def test_alternate_header_spellings(self, tmp_path):
        headers = list(HEADERS)
        headers[0], headers[7], headers[8] = "Symbol", "OPM current", "OPM preceding year"
        write_reference(tmp_path / "ref", [row("AAA", "X")], headers)
        reference = load_sector_reference(tmp_path / "ref")
        assert reference.rows[0].symbol == "AAA"
        assert reference.rows[0].opm_current == 19.0

    def test_missing_required_column_raises_rather_than_yielding_nones(self, tmp_path):
        headers = [h for h in HEADERS if h != "Industry"]
        rows = [[v for i, v in enumerate(row("AAA", "X")) if HEADERS[i] != "Industry"]]
        write_reference(tmp_path / "ref", rows, headers)
        with pytest.raises(SectorReferenceError, match="industry"):
            load_sector_reference(tmp_path / "ref")

    def test_basic_industry_is_never_mistaken_for_industry(self, tmp_path):
        """Regression: 'basicindustry' contains 'industry', so a substring fallback
        silently classified every company one level too deep when Industry was absent."""
        headers = [h for h in HEADERS if h != "Industry"]
        rows = [[v for i, v in enumerate(row("AAA", "X")) if HEADERS[i] != "Industry"]]
        write_reference(tmp_path / "ref", rows, headers)
        with pytest.raises(SectorReferenceError):
            load_sector_reference(tmp_path / "ref")

    def test_both_industry_columns_map_to_their_own_field(self, tmp_path):
        write_reference(tmp_path / "ref", [row("AAA", "Aerospace & Defense")])
        company = load_sector_reference(tmp_path / "ref").rows[0]
        assert company.industry == "Aerospace & Defense"
        assert company.basic_industry == "Aerospace & Defense sub"

    def test_extra_wording_around_a_header_still_matches(self, tmp_path):
        headers = list(HEADERS)
        headers[5] = "Sales growth 3Years %"
        write_reference(tmp_path / "ref", [row("AAA", "X")], headers)
        assert load_sector_reference(tmp_path / "ref").rows[0].sales_growth_3y == 18.0

    def test_multiple_files_merge_for_split_exports(self, tmp_path):
        directory = tmp_path / "ref"
        write_reference(directory, [row("AAA", "X")])
        with (directory / "band2.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(HEADERS)
            writer.writerow(row("BBB", "X"))
        reference = load_sector_reference(directory)
        assert {r.symbol for r in reference.rows} == {"AAA", "BBB"}

    def test_symbols_are_normalised_on_import(self, tmp_path):
        write_reference(tmp_path / "ref", [row("reliance.ns", "X")])
        assert load_sector_reference(tmp_path / "ref").rows[0].symbol == "RELIANCE"

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(SectorReferenceError):
            load_sector_reference(tmp_path / "absent")


# ---------------------------------------------------------------- conditions (step 3)

class TestConditions:
    def test_all_four_pass(self):
        assert company_passes(CompanyRow("A", industry="X", sales_growth_3y=18,
                                         profit_growth_3y=22, opm_current=19,
                                         opm_preceding=15, roce=20)) is True

    def test_one_failure_fails_the_company(self):
        assert company_passes(CompanyRow("A", industry="X", sales_growth_3y=18,
                                         profit_growth_3y=22, opm_current=19,
                                         opm_preceding=15, roce=5)) is False

    def test_missing_data_is_not_a_pass(self):
        """A company with no ROCE has not demonstrated ROCE above the bar."""
        company = CompanyRow("A", industry="X", sales_growth_3y=18, profit_growth_3y=22,
                             opm_current=19, opm_preceding=15, roce=None)
        assert evaluate_conditions(company)["roce"] is None
        assert company_passes(company) is False

    def test_margin_condition_compares_against_preceding_year(self):
        shrinking = CompanyRow("A", industry="X", opm_current=10, opm_preceding=15)
        assert evaluate_conditions(shrinking)["opm_expanding"] is False

    def test_disabling_a_condition_removes_it_from_the_test(self):
        weak_roce = CompanyRow("A", industry="X", sales_growth_3y=18, profit_growth_3y=22,
                               opm_current=19, opm_preceding=15, roce=5)
        assert company_passes(weak_roce) is False
        assert company_passes(weak_roce, enabled=["sales_growth_3y", "profit_growth_3y"]) is True

    def test_threshold_change_takes_effect_without_new_data(self):
        company = CompanyRow("A", industry="X", sales_growth_3y=8, profit_growth_3y=22,
                             opm_current=19, opm_preceding=15, roce=20)
        assert company_passes(company) is False
        config.save_overrides({"sector_leadership": {"conditions": {
            "sales_growth_3y": {"enabled": True, "min": 5.0}}}})
        assert company_passes(company) is True


# ------------------------------------------------------------------ breadth (step 4)

def build_reference(tmp_path, spec):
    """spec: {(sector, industry): (total, passing)}"""
    rows = []
    n = 0
    for (sector, industry), (total, passing) in spec.items():
        for i in range(total):
            n += 1
            rows.append(row(f"S{n:03d}", industry, sector, passing=i < passing))
    write_reference(tmp_path / "ref", rows)
    return load_sector_reference(tmp_path / "ref")


class TestBreadth:
    def test_percentage_matches_a_hand_count(self, tmp_path):
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 7)})
        entry = compute_breadth(reference)[0]
        assert (entry.numerator, entry.denominator) == (7, 10)
        assert entry.breadth_pct == 70.0

    def test_denominator_is_every_covered_company_not_just_passers(self, tmp_path):
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 3)})
        assert compute_breadth(reference)[0].denominator == 10

    @pytest.mark.parametrize(
        "passing,expected",
        [(7, Status.LEADERSHIP_ALIGNED), (6, Status.LEADERSHIP_ALIGNED),
         (5, Status.WATCH), (4, Status.WATCH), (3, Status.NOT_FLAGGED)],
    )
    def test_status_cutoffs(self, tmp_path, passing, expected):
        reference = build_reference(tmp_path, {("S", "I"): (10, passing)})
        assert compute_breadth(reference)[0].status is expected

    def test_thin_industry_is_suppressed_not_dropped(self, tmp_path):
        reference = build_reference(
            tmp_path, {("S", "Big"): (10, 8), ("S", "Thin"): (4, 4)}
        )
        entries = {e.industry: e for e in compute_breadth(reference)}
        assert "Thin" in entries, "thin industries must still be listed"
        assert entries["Thin"].status is Status.INSUFFICIENT_SAMPLE
        assert entries["Thin"].breadth_pct is None, "no percentage on too few companies"

    def test_gate_is_configurable(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "Thin"): (4, 4)})
        assert compute_breadth(reference)[0].status is Status.INSUFFICIENT_SAMPLE
        config.save_overrides({"sector_leadership": {"min_companies": 3}})
        assert compute_breadth(reference)[0].status is Status.LEADERSHIP_ALIGNED

    def test_toggling_a_condition_recomputes_from_the_same_data(self, tmp_path):
        rows = [row(f"S{i}", "I", passing=False, sales=18, profit=22, opm=19, opm_prev=15)
                for i in range(10)]
        write_reference(tmp_path / "ref", rows)
        reference = load_sector_reference(tmp_path / "ref")
        assert compute_breadth(reference)[0].breadth_pct == 0.0
        without_roce = ["sales_growth_3y", "profit_growth_3y", "opm_expanding"]
        assert compute_breadth(reference, enabled=without_roce)[0].breadth_pct == 100.0


# ----------------------------------------------------------------- overrides (step 6)

class TestOverrides:
    def test_force_yes_beats_a_low_computed_number(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 1)})
        entry = compute_breadth(reference, overrides={"I": Override.FORCE_YES})[0]
        assert entry.computed_status is Status.NOT_FLAGGED
        assert entry.status is Status.LEADERSHIP_ALIGNED
        assert "manual" in entry.status_label

    def test_force_no_beats_a_high_computed_number(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 10)})
        entry = compute_breadth(reference, overrides={"I": Override.FORCE_NO})[0]
        assert entry.computed_status is Status.LEADERSHIP_ALIGNED
        assert entry.status is Status.NOT_FLAGGED

    def test_auto_leaves_the_computed_value_alone(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 8)})
        entry = compute_breadth(reference, overrides={"I": Override.AUTO})[0]
        assert entry.status is entry.computed_status


# ------------------------------------------------------------------- history (step 5)

class TestHistoryAndTrend:
    def test_first_run_has_insufficient_history(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 7)})
        entries = apply_trend(compute_breadth(reference), {})
        assert entries[0].trend is Trend.INSUFFICIENT_HISTORY

    def test_trend_from_a_prior_quarter(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 7)})
        assert apply_trend(compute_breadth(reference), {"I": 40.0})[0].trend is Trend.RISING
        assert apply_trend(compute_breadth(reference), {"I": 90.0})[0].trend is Trend.FALLING
        assert apply_trend(compute_breadth(reference), {"I": 70.0})[0].trend is Trend.FLAT

    def test_watch_to_aligned_sets_the_rising_indicator(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 7)})
        entry = apply_trend(compute_breadth(reference), {"I": 50.0})[0]
        assert entry.rising_into_leadership is True

    def test_snapshots_round_trip(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 7)})
        path = tmp_path / "history.json"
        history = SectorHistory.load(path)
        history.record_snapshot(compute_breadth(reference), enabled_conditions(), label="FY2026-Q1")
        history.save()

        reloaded = SectorHistory.load(path)
        assert reloaded.quarters() == ["FY2026-Q1"]
        assert reloaded.records[0].company_count == 10

    def test_rerunning_a_quarter_replaces_rather_than_appends(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 7)})
        history = SectorHistory.load(tmp_path / "h.json")
        history.record_snapshot(compute_breadth(reference), [], label="FY2026-Q1")
        history.record_snapshot(compute_breadth(reference), [], label="FY2026-Q1")
        assert len(history.records) == 1

    def test_previous_breadth_reads_the_prior_quarter(self, tmp_path):
        reference = build_reference(tmp_path, {("S", "I"): (10, 4)})
        history = SectorHistory.load(tmp_path / "h.json")
        history.record_snapshot(compute_breadth(reference), [], label="FY2026-Q1")
        assert history.previous_breadth("FY2026-Q2") == {"I": 40.0}
        assert history.previous_breadth("FY2026-Q1") == {}

    def test_quarter_label_uses_indian_fiscal_year(self):
        assert quarter_label(date(2026, 5, 1)) == "FY2027-Q1"
        assert quarter_label(date(2026, 8, 1)) == "FY2027-Q2"
        assert quarter_label(date(2026, 2, 1)) == "FY2026-Q4"


# ------------------------------------------------------------ classification (step 2)

class TestClassification:
    def test_resolves_from_the_reference_export(self, tmp_path):
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 7)})
        store = ClassificationStore.load(tmp_path / "c.json")
        store.resolve(["S001"], reference)
        assert store.get("S001").industry == "Defence"
        assert store.get("S001").source is Source.REFERENCE

    def test_unknown_symbol_is_unresolved_never_guessed(self, tmp_path):
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 7)})
        store = ClassificationStore.load(tmp_path / "c.json")
        store.resolve(["NOTLISTED"], reference)
        classification = store.get("NOTLISTED")
        assert classification.source is Source.UNRESOLVED
        assert classification.industry == ""
        assert classification.resolved is False

    def test_suffixed_symbol_still_matches(self, tmp_path):
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 7)})
        store = ClassificationStore.load(tmp_path / "c.json")
        store.resolve(["s001.NS"], reference)
        assert store.get("S001").resolved is True

    def test_manual_override_persists_and_wins(self, tmp_path):
        path = tmp_path / "c.json"
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 7)})
        store = ClassificationStore.load(path)
        store.set_override("NOTLISTED", "Industrials", "Defence")
        store.save()

        reloaded = ClassificationStore.load(path)
        reloaded.resolve(["NOTLISTED"], reference)
        assert reloaded.get("NOTLISTED").industry == "Defence"
        assert reloaded.get("NOTLISTED").source is Source.MANUAL

    def test_clearing_an_override_returns_it_to_unresolved(self, tmp_path):
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 7)})
        store = ClassificationStore.load(tmp_path / "c.json")
        store.set_override("NOTLISTED", "Industrials", "Defence")
        store.clear_override("NOTLISTED")
        store.resolve(["NOTLISTED"], reference)
        assert store.get("NOTLISTED").resolved is False

    def test_every_ingestion_path_uses_one_resolution_call(self, tmp_path):
        """CSV, text-file and typed symbols all arrive as plain symbols, so one call
        classifies them identically - there is no per-path branch to diverge."""
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 7)})
        store = ClassificationStore.load(tmp_path / "c.json")
        store.resolve(["S001", "s002.ns", " S003 "], reference)
        assert all(store.get(s).resolved for s in ("S001", "S002", "S003"))

    def test_reference_refresh_reclassifies(self, tmp_path):
        store = ClassificationStore.load(tmp_path / "c.json")
        store.resolve(["S001"], None)
        assert store.get("S001").resolved is False
        reference = build_reference(tmp_path, {("Industrials", "Defence"): (10, 7)})
        store.resolve(["S001"], reference)
        assert store.get("S001").resolved is True


# --------------------------------------------------------------- ranking (step 8)

def scored(symbol, score):
    stock = ScoredStock(symbol=symbol, name=symbol)
    stock.core_score = score
    return stock


class TestRankWithinIndustry:
    def test_ranks_by_core_score_within_the_tracked_universe(self, tmp_path):
        store = ClassificationStore.load(tmp_path / "c.json")
        for symbol in ("A", "B", "C"):
            store.set_override(symbol, "S", "Defence")
        ranks = rank_within_industry([scored("A", 8), scored("B", 14), scored("C", 11)], store)
        assert ranks["B"] == (1, 3)
        assert ranks["C"] == (2, 3)
        assert ranks["A"] == (3, 3)

    def test_industries_rank_independently(self, tmp_path):
        store = ClassificationStore.load(tmp_path / "c.json")
        store.set_override("A", "S", "Defence")
        store.set_override("B", "S", "Cement")
        ranks = rank_within_industry([scored("A", 4), scored("B", 2)], store)
        assert ranks["A"] == (1, 1)
        assert ranks["B"] == (1, 1)

    def test_unresolved_stocks_are_excluded_from_ranking(self, tmp_path):
        store = ClassificationStore.load(tmp_path / "c.json")
        store.resolve(["A"], None)
        assert rank_within_industry([scored("A", 14)], store) == {}


class TestSymbolNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [("reliance.ns", "RELIANCE"), (" HAL ", "HAL"), ('"BEL"', "BEL"),
         ("TCS.BO", "TCS"), ("M&M", "M&M"), ("BAJAJ-AUTO", "BAJAJ-AUTO"), (None, "")],
    )
    def test_cases(self, raw, expected):
        assert normalise_symbol(raw) == expected
