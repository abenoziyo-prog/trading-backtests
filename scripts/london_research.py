"""London-session strategy research on NSX (last 18 months): five strategy families, 1/2/3/5/10-minute candles.

    python scripts/london_research.py          # writes reports/london/trades.parquet and grid.csv

Day D's overnight window runs from D-1 18:00 ET to D 09:29 ET (everything is flat before the US open, so
London trades never overlap the US bot). Times below are ET (minutes after midnight of day D).
  Asia 20:00-00:00, Asia+pre-London 20:00-02:00, London opening range (OR) 02:00-03:00.
R0 = previous US session range (PDH - PDL), 1-day look-back.

Families (research sources in reports/london/REPORT.md)
  DRIFT     European-open drift (NY Fed "Overnight Drift"): enter at 01:00/02:00, exit 03:00/05:00/09:29;
            mode long / reversal of yesterday's US move / long only after a US down day; stop none/0.25/0.5 R0
  ASIA_BO   London breakout of the Asia range: first fresh close beyond the range from 02:00/03:00,
            entries until 05:00/08:00; stop opposite side / midpoint; target 1R / 2R / 09:29
  ASIA_SWP  Asia-range sweep and reversal ("Judas"): a candle trades beyond the Asia high (low) and closes
            back inside -> short (long); window 02:00-05:00 or 03:00-05:30; stop = window extreme + 0.05 R0;
            target range midpoint / opposite side / 1R / 2R
  LON_OR    02:00-03:00 opening-range breakout from 03:00, entries until 05:00/08:00; stop opposite / mid;
            target 1R / 2R / 09:29
  PD_SWP    PDH / PDL sweep and reversal during 02:00-08:00; stop = extreme + 0.05 R0;
            target 0.25 R0 / 0.5 R0 / previous US close
Approved rules: multiple trades per day (one position per strategy), same-minute stop+target = stop,
1 NQ contract ($20/pt), 1 pt round trip, no slippage. Every trade carries the multi-variable features
known at its entry minute (see FEATURES).
"""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd

from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "london"
START, END = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09")
OPT_START = pd.Timestamp("2026-04-10")  # last 6 months: optimization period
COST, DPP = 1.0, 20.0
TFS = [1, 2, 3, 5, 10]
FLAT = 570  # 09:30: flat before the US open


