"""Backtesting (spec section 11) - a pandas forward-return study, not a trade simulator."""

from .forward_returns import (
    SURVIVORSHIP_CAVEAT,
    BacktestResult,
    forward_returns,
    replay_stage1,
    run_backtest,
)

__all__ = [
    "run_backtest",
    "replay_stage1",
    "forward_returns",
    "BacktestResult",
    "SURVIVORSHIP_CAVEAT",
]
