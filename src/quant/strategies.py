"""Example strategies. Each takes daily bars and returns a target position series.

Add your own by writing a function with the same shape and registering it in
STRATEGIES (and optionally PARAM_GRIDS for walk-forward optimization).
Use only causal calculations (rolling, ewm, shift(+n)) — never shift(-n).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def buy_and_hold(prices: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=prices.index)


def sma_cross(prices: pd.DataFrame, fast: int = 50, slow: int = 200, allow_short: bool = False) -> pd.Series:
    """Long when the fast SMA is above the slow SMA (classic trend filter)."""
    close = prices["close"]
    f, s = close.rolling(fast).mean(), close.rolling(slow).mean()
    signal = pd.Series(np.where(f > s, 1.0, -1.0 if allow_short else 0.0), index=close.index)
    return signal.where(s.notna(), 0.0)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def rsi_mean_reversion(
    prices: pd.DataFrame,
    period: int = 2,
    entry: float = 10,
    exit: float = 70,
    trend: int = 200,
) -> pd.Series:
    """Buy short-term oversold dips while above the long-term trend; exit on bounce."""
    close = prices["close"]
    r = rsi(close, period)
    uptrend = close > close.rolling(trend).mean()
    signal = pd.Series(np.nan, index=close.index)
    signal[(r < entry) & uptrend] = 1.0
    signal[(r > exit) | ~uptrend] = 0.0
    return signal.ffill().fillna(0.0)


def momentum(prices: pd.DataFrame, lookback: int = 252, skip: int = 21) -> pd.Series:
    """Time-series momentum: long when the trailing return (skipping the last month) is positive."""
    close = prices["close"]
    past = close.shift(skip) / close.shift(lookback) - 1
    return (past > 0).astype(float).where(past.notna(), 0.0)


STRATEGIES = {
    "buy_and_hold": buy_and_hold,
    "sma_cross": sma_cross,
    "rsi_mean_reversion": rsi_mean_reversion,
    "momentum": momentum,
}

PARAM_GRIDS = {
    "sma_cross": {"fast": [10, 20, 50, 100], "slow": [100, 150, 200, 250]},
    "rsi_mean_reversion": {"period": [2, 3, 5], "entry": [5, 10, 20], "exit": [60, 70, 80]},
    "momentum": {"lookback": [63, 126, 252], "skip": [0, 21]},
}
