"""Baselines an idea must improve on, replayed with the live bot's own engines (so they are exactly what trades):

  us         system R: GF-1, ORB-C, S3, L2 with the live exit rules (quant.live.engine.LIVE_EXITS)
  overnight  the overnight tactical book: TOKYO, EU_PRE, LONDON, ASIA_BO (quant.live.overnight)
Returns every shadow trade (taken or not) so an idea can be added to the one-position picker at the lowest priority.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant.live.engine import LIVE_EXITS, PRIORITY, Bar
from quant.live.overnight import TACTICAL, NightEngine, NightLevels
from quant.live.replay import replay

from .data import Context

ORDER = {"us": PRIORITY, "overnight": TACTICAL}
COLS = ["cand", "day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]


def us(ctx: Context) -> pd.DataFrame:
    rth = ctx.us.lr.rth_minutes("NSX")
    rows = [(t.strategy, pd.Timestamp(e.day.date()), t.side, t.entry_time, t.exit_time, t.pnl_usd, t.r_multiple)
            for e, _ in replay(rth, ctx.levels, exits=LIVE_EXITS) for t in e.trades]
    return _frame(rows)


def overnight(ctx: Context) -> pd.DataFrame:
    rows = []
    for n in ctx.nights:
        d = n["day"].to_pydatetime()
        eng = NightEngine(datetime(d.year, d.month, d.day), NightLevels(n["PDH"], n["PDL"], n["PDC"]), t0=n["t0"].to_pydatetime())
        for t, o, h, l, c in zip(n["t"], n["o"], n["h"], n["l"], n["c"]):
            eng.on_bar(Bar(t.to_pydatetime(), float(o), float(h), float(l), float(c)))
        eng.close()
        rows += [(t.strategy, n["day"], t.side, t.entry_time, t.exit_time, t.pnl_usd, t.r_multiple)
                 for t in eng.trades if t.strategy != "DRIFT"]
    return _frame(rows)


def _frame(rows) -> pd.DataFrame:
    t = pd.DataFrame(rows, columns=COLS)
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    return t
