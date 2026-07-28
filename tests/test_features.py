"""Tests for the six feature additions: thresholds, watchlist, symbol input, sectors,
industry ranking and concall extraction."""

from __future__ import annotations

import json

import pytest

from nse_screener import config
from nse_screener.models import ParamResult, ScoredStock, Verdict
from nse_screener.sectors import build_sector_views, is_sector_leader, review_status
from nse_screener.stage1.symbols import hits_from_text, load_symbol_file, parse_symbols
from nse_screener.stage2.screener_client import parse_concall_links, parse_industry_table
from nse_screener.thresholds import build_overrides, default_value, validate
from nse_screener.watchlist import Watchlist, WatchState


# ------------------------------------------------------------------ thresholds

class TestThresholdOverrides:
    @pytest.fixture(autouse=True)
    def isolated_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "USER_THRESHOLDS", tmp_path / "user_thresholds.json")
        config.settings.cache_clear()
        yield
        config.settings.cache_clear()

    def test_override_changes_the_live_setting(self):
        assert config.settings()["p2_capital_efficiency"]["roce_min_pct"] == 15.0
        config.save_overrides({"p2_capital_efficiency": {"roce_min_pct": 25.0}})
        assert config.settings()["p2_capital_efficiency"]["roce_min_pct"] == 25.0

    def test_override_is_a_deep_merge_not_a_replacement(self):
        config.save_overrides({"p2_capital_efficiency": {"roce_min_pct": 25.0}})
        section = config.settings()["p2_capital_efficiency"]
        assert section["roce_min_pct"] == 25.0
        assert "roe_min_pct" in section, "untouched keys must survive"

    def test_reset_restores_defaults(self):
        config.save_overrides({"p2_capital_efficiency": {"roce_min_pct": 25.0}})
        config.reset_overrides()
        assert config.settings()["p2_capital_efficiency"]["roce_min_pct"] == 15.0

    def test_corrupt_override_file_falls_back_to_defaults(self):
        config.USER_THRESHOLDS.write_text("{not valid json", encoding="utf-8")
        config.settings.cache_clear()
        assert config.settings()["p2_capital_efficiency"]["roce_min_pct"] == 15.0

    def test_values_equal_to_default_are_not_written(self):
        overrides = build_overrides(
            {
                ("p2_capital_efficiency", "roce_min_pct"): default_value(
                    "p2_capital_efficiency", "roce_min_pct"
                ),
                ("p2_capital_efficiency", "roe_min_pct"): 20.0,
            }
        )
        assert overrides == {"p2_capital_efficiency": {"roe_min_pct": 20.0}}

    def test_saved_file_is_readable_json(self):
        config.save_overrides({"tiers": {"elite_compounder_min_pct": 95.0}})
        assert json.loads(config.USER_THRESHOLDS.read_text()) == {
            "tiers": {"elite_compounder_min_pct": 95.0}
        }


class TestThresholdValidation:
    def test_inverted_band_is_rejected(self):
        problems = validate(
            {
                ("p2_capital_efficiency", "partial_band_low_pct"): 20.0,
                ("p2_capital_efficiency", "partial_band_high_pct"): 10.0,
            }
        )
        assert problems and "above" in problems[0]

    def test_ordered_band_is_accepted(self):
        assert validate(
            {
                ("p2_capital_efficiency", "partial_band_low_pct"): 12.0,
                ("p2_capital_efficiency", "partial_band_high_pct"): 15.0,
            }
        ) == []

    def test_tier_cutoffs_must_descend(self):
        problems = validate(
            {
                ("tiers", "elite_compounder_min_pct"): 60.0,
                ("tiers", "quality_grower_min_pct"): 70.0,
                ("tiers", "watchlist_min_pct"): 50.0,
            }
        )
        assert problems and "descend" in problems[0]

    def test_sensible_tiers_pass(self):
        assert validate(
            {
                ("tiers", "elite_compounder_min_pct"): 90.0,
                ("tiers", "quality_grower_min_pct"): 70.0,
                ("tiers", "watchlist_min_pct"): 50.0,
            }
        ) == []


# ------------------------------------------------------------------- watchlist

