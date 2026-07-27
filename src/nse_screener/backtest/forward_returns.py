"""Replay stage 1 over history and measure forward returns (spec section 11).

Deliberately plain pandas. The spec is explicit that a signal-to-forward-return study
answers the question being asked, and that vectorbt is only warranted if a later
iteration needs position sizing or a benchmark comparison.

Two biases are structural, not fixable at this data tier. Both are attached to every
result object rather than documented once and forgotten:
  - Survivorship: Screener.in only carries currently-listed companies, so anything that
    delisted, merged or went to zero inside the window is invisible. Results therefore
    flatter the strategy.
  - Look-ahead: quarterly results land 4-8 weeks after quarter end. REPORTING_LAG_DAYS
    holds fundamentals back accordingly, but it is an approximation of a real filing date,
    not the filing date itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from ..config import settings

# Spec section 11: results typically land 4-8 weeks after quarter end. The conservative
# end of that range is used so the replay never scores on data that was not yet public.
REPORTING_LAG_DAYS = 56

FORWARD_HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}

SURVIVORSHIP_CAVEAT = (
    "SURVIVORSHIP BIAS: this test only sees companies still listed today. Names that "
    "delisted, merged or went to zero during the window are absent, so returns here are "
    "biased upward and are NOT what the strategy would have produced live. "
    "LOOK-AHEAD: fundamentals are held back by an assumed "
    f"{REPORTING_LAG_DAYS}-day reporting lag, which approximates rather than reproduces "
    "actual filing dates."
)


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    summary: pd.DataFrame
    caveat: str = SURVIVORSHIP_CAVEAT
    universe_size: int = 0
    trigger_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [
            "=" * 78,
            "STAGE-1 FORWARD RETURN STUDY",
            "=" * 78,
            self.caveat,
            "",
            f"Universe: {self.universe_size} symbols | Triggers: {self.trigger_count}",
            "",
            self.summary.to_string() if not self.summary.empty else "(no triggers)",
            "",
            self.caveat,  # repeated deliberately - spec forbids relegating it to a footnote
        ]
        return "\n".join(parts)


def replay_stage1(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Find every date each symbol would have fired the stage-1 scan.

    `ohlcv` needs columns: symbol, date, close, volume. Thresholds come from
    config/settings.json so the replay and the live scan cannot drift apart.

    Only the price/volume conditions are replayable here. Promoter holding and debt/equity
    are point-in-time fundamentals that the free data tier cannot reconstruct historically;
    they are reported as unreplayed rather than silently dropped.
    """
    cfg = settings()["stage1"]
    frame = ohlcv.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["symbol", "date"])

    grouped = frame.groupby("symbol", group_keys=False)
    frame["vol_sma50"] = grouped["volume"].transform(lambda s: s.rolling(50, min_periods=50).mean())
    frame["prev_close"] = grouped["close"].shift(1)
    frame["pct_change"] = (frame["close"] - frame["prev_close"]) / frame["prev_close"] * 100.0

    triggered = frame[
        (frame["volume"] > frame["vol_sma50"] * cfg["volume_sma_multiple"])
        & (frame["close"] > cfg["min_close"])
        & (frame["pct_change"] >= cfg["min_pct_change"])
        & (frame["vol_sma50"] >= cfg["min_volume_sma_50"])
        & (frame["volume"] > cfg["min_volume"])
    ]
    return triggered[["symbol", "date", "close", "volume", "pct_change"]].reset_index(drop=True)


def forward_returns(
    ohlcv: pd.DataFrame, triggers: pd.DataFrame, horizons: dict[str, int] | None = None
) -> pd.DataFrame:
    """Attach forward returns at each horizon to every trigger row."""
    horizons = horizons or FORWARD_HORIZONS
    frame = ohlcv.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)

    for label, bars in horizons.items():
        frame[f"fwd_{label}"] = (
            frame.groupby("symbol", group_keys=False)["close"].shift(-bars) / frame["close"] - 1.0
        ) * 100.0

    columns = ["symbol", "date"] + [f"fwd_{label}" for label in horizons]
    return triggers.merge(frame[columns], on=["symbol", "date"], how="left")


def summarise(trades: pd.DataFrame, horizons: dict[str, int] | None = None) -> pd.DataFrame:
    horizons = horizons or FORWARD_HORIZONS
    rows = []
    for label in horizons:
        column = f"fwd_{label}"
        if column not in trades:
            continue
        series = trades[column].dropna()
        if series.empty:
            rows.append({"horizon": label, "n": 0})
            continue
        rows.append(
            {
                "horizon": label,
                "n": int(series.size),
                "mean_%": round(float(series.mean()), 2),
                "median_%": round(float(series.median()), 2),
                "hit_rate_%": round(float((series > 0).mean() * 100), 1),
                "p25_%": round(float(series.quantile(0.25)), 2),
                "p75_%": round(float(series.quantile(0.75)), 2),
                "worst_%": round(float(series.min()), 2),
                "best_%": round(float(series.max()), 2),
            }
        )
    return pd.DataFrame(rows)


def run_backtest(
    ohlcv: pd.DataFrame,
    score_lookup: dict[tuple[str, date], int] | None = None,
    min_score: int | None = None,
) -> BacktestResult:
    """Full study: replay stage 1, optionally filter by a point-in-time stage-2 score.

    `score_lookup` maps (symbol, as-of date) to a core score already lagged by
    REPORTING_LAG_DAYS. It is optional because stage 1 is the part that backtests cleanly;
    stage 2 depends on per-company historical exports that must be gathered manually.
    """
    warnings = [
        "Stage-1 replay covers the price/volume conditions only. Promoter holding >=50% "
        "and debt/equity <=1.25 are point-in-time fundamentals not reconstructable from "
        "free data, so the historical trigger set is WIDER than the live scan's.",
    ]

    triggers = replay_stage1(ohlcv)
    trades = forward_returns(ohlcv, triggers)

    if score_lookup is not None and min_score is not None:
        def keep(row) -> bool:
            as_of = row["date"].date() - timedelta(days=REPORTING_LAG_DAYS)
            score = score_lookup.get((row["symbol"], as_of))
            return score is not None and score >= min_score

        before = len(trades)
        trades = trades[trades.apply(keep, axis=1)]
        warnings.append(
            f"Stage-2 score filter (>= {min_score}) applied with a {REPORTING_LAG_DAYS}-day "
            f"reporting lag: {before} triggers reduced to {len(trades)}."
        )
    else:
        warnings.append(
            "No stage-2 score filter applied - these are raw stage-1 signal returns."
        )

    return BacktestResult(
        trades=trades,
        summary=summarise(trades),
        universe_size=int(ohlcv["symbol"].nunique()),
        trigger_count=int(len(triggers)),
        warnings=warnings,
    )
