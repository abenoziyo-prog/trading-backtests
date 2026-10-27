"""Last-half-hour momentum. PRE-REGISTERED 2026-10-27, before any result was run.

Based on the published intraday-momentum effect in index futures: the return from the previous close to 10:00
(overnight move plus the first half hour) predicts the direction of the last half hour. Nothing in the live US book
trades 15:30-16:00.
"""
import numpy as np
import pandas as pd

NAME = "lasthalfhour_momentum"
SESSION = "us"
HYPOTHESIS = ("At 15:30 ET go long if NQ is above the previous close at 10:00 ET (overnight + first half hour "
              "positive), short if below; exit at the 16:00 close. Stop 0.15 x previous US range. Late-day "
              "rebalancing and hedging flows tend to follow the morning's direction.")
PARAMS = dict(stop_r=0.15, signal="prev_close", entry_min=930)
NEIGHBORS = [dict(stop_r=0.10), dict(stop_r=0.25), dict(stop_r=None), dict(signal="open"), dict(entry_min=900)]


def trades(ctx, stop_r=0.15, signal="prev_close", entry_min=930) -> pd.DataFrame:
    o, lv = ctx.us, ctx.levels
    rows = []
    for d in ctx.us_days:
        day, mod = d["day"], d["mod"]
        k10 = int(np.searchsorted(mod, 600)) - 1          # last bar before 10:00
        ke = int(np.searchsorted(mod, entry_min)) - 1     # signal bar: its close is the entry price
        if k10 < 0 or mod[k10] < 590 or ke <= k10 or mod[ke] < entry_min - 5 or ke + 1 >= len(mod):
            continue                                       # missing data or early close
        ref = lv.loc[day, "PDC"] if signal == "prev_close" else d["o"][0]
        move = d["c"][k10] - ref
        if move == 0:
            continue
        side = 1 if move > 0 else -1
        entry, risk = d["c"][ke], (stop_r or 0.15) * d["R0"]
        stop = entry - side * stop_r * d["R0"] if stop_r else -side * np.inf
        j, px, _ = o.exit_trade(d, ke + 1, side, stop, None)
        pts = side * (px - entry) - o.COST
        rows.append(dict(day=day, side=side, entry_time=d["t"][ke + 1], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                         pnl_usd=pts * o.DPP, r_multiple=pts / risk))
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
