"""Opening-range breakout after a narrow-range day (NR4). PRE-REGISTERED 2026-10-27, before any result was run.

ORB-C (long only, every day) is the best live strategy so far. Breakouts are known to follow volatility
compression; this trades both directions, but only after the narrowest US session of the last 4. The optimizer
library's 'narrow' filter used today's opening range, not the previous day's range, so this is a new family.
"""
import pandas as pd

NAME = "nr_day_orb"
SESSION = "us"
HYPOTHESIS = ("When the previous US session's range was the smallest of the last 4 (NR4), trade the first 5-minute "
              "close outside the 15-minute opening range in its direction; stop at the other side of the range, "
              "target 2R. Compression tends to release into a trend day.")
PARAMS = dict(nr=4, or_min=15, tf=5, stop="opposite", target_r=2.0)
NEIGHBORS = [dict(nr=7), dict(tf=3), dict(tf=10), dict(target_r=1.0), dict(stop="mid")]


def trades(ctx, nr=4, or_min=15, tf=5, stop="opposite", target_r=2.0) -> pd.DataFrame:
    o, days = ctx.us, ctx.us_days
    ranges = [d["R0"] for d in days]                   # R0 = the previous session's range
    rows = []
    for i, d in enumerate(days):
        if i < nr - 1 or ranges[i] > min(ranges[i - nr + 1:i + 1]):
            continue
        k = o.or_end(d, or_min)
        if k == 0 or k >= len(d["c"]):
            continue
        orh, orl = d["h"][:k].max(), d["l"][:k].min()
        cd = o.candles_after(d, k, tf)
        for last, cl in zip(cd["last"], cd["c"]):
            if d["mod"][last] + 1 > o.LAST_ENTRY:
                break
            side = 1 if cl > orh else -1 if cl < orl else 0
            if not side:
                continue
            pos = last + 1
            if pos >= len(d["c"]):
                break
            stp = (orl if side == 1 else orh) if stop == "opposite" else (orh + orl) / 2
            risk = side * (cl - stp)
            if risk > 0:
                j, px, _ = o.exit_trade(d, pos, side, stp, cl + side * target_r * risk)
                pts = side * (px - cl) - o.COST
                rows.append(dict(day=d["day"], side=side, entry_time=d["t"][pos], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                                 pnl_usd=pts * o.DPP, r_multiple=pts / risk))
            break                                      # first breakout only
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
