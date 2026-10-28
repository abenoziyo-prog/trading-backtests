"""Turn-of-month long (pre-registered 2026-10-28, reports/new_ideas_2026-10-28/PREREGISTRATION.md)."""
from datetime import timedelta

import pandas as pd

from quant.live.calendar import Calendar

NAME = "tom_long"
SESSION = "us"
HYPOTHESIS = ("Long at the 09:30 open on the last trading day of the month and the first 3 of the next, exit 16:00, "
              "stop 0.5 x previous range: month-end flows concentrate index gains in these days (Lakonishok & Smidt "
              "1988; McConnell & Xu 2008).")
PARAMS = dict(before=1, after=3, stop_r=0.5)
NEIGHBORS = [dict(stop_r=0.25), dict(stop_r=1.0), dict(after=2), dict(after=4)]
CAL = Calendar()


def tom_day(day, before: int, after: int) -> bool:
    """Is `day` among the last `before` or first `after` trading days of its month (exchange calendar, known ahead)?"""
    d = day.date() if hasattr(day, "date") else day
    k, x = 0, d
    while (x := x - timedelta(days=1)).month == d.month:
        k += CAL.is_trading_day(x)
    if k < after:
        return True
    k, x = 0, d
    while (x := x + timedelta(days=1)).month == d.month:
        k += CAL.is_trading_day(x)
    return k < before


def trades(ctx, before=1, after=3, stop_r=0.5) -> pd.DataFrame:
    o = ctx.us
    rows = []
    for d in ctx.us_days:
        if not tom_day(d["day"], before, after) or len(d["c"]) < 2:
            continue
        entry = d["o"][0]
        stop = entry - stop_r * d["R0"]
        j, px, _ = o.exit_trade(d, 0, 1, stop, None)
        pts = (px - entry) - o.COST
        rows.append(dict(day=d["day"], side=1, entry_time=d["t"][0], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                         pnl_usd=pts * o.DPP, r_multiple=pts / (stop_r * d["R0"])))
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
