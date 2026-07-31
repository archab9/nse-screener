"""Regressions found during a verification pass over the assembled app.

Each test here corresponds to a defect that was live in the pushed branch.
"""

from __future__ import annotations

import json

import pytest

from nse_screener import config
from nse_screener.stage2.fundamentals import AnnualPoint, CompanyFundamentals, QuarterPoint
from nse_screener.stage2.params import evaluate_p1, evaluate_p2, evaluate_p3, evaluate_p4
from nse_screener.thresholds import build_overrides, default_value


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_THRESHOLDS", tmp_path / "user_thresholds.json")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


class TestCountSettingsTolerateFloats:
    """A float in a count-like setting crashed every run with

        TypeError: slice indices must be integers

    because QDoubleSpinBox writes 4.0 even at zero decimals, and params.py sliced with it.
    Config files also get hand-edited, so the read side must cope regardless.
    """

    def company(self) -> CompanyFundamentals:
        return CompanyFundamentals(
            symbol="X",
            quarterly=[QuarterPoint(f"202{i // 4}-Q{i % 4 + 1}", 100 + i, 10 + i) for i in range(12)],
            annual=[
                AnnualPoint(fy=2021 + i, roce_pct=18, roe_pct=17, cfo=100, capex=20,
                            pat=90, receivable_days=40)
                for i in range(5)
            ],
            market_cap_cr=1000,
        )

    @pytest.mark.parametrize(
        "section,key",
        [
            ("p1_record_financials", "lookback_quarters"),
            ("p2_capital_efficiency", "years_window"),
            ("p2_capital_efficiency", "years_required"),
            ("p3_fcf_quality", "years_window"),
            ("p3_fcf_quality", "years_required_yes"),
            ("p4_earnings_quality", "years_window"),
            ("p4_earnings_quality", "years_required"),
        ],
    )
    def test_float_count_does_not_crash(self, section, key):
        config.save_overrides({section: {key: float(default_value(section, key))}})
        company = self.company()
        for evaluate in (evaluate_p1, evaluate_p2, evaluate_p3, evaluate_p4):
            evaluate(company)  # must not raise

    def test_editor_writes_counts_as_int(self):
        overrides = build_overrides({("p2_capital_efficiency", "years_window"): 4.0})
        value = overrides["p2_capital_efficiency"]["years_window"]
        assert isinstance(value, int), f"count written as {type(value).__name__}"
        assert value == 4

    def test_editor_keeps_real_thresholds_as_float(self):
        overrides = build_overrides({("p2_capital_efficiency", "roce_min_pct"): 17.5})
        assert overrides["p2_capital_efficiency"]["roce_min_pct"] == 17.5


class TestOverridesAreUserStateNotProjectConfig:
    """The override file was written into the version-controlled config/ directory and
    got committed, silently changing P2's window from 5 years to 4 for anyone who
    checked the branch out."""

    def test_default_location_is_outside_the_repo(self, monkeypatch):
        monkeypatch.undo()
        config.settings.cache_clear()
        assert config.ROOT not in config.USER_THRESHOLDS.parents, (
            f"user state must not live inside the repo: {config.USER_THRESHOLDS}"
        )

    def test_gitignore_covers_the_legacy_location(self):
        text = (config.ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "config/user_thresholds.json" in text

    def test_saved_file_is_a_sparse_record_of_changes_only(self):
        config.save_overrides(build_overrides({
            ("p2_capital_efficiency", "roce_min_pct"): 17.5,
            ("p2_capital_efficiency", "roe_min_pct"): default_value(
                "p2_capital_efficiency", "roe_min_pct"
            ),
        }))
        saved = json.loads(config.USER_THRESHOLDS.read_text())
        assert saved == {"p2_capital_efficiency": {"roce_min_pct": 17.5}}