class TestWatchlist:
    def test_states_round_trip_through_disk(self, tmp_path):
        path = tmp_path / "watchlist.json"
        w = Watchlist.load(path)
        w.set_state("hal", WatchState.WATCHING, tier="ELITE COMPOUNDER", score="14/14")
        w.set_state("VSSL", WatchState.REMOVED)
        w.save()

        reloaded = Watchlist.load(path)
        assert reloaded.state_of("HAL") is WatchState.WATCHING
        assert reloaded.state_of("VSSL") is WatchState.REMOVED
        assert reloaded.entries["HAL"].tier_when_added == "ELITE COMPOUNDER"

    def test_symbols_are_case_insensitive(self, tmp_path):
        w = Watchlist.load(tmp_path / "w.json")
        w.set_state("hal", WatchState.WATCHING)
        assert w.state_of("HAL") is WatchState.WATCHING
        assert w.state_of("Hal") is WatchState.WATCHING

    def test_removed_symbols_are_reported_for_exclusion(self, tmp_path):
        w = Watchlist.load(tmp_path / "w.json")
        w.set_state("A", WatchState.REMOVED)
        w.set_state("B", WatchState.WATCHING)
        assert w.removed_symbols() == {"A"}

    def test_removed_is_distinct_from_never_seen(self, tmp_path):
        """A dismissed stock must stay dismissed, not resurface as new."""
        path = tmp_path / "w.json"
        w = Watchlist.load(path)
        w.set_state("X", WatchState.REMOVED)
        w.save()
        assert Watchlist.load(path).state_of("X") is WatchState.REMOVED
        assert Watchlist.load(path).state_of("NEVERSEEN") is WatchState.NONE

    def test_notes_persist(self, tmp_path):
        path = tmp_path / "w.json"
        w = Watchlist.load(path)
        w.set_note("HAL", "defence order book")
        w.save()
        assert Watchlist.load(path).entries["HAL"].note == "defence order book"

    def test_corrupt_file_starts_clean(self, tmp_path):
        path = tmp_path / "w.json"
        path.write_text("{broken", encoding="utf-8")
        assert Watchlist.load(path).entries == {}

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert Watchlist.load(tmp_path / "absent.json").watching() == []


# --------------------------------------------------------------- symbol input

class TestSymbolParsing:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("HAL BEL RELIANCE", ["HAL", "BEL", "RELIANCE"]),
            ("HAL,BEL,RELIANCE", ["HAL", "BEL", "RELIANCE"]),
            ("HAL\nBEL\nRELIANCE", ["HAL", "BEL", "RELIANCE"]),
            ("hal bel", ["HAL", "BEL"]),
            ("M&M BAJAJ-AUTO", ["M&M", "BAJAJ-AUTO"]),
        ],
    )
    def test_separators_and_case(self, text, expected):
        assert parse_symbols(text)[0] == expected

    def test_duplicates_collapse(self):
        assert parse_symbols("HAL HAL hal")[0] == ["HAL"]

    def test_header_words_dropped(self):
        assert parse_symbols("Symbol\nHAL\nBEL")[0] == ["HAL", "BEL"]

    def test_junk_is_reported_not_silently_dropped(self):
        accepted, rejected = parse_symbols("HAL bogus!!token BEL")
        assert accepted == ["HAL", "BEL"]
        assert rejected == ["BOGUS!!TOKEN"]

    def test_empty_input(self):
        assert parse_symbols("") == ([], [])

    def test_manual_hits_are_flagged_as_price_backfilled(self):
        hits, _ = hits_from_text("HAL BEL")
        assert len(hits) == 2
        assert all(h.price_backfilled for h in hits)

    def test_text_file_round_trip(self, tmp_path):
        path = tmp_path / "symbols.txt"
        path.write_text("HAL\nBEL\n\nRELIANCE\n", encoding="utf-8")
        hits, rejected = load_symbol_file(path)
        assert [h.symbol for h in hits] == ["HAL", "BEL", "RELIANCE"]
        assert rejected == []


# ------------------------------------------------------------------- sectors

def make_stock(symbol: str, sector: str, rank: int | None) -> ScoredStock:
    verdict = Verdict.YES if (rank is not None and rank <= 3) else Verdict.NO
    result = ParamResult(
        "P8",
        verdict,
        "",
        {"Tailwind sector": sector, "Rank by market cap": rank, "Companies in industry": 25},
    )
    stock = ScoredStock(symbol=symbol, name=symbol, results={"P8": result})
    stock.tailwind_sector = sector
    stock.sector_tailwind = verdict is Verdict.YES
    return stock


