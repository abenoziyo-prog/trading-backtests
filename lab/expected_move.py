"""Expected-move exits for the US book (shared by the ideas us_em_*). PRE-REGISTERED 2026-10-27.

Expected move (EM) of a US session, in /NQ points = yesterday's VIX close / 16 (the options' 1-day standard deviation,
in %; "rule of 16") x the session's first 09:30 open. Checked before registering (scripts/vix_rule_of_16.py): over
10 years SPX's cash-session range averaged 0.90-0.99 x VIX/16 and VIX/16 ranked days better than the 10-day average
range or 20-day realized volatility; NQ's range averaged 1.13 x VIX/16 over the strategies' 18 months. No scale is
fitted here: EM is the raw rule, and the multiples in each idea are round numbers chosen before any run.

The idea replays the live US engine (GF-1, ORB-C, S3, L2, one position at a time, same signals and priority) with the
idea's exit rules per day and returns the trades it takes. Days without a VIX close are left out (no trades).
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from quant.live.engine import LIVE_EXITS, ExitPolicy
from quant.live.replay import replay

US = ["GF-1", "ORB-C", "S3", "L2"]
COLS = ["cand", "day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]


def em_points(ctx, day: pd.Timestamp, bars: pd.DataFrame) -> float:
    vix = ctx.features["vix"].get(day, np.nan) if "vix" in ctx.features else np.nan
    return float(vix) / 16 / 100 * float(bars.open.iloc[0]) if np.isfinite(vix) and vix > 0 else np.nan


def ready(ctx, day) -> bool:
    """The shadow book records a day only when its VIX close is known (otherwise it retries later)."""
    return "vix" in ctx.features and np.isfinite(ctx.features["vix"].get(pd.Timestamp(day), np.nan))


def us_trades(ctx, rules: Callable[[float], dict[str, ExitPolicy]]) -> pd.DataFrame:
    """Taken trades of the US engine with `rules(em)` as the exit rules each day (days without EM skipped)."""
    skip = set()

    def exits(day, bars):
        em = em_points(ctx, day, bars)
        if not np.isfinite(em):
            skip.add(day)
            return LIVE_EXITS
        return rules(em)

    rows = [(t.strategy, pd.Timestamp(e.day.date()), t.side, t.entry_time, t.exit_time, t.pnl_usd, t.r_multiple)
            for e, _ in replay(ctx.rth, ctx.levels, exits=exits) for t in e.trades if t.taken and t.exit is not None]
    t = pd.DataFrame(rows, columns=COLS)
    t = t[~t.day.isin(skip)].reset_index(drop=True)
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    return t


def l2_live() -> ExitPolicy:
    return LIVE_EXITS["L2"]
