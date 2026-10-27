"""VWAP and its standard-deviation bands, anchored per session or per week, on 1-minute bars.

Everything is known at the close of the bar it is stamped on (cumulative sums up to and including that minute):
  vwap   sum(price x volume) / sum(volume) since the anchor; price = the bar's own VWAP when the source has one
         (Alpaca), else the typical price (high + low + close) / 3 (Schwab, Yahoo)
  sd     volume-weighted standard deviation of price around the VWAP since the anchor:
         sqrt(sum(volume x price^2) / sum(volume) - vwap^2)   (the usual "VWAP standard deviation bands")
  z      (close - vwap) / sd: where the close sits in band units (+2 = on the upper 2-sigma band)
Anchors: "session" (US session 09:30-16:00 ET, reset each day) or "week" (US sessions of the calendar week, reset each
Monday). Bars outside 09:30-16:00 are dropped first. Bars with no volume add nothing.
`map_bands` moves VWAP levels from one instrument onto another's price scale with the live price ratio, e.g. QQQ's
volume-based VWAP onto /NQ (NSX has no volume) — a proxy, checked against /NQ's own VWAP in scripts/vwap_study.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RTH = (570, 960)  # minutes after midnight: 09:30 and 16:00 ET


def rth(df: pd.DataFrame) -> pd.DataFrame:
    mod = df.index.hour * 60 + df.index.minute
    return df[(mod >= RTH[0]) & (mod < RTH[1]) & (df.index.dayofweek < 5)]


def anchor_key(idx: pd.DatetimeIndex, anchor: str) -> np.ndarray:
    day = idx.tz_localize(None).normalize()
    if anchor == "session":
        return np.asarray(day)
    if anchor == "week":
        return np.asarray(day - pd.to_timedelta(day.dayofweek, unit="D"))
    raise ValueError(f"unknown anchor {anchor!r}")


def vwap_bands(df: pd.DataFrame, anchor: str = "session") -> pd.DataFrame:
    """1-minute bars (NY time, columns open/high/low/close/volume, optional vwap) -> the RTH bars with vwap, sd, z."""
    d = rth(df).copy()
    price = d["vwap"] if "vwap" in d and d["vwap"].notna().all() else (d.high + d.low + d.close) / 3
    v = d.volume.fillna(0).astype(float).to_numpy()
    p = price.to_numpy(dtype=float)
    key = anchor_key(d.index, anchor)
    g = pd.DataFrame(dict(k=key, v=v, pv=p * v, ppv=p * p * v)).groupby("k", sort=False)
    cv, cpv, cppv = g.v.cumsum().to_numpy(), g.pv.cumsum().to_numpy(), g.ppv.cumsum().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        vw = np.where(cv > 0, cpv / cv, np.nan)
        var = np.where(cv > 0, cppv / cv - vw * vw, np.nan)
    d["vwap_px"] = vw
    d["sd"] = np.sqrt(np.clip(var, 0, None))
    with np.errstate(invalid="ignore", divide="ignore"):
        d["z"] = np.where(d.sd > 0, (d.close - d.vwap_px) / d.sd, np.nan)
    return d.drop(columns=[c for c in ("vwap",) if c in d]).rename(columns={"vwap_px": "vwap"})


def band(d: pd.DataFrame, k: float) -> pd.Series:
    """The VWAP + k standard deviations line."""
    return d.vwap + k * d.sd


def resample(d: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """n-minute bars anchored at 09:30, stamped at their LAST minute (so a bar's values are known at that minute's
    close); vwap/sd/z are taken from that last minute."""
    if minutes == 1:
        return d
    day = d.index.tz_localize(None).normalize()
    mod = d.index.hour * 60 + d.index.minute - RTH[0]
    key = pd.MultiIndex.from_arrays([day, mod // minutes])
    g = d.groupby(key)
    out = pd.DataFrame(dict(open=g.open.first(), high=g.high.max(), low=g.low.min(), close=g.close.last(),
                            volume=g.volume.sum(), vwap=g.vwap.last(), sd=g.sd.last(), z=g.z.last(),
                            t=g.apply(lambda x: x.index[-1], include_groups=False)))
    return out.set_index("t").sort_index()


def map_bands(src: pd.DataFrame, dst_close: pd.Series) -> pd.DataFrame:
    """src's vwap and sd moved onto dst's price scale minute by minute (x dst_close / src.close)."""
    j = src[["close", "vwap", "sd"]].join(dst_close.rename("dst"), how="inner")
    r = j.dst / j.close
    return pd.DataFrame(dict(vwap=j.vwap * r, sd=j.sd * r), index=j.index)


def noise_area(m: pd.DataFrame, lookback: int = 14) -> pd.DataFrame:
    """The intraday "noise area" of Zarattini, Aziz & Barbon (2024): for each minute of the US session, the average
    absolute move from that day's 09:30 open over the previous `lookback` sessions (|close / open - 1|, same minute).
    Bands: upper = max(open, previous close) x (1 + sigma), lower = min(open, previous close) x (1 - sigma). Inputs:
    1-minute US-session bars (NY time). Returns per minute: sigma, upper, lower (NaN until `lookback` sessions exist)."""
    d = rth(m)
    day = d.index.tz_localize(None).normalize()
    mod = d.index.hour * 60 + d.index.minute
    op = d.groupby(day).open.transform("first")
    move = (d.close / op - 1).abs()
    grid = pd.DataFrame(dict(day=day, mod=mod, move=move.to_numpy())).pivot_table(index="day", columns="mod", values="move")
    sig = grid.rolling(lookback, min_periods=lookback).mean().shift()          # previous sessions only
    sigma = sig.stack().reindex(pd.MultiIndex.from_arrays([day, mod])).to_numpy()
    prev_close = d.groupby(day).close.last().shift()
    pc = pd.Series(prev_close.reindex(day).to_numpy(), index=d.index)
    hi, lo = np.maximum(op, pc), np.minimum(op, pc)
    return pd.DataFrame(dict(sigma=sigma, upper=hi * (1 + sigma), lower=lo * (1 - sigma)), index=d.index)