def load_days() -> list[dict]:
    lv = pd.read_csv(ROOT / "reports/us_session_levels/daily_levels_NSX.csv", index_col=0, parse_dates=[0])
    m = s.load(s.INSTRUMENTS["NSX"])
    m = m[(m.index >= pd.Timestamp("2025-03-20", tz=s.TZ)) & (m.index < pd.Timestamp("2026-10-10", tz=s.TZ))]
    idx = m.index
    # previous US session open (for yesterday's US move)
    mod_all = idx.hour * 60 + idx.minute
    rth = m[(mod_all >= 570) & (mod_all < 960)]
    us_open = rth.open.groupby(rth.index.tz_localize(None).normalize()).first()
    vix = pd.read_parquet(ROOT / "data/free/VIX_daily_cboe.parquet")
    vix.index = pd.to_datetime(vix.index).normalize()
    dxy = pd.read_parquet(ROOT / "data/free/DXY_1min_rebuilt.parquet", columns=["close"])
    dxy = dxy[(dxy.index >= pd.Timestamp("2025-03-20", tz=s.TZ)) & (dxy.index < pd.Timestamp("2026-10-10", tz=s.TZ))].close
    dxy.index = dxy.index.tz_convert(s.TZ)
    dxy_t, dxy_v = dxy.index.asi8, dxy.to_numpy()
    days = []
    for day, r in lv.iterrows():
        if not (START <= day <= END) or not (r.PDH - r.PDL) > 0:
            continue
        d0 = pd.Timestamp(day, tz=s.TZ)
        a, b = idx.searchsorted(d0 - pd.Timedelta(hours=6)), idx.searchsorted(d0 + pd.Timedelta(minutes=FLAT))
        g = m.iloc[a:b]
        if len(g) < 600:  # need most of the overnight session (18:00-09:29 = 930 minutes)
            continue
        mod = ((g.index - d0).total_seconds() // 60).astype(int).to_numpy()
        prev = pd.Timestamp(r.prev_day)
        if prev not in us_open.index:
            continue
        prev_close_t = (pd.Timestamp(prev, tz=s.TZ) + pd.Timedelta(hours=16)).value
        k = np.searchsorted(dxy_t, prev_close_t, side="right") - 1
        vprev = vix.close.loc[:prev]
        days.append(dict(day=day, t=g.index, mod=mod, o=g.open.to_numpy(), h=g.high.to_numpy(), l=g.low.to_numpy(),
                         c=g.close.to_numpy(), PDH=r.PDH, PDL=r.PDL, PDC=r.PDC, R0=r.PDH - r.PDL,
                         prev_us_open=us_open.loc[prev], vix_prev=vprev.iloc[-1] if len(vprev) else np.nan,
                         vix_chg=(vprev.iloc[-1] / vprev.iloc[-2] - 1) * 100 if len(vprev) > 1 else np.nan,
                         dxy_prev_close=dxy_v[k] if k >= 0 else np.nan, dxy_t=dxy_t, dxy_v=dxy_v))
    return days


def rng(d: dict, a: int, b: int) -> tuple[float, float] | None:
    sel = (d["mod"] >= a) & (d["mod"] < b)
    if sel.sum() < 0.8 * (b - a):
        return None
    return d["h"][sel].max(), d["l"][sel].min()


def candles(d: dict, a: int, b: int, tf: int) -> dict | None:
    """Clock-time candles of tf minutes over [a, b) (minutes after midnight of day D)."""
    i0, i1 = np.searchsorted(d["mod"], a), np.searchsorted(d["mod"], b)
    if i1 - i0 < 2:
        return None
    key = (d["mod"][i0:i1] - a) // tf
    st = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    en = np.r_[st[1:], len(key)] - 1
    return dict(h=np.maximum.reduceat(d["h"][i0:i1], st), l=np.minimum.reduceat(d["l"][i0:i1], st),
                c=d["c"][i0 + en], o=d["o"][i0 + st], last=i0 + en, i0=i0)


def exit_trade(d: dict, pos: int, side: int, stop: float | None, target: float | None, end: int) -> tuple[int, float, str]:
    j_end = np.searchsorted(d["mod"], end) - 1
    if j_end < pos:
        return pos, d["o"][pos], "time"
    h, l = d["h"][pos:j_end + 1], d["l"][pos:j_end + 1]
    s_hit = np.zeros(len(h), bool) if stop is None else ((l <= stop) if side == 1 else (h >= stop))
    t_hit = np.zeros(len(h), bool) if target is None else ((h >= target) if side == 1 else (l <= target))
    i_s = int(np.argmax(s_hit)) if s_hit.any() else None
    i_t = int(np.argmax(t_hit)) if t_hit.any() else None
    if i_s is not None and (i_t is None or i_s <= i_t):
        return pos + i_s, stop, "stop"
    if i_t is not None:
        return pos + i_t, target, "target"
    return j_end, d["c"][j_end], "time"


FEATURES = ["asia_range_R", "pre_range_R", "or_range_R", "prev_ret_al", "close_loc_al", "gap_al", "pos_pd_al",
            "pre_sweep_al", "vix_prev", "vix_chg", "dxy_al", "R0_pct", "entry_hour", "monday", "friday"]


def features(d: dict, ctx: dict, idx: int, side: int, px: float) -> dict:
    R0 = d["R0"]
    t = d["t"][idx]
    k = np.searchsorted(d["dxy_t"], t.value - 60_000_000_000, side="right") - 1  # last DXY bar closed by the entry minute
    dxy_now = d["dxy_v"][k] if k >= 0 else np.nan
    return dict(asia_range_R=ctx["asia_range_R"], pre_range_R=ctx["pre_range_R"],
                or_range_R=ctx["or_range_R"] if d["mod"][idx] >= 180 else np.nan,
                prev_ret_al=side * (d["PDC"] - d["prev_us_open"]) / R0,
                close_loc_al=side * ((d["PDC"] - d["PDL"]) / R0 - 0.5),
                gap_al=side * (px - d["PDC"]) / R0, pos_pd_al=side * (px - (d["PDH"] + d["PDL"]) / 2) / R0,
                pre_sweep_al=side * ctx["pre_sweep"], vix_prev=d["vix_prev"], vix_chg=d["vix_chg"],
                dxy_al=side * (dxy_now / d["dxy_prev_close"] - 1) * 100, R0_pct=R0 / d["PDC"] * 100,
                entry_hour=d["mod"][idx] / 60, monday=float(d["day"].dayofweek == 0), friday=float(d["day"].dayofweek == 4))


def day_context(d: dict) -> dict | None:
    asia, ext, pre, lor = rng(d, -240, 0), rng(d, -240, 120), rng(d, 0, 120), rng(d, 120, 180)
    if asia is None or ext is None or pre is None:
        return None
    R0 = d["R0"]
    sweep = (1 if pre[0] > asia[0] else 0) - (1 if pre[1] < asia[1] else 0)  # +1 swept Asia high only, -1 low only
    return dict(asia=asia, asia_ext=ext, lor=lor, asia_range_R=(asia[0] - asia[1]) / R0, pre_range_R=(pre[0] - pre[1]) / R0,
                or_range_R=(lor[0] - lor[1]) / R0 if lor else np.nan, pre_sweep=sweep)


def walk(d, ctx, cd, cand_long, cand_short, make, deadline, end, rows, key):
    """Take candidate signal candles in order, one position at a time; make(i, side) -> (stop, target) or None."""
    sig = np.flatnonzero(cand_long | cand_short)
    busy = -1
    for i in sig:
        last = cd["last"][i]
        if last <= busy:
            continue
        if d["mod"][last] + 1 > deadline:
            break
        pos = last + 1
        if pos >= len(d["c"]) or d["mod"][pos] >= end:
            break
        side = 1 if cand_long[i] else -1
        st = make(i, side)
        if st is None:
            continue
        stop, target = st
        entry = cd["c"][i]
        if stop is not None and (entry - stop) * side <= 0:
            continue
        if target is not None and (target - entry) * side <= 0:
            continue
        j, px, why = exit_trade(d, pos, side, stop, target, end)
        pts = side * (px - entry) - COST
        risk = abs(entry - stop) if stop is not None else 0.25 * d["R0"]
        rows.append(dict(**key, day=d["day"], side=side, entry_time=d["t"][pos], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                         entry=entry, stop=stop, target=target, exit=px, exit_reason=why, pnl_usd=pts * DPP, r_multiple=pts / risk,
                         **features(d, ctx, pos, side, entry)))
        busy = j


def run_day(d: dict, rows: list) -> None:
    ctx = day_context(d)
    if ctx is None:
        return
    R0 = d["R0"]
    # DRIFT: time-based, once per day
    for ent, ex, mode, stp in itertools.product([60, 120], [180, 300, FLAT], ["long", "reversal", "long_after_down"], [None, 0.25, 0.5]):
        i = np.searchsorted(d["mod"], ent)
        if i == 0 or i >= len(d["c"]) or d["mod"][i] >= ex:
            continue
        prev = d["PDC"] - d["prev_us_open"]
        if mode == "long_after_down" and prev >= 0:
            continue
        side = 1 if mode != "reversal" else (-1 if prev > 0 else 1)
        entry = d["c"][i - 1]
        stop = None if stp is None else entry - side * stp * R0
        j, px, why = exit_trade(d, i, side, stop, None, ex)
        pts = side * (px - entry) - COST
        rows.append(dict(family="DRIFT", config=f"in{ent // 60:02d}:00|out{ex // 60:02d}:{ex % 60:02d}|{mode}|stop{stp}", tf=0,
                         day=d["day"], side=side, entry_time=d["t"][i], exit_time=d["t"][j] + pd.Timedelta(minutes=1), entry=entry,
                         stop=stop, target=None, exit=px, exit_reason=why, pnl_usd=pts * DPP,
                         r_multiple=pts / (stp * R0 if stp else 0.25 * R0), **features(d, ctx, i, side, entry)))
    for tf in TFS:
        # ASIA_BO and LON_OR: fresh close beyond a range
        bo_specs = [(f"ASIA_BO", rname, start, dl) for rname in ("asia", "asia_ext") for start in (120, 180) for dl in (300, 480)]
        bo_specs += [("LON_OR", "lor", 180, dl) for dl in (300, 480)]
        for fam, rname, start, dl in bo_specs:
            r_ = ctx[rname]
            if r_ is None:
                continue
            H, L = r_
            cd = candles(d, start, FLAT, tf)
            if cd is None:
                continue
            before = cd["i0"] - 1
            inside = (cd["c"] >= L) & (cd["c"] <= H)
            prev_in = np.r_[True if fam == "LON_OR" else (L <= d["c"][before] <= H if before >= 0 else True), inside[:-1]]
            up, dn = (cd["c"] > H) & prev_in, (cd["c"] < L) & prev_in
            for dirn, stp, tg in itertools.product(["both", "long", "short"], ["opp", "mid"], [1.0, 2.0, None]):
                cl = up if dirn != "short" else np.zeros_like(up)
                cs = dn if dirn != "long" else np.zeros_like(dn)

                def make(i, side, stp=stp, tg=tg):
                    stop = (L if side == 1 else H) if stp == "opp" else (H + L) / 2
                    risk = (cd["c"][i] - stop) * side
                    return stop, (None if tg is None else cd["c"][i] + side * tg * risk)
                walk(d, ctx, cd, cl, cs, make, dl, FLAT, rows,
                     dict(family=fam, config=f"{rname}|from{start // 60:02d}:00|until{dl // 60:02d}:00|{dirn}|stop_{stp}|tgt_{tg}", tf=tf))
        # ASIA_SWP and PD_SWP: sweep beyond a level, close back inside
        sw_specs = [("ASIA_SWP", rname, a, b) for rname in ("asia", "asia_ext") for a, b in ((120, 300), (180, 330))]
        sw_specs += [("PD_SWP", "pd", 120, 480)]
        for fam, rname, a, b in sw_specs:
            H, L = (d["PDH"], d["PDL"]) if rname == "pd" else ctx[rname]
            cd = candles(d, a, b, tf)
            if cd is None:
                continue
            prev_c = np.r_[d["c"][cd["i0"] - 1] if cd["i0"] > 0 else cd["o"][0], cd["c"][:-1]]
            short_sig = ((cd["h"] > H) | (prev_c > H)) & (cd["c"] < H)
            long_sig = ((cd["l"] < L) | (prev_c < L)) & (cd["c"] > L)
            hi_run, lo_run = np.maximum.accumulate(cd["h"]), np.minimum.accumulate(cd["l"])
            tgts = ["mid", "opp", 1.0, 2.0] if fam == "ASIA_SWP" else [0.25, 0.5, "PDC"]
            for dirn, tg in itertools.product(["both", "long", "short"], tgts):
                cl = long_sig if dirn != "short" else np.zeros_like(long_sig)
                cs = short_sig if dirn != "long" else np.zeros_like(short_sig)

                def make(i, side, tg=tg):
                    stop = hi_run[i] + 0.05 * R0 if side == -1 else lo_run[i] - 0.05 * R0
                    e = cd["c"][i]
                    if tg == "mid":
                        return stop, (H + L) / 2
                    if tg == "opp":
                        return stop, (L if side == -1 else H)
                    if tg == "PDC":
                        return stop, d["PDC"]
                    if fam == "PD_SWP":
                        return stop, e + side * tg * R0
                    return stop, e + side * tg * abs(e - stop)
                walk(d, ctx, cd, cl, cs, make, b, FLAT, rows,
                     dict(family=fam, config=f"{rname}|{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}|{dirn}|tgt_{tg}", tf=tf))


def metrics(g: pd.DataFrame) -> dict:
    p = g.pnl_usd.to_numpy()
    if len(p) == 0:
        return dict(trades=0)
    eq = np.cumsum(p)
    w, lo = p[p > 0], p[p <= 0]
    r = g.r_multiple.to_numpy()
    return dict(trades=len(p), win_rate=len(w) / len(p), profit_factor=w.sum() / -lo.sum() if lo.sum() < 0 else np.inf,
                net_pnl=p.sum(), avg_trade=p.mean(), max_drawdown=(eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min(),
                t_stat=r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 and r.std(ddof=1) > 0 else np.nan,
                avg_minutes=((g.exit_time - g.entry_time).dt.total_seconds() / 60).mean())


PERIODS = {"opt: last 6m": (OPT_START, END), "check: prior 12m": (START, OPT_START - pd.Timedelta(days=1)), "18m": (START, END)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    days = load_days()
    print(f"{len(days)} days", flush=True)
    rows: list = []
    for n, d in enumerate(days):
        run_day(d, rows)
        if n % 50 == 0:
            print(f"  day {n}: {len(rows):,} trades", flush=True)
    t = pd.DataFrame(rows)
    t["cand"] = t.family + "|" + t.config + "|" + t.tf.astype(str) + "m"
    t.to_parquet(OUT / "trades.parquet")
    grid = []
    for (fam, cfg, tf), g in t.groupby(["family", "config", "tf"]):
        for p, (a, b) in PERIODS.items():
            grid.append(dict(family=fam, config=cfg, tf=tf, period=p, **metrics(g[(g.day >= a) & (g.day <= b)])))
    pd.DataFrame(grid).to_csv(OUT / "grid.csv", index=False)
    print(f"{t.cand.nunique()} candidates, {len(t):,} trades")


if __name__ == "__main__":
    main()
