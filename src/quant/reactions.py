"""How price reacts when it first trades through a prior high or low.

Reference levels (each tested in a window that starts after the level is final):
  PDH / PDL     previous trading day's high / low       -> tested during the next trading day
  PWH / PWL     previous week's high / low              -> tested during the next week
  ASIA_H / _L   Asia session (20:00-00:00) high / low   -> tested 00:00-16:00 the same trading day
  LON_H / _L    London session (02:00-05:00) high / low -> tested 05:00-16:00 the same trading day

Event, per timeframe (4h / 1h / 30m / 15m bars built from 1-minute data):
  the first bar in the window that trades through the level. It is classified
  at that bar's close (no lookahead):
    rejected  - traded through but closed back inside the level
    accepted  - closed beyond the level
    gap       - the bar opened beyond the level (level skipped, not tested)

Outcome, measured on 1-minute bars from the touch bar's close until the end of
that trading day:
    race      - 1 if price moved k x ATR back toward the inside ("reversal")
                before moving k x ATR further beyond ("continuation"), 0 if the
                reverse, NaN if neither happened. Symmetric targets: a coin flip
                scores ~50%.
    ret_1h / ret_4h / ret_eod - move from entry, in ATR units, signed so that a
                positive number means reversal (back inside).
ATR = 14-day average true range of the instrument's trading day, known at the
prior day's close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import sessions as s

TFS = {"4h": 240, "1h": 60, "30m": 30, "15m": 15}
RACE_K = (0.25, 0.5)
DAY_END = {"futures": "17:00", "rth": "16:00"}


# ------------------------------------------------------------------ preparation

def rth_only(df: pd.DataFrame) -> pd.DataFrame:
    mod = s._minutes(df.index.tz_convert(s.TZ))
    return df[(mod >= 570) & (mod < 960)]


def trading_minutes(inst: s.Instrument, df: pd.DataFrame) -> pd.DataFrame:
    """1-minute bars that belong to the instrument's trading day (QQQ: regular session)."""
    return rth_only(df) if inst.day == "rth" else df


def daily_table(inst: s.Instrument, df: pd.DataFrame, min_coverage: float = 0.85) -> pd.DataFrame:
    """Per trading day: OHLC, start/end, ATR14 known at the prior close, and a validity flag."""
    m = trading_minutes(inst, df)
    day = s.trading_day(m.index, "futures" if inst.day == "futures" else "calendar")
    d = s.aggregate(m, [day], ["trading_day"])
    pc = d["close"].shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14, min_periods=10).mean().shift()  # known before the day starts
    typical = d.groupby(d.index.year)["bars"].transform("median")
    d["valid"] = d["bars"] >= min_coverage * typical
    d["week"] = s.week_of(d.index)
    return d


