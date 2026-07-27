"""Tests for the live Screener.in parser.

The fixture mirrors the real page markup verified against a live public company page:
sections keyed by id, tables classed data-table, headers carrying data-date-key, labels in
td.text wrapped in a button with a trailing '+', values in span.number, and the
classification rendered as nested /market/ links.
"""

from __future__ import annotations

import pytest

from nse_screener.stage2.params import classify_tailwind_sector
from nse_screener.stage2.screener_client import (
    ScreenerParseError,
    blended_eps_cagr,
    parse_company_page,
    parse_data_table,
    parse_growth_table,
    parse_industry_chain,
    parse_top_ratios,
)

TOP_RATIOS = """
<ul id="top-ratios">
  <li><span class="name">Market Cap</span>
      <span class="nowrap value">₹ <span class="number">3,09,194</span> Cr.</span></li>
  <li><span class="name">Current Price</span>
      <span class="nowrap value">₹ <span class="number">4,624</span></span></li>
  <li><span class="name">High / Low</span>
      <span class="nowrap value">₹ <span class="number">5,675</span> / <span class="number">3,046</span></span></li>
  <li><span class="name">Stock P/E</span>
      <span class="nowrap value"><span class="number">34.1</span></span></li>
  <li><span class="name">Book Value</span>
      <span class="nowrap value">₹ <span class="number">611</span></span></li>
  <li><span class="name">Dividend Yield</span>
      <span class="nowrap value"><span class="number">0.97</span> %</span></li>
  <li><span class="name">ROCE</span>
      <span class="nowrap value"><span class="number">32.4</span> %</span></li>
  <li><span class="name">ROE</span>
      <span class="nowrap value"><span class="number">22.2</span> %</span></li>
</ul>
"""


def data_table(rows: dict[str, list[str]], dates: list[str]) -> str:
    head = "".join(f'<th class="" data-date-key="{d}"> {_label(d)} </th>' for d in dates)
    body = ""
    for label, values in rows.items():
        cells = "".join(f'<td class=""> {v} </td>' for v in values)
        body += (
            f'<tr class="stripe"><td class="text">'
            f'<button class="button-plain">{label}&nbsp;<span class="blue-icon">+</span>'
            f"</button></td>{cells}</tr>"
        )
    return (
        '<table class="data-table responsive-text-nowrap">'
        f"<thead><tr><th class='text'></th>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


def _label(iso: str) -> str:
    month = {"03": "Mar", "06": "Jun", "09": "Sep", "12": "Dec"}[iso[5:7]]
    return f"{month} {iso[:4]}"


QUARTER_DATES = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]
FY_DATES = ["2023-03-31", "2024-03-31", "2025-03-31", "2026-03-31"]

PAGE = f"""
<html><body>
<h1>Hindustan Aeronautics Ltd</h1>
{TOP_RATIOS}
<div class="peers">
  <a href="/market/IN05/">Industrials</a>
  <a href="/market/IN05/IN0502/">Capital Goods</a>
  <a href="/market/IN05/IN0502/IN050201/">Aerospace &amp; Defense</a>
</div>
<section id="quarters">{data_table(
    {"Sales": ["6,957", "7,004", "8,236", "14,769"],
     "Net Profit": ["1,437", "1,510", "1,633", "4,496"]}, QUARTER_DATES)}</section>
<section id="profit-loss">{data_table(
    {"Sales": ["26,928", "30,381", "30,981", "37,032"],
     "Net Profit": ["5,828", "7,595", "8,317", "9,076"]}, FY_DATES)}</section>
<section id="balance-sheet">{data_table(
    {"Equity Capital": ["669", "669", "669", "669"],
     "Reserves": ["21,013", "28,395", "34,113", "40,192"]}, FY_DATES)}</section>
<section id="cash-flow">{data_table(
    {"Cash from Operating Activity": ["6,012", "8,226", "13,645", "10,920"],
     "Free Cash Flow": ["4,800", "6,479", "11,892", "8,451"]}, FY_DATES)}</section>
<section id="ratios">{data_table(
    {"ROCE %": ["31", "39", "34", "32"],
     "Debtor Days": ["70", "55", "55", "45"]}, FY_DATES)}</section>
<section id="shareholding">{data_table(
    {"Promoters": ["71.64", "71.64", "71.64", "71.64"],
     "FIIs": ["11.60", "10.80", "9.90", "9.34"],
     "DIIs": ["10.20", "10.90", "11.50", "11.90"],
     "Government": ["0.00", "0.00", "0.00", "0.00"],
     "Public": ["6.56", "6.66", "6.96", "7.12"]}, QUARTER_DATES)}</section>
<table><tr><td>Compounded Profit Growth</td><td></td></tr>
<tr><td>10 Years:</td><td>16%</td></tr><tr><td>5 Years:</td><td>23%</td></tr>
<tr><td>3 Years:</td><td>17%</td></tr><tr><td>TTM:</td><td>9%</td></tr></table>
</body></html>
"""


class TestTopRatios:
    def test_reads_values_from_span_number(self):
        ratios = parse_top_ratios(PAGE)
        assert ratios["market cap"] == 309194.0
        assert ratios["current price"] == 4624.0
        assert ratios["book value"] == 611.0
        assert ratios["dividend yield"] == 0.97

    def test_label_containing_a_slash_is_not_dropped(self):
        """Regression: 'Stock P/E' was skipped by a slash check meant for 'High / Low'."""
        assert parse_top_ratios(PAGE)["stock p/e"] == 34.1

    def test_two_number_value_takes_the_first(self):
        assert parse_top_ratios(PAGE)["high / low"] == 5675.0

    def test_missing_box_returns_empty(self):
        assert parse_top_ratios("<html></html>") == {}


