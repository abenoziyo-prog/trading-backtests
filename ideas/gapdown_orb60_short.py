"""Gap-down opening-range breakdown short (from the 2026-10-22 down-day research)."""
import pandas as pd

NAME = "gapdown_orb60_short"
SESSION = "us"
HYPOTHESIS = ("On gap-down opens (open more than 0.10 x previous range below the previous close), short the first fresh "
              "close below the 60-minute opening range; stop at the range midpoint, target 1R. Down-day momentum "
              "should carry breakdowns further than on normal days.")
PARAMS = dict(or_min=60, tf=10, stop="mid", target_r=1.0)
NEIGHBORS = [dict(tf=3), dict(tf=5), dict(stop="opposite"), dict(target_r=2.0), dict(or_min=30)]


def trades(ctx, or_min=60, tf=10, stop="mid", target_r=1.0) -> pd.DataFrame:
    o, f = ctx.us, ctx.features
    rows = []
    for d in ctx.us_days:
        day = d["day"]
        if day not in f.index or not f.loc[day, "gap_down"]:
            continue
        k = o.or_end(d, or_min)
        if k == 0 or k >= len(d["c"]):
            continue
        orh, orl = d["h"][:k].max(), d["l"][:k].min()
        cd = o.candles_after(d, k, tf)
        prev_inside, busy = True, -1
        for i in range(len(cd["c"])):
            last, cl = cd["last"][i], cd["c"][i]
            inside = orl <= cl <= orh
            if last <= busy:
                prev_inside = inside
                continue
            fresh = cl < orl and prev_inside
            prev_inside = inside
            if not fresh:
                continue
            if d["mod"][last] + 1 > o.LAST_ENTRY:
                break
            pos = last + 1
            if pos >= len(d["c"]):
                break
            stp = orh if stop == "opposite" else (orh + orl) / 2
            risk = stp - cl
            if risk <= 0:
                continue
            j, px, _ = o.exit_trade(d, pos, -1, stp, cl - target_r * risk)
            pts = (cl - px) - o.COST
            rows.append(dict(day=day, side=-1, entry_time=d["t"][pos], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                             pnl_usd=pts * o.DPP, r_multiple=pts / risk))
            busy = j
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
