"""Tests for the parameter rules in spec section 3, including its explicit revisions."""

from __future__ import annotations

from nse_screener.models import Verdict
from nse_screener.stage2.fundamentals import (
    AnnualPoint,
    CompanyFundamentals,
    QuarterPoint,
    ShareholdingPoint,
    _quarter_sort_key,
)
from nse_screener.stage2.params import (
    evaluate_p1,
    evaluate_p2,
    evaluate_p4,
    evaluate_p5,
    evaluate_p6,
    evaluate_p7,
)

QUARTERS = [f"{y}-Q{q}" for y in range(2021, 2026) for q in range(1, 5)]


def company(**kwargs) -> CompanyFundamentals:
    base = {"symbol": "TEST", "name": "Test Ltd", "industry": "Software - Application"}
    return CompanyFundamentals(**(base | kwargs))


def quarterly(sales: list[float], profit: list[float]) -> list[QuarterPoint]:
    labels = QUARTERS[-len(sales) :]
    return [QuarterPoint(labels[i], sales[i], profit[i]) for i in range(len(sales))]


def annual(**series) -> list[AnnualPoint]:
    n = len(next(iter(series.values())))
    years = list(range(2026 - n, 2026))
    return [AnnualPoint(fy=years[i], **{k: v[i] for k, v in series.items()}) for i in range(n)]


def shareholding(**series) -> list[ShareholdingPoint]:
    n = len(next(iter(series.values())))
    labels = QUARTERS[-n:]
    return [ShareholdingPoint(labels[i], **{k: v[i] for k, v in series.items()}) for i in range(n)]


class TestP1Seasonality:
    """The revision exists to stop a repeating strong quarter reading as a 5-year high."""

    def test_ttm_smoothing_ignores_a_repeating_seasonal_spike(self):
        # Flat business with a big Q3 every year: no real growth, latest quarter is not a peak.
        sales = [100 * (1.6 if i % 4 == 2 else 1.0) for i in range(20)]
        c = company(quarterly=quarterly(sales, [s * 0.1 for s in sales]))
        result = evaluate_p1(c)
        assert result.verdict is not Verdict.YES

    def test_genuine_growth_scores_yes(self):
        sales = [100 * (1.05**i) for i in range(20)]
        c = company(quarterly=quarterly(sales, [s * 0.12 for s in sales]))
        assert evaluate_p1(c).verdict is Verdict.YES

    def test_highs_without_cagr_is_partial_and_flags_cyclical_peak(self):
        sales = [100 * (1.005**i) for i in range(20)]  # ~2% CAGR, below the 12% bar
        c = company(quarterly=quarterly(sales, [s * 0.1 for s in sales]))
        result = evaluate_p1(c)
        assert result.verdict is Verdict.PARTIAL
        assert any(f.code == "cyclical_peak" for f in result.flags)

    def test_insufficient_history_is_unknown_not_no(self):
        c = company(quarterly=quarterly([100, 110, 120], [10, 11, 12]))
        assert evaluate_p1(c).verdict is Verdict.UNKNOWN


class TestP2Leverage:
    def test_leverage_gap_downgrades_yes_to_partial(self):
        c = company(
            annual=annual(
                roce_pct=[16, 17, 18, 19, 20],
                roe_pct=[26, 27, 28, 29, 30],  # gap > 8pp every year
            )
        )
        result = evaluate_p2(c)
        assert result.verdict is Verdict.PARTIAL
        assert any(f.code == "leverage_inflated_roe" for f in result.flags)

    def test_clean_returns_score_yes(self):
        c = company(annual=annual(roce_pct=[18, 19, 20, 21, 22], roe_pct=[17, 18, 19, 20, 21]))
        result = evaluate_p2(c)
        assert result.verdict is Verdict.YES
        assert not any(f.code == "leverage_inflated_roe" for f in result.flags)

    def test_capital_intensive_industry_uses_the_10pct_bar(self):
        returns = {"roce_pct": [11, 12, 12.5, 13, 13.5], "roe_pct": [16, 17, 18, 19, 20]}
        strict = company(industry="Software - Application", annual=annual(**returns))
        relaxed = company(industry="Construction & Infrastructure", annual=annual(**returns))
        assert evaluate_p2(relaxed).verdict is Verdict.YES
        assert evaluate_p2(strict).verdict is not Verdict.YES

    def test_pricing_power_is_informational_only(self):
        c = company(annual=annual(roce_pct=[26, 27, 28, 29, 30], roe_pct=[20, 21, 22, 23, 24]))
        result = evaluate_p2(c)
        assert any(f.code == "pricing_power" and f.severity == "positive" for f in result.flags)


