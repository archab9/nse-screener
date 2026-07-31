"""Tests for the scoring rules the spec is most emphatic about (section 4)."""

from __future__ import annotations

import pytest

from nse_screener.models import PARAM_IDS, ParamResult, ScoredStock, Tier, Verdict
from nse_screener.scoring import (
    active_maximum,
    rank,
    score_all,
    score_stock,
    tier_for,
)


def make_stock(verdicts: dict[str, Verdict], symbol: str = "TEST", pegy: float | None = None):
    return ScoredStock(
        symbol=symbol,
        name=symbol,
        results={pid: ParamResult(pid, v) for pid, v in verdicts.items()},
        pegy=pegy,
    )


def all_yes() -> dict[str, Verdict]:
    return {pid: Verdict.YES for pid in PARAM_IDS}


class TestToggleRemovesFromDenominator:
    """The spec's central rule: off means excluded, not zeroed."""

    def test_all_on_gives_max_of_14(self):
        stock = score_stock(make_stock(all_yes()), None)
        assert stock.core_score == 14
        assert stock.active_max == 14
        assert stock.pct_of_max == 100.0

    def test_toggling_off_shrinks_the_denominator(self):
        toggles = {pid: pid != "P5" for pid in PARAM_IDS}
        stock = score_stock(make_stock(all_yes()), toggles)
        assert stock.active_max == 12
        assert stock.core_score == 12
        assert stock.pct_of_max == 100.0

    def test_switching_off_a_failing_param_raises_the_percentage(self):
        verdicts = all_yes() | {"P5": Verdict.NO}
        with_p5 = score_stock(make_stock(verdicts), None)
        assert with_p5.core_score == 12 and with_p5.active_max == 14

        without_p5 = score_stock(make_stock(verdicts), {pid: pid != "P5" for pid in PARAM_IDS})
        assert without_p5.core_score == 12 and without_p5.active_max == 12
        assert without_p5.pct_of_max > with_p5.pct_of_max

    def test_off_param_is_not_merely_zeroed(self):
        """Regression guard: zeroing instead of excluding would keep active_max at 14."""
        verdicts = all_yes() | {"P3": Verdict.NO}
        stock = score_stock(make_stock(verdicts), {pid: pid != "P3" for pid in PARAM_IDS})
        assert stock.active_max == 12, "denominator must shrink, not stay at 14"
        assert stock.pct_of_max == 100.0

    @pytest.mark.parametrize("n_off", range(0, 7))
    def test_active_max_tracks_toggle_count(self, n_off):
        off = set(PARAM_IDS[:n_off])
        toggles = {pid: pid not in off for pid in PARAM_IDS}
        assert active_maximum(toggles) == (len(PARAM_IDS) - n_off) * 2

    def test_all_params_off_does_not_divide_by_zero(self):
        stock = score_stock(make_stock(all_yes()), {pid: False for pid in PARAM_IDS})
        assert stock.active_max == 0
        assert stock.pct_of_max == 0.0
        assert stock.tier is Tier.EXCLUDED


class TestTiering:
    """Tiers are a percentage of the active maximum, never a fixed point total."""

    @pytest.mark.parametrize(
        "pct,expected",
        [
            (100.0, Tier.ELITE_COMPOUNDER),
            (90.0, Tier.ELITE_COMPOUNDER),
            (89.9, Tier.QUALITY_GROWER),
            (70.0, Tier.QUALITY_GROWER),
            (69.9, Tier.WATCHLIST),
            (50.0, Tier.WATCHLIST),
            (49.9, Tier.EXCLUDED),
            (0.0, Tier.EXCLUDED),
        ],
    )
    def test_boundaries(self, pct, expected):
        assert tier_for(pct) is expected

    def test_same_raw_score_different_tier_when_denominator_moves(self):
        """10 points is QUALITY GROWER out of 14, but ELITE out of 10."""
        verdicts = all_yes() | {"P6": Verdict.NO, "P7": Verdict.NO}
        full = score_stock(make_stock(verdicts), None)
        assert full.core_score == 10 and full.tier is Tier.QUALITY_GROWER

        toggles = {pid: pid not in ("P6", "P7") for pid in PARAM_IDS}
        reduced = score_stock(make_stock(verdicts), toggles)
        assert reduced.core_score == 10 and reduced.active_max == 10
        assert reduced.tier is Tier.ELITE_COMPOUNDER


