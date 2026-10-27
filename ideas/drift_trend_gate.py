"""The overnight drift, only in an uptrend. PRE-REGISTERED 2026-10-27, before any result was run.

A replacement, not an add-on: gate G4 replace compares this against the drift book as it trades now. The other
variant discussed (half size) is a sizing decision and does not change the drift's edge, so it is not tested here.
Earlier filter studies overfit; this is one rule with one parameter, fixed in advance.
"""
import pandas as pd

NAME = "drift_trend_gate"
SESSION = "overnight"
BASELINE = "drift"
REPLACES = "DRIFT"
HYPOTHESIS = ("Take the overnight drift long only when the previous US close is above its 20-session average. The "
              "drift is mostly the market's upward bias; its losses cluster in down-trends (Feb-Mar 2026: -$31K).")
PARAMS = dict(sma=20)
NEIGHBORS = [dict(sma=10), dict(sma=50)]


def trades(ctx, sma=20) -> pd.DataFrame:
    from lab import baselines
    d = baselines.drift(ctx)
    pdc = pd.Series({n["day"]: n["PDC"] for n in ctx.nights}).sort_index()
    up = pdc[pdc > pdc.rolling(sma).mean()].index      # the previous close is known at 18:00
    return d[d.day.isin(up)][["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]]