class TestP4TwoOfThree:
    """Spec resolves the original 3-vs-2 contradiction in favour of 2-of-3."""

    def test_two_of_three_qualifies(self):
        c = company(annual=annual(cfo=[100, 90, 50], pat=[100, 100, 100]))
        assert evaluate_p4(c).verdict is Verdict.YES

    def test_one_of_three_does_not(self):
        c = company(annual=annual(cfo=[100, 50, 40], pat=[100, 100, 100]))
        assert evaluate_p4(c).verdict is not Verdict.YES

    def test_receivable_days_spike_raises_a_flag(self):
        c = company(
            annual=annual(
                cfo=[100, 100, 100], pat=[100, 100, 100], receivable_days=[50, 50, 70]
            )
        )
        result = evaluate_p4(c)
        assert result.verdict is Verdict.YES  # flag does not change the verdict
        assert any(f.code == "receivable_days_spike" for f in result.flags)


class TestP5Institutional:
    def test_both_rising_is_yes(self):
        c = company(
            shareholding=shareholding(
                fii_pct=[5, 5.5, 6, 6.5, 7, 7.5], dii_pct=[8, 8.5, 9, 9.5, 10, 10.5]
            )
        )
        assert evaluate_p5(c).verdict is Verdict.YES

    def test_flat_within_tolerance_counts_as_rising(self):
        c = company(
            shareholding=shareholding(
                fii_pct=[5, 5, 4.9, 4.8, 4.7, 4.5], dii_pct=[8, 8, 8, 8, 8, 8]
            )
        )
        assert evaluate_p5(c).verdict is Verdict.YES

    def test_one_declining_is_partial(self):
        c = company(
            shareholding=shareholding(
                fii_pct=[9, 8, 7, 6, 5, 4], dii_pct=[8, 8.5, 9, 9.5, 10, 10.5]
            )
        )
        assert evaluate_p5(c).verdict is Verdict.PARTIAL

    def test_both_declining_is_no(self):
        c = company(
            shareholding=shareholding(
                fii_pct=[9, 8, 7, 6, 5, 4], dii_pct=[12, 11, 10, 9, 8, 7]
            )
        )
        assert evaluate_p5(c).verdict is Verdict.NO

    def test_marquee_exit_flag_on_a_sharp_single_quarter_drop(self):
        c = company(
            shareholding=shareholding(
                fii_pct=[9, 9, 5.5, 5.5, 5.5, 5.5], dii_pct=[8, 8, 8, 8, 8, 8]
            )
        )
        assert any(f.code == "marquee_exit" for f in evaluate_p5(c).flags)


class TestP6Binary:
    def test_never_returns_partial(self):
        cases = [
            [60, 60, 60, 60],
            [60, 55, 50, 45],
            [30, 30, 30, 30],
            [51, 51, 45, 45],
        ]
        for holdings in cases:
            c = company(shareholding=shareholding(promoter_pct=holdings))
            assert evaluate_p6(c).verdict in (Verdict.YES, Verdict.NO)

    def test_single_quarter_cliff_fails(self):
        c = company(shareholding=shareholding(promoter_pct=[60, 60, 54, 54]))
        assert evaluate_p6(c).verdict is Verdict.NO

    def test_stable_sub_50_holding_still_passes(self):
        c = company(shareholding=shareholding(promoter_pct=[42, 42.2, 41.9, 42.1]))
        assert evaluate_p6(c).verdict is Verdict.YES

    def test_psu_exception(self):
        c = company(is_psu=True, shareholding=shareholding(promoter_pct=[56, 56, 56, 56]))
        assert evaluate_p6(c).verdict is Verdict.YES

    def test_pledge_flag(self):
        c = company(
            shareholding=shareholding(promoter_pct=[60, 60, 60, 60], pledge_pct=[25, 25, 25, 25])
        )
        assert any(f.code == "pledge_risk" for f in evaluate_p6(c).flags)


