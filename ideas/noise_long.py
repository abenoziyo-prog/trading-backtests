"""Noise-boundary intraday momentum, long only (reports/deep_research_2026-10-28/REPORT.md; rules: docs/NEW_STRATEGIES.md)."""
import numpy as np
import pandas as pd

NAME = "noise_long"
SESSION = "us"
HYPOTHESIS = ("Intraday momentum (Zarattini, Aziz & Barbon 2024): when a 5-minute candle closes above max(open, previous "
              "close) x (1 + 1.5 x the average absolute move from the open at that minute over the previous 14 sessions), "
              "the move tends to continue. Long only; stop 0.30 x previous range, target 2R, else exit 16:00. One trade "
              "a day. Profitable at every candle size in 2016-2024, the 2024-25 train year and the unseen 2025-26 test.")
PARAMS = dict(k=1.5, tf=5, stop_r=0.30, target_r=2.0, lookback=14)
NEIGHBORS = [dict(tf=3), dict(tf=10), dict(k=1.0), dict(stop_r=0.2), dict(target_r=3.0)]


def sigma(days: list[dict], i: int, lookback: int) -> np.ndarray | None:
    """Average |close / open - 1| per minute of the session over the `lookback` sessions before days[i]."""
    if i < lookback:
        return None
    grid = np.full((lookback, 390), np.nan)
    for r, d in enumerate(days[i - lookback:i]):
        m = d["mod"] - 570
        ok = (m >= 0) & (m < 390)
        grid[r, m[ok]] = np.abs(d["c"][ok] / d["o"][0] - 1)
    with np.errstate(all="ignore"):
        s = np.nanmean(grid, axis=0)
    return s


def trades(ctx, k=1.5, tf=5, stop_r=0.30, target_r=2.0, lookback=14) -> pd.DataFrame:
    o, lv, days = ctx.us, ctx.levels, ctx.us_days
    rows = []
    for i, d in enumerate(days):
        if d["day"] not in lv.index or not d["R0"] > 0:
            continue
        s = sigma(days, i, lookback)
        if s is None:
            continue
        pdc = float(lv.loc[d["day"], "PDC"])
        cd = o.candles_after(d, 0, tf)
        for j in range(len(cd["c"])):
            last = cd["last"][j]
            if d["mod"][last] + 1 > o.LAST_ENTRY:
                break
            sg = s[d["mod"][last] - 570]
            if np.isnan(sg) or not cd["c"][j] > max(d["o"][0], pdc) * (1 + k * sg) or last + 1 >= len(d["c"]):
                continue
            entry, pos = cd["c"][j], last + 1
            risk = stop_r * d["R0"]
            jx, px, _ = o.exit_trade(d, pos, 1, entry - risk, entry + target_r * risk)
            pts = (px - entry) - o.COST
            rows.append(dict(day=d["day"], side=1, entry_time=d["t"][pos], exit_time=d["t"][jx] + pd.Timedelta(minutes=1),
                             pnl_usd=pts * o.DPP, r_multiple=pts / risk))
            break
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