class TestNoHardGate:
    def test_failing_a_parameter_still_ranks(self):
        verdicts = all_yes() | {"P4": Verdict.NO}
        stock = score_stock(make_stock(verdicts), None)
        assert stock.tier is not Tier.EXCLUDED
        assert stock.core_score == 12

    def test_partials_accumulate_rather_than_disqualify(self):
        # P6 is binary, so it takes YES here; the other six score PARTIAL.
        # 6 x 1 + 2 = 8 out of 14 -> WATCHLIST, i.e. still ranked, not gated out.
        verdicts = {pid: Verdict.PARTIAL for pid in PARAM_IDS} | {"P6": Verdict.YES}
        stock = score_stock(make_stock(verdicts), None)
        assert stock.core_score == 8
        assert stock.tier is Tier.WATCHLIST

    def test_all_partial_including_binary_p6(self):
        """P6 cannot return PARTIAL, so an all-PARTIAL sheet is 6/14, not 7/14."""
        stock = score_stock(make_stock({pid: Verdict.PARTIAL for pid in PARAM_IDS}), None)
        assert stock.core_score == 6
        assert stock.tier is Tier.EXCLUDED


class TestP8Separation:
    def test_p8_never_contributes_points(self):
        verdicts = all_yes()
        without = score_stock(make_stock(verdicts), None).core_score

        stock = make_stock(verdicts)
        stock.results["P8"] = ParamResult("P8", Verdict.YES)
        with_p8 = score_stock(stock, None).core_score

        assert with_p8 == without == 14

    def test_p8_toggle_does_not_change_active_max(self):
        assert active_maximum({**{p: True for p in PARAM_IDS}, "P8": False}) == 14


class TestBinaryP6:
    def test_partial_on_binary_param_scores_zero_not_one(self):
        stock = score_stock(make_stock(all_yes() | {"P6": Verdict.PARTIAL}), None)
        assert stock.core_score == 12


class TestUnknownHandling:
    def test_unknown_scores_zero_but_still_counts_in_denominator(self):
        stock = score_stock(make_stock(all_yes() | {"P2": Verdict.UNKNOWN}), None)
        assert stock.core_score == 12
        assert stock.active_max == 14

    def test_missing_result_counts_in_denominator(self):
        verdicts = {pid: Verdict.YES for pid in PARAM_IDS if pid != "P7"}
        stock = score_stock(make_stock(verdicts), None)
        assert stock.core_score == 12
        assert stock.active_max == 14


class TestRanking:
    def test_sorted_by_score_then_pegy(self):
        a = make_stock(all_yes(), "AAA", pegy=1.4)
        b = make_stock(all_yes(), "BBB", pegy=0.7)
        c = make_stock(all_yes() | {"P1": Verdict.NO}, "CCC", pegy=0.1)

        ranked = score_all([a, b, c], None)
        assert [s.symbol for s in ranked] == ["BBB", "AAA", "CCC"]

    def test_missing_pegy_sorts_last_within_a_score_band(self):
        a = make_stock(all_yes(), "AAA", pegy=None)
        b = make_stock(all_yes(), "BBB", pegy=2.9)
        assert [s.symbol for s in score_all([a, b], None)] == ["BBB", "AAA"]

    def test_rank_is_stable_for_identical_stocks(self):
        stocks = [score_stock(make_stock(all_yes(), s), None) for s in ("ZZZ", "AAA", "MMM")]
        assert [s.symbol for s in rank(stocks)] == ["AAA", "MMM", "ZZZ"]


class TestEmptyInput:
    def test_empty_list_is_not_an_error(self):
        assert score_all([], None) == []
