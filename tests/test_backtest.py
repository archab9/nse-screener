"""Tests for the forward-return study (spec section 11)."""

from __future__ import annotations

import pandas as pd
import pytest

from nse_screener.backtest.forward_returns import (
    SURVIVORSHIP_CAVEAT,
    forward_returns,
    replay_stage1,
    run_backtest,
    summarise,
)


def make_ohlcv(symbol="TEST", days=400, base=100.0, volume=100_000) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "close": [base * (1.0005**i) for i in range(days)],
            "volume": [volume] * days,
        }
    )


class TestStage1Replay:
    def test_no_trigger_on_flat_volume_and_price(self):
        assert replay_stage1(make_ohlcv()).empty

    def test_volume_spike_with_price_jump_triggers(self):
        frame = make_ohlcv()
        spike = 300
        frame.loc[spike, "volume"] = 100_000 * 10
        frame.loc[spike, "close"] = frame.loc[spike - 1, "close"] * 1.09
        triggers = replay_stage1(frame)
        assert len(triggers) == 1
        assert triggers.iloc[0]["date"] == frame.loc[spike, "date"]

    def test_volume_spike_without_price_move_does_not_trigger(self):
        frame = make_ohlcv()
        frame.loc[300, "volume"] = 100_000 * 10
        assert replay_stage1(frame).empty

    def test_price_jump_without_volume_does_not_trigger(self):
        frame = make_ohlcv()
        frame.loc[300, "close"] = frame.loc[299, "close"] * 1.09
        assert replay_stage1(frame).empty

    def test_penny_stock_below_close_floor_is_filtered(self):
        frame = make_ohlcv(base=12.0)
        frame.loc[300, "volume"] = 100_000 * 10
        frame.loc[300, "close"] = frame.loc[299, "close"] * 1.09
        assert replay_stage1(frame).empty

    def test_illiquid_name_below_volume_floor_is_filtered(self):
        frame = make_ohlcv(volume=5_000)
        frame.loc[300, "volume"] = 5_000 * 10
        frame.loc[300, "close"] = frame.loc[299, "close"] * 1.09
        assert replay_stage1(frame).empty

    def test_first_49_days_never_trigger_for_want_of_an_sma(self):
        frame = make_ohlcv()
        frame.loc[10, "volume"] = 100_000 * 10
        frame.loc[10, "close"] = frame.loc[9, "close"] * 1.09
        assert replay_stage1(frame).empty


class TestForwardReturns:
    def test_horizons_are_attached(self):
        frame = make_ohlcv()
        frame.loc[100, "volume"] = 100_000 * 10
        frame.loc[100, "close"] = frame.loc[99, "close"] * 1.09
        trades = forward_returns(frame, replay_stage1(frame))
        for label in ("1m", "3m", "6m", "12m"):
            assert f"fwd_{label}" in trades.columns

    def test_return_maths(self):
        dates = pd.bdate_range("2024-01-01", periods=100)
        frame = pd.DataFrame(
            {"symbol": "X", "date": dates, "close": [100.0] * 100, "volume": [1] * 100}
        )
        frame.loc[50, "close"] = 200.0  # +100% exactly 21 bars after bar 29
        triggers = pd.DataFrame({"symbol": ["X"], "date": [dates[29]]})
        trades = forward_returns(frame, triggers, {"1m": 21})
        assert trades.iloc[0]["fwd_1m"] == pytest.approx(100.0)

    def test_horizon_past_the_data_end_is_nan_not_zero(self):
        frame = make_ohlcv(days=260)
        frame.loc[255, "volume"] = 100_000 * 10
        frame.loc[255, "close"] = frame.loc[254, "close"] * 1.09
        trades = forward_returns(frame, replay_stage1(frame))
        assert pd.isna(trades.iloc[0]["fwd_12m"])


class TestSummary:
    def test_reports_distribution_not_just_the_mean(self):
        trades = pd.DataFrame({"fwd_1m": [10.0, -5.0, 20.0, -2.0, 8.0]})
        summary = summarise(trades, {"1m": 21})
        row = summary.iloc[0]
        assert row["n"] == 5
        assert row["hit_rate_%"] == 60.0
        for column in ("median_%", "p25_%", "p75_%", "worst_%", "best_%"):
            assert column in summary.columns

    def test_empty_horizon_does_not_crash(self):
        summary = summarise(pd.DataFrame({"fwd_1m": [float("nan")] * 3}), {"1m": 21})
        assert summary.iloc[0]["n"] == 0


class TestCaveatsAreNotFootnotes:
    """Spec section 11 requires the survivorship caveat alongside every result."""

    def test_result_carries_the_caveat(self):
        result = run_backtest(make_ohlcv())
        assert result.caveat == SURVIVORSHIP_CAVEAT
        assert "SURVIVORSHIP BIAS" in result.caveat

    def test_printed_output_states_it_twice(self):
        text = str(run_backtest(make_ohlcv()))
        assert text.count("SURVIVORSHIP BIAS") >= 2

    def test_look_ahead_lag_is_named(self):
        assert "LOOK-AHEAD" in SURVIVORSHIP_CAVEAT

    def test_unreplayed_stage1_conditions_are_flagged(self):
        result = run_backtest(make_ohlcv())
        joined = " ".join(result.warnings)
        assert "promoter" in joined.lower() and "debt/equity" in joined.lower()

    def test_absence_of_a_stage2_filter_is_stated(self):
        result = run_backtest(make_ohlcv())
        assert any("No stage-2 score filter" in w for w in result.warnings)


class TestEmptyInput:
    def test_no_triggers_produces_an_empty_but_valid_result(self):
        result = run_backtest(make_ohlcv())
        assert result.trigger_count == 0
        assert result.trades.empty
        assert "SURVIVORSHIP BIAS" in str(result)
