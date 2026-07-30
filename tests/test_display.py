"""Tests for the shared pass/fail, sector-leader and headline-positive formatting."""

from __future__ import annotations

import pytest

from nse_screener.display import (
    BADGE,
    format_snapshot_detail,
    headline_positive,
    is_leader,
    leader_label,
    leader_short,
    parameter_badges,
    passed_failed,
)
from nse_screener.models import PARAM_IDS, ParamResult, ScoredStock, Tier, Verdict
from nse_screener.run_history import snapshot_stock


class TestParameterBadges:
    def test_one_badge_per_parameter(self):
        verdicts = {pid: "YES" for pid in PARAM_IDS}
        badges = parameter_badges(verdicts)
        assert badges.count(BADGE["YES"]) == len(PARAM_IDS)

    def test_each_verdict_gets_its_own_mark(self):
        verdicts = {"P1": "YES", "P2": "PARTIAL", "P3": "NO", "P4": "N/A"}
        badges = parameter_badges(verdicts)
        assert "P1✓" in badges and "P2~" in badges and "P3✗" in badges and "P4·" in badges

    def test_switched_off_parameter_is_bracketed_not_failed(self):
        """An off parameter must never look like a failure."""
        verdicts = {pid: "YES" for pid in PARAM_IDS}
        badges = parameter_badges(verdicts, {pid: pid != "P5" for pid in PARAM_IDS})
        assert "[P5]" in badges
        assert "P5✗" not in badges

    def test_missing_verdict_shows_no_data(self):
        assert "P1·" in parameter_badges({})


class TestPassedFailed:
    def test_splits_by_verdict(self):
        verdicts = {"P1": "YES", "P2": "NO", "P3": "YES", "P4": "NO"}
        passed, failed = passed_failed(verdicts)
        assert [p.split()[0] for p in passed] == ["P1", "P3"]
        assert [f.split()[0] for f in failed] == ["P2", "P4"]

    def test_partial_counts_as_neither(self):
        passed, failed = passed_failed({"P1": "PARTIAL"})
        assert passed == [] and failed == []

    def test_disabled_parameters_excluded_entirely(self):
        verdicts = {pid: "YES" for pid in PARAM_IDS}
        passed, _ = passed_failed(verdicts, {pid: pid != "P1" for pid in PARAM_IDS})
        assert not any(p.startswith("P1 ") for p in passed)

    def test_names_are_included_for_readability(self):
        passed, _ = passed_failed({"P1": "YES"})
        assert passed == ["P1 Record financials"]


class TestSectorLeader:
    @pytest.mark.parametrize("rank", [1, 2, 3])
    def test_top_three_is_a_leader(self, rank):
        assert is_leader(rank)
        assert leader_label(rank, 25, "Aerospace & Defense") == f"#{rank} of 25 in Aerospace & Defense"

    @pytest.mark.parametrize("rank", [4, 9, 25])
    def test_outside_top_three_is_not(self, rank):
        assert not is_leader(rank)
        assert leader_label(rank, 25, "Aerospace & Defense") == ""

    def test_unranked_is_not_a_leader(self):
        assert not is_leader(None)
        assert leader_label(None, 25, "X") == ""

    def test_industry_name_is_required(self):
        assert leader_label(1, 25, "") == ""

    def test_short_form_carries_the_industry(self):
        assert leader_short(2, 12, "Cement") == "Top 3 - #2 of 12 in Cement"

    def test_missing_total_still_renders(self):
        assert leader_label(1, None, "Cement") == "#1 in Cement"


class TestHeadlinePositive:
    def test_prefers_the_strongest_concall_positive(self):
        assert headline_positive(
            ["Won a Rs 1,200 crore order"], ["Business segments"], "About text"
        ) == "Won a Rs 1,200 crore order"

    def test_falls_back_to_key_points(self):
        assert headline_positive([], ["Manufactures aircraft and helicopters"], "About") == (
            "Manufactures aircraft and helicopters"
        )

    def test_falls_back_to_about(self):
        assert headline_positive([], [], "A long enough about blurb here") == (
            "A long enough about blurb here"
        )

    def test_nothing_available(self):
        assert headline_positive([], [], "") == ""

    def test_skips_too_short_candidates(self):
        assert headline_positive(["ok"], ["also short"], "A proper description here") == (
            "A proper description here"
        )


def make_snapshot(**over):
    verdicts = {"P1": Verdict.YES, "P2": Verdict.YES, "P3": Verdict.NO,
                "P4": Verdict.PARTIAL, "P5": Verdict.YES, "P6": Verdict.YES, "P7": Verdict.NO}
    stock = ScoredStock(
        symbol="HAL", name="Hindustan Aeronautics", industry="Aerospace & Defense",
        results={p: ParamResult(p, v, f"{p} detail") for p, v in verdicts.items()},
    )
    stock.core_score, stock.active_max, stock.tier = 10, 14, Tier.QUALITY_GROWER

    class Company:
        industry_rank = over.get("rank", 1)
        industry_peer_count = 25
        concall_date = "May 2026"
        concall_summary = over.get(
            "summary",
            "- Won a Rs 1,200 crore order from an overseas customer.\n"
            "- Export demand remained subdued.",
        )
        key_points = ["Manufactures aircraft and helicopters"]
        about = "Hindustan Aeronautics designs and builds aircraft."

    return snapshot_stock(stock, None, Company())


class TestSnapshotCarriesNarrative:
    def test_concall_split_is_stored(self):
        snap = make_snapshot()
        assert any("order" in p for p in snap.concall_positives)
        assert any("subdued" in n for n in snap.concall_negatives)

    def test_headline_positive_is_stored(self):
        assert "order" in make_snapshot().headline_positive

    def test_industry_rank_is_stored(self):
        snap = make_snapshot()
        assert (snap.industry_rank, snap.industry_peer_count) == (1, 25)

    def test_missing_company_degrades_cleanly(self):
        stock = ScoredStock(symbol="X", name="X", results={})
        snap = snapshot_stock(stock, None, None)
        assert snap.concall_positives == [] and snap.headline_positive == ""


class TestSharedDetailView:
    def test_shows_pass_fail_leader_and_concall(self):
        text = format_snapshot_detail(make_snapshot())
        assert "Passed (4):" in text and "Failed (2):" in text
        assert "Biggest positive:" in text
        assert "+ Won a Rs 1,200 crore order" in text
        assert "- Export demand remained subdued" in text

    def test_cap_category_is_shown_when_known(self):
        snap = make_snapshot()
        snap.cap_category = "Mid cap"
        snap.market_cap_cr = 14500
        assert "Mid cap" in format_snapshot_detail(snap)

    def test_no_concall_falls_back_to_business_text(self):
        text = format_snapshot_detail(make_snapshot(summary=""))
        assert "Business:" in text

    def test_parameter_evidence_is_present(self):
        assert "P1 Record financials: YES" in format_snapshot_detail(make_snapshot())
