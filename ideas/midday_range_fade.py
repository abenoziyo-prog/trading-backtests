"""Midday range fade on quiet mornings. PRE-REGISTERED 2026-10-27, before any result was run."""
import numpy as np
import pandas as pd

NAME = "midday_range_fade"
SESSION = "us"
HYPOTHESIS = ("On days whose 09:30-11:30 range is below the median of the previous 20 sessions, fade the first "
              "5-minute close outside the 11:00-11:30 range back to its midpoint; stop 0.5 x the range height beyond "
              "it, entries 11:30-13:30, flat by 14:00. Lunch hours on quiet days are mean-reverting.")
PARAMS = dict(tf=5, stop_h=0.5, entry_end=810, time_stop=840, quiet=True, target="mid")
NEIGHBORS = [dict(quiet=False), dict(tf=10), dict(stop_h=1.0), dict(time_stop=900), dict(target="opposite")]


def exit_by(d, pos, side, stop, target, end_mod):
    j_end = int(np.searchsorted(d["mod"], end_mod)) - 1
    for j in range(pos, j_end + 1):
        if (d["l"][j] <= stop) if side == 1 else (d["h"][j] >= stop):
            return j, stop
        if (d["h"][j] >= target) if side == 1 else (d["l"][j] <= target):
            return j, target
    return j_end, d["c"][j_end]


def trades(ctx, tf=5, stop_h=0.5, entry_end=810, time_stop=840, quiet=True, target="mid") -> pd.DataFrame:
    o = ctx.us
    rows, hist = [], []
    for d in ctx.us_days:
        mod = d["mod"]
        a, b = int(np.searchsorted(mod, 660)), int(np.searchsorted(mod, 690))
        if b - a < 25 or b >= len(mod) or mod[-1] < time_stop:
            continue                                       # missing data or early close
        morning = d["h"][:b].max() - d["l"][:b].min()
        prior, hist = hist[-20:], hist + [morning]
        if quiet and (len(prior) < 20 or morning >= np.median(prior)):
            continue
        rh, rl = d["h"][a:b].max(), d["l"][a:b].min()
        height = rh - rl
        if height <= 0:
            continue
        cd = o.candles_after(d, b, tf)
        for last, cl in zip(cd["last"], cd["c"]):
            if mod[last] + 1 > entry_end:
                break
            side = -1 if cl > rh else 1 if cl < rl else 0
            if not side:
                continue
            stop = rh + stop_h * height if side == -1 else rl - stop_h * height
            tgt = (rh + rl) / 2 if target == "mid" else (rl if side == -1 else rh)
            risk = side * (cl - stop)
            pos = last + 1
            if risk <= 0 or pos >= len(mod):
                continue                                   # already beyond the stop: wait for a cleaner signal
            j, px = exit_by(d, pos, side, stop, tgt, time_stop)
            pts = side * (px - cl) - o.COST
            rows.append(dict(day=d["day"], side=side, entry_time=d["t"][pos], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                             pnl_usd=pts * o.DPP, r_multiple=pts / risk))
            break                                          # one trade a day
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
