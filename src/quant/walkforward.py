"""Walk-forward (out-of-sample) testing.

Each step picks the best parameters on a training window, then trades them on
the following, unseen test window. Stitching the test windows together gives
a return series much closer to what live trading would have produced than a
single backtest optimized over the whole history.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from . import engine, metrics


@dataclass
class WalkForwardResult:
    returns: pd.Series  # stitched out-of-sample returns
    position: pd.Series
    windows: pd.DataFrame  # one row per step: dates, chosen params, in/out-of-sample score

    def stats(self, rf: float = 0.0) -> dict:
        return metrics.summary(self.returns, self.position, rf=rf)


def walk_forward(
    prices: pd.DataFrame,
    strategy: Callable[..., pd.Series],
    param_grid: dict[str, list],
    train_years: int = 5,
    test_years: int = 1,
    score: str = "sharpe",
    **run_kwargs,
) -> WalkForwardResult:
    keys = list(param_grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*param_grid.values())]
    idx = prices.index

    # Indicators are causal, so one full-history run per combo is leak-free;
    # each window just slices it.
    runs = {}
    for combo in combos:
        if "fast" in combo and "slow" in combo and combo["fast"] >= combo["slow"]:
            continue
        runs[tuple(combo.items())] = engine.run(prices, strategy(prices, **combo), **run_kwargs)

    oos_ret, oos_pos, rows = [], [], []
    train_start = idx[0]
    while True:
        train_end = train_start + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(years=test_years)
        if train_end >= idx[-1]:
            break
        train = (idx >= train_start) & (idx < train_end)
        test = (idx >= train_end) & (idx < test_end)

        best_key, best_score = None, -float("inf")
        for key, res in runs.items():
            s = metrics.summary(res.returns[train]).get(score, float("nan"))
            if pd.notna(s) and s > best_score:
                best_key, best_score = key, s
        if best_key is None:
            break

        best = runs[best_key]
        oos_ret.append(best.returns[test])
        oos_pos.append(best.position[test])
        oos_score = metrics.summary(best.returns[test]).get(score, float("nan"))
        rows.append({
            "train_start": train_start.date(),
            "test_start": train_end.date(),
            "test_end": min(test_end, idx[-1]).date(),
            **dict(best_key),
            f"in_sample_{score}": round(best_score, 2),
            f"out_sample_{score}": round(oos_score, 2) if pd.notna(oos_score) else None,
        })
        train_start += pd.DateOffset(years=test_years)

    if not oos_ret:
        raise ValueError("History too short for the requested train/test windows")
    return WalkForwardResult(
        returns=pd.concat(oos_ret),
        position=pd.concat(oos_pos),
        windows=pd.DataFrame(rows),
    )
