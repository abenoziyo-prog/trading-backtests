"""Asia-session PDL wick fade (from the 2026-10-22 overnight level research)."""
import numpy as np
import pandas as pd

NAME = "asia_pdl_wick_fade"
SESSION = "overnight"
HYPOTHESIS = ("During Asia (18:00-02:00 ET), when a 1-minute candle trades below the previous US low (PDL) but closes "
              "back above it, buy: a wick-only touch of PDL usually rejects. Stop 0.2 x previous range, hold to 09:29.")
PARAMS = dict(tf=1, stop_r=0.2)
NEIGHBORS = [dict(tf=2), dict(tf=3), dict(stop_r=0.1), dict(stop_r=0.3)]


def trades(ctx, tf=1, stop_r=0.2) -> pd.DataFrame:
    ons = ctx.night
    rows: list = []
    for n in ctx.nights:
        cd = ons.candles(n, 0, ons.FLAT, tf)
        if cd is None:
            continue
        L = n["PDL"]
        prev_c = np.r_[n["c"][cd["i0"] - 1] if cd["i0"] > 0 else cd["o"][0], cd["c"][:-1]]
        wick_dn = (cd["l"] < L) & (cd["c"] >= L) & (prev_c >= L)

        def make(i, side, R0=n["R0"]):
            return cd["c"][i] - side * stop_r * R0, None
        ons.walk(n, cd, wick_dn, np.zeros_like(wick_dn), make, 480, rows, dict(family="IDEA", config=NAME, tf=tf))
    t = pd.DataFrame(rows)
    return t[["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]] if len(t) else \
        pd.DataFrame(columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