class TestP7Valuation:
    SECTORS = {"NIFTY IT": {"pe": 26.0, "pb": 7.0}, "NIFTY 500": {"pe": 23.7, "pb": 4.0}}

    def test_cheap_and_reasonable_pb_is_yes(self):
        c = company(pe=15.0, pb=6.0, eps_cagr_pct=18.0, dividend_yield_pct=1.0)
        assert evaluate_p7(c, self.SECTORS).verdict is Verdict.YES

    def test_expensive_on_both_is_no(self):
        c = company(pe=90.0, pb=20.0, eps_cagr_pct=10.0, dividend_yield_pct=0.0)
        assert evaluate_p7(c, self.SECTORS).verdict is Verdict.NO

    def test_high_growth_roce_exception_relaxes_the_pegy_bar(self):
        kwargs = {"pe": 40.0, "pb": 6.0, "eps_cagr_pct": 30.0, "dividend_yield_pct": 0.0}
        plain = company(**kwargs, annual=annual(roce_pct=[12, 13, 14]))
        high = company(**kwargs, annual=annual(roce_pct=[24, 25, 26]))
        # PEGY = 40/30 = 1.33: above the 1.0 bar, below the 1.5 high-growth bar.
        assert evaluate_p7(plain, self.SECTORS).verdict is Verdict.PARTIAL
        assert evaluate_p7(high, self.SECTORS).verdict is Verdict.YES

    def test_missing_sector_benchmark_reports_partial_not_invented_yes(self):
        c = company(pe=15.0, pb=6.0, eps_cagr_pct=18.0, dividend_yield_pct=1.0)
        result = evaluate_p7(c, {})
        assert result.verdict is Verdict.PARTIAL
        assert "unavailable" in result.detail

    def test_missing_eps_growth_is_unknown(self):
        c = company(pe=15.0, pb=2.0, eps_cagr_pct=None)
        assert evaluate_p7(c, self.SECTORS).verdict is Verdict.UNKNOWN

    def test_negative_growth_is_no_not_a_crash(self):
        c = company(pe=15.0, pb=2.0, eps_cagr_pct=-20.0, dividend_yield_pct=1.0)
        assert evaluate_p7(c, self.SECTORS).verdict is Verdict.NO


class TestQuarterParsing:
    def test_formats_sort_chronologically(self):
        assert _quarter_sort_key("2024-Q1") < _quarter_sort_key("2024-Q2")
        assert _quarter_sort_key("2024-Q4") < _quarter_sort_key("2025-Q1")

    def test_alternate_spellings(self):
        assert _quarter_sort_key("2024Q3") == _quarter_sort_key("2024-Q3")
        assert _quarter_sort_key("Q3 2024") == _quarter_sort_key("2024-Q3")
        assert _quarter_sort_key("Q3-2024") == _quarter_sort_key("2024-Q3")

    def test_screener_month_year_headers_sort_chronologically(self):
        """Screener.in renders quarter columns as 'Sep 2024'.

        Regression: an earlier version mapped month names onto Indian fiscal-year quarter
        numbers (Jun -> Q1 ... Mar -> Q4), which put Mar 2024 AFTER Dec 2024 and silently
        scrambled every company's history. Ordering must be plain calendar order.
        """
        labels = ["Mar 2024", "Jun 2024", "Sep 2024", "Dec 2024", "Mar 2025"]
        assert sorted(labels, key=_quarter_sort_key) == labels

    def test_iso_date_keys_from_the_live_page(self):
        """Screener.in column headers carry data-date-key='YYYY-MM-DD'."""
        keys = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"]
        assert sorted(keys, key=_quarter_sort_key) == keys

    def test_formats_share_one_comparable_scale(self):
        """ISO, 'Mmm YYYY' and 'YYYY-Qn' must all land on the same (year, month) scale."""
        assert _quarter_sort_key("2024-03-31") == _quarter_sort_key("Mar 2024")
        assert _quarter_sort_key("Mar 2024") == _quarter_sort_key("2024-Q1")
        assert _quarter_sort_key("2024-12-31") == _quarter_sort_key("2024-Q4")

    def test_unparseable_label_sorts_first_rather_than_scrambling_history(self):
        assert _quarter_sort_key("garbage") == (0, 0)
        assert _quarter_sort_key("") == (0, 0)
        assert _quarter_sort_key("garbage") < _quarter_sort_key("2021-Q1")