def tf_bars(inst: s.Instrument, df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Timeframe bars from the trading-day minutes (QQQ 4h = 09:30-13:30 and 13:30-16:00)."""
    m = trading_minutes(inst, df)
    lv = s.aggregate(m, [s.bar_start(m.index, minutes, inst.origin)], ["bar_start"])
    lv = lv.reset_index(drop=True)
    lv["close_time"] = lv["end"] + pd.Timedelta(minutes=1)  # entry happens after the bar's last minute
    return lv.sort_values("start").reset_index(drop=True)


# ------------------------------------------------------------------ reference levels

def reference_levels(inst: s.Instrument, df: pd.DataFrame, daily: pd.DataFrame, with_sessions: bool) -> pd.DataFrame:
    """One row per (level, test window). Only windows whose day is valid are kept."""
    rows = []
    valid = daily[daily.valid]
    days = valid.index
    # previous day -> next valid day
    for prev, cur in zip(days[:-1], days[1:]):
        p, c = valid.loc[prev], valid.loc[cur]
        if (cur - prev).days > 5:  # data gap, not a normal next day
            continue
        for kind, side, price in (("PDH", 1, p.high), ("PDL", -1, p.low)):
            rows.append(dict(level=kind, side=side, price=price, ref_day=prev, test_day=cur,
                             win_start=c.start, win_end=c.end + pd.Timedelta(minutes=1)))
    # previous week -> next week
    wk = valid.groupby("week").agg(high=("high", "max"), low=("low", "min"), n=("high", "size"),
                                   start=("start", "first"), end=("end", "last"))
    wk = wk[wk.n >= 3]
    for prev, cur in zip(wk.index[:-1], wk.index[1:]):
        if (cur - prev).days != 7:
            continue
        p, c = wk.loc[prev], wk.loc[cur]
        for kind, side, price in (("PWH", 1, p.high), ("PWL", -1, p.low)):
            rows.append(dict(level=kind, side=side, price=price, ref_day=prev, test_day=cur,
                             win_start=c.start, win_end=c.end + pd.Timedelta(minutes=1)))
    if with_sessions:
        sess = s.session_levels(inst, df)
        sess = sess[sess.trading_day.isin(days) & (sess.coverage >= 0.9)]
        windows = {"asia": ("ASIA", "00:00"), "london": ("LON", "05:00")}
        for _, r in sess[sess.session.isin(list(windows))].iterrows():
            tag, begin = windows[r.session]
            d0 = pd.Timestamp(r.trading_day).tz_localize(s.TZ)
            ws = d0 + pd.Timedelta(hours=int(begin[:2]))
            we = d0 + pd.Timedelta(hours=16)
            for kind, side, price in ((f"{tag}_H", 1, r.high), (f"{tag}_L", -1, r.low)):
                rows.append(dict(level=kind, side=side, price=price, ref_day=r.trading_day, test_day=r.trading_day,
                                 win_start=ws, win_end=we))
    lv = pd.DataFrame(rows)
    lv["atr"] = lv["test_day"].map(daily["atr"]).where(lv.level.str.startswith(("PD", "ASIA", "LON")))
    weekly_atr = daily["atr"].groupby(daily["week"]).first()
    is_week = lv.level.str.startswith("PW")
    lv.loc[is_week, "atr"] = lv.loc[is_week, "test_day"].map(weekly_atr)
    return lv.dropna(subset=["atr"]).reset_index(drop=True)


# ------------------------------------------------------------------ events and outcomes

def detect_events(levels: pd.DataFrame, bars: pd.DataFrame, tf: str) -> pd.DataFrame:
    """First bar in each window that trades through its level, classified at the bar close."""
    starts = bars["start"].to_numpy()
    hi, lo, op, cl = (bars[c].to_numpy() for c in ("high", "low", "open", "close"))
    a = np.searchsorted(starts, levels["win_start"].to_numpy(), side="left")
    b = np.searchsorted(starts, levels["win_end"].to_numpy(), side="left")
    out = []
    for i, r in enumerate(levels.itertuples(index=False)):
        if a[i] >= b[i]:
            continue
        seg = slice(a[i], b[i])
        through = hi[seg] > r.price if r.side == 1 else lo[seg] < r.price
        if not through.any():
            continue
        j = a[i] + int(np.argmax(through))
        beyond_open = op[j] > r.price if r.side == 1 else op[j] < r.price
        closed_beyond = cl[j] > r.price if r.side == 1 else cl[j] < r.price
        cls = "gap" if beyond_open else ("accepted" if closed_beyond else "rejected")
        out.append(dict(**r._asdict(), tf=tf, touch_bar=bars.at[j, "start"], entry_time=bars.at[j, "close_time"],
                        entry=cl[j], bar_high=hi[j], bar_low=lo[j], reaction=cls,
                        bars_into_window=j - a[i]))
    return pd.DataFrame(out)


def add_outcomes(ev: pd.DataFrame, minutes: pd.DataFrame, inst: s.Instrument, ks=RACE_K) -> pd.DataFrame:
    """First-passage race and fixed-horizon returns on 1-minute bars after entry."""
    m = trading_minutes(inst, minutes)
    t = m.index.as_unit("ns").asi8
    hi, lo, cl = m["high"].to_numpy(), m["low"].to_numpy(), m["close"].to_numpy()
    day_of = s.trading_day(m.index, "futures" if inst.day == "futures" else "calendar")
    day_end = pd.Series(t, index=day_of).groupby(level=0).max()  # last minute of each trading day
    entry_ns = pd.DatetimeIndex(ev["entry_time"]).as_unit("ns").asi8
    touch_day = s.trading_day(pd.DatetimeIndex(ev["touch_bar"]), "futures" if inst.day == "futures" else "calendar")
    end_ns = day_end.reindex(touch_day).to_numpy()
    a = np.searchsorted(t, entry_ns, side="left")
    b = np.searchsorted(t, end_ns, side="right")
    side = ev["side"].to_numpy()
    entry = ev["entry"].to_numpy()
    atr = ev["atr"].to_numpy()
    res = {f"race_{k}": np.full(len(ev), np.nan) for k in ks}
    rets = {h: np.full(len(ev), np.nan) for h in ("ret_1h", "ret_4h", "ret_eod")}
    hz = {"ret_1h": 60 * 60 * 10**9, "ret_4h": 4 * 60 * 60 * 10**9}
    for i in range(len(ev)):
        if not (a[i] < b[i]) or np.isnan(end_ns[i]):
            continue
        seg = slice(a[i], b[i])
        # reversal = move back inside: down for highs (side +1), up for lows
        rev_extreme = (entry[i] - lo[seg]) if side[i] == 1 else (hi[seg] - entry[i])
        cont_extreme = (hi[seg] - entry[i]) if side[i] == 1 else (entry[i] - lo[seg])
        for k in ks:
            thr = k * atr[i]
            r_hit = np.flatnonzero(rev_extreme >= thr)
            c_hit = np.flatnonzero(cont_extreme >= thr)
            r0 = r_hit[0] if r_hit.size else np.inf
            c0 = c_hit[0] if c_hit.size else np.inf
            if r0 == c0 == np.inf:
                continue
            # same minute reaching both: unknown order -> leave undecided
            res[f"race_{k}"][i] = np.nan if r0 == c0 else float(r0 < c0)
        for name in ("ret_1h", "ret_4h"):
            j = np.searchsorted(t, entry_ns[i] + hz[name], side="right") - 1
            j = min(max(j, a[i]), b[i] - 1)
            rets[name][i] = -side[i] * (cl[j] - entry[i]) / atr[i]
        rets["ret_eod"][i] = -side[i] * (cl[b[i] - 1] - entry[i]) / atr[i]
    out = ev.copy()
    for k, v in {**res, **rets}.items():
        out[k] = v
    return out


# ------------------------------------------------------------------ conditions (DXY, VIX)

def add_conditions(ev: pd.DataFrame, dxy: pd.DataFrame, vix_daily: pd.DataFrame, vix_min: pd.DataFrame | None) -> pd.DataFrame:
    """Context known at entry time.

    dxy_day_bp   DXY move since its trading-day open (17:00 roll), in bp
    dxy_prev_bp  DXY's previous trading-day move, in bp
    vix_prev     prior-day official VIX close (Cboe)
    vix_chg_pct  VIX move from the prior official close to the last 1-minute print before entry (2024-25 only)
    """
    out = ev.copy()
    entry = pd.DatetimeIndex(out["entry_time"])
    dx = dxy["close"]
    dx_day = s.trading_day(dx.index, "futures")
    dx_open = dx.groupby(dx_day).first()
    dx_close = dx.groupby(dx_day).last()
    pos = dx.index.searchsorted(entry, side="left") - 1  # last DXY minute strictly before entry
    ok = pos >= 0
    last = np.where(ok, dx.to_numpy()[np.clip(pos, 0, None)], np.nan)
    last_time = dx.index[np.clip(pos, 0, None)]
    fresh = (entry - last_time) <= pd.Timedelta(minutes=15)
    eday = s.trading_day(entry, "futures")
    out["dxy_day_bp"] = np.where(fresh, (np.log(last / dx_open.reindex(eday).to_numpy())) * 1e4, np.nan)
    prev_ret = np.log(dx_close / dx_close.shift()) * 1e4
    # previous completed DXY trading day relative to the entry's trading day
    order = dx_close.index
    idx = order.searchsorted(eday, side="left") - 1
    out["dxy_prev_bp"] = np.where(idx >= 1, prev_ret.to_numpy()[np.clip(idx, 0, None)], np.nan)
    # VIX: prior official close for the entry's calendar date
    vd = vix_daily["close"]
    local = entry.tz_convert(s.TZ).tz_localize(None)
    edate = local.normalize()
    # the official close (16:15) is known for entries after 16:15 on the same date
    cutoff = edate + pd.to_timedelta(((local.hour * 60 + local.minute) >= 975).astype(int), unit="D")
    vi = vd.index.searchsorted(cutoff, side="left") - 1
    out["vix_prev"] = np.where(vi >= 0, vd.to_numpy()[np.clip(vi, 0, None)], np.nan)
    if vix_min is not None and len(vix_min):
        vm = vix_min["close"]
        p = vm.index.searchsorted(entry, side="left") - 1
        okv = (p >= 0)
        lastv = vm.to_numpy()[np.clip(p, 0, None)]
        lt = vm.index[np.clip(p, 0, None)]
        same_day = lt.tz_convert(s.TZ).tz_localize(None).normalize() == edate
        fresh_v = ((entry - lt) <= pd.Timedelta(minutes=15)) & same_day & okv
        out["vix_chg_pct"] = np.where(fresh_v, (lastv / out["vix_prev"] - 1) * 100, np.nan)
    else:
        out["vix_chg_pct"] = np.nan
    return out


def bucketize(ev: pd.DataFrame) -> pd.DataFrame:
    """Readable condition buckets, expressed relative to the direction of the test.

    Stocks and both the dollar and VIX usually move opposite to stocks. So when a
    HIGH is being broken (stocks pushing up), DXY or VIX falling is "aligned"
    with the break and rising is "against" it; for a LOW it is the reverse.
      dxy_vs_move       DXY move so far today: against (> 15 bp), flat, aligned
      dxy_prev_vs_move  DXY's previous day: against (> 20 bp), flat, aligned
      vix_vs_move       VIX vs prior close: against (> 3%), flat, aligned (2024-25 only)
      vix_regime        prior-day VIX close: <15, 15-20, 20-30, >30
      touch_session     when the level was hit (New York time)
    """
    out = ev.copy()
    out["vix_regime"] = pd.cut(out["vix_prev"], [0, 15, 20, 30, 200], labels=["<15", "15-20", "20-30", ">30"], right=False)
    # DXY relative to the direction of the test: moving with stocks' break or against it
    # Stocks and the dollar usually move opposite. For a high (stocks pushing up), DXY falling = "aligned".
    signed = -out["side"] * out["dxy_day_bp"]
    out["dxy_vs_move"] = pd.cut(signed, [-1e9, -15, 15, 1e9], labels=["dxy_against", "dxy_flat", "dxy_aligned"])
    signed_prev = -out["side"] * out["dxy_prev_bp"]
    out["dxy_prev_vs_move"] = pd.cut(signed_prev, [-1e9, -20, 20, 1e9], labels=["dxy_against", "dxy_flat", "dxy_aligned"])
    # VIX intraday: for a high being tested, VIX falling supports the break ("aligned")
    signed_v = -out["side"] * out["vix_chg_pct"]
    out["vix_vs_move"] = pd.cut(signed_v, [-1e9, -3, 3, 1e9], labels=["vix_against", "vix_flat", "vix_aligned"])
    hour = pd.DatetimeIndex(out["touch_bar"]).tz_convert(s.TZ).hour
    out["touch_session"] = np.select(
        [(hour >= 18) | (hour < 2), hour < 5, hour < 9, hour < 11, hour < 13, hour < 16],
        ["overnight", "london", "pre_ny", "ny_am", "midday", "ny_pm"], default="after_close")
    return out


# ------------------------------------------------------------------ statistics

def wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def pval_vs(p0: float, k: float, n: float) -> float:
    """Two-sided normal-approximation p-value for a proportion k/n vs p0."""
    if n == 0:
        return np.nan
    from math import erf, sqrt
    z = (k / n - p0) / sqrt(p0 * (1 - p0) / n)
    return 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))


def bh(p: pd.Series) -> pd.Series:
    """Benjamini-Hochberg adjusted p-values (false discovery rate)."""
    p = p.astype(float)
    ok = p.notna()
    q = pd.Series(np.nan, index=p.index)
    pv = p[ok].sort_values()
    m = len(pv)
    if m == 0:
        return q
    adj = (pv * m / np.arange(1, m + 1)).iloc[::-1].cummin().iloc[::-1].clip(upper=1)
    q[adj.index] = adj
    return q


def summarize(ev: pd.DataFrame, by: list[str], race: str = "race_0.25") -> pd.DataFrame:
    """Reversal-first rate with 95% CI, sample sizes and mean returns per group."""
    def f(g: pd.DataFrame) -> pd.Series:
        r = g[race].dropna()
        n, k = len(r), r.sum()
        lo, hi = wilson(k, n)
        return pd.Series({
            "events": len(g), "decided": n, "reversal_rate": k / n if n else np.nan, "ci_low": lo, "ci_high": hi,
            "ret_1h": g["ret_1h"].mean(), "ret_4h": g["ret_4h"].mean(), "ret_eod": g["ret_eod"].mean(),
            "p_vs_50": pval_vs(0.5, k, n),
        })
    return ev.groupby(by, observed=True).apply(f, include_groups=False).reset_index()
