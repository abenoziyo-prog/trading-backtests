"""Session and timeframe high/low levels from 1-minute bars.

For every period (trading day, Asia/London/NY AM/NY PM session, or 4H/1H/30m/15m
bar) this computes open/high/low/close, the minute the high and low were made,
how complete the period is, and - when the instrument has volume - VWAP and the
volume point of control (POC: the price bin with the most volume).

For days and sessions it also finds when each high/low was first taken out
afterwards ("swept"), like the extended lines on a sessions chart.

All times are New York local time (America/New_York).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "free"
TZ = "America/New_York"

# name: (start, end) in New York time. An end <= start wraps past midnight.
SESSIONS: dict[str, tuple[str, str]] = {
    "asia": ("20:00", "00:00"),
    "london": ("02:00", "05:00"),
    "ny_am": ("09:30", "11:00"),
    "ny_pm": ("13:30", "16:00"),
}
TIMEFRAMES = {"4h": 240, "1h": 60, "30m": 30, "15m": 15}
SWEEP_HORIZON = pd.Timedelta(days=7)  # look this far ahead for a sweep


@dataclass(frozen=True)
class Instrument:
    name: str
    file: str
    day: str  # "futures": 24h, day ends 17:00 NY | "rth": 09:30-16:00 | "calendar": calendar date
    origin: str  # wall-clock anchor for intraday bars (TradingView-style)
    poc_bin: float | None = None  # price bin for the volume profile (instruments with volume)


INSTRUMENTS = {
    "NSX": Instrument("NSX", "NSXUSD_1min_histdata.parquet", "futures", "18:00"),
    "DXY": Instrument("DXY", "DXY_1min_rebuilt.parquet", "futures", "18:00"),
    "QQQ": Instrument("QQQ", "QQQ_1min_2024_2025.parquet", "rth", "09:30", poc_bin=0.10),
    "VIX": Instrument("VIX", "VIX_1min_2024_2025.parquet", "calendar", "09:30"),
}


def load(inst: Instrument) -> pd.DataFrame:
    return pd.read_parquet(DATA / inst.file)


def _minutes(local: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(local.hour * 60 + local.minute)


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def trading_day(idx: pd.DatetimeIndex, kind: str) -> pd.DatetimeIndex:
    """Trading date each bar belongs to (naive date).

    futures: the day runs 17:00 -> 17:00 NY, so Sunday 18:00 bars and the
    Asia session at 20:00 belong to the next calendar day's trading day.
    """
    local = idx.tz_convert(TZ).tz_localize(None)
    if kind == "futures":
        local = local + pd.Timedelta(hours=7)
    return local.normalize()


def session_of(idx: pd.DatetimeIndex, sessions: dict[str, tuple[str, str]] = SESSIONS) -> pd.Series:
    """Session name for each bar (None outside all sessions)."""
    mod = _minutes(idx.tz_convert(TZ))
    out = np.full(len(idx), None, dtype=object)
    for name, (a, b) in sessions.items():
        s, e = _hhmm(a), _hhmm(b)
        mask = (mod >= s) & (mod < e) if e > s else (mod >= s) | (mod < e)
        out[mask] = name
    return pd.Series(out, index=idx)


def session_length(name: str) -> int:
    s, e = (_hhmm(x) for x in SESSIONS[name])
    return (e - s) % 1440 or 1440


def bar_start(idx: pd.DatetimeIndex, minutes: int, origin: str) -> pd.DatetimeIndex:
    """Start of the `minutes`-long bar each 1-minute bar falls in, anchored at `origin` wall time."""
    local = idx.tz_convert(TZ).tz_localize(None)
    o = _hhmm(origin)
    m = (np.asarray(local.hour * 60 + local.minute) - o) % 1440
    offset = m % minutes
    start = local.floor("min") - pd.to_timedelta(offset, unit="min")
    return pd.DatetimeIndex(start)


def aggregate(df: pd.DataFrame, keys: list[pd.Series | np.ndarray], key_names: list[str], poc_bin: float | None = None) -> pd.DataFrame:
    """OHLC, high/low times, bar count, and (with volume) VWAP and volume POC per group."""
    work = df.copy()
    work["_t"] = df.index
    for name, k in zip(key_names, keys):
        work[name] = np.asarray(k)
    g = work.groupby(key_names, sort=True, observed=True)
    out = pd.DataFrame({
        "start": g["_t"].first(), "end": g["_t"].last(),
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
        "bars": g["close"].size(),
    })
    out["high_time"] = _time_of_extreme(work, key_names, "high", "max")
    out["low_time"] = _time_of_extreme(work, key_names, "low", "min")
    if "volume" in df.columns and float(df["volume"].sum()) > 0:
        price = df["vwap"] if "vwap" in df.columns else (df["high"] + df["low"] + df["close"]) / 3
        work["_pv"] = price.values * df["volume"].values
        out["volume"] = g["volume"].sum()
        if "trade_count" in df.columns:
            out["trade_count"] = g["trade_count"].sum()
        out["vwap"] = g["_pv"].sum() / out["volume"].where(out["volume"] > 0)
        if poc_bin:
            work["_bin"] = np.round(price.values / poc_bin) * poc_bin
            prof = work.groupby(key_names + ["_bin"], sort=False, observed=True)["volume"].sum()
            top = prof.loc[prof.groupby(level=list(range(len(key_names)))).idxmax()]
            out["poc"] = pd.Series(top.index.get_level_values("_bin"), index=top.index.droplevel("_bin")).reindex(out.index).round(4)
            out["poc_volume"] = pd.Series(top.values, index=top.index.droplevel("_bin")).reindex(out.index)
    return out


def _time_of_extreme(work: pd.DataFrame, key_names: list[str], col: str, how: str) -> np.ndarray:
    """Timestamp of the first bar that made the group's max/min of `col`."""
    ext = work.groupby(key_names, sort=True, observed=True)[col].transform(how)
    hit = work[work[col] == ext]
    first = hit.groupby(key_names, sort=True, observed=True)["_t"].first()
    return first.array  # keeps the New York time zone