class TestDataTable:
    def test_periods_come_from_date_keys(self):
        table = parse_data_table(f"<section>{data_table({'Sales': ['1', '2']}, QUARTER_DATES[:2])}</section>")
        assert table.periods == ["2025-06-30", "2025-09-30"]

    def test_label_strips_nbsp_and_expander_glyph(self):
        table = parse_data_table(f"<section>{data_table({'Sales': ['1', '2']}, QUARTER_DATES[:2])}</section>")
        assert "Sales" in table.rows

    def test_row_lookup_is_case_insensitive_with_aliases(self):
        table = parse_data_table(f"<section>{data_table({'Net Profit': ['5', '6']}, QUARTER_DATES[:2])}</section>")
        assert table.row("net profit") == [5.0, 6.0]
        assert table.row("Profit after tax", "Net Profit") == [5.0, 6.0]

    def test_unknown_row_returns_none(self):
        table = parse_data_table(f"<section>{data_table({'Sales': ['1']}, QUARTER_DATES[:1])}</section>")
        assert table.row("Nonexistent") is None

    def test_missing_table_raises(self):
        with pytest.raises(ScreenerParseError):
            parse_data_table("<section>no table here</section>")


class TestIndustryChain:
    def test_orders_broad_to_specific_by_href_depth(self):
        assert parse_industry_chain(PAGE) == ["Industrials", "Capital Goods", "Aerospace & Defense"]

    def test_chain_feeds_sector_classification(self):
        chain = " > ".join(parse_industry_chain(PAGE))
        assert classify_tailwind_sector(chain) == "Defense & strategic manufacturing"


class TestGrowthTables:
    def test_reads_horizon_rows(self):
        growth = parse_growth_table(PAGE, "Compounded Profit Growth")
        assert growth["5 years"] == 23.0
        assert growth["3 years"] == 17.0

    def test_blended_eps_cagr_averages_3y_and_5y(self):
        assert blended_eps_cagr(PAGE) == pytest.approx(20.0)

    def test_missing_table_returns_empty(self):
        assert parse_growth_table("<html></html>", "Compounded Profit Growth") == {}


@pytest.fixture(scope="module")
def company():
    return parse_company_page("HAL", PAGE)


class TestCompanyPage:
    def test_identity_and_classification(self, company):
        assert company.symbol == "HAL"
        assert company.name == "Hindustan Aeronautics Ltd"
        assert company.industry == "Aerospace & Defense"
        assert company.industry_path.startswith("Industrials >")

    def test_pb_is_price_over_book_value_per_share(self, company):
        assert company.pb == pytest.approx(4624 / 611, rel=1e-3)

    def test_quarterly_series_sorted_and_complete(self, company):
        quarters = company.sorted_quarterly()
        assert [q.quarter for q in quarters] == QUARTER_DATES
        assert quarters[-1].net_sales == 14769.0

    def test_roe_is_derived_from_the_balance_sheet(self, company):
        """Screener publishes ROCE per year but not ROE, so P2 needs it computed."""
        latest = company.sorted_annual()[-1]
        expected = 9076.0 / (669.0 + 40192.0) * 100
        assert latest.roe_pct == pytest.approx(expected, rel=1e-3)

    def test_capex_derived_from_published_free_cash_flow(self, company):
        latest = company.sorted_annual()[-1]
        assert latest.cfo == 10920.0
        assert latest.capex == pytest.approx(10920.0 - 8451.0)
        assert latest.fcf == pytest.approx(8451.0)

    def test_receivable_days_and_roce(self, company):
        latest = company.sorted_annual()[-1]
        assert latest.receivable_days == 45.0
        assert latest.roce_pct == 32.0

    def test_shareholding_series(self, company):
        latest = company.sorted_shareholding()[-1]
        assert latest.promoter_pct == 71.64
        assert latest.fii_pct == 9.34
        assert latest.dii_pct == 11.90

    def test_pledge_is_absent_not_invented(self, company):
        """The shareholding table exposes no pledge row - P6 must see None, not 0."""
        assert all(s.pledge_pct is None for s in company.shareholding)

    def test_non_company_page_raises(self):
        with pytest.raises(ScreenerParseError):
            parse_company_page("XXX", "<html><body>404</body></html>")


class TestGovernmentHoldingBecomesPromoter:
    """A PSU reports its stake under Government, not Promoters. P6's PSU exception needs
    that stake to arrive as the promoter holding, with is_psu set."""

    def test_government_stake_fills_an_empty_promoter_row(self):
        psu_page = PAGE.replace(
            f'<section id="shareholding">{data_table(
                {"Promoters": ["71.64", "71.64", "71.64", "71.64"],
                 "FIIs": ["11.60", "10.80", "9.90", "9.34"],
                 "DIIs": ["10.20", "10.90", "11.50", "11.90"],
                 "Government": ["0.00", "0.00", "0.00", "0.00"],
                 "Public": ["6.56", "6.66", "6.96", "7.12"]}, QUARTER_DATES)}</section>',
            f'<section id="shareholding">{data_table(
                {"Promoters": ["0.00", "0.00", "0.00", "0.00"],
                 "FIIs": ["11.60", "10.80", "9.90", "9.34"],
                 "DIIs": ["10.20", "10.90", "11.50", "11.90"],
                 "Government": ["56.92", "56.92", "56.92", "56.92"],
                 "Public": ["6.56", "6.66", "6.96", "7.12"]}, QUARTER_DATES)}</section>',
        )
        company = parse_company_page("PSU", psu_page)
        assert company.is_psu is True
        assert company.sorted_shareholding()[-1].promoter_pct == 56.92

    def test_ordinary_company_is_not_marked_psu(self, company):
        assert company.is_psu is False