class TestSectorViews:
    def test_leader_requires_both_sector_and_top3(self):
        leader = make_stock("HAL", "Defense & strategic manufacturing", 1)
        laggard = make_stock("XYZ", "Defense & strategic manufacturing", 9)
        assert is_sector_leader(leader) is True
        assert is_sector_leader(laggard) is False

    def test_views_split_leaders_from_plain_members(self):
        stocks = [
            make_stock("HAL", "Defense & strategic manufacturing", 1),
            make_stock("XYZ", "Defense & strategic manufacturing", 9),
        ]
        views = {v.name: v for v in build_sector_views(stocks)}
        defence = views["Defense & strategic manufacturing"]
        assert defence.leaders == ["HAL"]
        assert set(defence.matched) == {"HAL", "XYZ"}

    def test_every_configured_sector_gets_a_view_with_rationale(self):
        views = build_sector_views([])
        assert len(views) == 5
        assert all(v.rationale for v in views), "each sector needs its reasoning shown"

    def test_review_status_flags_a_stale_list(self, monkeypatch):
        from datetime import date

        message, stale = review_status(today=date(2030, 1, 1))
        assert stale is True
        assert "Overdue" in message

    def test_review_status_fresh(self):
        from datetime import date

        message, stale = review_status(today=date(2026, 7, 28))
        assert stale is False
        assert "last reviewed" in message


# --------------------------------------------- industry ranking and concalls

INDUSTRY_HTML = """
<table><tr><th>S.No.</th><th>Name</th><th>CMP Rs.</th><th>P/E</th><th>Mar Cap Rs.Cr.</th></tr>
<tr><td>1.</td><td><a href="/company/HAL/consolidated/">Hind.Aeronautics</a></td>
    <td>4563</td><td>33.4</td><td>3,05,175.42</td></tr>
<tr><td>2.</td><td><a href="/company/BEL/consolidated/">Bharat Electron</a></td>
    <td>394</td><td>46.9</td><td>2,88,005.29</td></tr>
<tr><td>3.</td><td><a href="/company/BDL/">Bharat Dynamics</a></td>
    <td>1243</td><td>108.4</td><td>45,563.73</td></tr>
</table>
"""


class TestIndustryTable:
    def test_symbols_come_from_the_link_not_the_abbreviated_name(self):
        rows = parse_industry_table(INDUSTRY_HTML)
        assert [symbol for symbol, _ in rows] == ["HAL", "BEL", "BDL"]

    def test_market_caps_parse_with_indian_grouping(self):
        rows = dict(parse_industry_table(INDUSTRY_HTML))
        assert rows["HAL"] == pytest.approx(305175.42)

    def test_missing_market_cap_column_returns_empty(self):
        assert parse_industry_table("<table><tr><th>Name</th></tr></table>") == []

    def test_no_table_returns_empty(self):
        assert parse_industry_table("<html>nothing</html>") == []


CONCALL_HTML = """
<section id="documents">
  <ul><li>Aug 2026 <a href="https://bse.example/ann.pdf">Board Meeting Outcome</a></li></ul>
  <ul class="list-links">
    <li><div>May 2026</div>
        <a class="concall-link" href="https://bse.example/t1.pdf">Transcript</a>
        <a class="concall-link" href="https://youtu.be/abc">REC</a></li>
    <li><div>May 2025</div>
        <a class="concall-link" href="https://bse.example/t2.pdf">Transcript</a></li>
  </ul>
</section>
"""


class TestConcallLinks:
    def test_only_concall_rows_are_returned(self):
        links = parse_concall_links(CONCALL_HTML)
        kinds = {kind for _d, kind, _u in links}
        assert "Board Meeting Outcome" not in kinds, "announcements are not earnings calls"
        assert kinds == {"Transcript", "REC"}

    def test_dates_are_attached(self):
        links = parse_concall_links(CONCALL_HTML)
        assert links[0][0] == "May 2026"

    def test_newest_first(self):
        dates = [d for d, _k, _u in parse_concall_links(CONCALL_HTML)]
        assert dates[0] == "May 2026"
        assert dates[-1] == "May 2025"

    def test_no_documents_section(self):
        assert parse_concall_links("<html></html>") == []