def find_sweeps(levels: pd.DataFrame, df: pd.DataFrame, horizon: pd.Timedelta = SWEEP_HORIZON) -> pd.DataFrame:
    """When each period's high/low was first exceeded after the period ended.

    high_swept_at: first bar after `end` whose high > period high.
    low_swept_at:  first bar after `end` whose low < period low.
    NaT if not taken out within `horizon` (or before the data ends).
    """
    t = df.index.as_unit("ns").asi8
    hi, lo = df["high"].to_numpy(), df["low"].to_numpy()
    ends = pd.DatetimeIndex(levels["end"]).as_unit("ns").asi8
    first = np.searchsorted(t, ends, side="right")
    last = np.searchsorted(t, ends + horizon.value, side="right")
    hs = np.full(len(levels), np.iinfo(np.int64).min, dtype=np.int64)
    ls = hs.copy()
    lh, ll = levels["high"].to_numpy(), levels["low"].to_numpy()
    for i in range(len(levels)):
        a, b = first[i], last[i]
        if a >= b:
            continue
        up = np.flatnonzero(hi[a:b] > lh[i])
        if up.size:
            hs[i] = t[a + up[0]]
        dn = np.flatnonzero(lo[a:b] < ll[i])
        if dn.size:
            ls[i] = t[a + dn[0]]
    out = levels.copy()
    missing = np.iinfo(np.int64).min
    for col, arr in (("high_swept_at", hs), ("low_swept_at", ls)):
        stamps = pd.to_datetime(np.where(arr == missing, np.datetime64("NaT", "ns"), arr.astype("datetime64[ns]")), utc=True)
        out[col] = pd.DatetimeIndex(stamps).tz_convert(TZ)
    return out


def daily_levels(inst: Instrument, df: pd.DataFrame) -> pd.DataFrame:
    day = trading_day(df.index, "futures" if inst.day == "futures" else "calendar")
    sel = df
    if inst.day == "rth":  # official stock day = regular session only
        mod = _minutes(df.index.tz_convert(TZ))
        keep = (mod >= 570) & (mod < 960)
        sel, day = df[keep], day[keep]
    lv = aggregate(sel, [day], ["trading_day"], inst.poc_bin)
    lv = find_sweeps(lv, df)
    lv.insert(0, "instrument", inst.name)
    return lv.reset_index()


def session_levels(inst: Instrument, df: pd.DataFrame) -> pd.DataFrame:
    sess = session_of(df.index)
    inside = sess.notna().to_numpy()
    sub = df[inside]
    day = trading_day(sub.index, "futures")  # 20:00 Asia belongs to the next day for every instrument
    lv = aggregate(sub, [day, sess[inside].to_numpy()], ["trading_day", "session"], inst.poc_bin)
    lv = find_sweeps(lv, df)
    lv = lv.reset_index()
    lv["expected_bars"] = lv["session"].map(session_length)
    lv["coverage"] = (lv["bars"] / lv["expected_bars"]).round(3)
    order = {name: i for i, name in enumerate(SESSIONS)}
    lv = lv.sort_values(["trading_day", "session"], key=lambda s: s.map(order) if s.name == "session" else s)
    lv.insert(0, "instrument", inst.name)
    return lv.reset_index(drop=True)


def timeframe_levels(inst: Instrument, df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    start = bar_start(df.index, minutes, inst.origin)
    lv = aggregate(df, [start], ["bar_start"], inst.poc_bin)
    lv = lv.reset_index()
    lv["bar_start"] = lv["bar_start"].dt.tz_localize(TZ, ambiguous="NaT", nonexistent="shift_forward")
    lv["trading_day"] = trading_day(pd.DatetimeIndex(lv["start"]), "futures" if inst.day == "futures" else "calendar")
    lv["coverage"] = (lv["bars"] / minutes).round(3)
    lv.insert(0, "instrument", inst.name)
    return lv


def week_of(trading_days: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Monday of each trading day's week (futures Sunday-evening bars already roll to Monday)."""
    d = pd.DatetimeIndex(trading_days)
    return (d - pd.to_timedelta(d.dayofweek, unit="D")).normalize()


def weekly_levels(inst: Instrument, df: pd.DataFrame) -> pd.DataFrame:
    """Weekly high/low: the week is the Mon-Fri trading days (QQQ: regular session only)."""
    day = trading_day(df.index, "futures" if inst.day == "futures" else "calendar")
    sel = df
    if inst.day == "rth":
        mod = _minutes(df.index.tz_convert(TZ))
        keep = (mod >= 570) & (mod < 960)
        sel, day = df[keep], day[keep]
    lv = aggregate(sel, [week_of(day)], ["week"], inst.poc_bin)
    lv = find_sweeps(lv, df, horizon=pd.Timedelta(days=28))
    lv.insert(0, "instrument", inst.name)
    return lv.reset_index()
