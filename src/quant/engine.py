"""Vectorized daily backtest engine.

A strategy produces a *target position* per day in [-1, 1] (fraction of equity,
negative = short), computed from data available at that day's close. The
engine lags it by `delay` bars before it earns returns, so a signal can never
trade on information it did not have.

With delay=1 the position is entered at the close of the signal bar (like a
market-on-close order). Use delay=2 for a stricter "next day's close" fill.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import metrics


@dataclass
class BacktestResult:
    returns: pd.Series
    equity: pd.Series
    position: pd.Series
    costs: pd.Series

    def stats(self, rf: float = 0.0) -> dict:
        return metrics.summary(self.returns, self.position, rf=rf)


def run(
    prices: pd.DataFrame,
    signal: pd.Series,
    cost_bps: float = 1.0,
    slippage_bps: float = 2.0,
    delay: int = 1,
    initial_capital: float = 100_000.0,
) -> BacktestResult:
    """Simulate holding `signal` on `prices['close']`.

    cost_bps and slippage_bps are charged on every unit of turnover
    (going 0 -> 1 costs once, 1 -> -1 costs twice).
    """
    if delay < 1:
        raise ValueError("delay must be >= 1 to avoid look-ahead bias")
    close = prices["close"]
    target = signal.reindex(close.index).fillna(0.0).clip(-1.0, 1.0)
    position = target.shift(delay).fillna(0.0)
    asset_ret = close.pct_change().fillna(0.0)

    turnover = position.diff().abs()
    turnover.iloc[0] = abs(position.iloc[0])
    costs = turnover * (cost_bps + slippage_bps) / 1e4

    returns = position * asset_ret - costs
    equity = initial_capital * (1 + returns).cumprod()
    return BacktestResult(returns=returns, equity=equity, position=position, costs=costs)
