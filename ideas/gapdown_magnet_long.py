"""Unfilled gap-down magnet: next-session long back toward the gap. PRE-REGISTERED 2026-10-27. Designed on the first
12 months only (scripts/gap_bias_study.py); the last 6 months were not looked at before this rule was fixed.

Design evidence: gap-downs left unfilled at the close were filled within 5 sessions 63-82% of the time (gap-ups only
44-61%), and 41-50% the very next session. Caveat: the design year was a rising market, which flatters any long.
"""
import pandas as pd

NAME = "gapdown_magnet_long"
SESSION = "us"
HYPOTHESIS = ("If the previous session gapped down by at least 0.10 x its previous range and never traded back to "
              "the close before the gap, go long at the close of today's first 5-minute candle with that unfilled "
              "close as the target; stop 0.25 x previous range; flat at 16:00. An open gap below a prior close acts "
              "as a magnet: shorts from the gap cover into it.")
PARAMS = dict(min_gap=0.10, entry_min=5, stop_r=0.25, target="gap")
NEIGHBORS = [dict(min_gap=0.25), dict(stop_r=0.5), dict(entry_min=15), dict(target="half")]


def trades(ctx, min_gap=0.10, entry_min=5, stop_r=0.25, target="gap") -> pd.DataFrame:
    o, lv, days = ctx.us, ctx.levels, ctx.us_days
    rows = []
    for p, d in zip(days, days[1:]):
        level = lv.loc[p["day"], "PDC"]                    # the close before yesterday's gap
        if p["o"][0] - level > -min_gap * p["R0"] or (p["h"] >= level).any():
            continue                                       # no gap-down, or it was filled
        k = o.or_end(d, entry_min)
        if k == 0 or k >= len(d["c"]):
            continue
        entry = d["c"][k - 1]
        tgt = level if target == "gap" else entry + (level - entry) / 2
        if tgt <= entry:
            continue                                       # already back at the gap
        stop = entry - stop_r * d["R0"]
        j, px, _ = o.exit_trade(d, k, 1, stop, tgt)
        pts = (px - entry) - o.COST
        rows.append(dict(day=d["day"], side=1, entry_time=d["t"][k], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                         pnl_usd=pts * o.DPP, r_multiple=pts / (entry - stop)))
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
