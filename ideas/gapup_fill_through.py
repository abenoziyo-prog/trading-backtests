"""Gap-up fill-through short. PRE-REGISTERED 2026-10-27. Designed on the first 12 months only (scripts/gap_bias_study.py);
the last 6 months were not looked at before this rule was fixed.

Design evidence: up-gaps that fill 30+ minutes after the open run on through the previous close (next 60 minutes:
0.45R through vs 0.22R back toward the open). Down-gaps do not mirror this (design PF 0.75), so it is short only, like
GF-1. This is the leg after GF-1's target: GF-1 shorts a gap-up down to the previous close, this shorts the break of it.
"""
import numpy as np
import pandas as pd

NAME = "gapup_fill_through"
SESSION = "us"
HYPOTHESIS = ("On a gap-up of at least 0.10 x previous range, when price first trades back to the previous close 10+ "
              "minutes after the open, short at the close of that minute; stop 0.5 x gap above the previous close, "
              "target 0.5 x previous range below it. A late fill means the gap-up buyers are trapped: the fill turns "
              "into a breakdown instead of support.")
PARAMS = dict(min_gap=0.10, after=10, stop_g=0.5, target_r=0.5)
NEIGHBORS = [dict(after=30), dict(target_r=0.25), dict(stop_g=1.0), dict(min_gap=0.25)]


def trades(ctx, min_gap=0.10, after=10, stop_g=0.5, target_r=0.5) -> pd.DataFrame:
    o, lv = ctx.us, ctx.levels
    rows = []
    for d in ctx.us_days:
        pdc = lv.loc[d["day"], "PDC"]
        gap = d["o"][0] - pdc
        if gap < min_gap * d["R0"]:
            continue
        hit = d["l"] <= pdc
        if not hit.any():
            continue
        fi = int(np.argmax(hit))
        if d["mod"][fi] - 570 < after or d["mod"][fi] + 1 > o.LAST_ENTRY or fi + 1 >= len(d["c"]):
            continue
        entry = d["c"][fi]
        stop = pdc + stop_g * gap
        risk = stop - entry
        if risk <= 0:
            continue
        j, px, _ = o.exit_trade(d, fi + 1, -1, stop, pdc - target_r * d["R0"])
        pts = (entry - px) - o.COST
        rows.append(dict(day=d["day"], side=-1, entry_time=d["t"][fi + 1], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                         pnl_usd=pts * o.DPP, r_multiple=pts / risk))
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
