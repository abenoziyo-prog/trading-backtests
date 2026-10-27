"""Evening fade after an outsized US session. PRE-REGISTERED 2026-10-27, before any result was run.

A complement to the long-only drift: it can be short.
"""
import numpy as np
import pandas as pd

NAME = "evening_fade_big_day"
SESSION = "overnight"
HYPOTHESIS = ("When the previous US session's range was at least 1.5 x the average of the 20 before it, fade that "
              "session's direction (open -> close) at the 18:00 reopen; stop 0.25 x that range, exit at 20:00. "
              "After an outsized day, the first evening hours tend to give some of the move back.")
PARAMS = dict(k=1.5, exit_rel=120, stop_r=0.25)
NEIGHBORS = [dict(k=1.25), dict(k=2.0), dict(exit_rel=240), dict(stop_r=0.15)]


def trades(ctx, k=1.5, exit_rel=120, stop_r=0.25) -> pd.DataFrame:
    rows, hist = [], []
    for n in ctx.nights:
        prior, hist = hist[-20:], hist + [n["R0"]]
        if len(prior) < 20 or n["R0"] < k * np.mean(prior) or n["rel"][0] > 5:
            continue
        move = n["PDC"] - n["prev_open"]
        if move == 0:
            continue
        side = -1 if move > 0 else 1
        entry = n["o"][0]
        ctx.night.record(rows, n, {}, side, 0, entry, entry - side * stop_r * n["R0"], None, exit_rel)
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
