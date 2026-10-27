"""Failed opening-range breakout, entered on the reclaim of the session average. PRE-REGISTERED 2026-10-27.

NSX has no volume, so the session average is a time-weighted average of the typical price (TWAP), not VWAP.
"""
import numpy as np
import pandas as pd

NAME = "failed_orb_twap_reclaim"
SESSION = "us"
HYPOTHESIS = ("After a 5-minute close outside the 15-minute opening range, a later 5-minute close back across the "
              "session TWAP marks a failed breakout: trade toward the other side of the range, stop at the extreme "
              "made since the breakout, entries until 12:00. Trapped breakout traders fuel the reversal.")
PARAMS = dict(or_min=15, tf=5, entry_end=720, target="range")
NEIGHBORS = [dict(tf=3), dict(tf=10), dict(entry_end=780), dict(target="1.5R"), dict(or_min=30)]


def trades(ctx, or_min=15, tf=5, entry_end=720, target="range") -> pd.DataFrame:
    o = ctx.us
    rows = []
    for d in ctx.us_days:
        k = o.or_end(d, or_min)
        if k == 0 or k >= len(d["c"]):
            continue
        orh, orl = d["h"][:k].max(), d["l"][:k].min()
        tp = (d["h"] + d["l"] + d["c"]) / 3
        twap = np.cumsum(tp) / np.arange(1, len(tp) + 1)
        cd = o.candles_after(d, k, tf)
        broke, ext = 0, None
        for i, (last, cl) in enumerate(zip(cd["last"], cd["c"])):
            if d["mod"][last] + 1 > entry_end:
                break
            if not broke:
                broke = 1 if cl > orh else -1 if cl < orl else 0
                ext = cd["h"][i] if broke == 1 else cd["l"][i]
                continue
            ext = max(ext, cd["h"][i]) if broke == 1 else min(ext, cd["l"][i])
            if (broke == 1 and cl >= twap[last]) or (broke == -1 and cl <= twap[last]):
                continue
            side = -broke
            risk = side * (cl - ext) * -1                  # stop at the post-breakout extreme
            tgt = (orl if side == -1 else orh) if target == "range" else cl + side * 1.5 * risk
            pos = last + 1
            if risk > 0 and side * (tgt - cl) > 0 and pos < len(d["c"]):
                j, px, _ = o.exit_trade(d, pos, side, ext, tgt)
                pts = side * (px - cl) - o.COST
                rows.append(dict(day=d["day"], side=side, entry_time=d["t"][pos], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                                 pnl_usd=pts * o.DPP, r_multiple=pts / risk))
            break                                          # one trade a day
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
