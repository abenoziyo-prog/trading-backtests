"""Overnight (Asia + London) research on NSX, last 18 months: what moves price between the US close and the US open?

    python scripts/overnight_research.py        # writes reports/overnight/ (CSVs + charts)

Night D runs from D-1 18:00 ET (futures reopen) to D 09:29 ET. Levels come from the previous US session (09:30-16:00):
PDH, PDL, PDC; R0 = PDH - PDL. Times are America/New_York. Periods: first 12 months (2025-04-10 -> 2026-04-09) and
last 6 months (2026-04-10 -> 2026-10-09) are reported separately, so patterns can be checked for stability.

Layer A  seasonality: mean move and volatility of every 15-minute bucket from 18:00 to 09:30 ET.
Layer B  session kick-offs at their exact local opening times (DST-correct):
           globex 18:00 ET, tokyo 09:00 JST, hk/china 09:30 HKT, fixed 21:00 ET, eu_preopen 07:00 UK,
           london 08:00 UK, us_data 08:30 ET.
         First 5/15/30 minutes, the 60 minutes before, and whether the first 15 minutes continue or reverse over the
         next 60/120 minutes.
Layer C  reactions at levels overnight: first touch of PDH / PDL / PDC (and, in the London window, the Asia
         20:00-02:00 high/low). Rejection vs break decided by which comes first: price moving k x R0 back inside,
         or k x R0 beyond (k = 0.05 / 0.10 / 0.20), within the night. Split by session (Asia 18:00-02:00, London
         02:00-09:30) and by how the touch candle closed (1/3/5/10-minute candles: wick only vs close beyond).
Layer D  conditions for the level reactions and kick-offs: prior US day up/down, evening gap (18:00 open vs PDC),
         VIX regime (previous close), DXY overnight move, Monday.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "overnight"
NY = ZoneInfo("America/New_York")
START, END, SPLIT = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09"), pd.Timestamp("2026-04-10")
PERIODS = {"first 12m": (START, SPLIT - pd.Timedelta(days=1)), "last 6m": (SPLIT, END), "18m": (START, END)}
TFS = [1, 3, 5, 10]
EVENTS = {"globex 18:00 ET": ("America/New_York", time(18, 0), -1), "tokyo 09:00 JST": ("Asia/Tokyo", time(9, 0), 0),
          "21:00 ET": ("America/New_York", time(21, 0), -1), "hk/china 09:30 HKT": ("Asia/Hong_Kong", time(9, 30), 0),
          "eu pre-open 07:00 UK": ("Europe/London", time(7, 0), 0), "london 08:00 UK": ("Europe/London", time(8, 0), 0),
          "us data 08:30 ET": ("America/New_York", time(8, 30), 0)}


# ---------------------------------------------------------------- data
def load_nights() -> list[dict]:
    lv = pd.read_csv(ROOT / "reports/us_session_levels/daily_levels_NSX.csv", index_col=0, parse_dates=[0])
    m = s.load(s.INSTRUMENTS["NSX"])
    m = m[(m.index >= pd.Timestamp("2025-03-25", tz=s.TZ)) & (m.index < pd.Timestamp("2026-10-10", tz=s.TZ))]
    mod = m.index.hour * 60 + m.index.minute
    rth = m[(mod >= 570) & (mod < 960)]
    us_open = rth.open.groupby(rth.index.tz_localize(None).normalize()).first()
    vix = pd.read_parquet(ROOT / "data/free/VIX_daily_cboe.parquet").close
    vix.index = pd.to_datetime(vix.index).normalize()
    dxy = pd.read_parquet(ROOT / "data/free/DXY_1min_rebuilt.parquet", columns=["close"])
    dxy = dxy[(dxy.index >= pd.Timestamp("2025-03-25", tz=s.TZ)) & (dxy.index < pd.Timestamp("2026-10-10", tz=s.TZ))].close
    dxy_t, dxy_v = dxy.index.asi8, dxy.to_numpy()
    idx = m.index
    nights = []
    for day, r in lv.iterrows():
        if not (START <= day <= END) or not (r.PDH - r.PDL) > 0:
            continue
        prev = pd.Timestamp(r.prev_day)
        t0 = pd.Timestamp(datetime.combine((day - pd.Timedelta(days=1)).date(), time(18, 0)), tz=NY)
        t1 = pd.Timestamp(datetime.combine(day.date(), time(9, 30)), tz=NY)
        a, b = idx.searchsorted(t0), idx.searchsorted(t1)
        g = m.iloc[a:b]
        if len(g) < 800 or prev not in us_open.index:
            continue
        rel = ((g.index - t0).total_seconds() // 60).astype(int).to_numpy()
        vp = vix.loc[:prev]
        k0 = np.searchsorted(dxy_t, (pd.Timestamp(prev, tz=NY) + pd.Timedelta(hours=16)).value, side="right") - 1
        k1 = np.searchsorted(dxy_t, (t1 - pd.Timedelta(minutes=1)).value, side="right") - 1
        nights.append(dict(day=day, t0=t0, rel=rel, t=g.index, o=g.open.to_numpy(), h=g.high.to_numpy(), l=g.low.to_numpy(),
                           c=g.close.to_numpy(), PDH=r.PDH, PDL=r.PDL, PDC=r.PDC, R0=r.PDH - r.PDL, prev_open=us_open.loc[prev],
                           vix=vp.iloc[-1] if len(vp) else np.nan,
                           dxy_chg=(dxy_v[k1] / dxy_v[k0] - 1) * 100 if k0 >= 0 and k1 >= 0 else np.nan))
    return nights


def px_before(n: dict, rel_min: int) -> float:
    """Close of the last bar that started before rel_min (minutes after 18:00); NaN if none."""
    i = np.searchsorted(n["rel"], rel_min) - 1
    return n["c"][i] if i >= 0 else np.nan


def event_rel(n: dict, tz: str, t: time, day_offset: int) -> int:
    d = (n["day"] + pd.Timedelta(days=day_offset)).date()
    local = datetime.combine(d, t, tzinfo=ZoneInfo(tz))
    return int((pd.Timestamp(local).tz_convert(NY) - n["t0"]).total_seconds() // 60)


def period_of(day: pd.Timestamp) -> str:
    return "first 12m" if day < SPLIT else "last 6m"


# ---------------------------------------------------------------- layer A
def layer_a(nights: list[dict]) -> pd.DataFrame:
    rows = []
    for n in nights:
        for b in range(0, 930, 15):
            p0, p1 = px_before(n, b), px_before(n, b + 15)
            if np.isfinite(p0) and np.isfinite(p1):
                rows.append(dict(day=n["day"], bucket=b, move_pts=p1 - p0, move_bp=(p1 / p0 - 1) * 1e4))
    d = pd.DataFrame(rows)
    d["period"] = d.day.map(period_of)
    out = []
    for per, g in list(d.groupby("period")) + [("18m", d)]:
        for b, x in g.groupby("bucket"):
            v = x.move_bp
            out.append(dict(period=per, bucket=b, start=(datetime(2000, 1, 1, 18) + timedelta(minutes=int(b))).strftime("%H:%M"),
                            n=len(v), mean_bp=v.mean(), t=v.mean() / (v.std(ddof=1) / np.sqrt(len(v))),
                            mean_pts=x.move_pts.mean(), up_pct=(v > 0).mean(), abs_bp=v.abs().mean()))
    return pd.DataFrame(out)


def chart_a(a: pd.DataFrame) -> None:
    x = a[a.period == "18m"].sort_values("bucket")
    f = a[a.period == "first 12m"].set_index("bucket").mean_bp
    l6 = a[a.period == "last 6m"].set_index("bucket").mean_bp
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 8), dpi=150, sharex=True, gridspec_kw=dict(height_ratios=[3, 2]))
    fig.patch.set_facecolor("#fcfcfb")
    pos = np.arange(len(x))
    ax1.bar(pos - 0.2, f.reindex(x.bucket).values, width=0.4, color="#2a78d6", label="first 12 months")
    ax1.bar(pos + 0.2, l6.reindex(x.bucket).values, width=0.4, color="#eb6834", label="last 6 months")
    ax1.axhline(0, color="#8a8984", lw=0.8)
    ax1.set_ylabel("Mean move per 15 min (bp)", color="#3d3d3a")
    ax1.legend(frameon=False, fontsize=9, loc="upper right")
    ax2.plot(pos, x.abs_bp.values, color="#1baf7a", lw=2)
    ax2.set_ylabel("Volatility: mean |move| (bp)", color="#3d3d3a")
    marks = {"18:00": "Globex open", "19:00": "Tokyo (winter)", "20:00": "Tokyo (summer)", "21:30": "HK/China (summer)",
             "02:00": "EU pre-open", "03:00": "London open", "08:30": "US data", "09:15": ""}
    for ax in (ax1, ax2):
        ax.set_facecolor("#fcfcfb")
        ax.grid(axis="y", color="#e6e5e0", lw=0.8); ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color("#c3c2b7")
        ax.tick_params(colors="#5f5e5a", labelsize=8)
        for st, lab in marks.items():
            if lab and st in set(x.start):
                i = list(x.start).index(st)
                ax.axvline(i - 0.5, color="#c3c2b7", lw=0.8, ls=(0, (3, 3)))
                if ax is ax1:
                    ax.text(i - 0.4, ax1.get_ylim()[1], lab, fontsize=8, color="#5f5e5a", va="top")
    ax2.set_xticks(pos[::4]); ax2.set_xticklabels(list(x.start)[::4], rotation=0)
    ax2.set_xlabel("Bucket start (ET), night = 18:00 → 09:30", color="#3d3d3a")
    fig.suptitle("NSX overnight seasonality, 15-minute buckets  ·  2025-04-10 → 2026-10-09", x=0.01, ha="left", fontsize=12, color="#1f1f1d")
    fig.tight_layout()
    fig.savefig(OUT / "layer_a_seasonality.png", facecolor=fig.get_facecolor())


# ---------------------------------------------------------------- layer B
def layer_b(nights: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for n in nights:
        prior_up = n["PDC"] > n["prev_open"]
        gap = (n["o"][0] - n["PDC"]) / n["R0"]
        for ev, (tz, t, off) in EVENTS.items():
            e = event_rel(n, tz, t, off)
            if e < 0 or e + 135 > 930:
                continue
            i = np.searchsorted(n["rel"], e)
            if i >= len(n["rel"]) or n["rel"][i] - e > 2:
                continue
            p0 = n["o"][i]
            pre = p0 - px_before(n, e - 60) if e >= 60 else np.nan
            r5, r15, r30 = (px_before(n, e + k) - p0 for k in (5, 15, 30))
            n60, n120 = px_before(n, e + 75) - px_before(n, e + 15), px_before(n, e + 135) - px_before(n, e + 15)
            sel_b = (n["rel"] >= e - 60) & (n["rel"] < e)
            sel_a = (n["rel"] >= e) & (n["rel"] < e + 60)
            rng_b = n["h"][sel_b].max() - n["l"][sel_b].min() if sel_b.any() else np.nan
            rng_a = n["h"][sel_a].max() - n["l"][sel_a].min() if sel_a.any() else np.nan
            rows.append(dict(day=n["day"], event=ev, et=(n["t0"] + pd.Timedelta(minutes=e)).strftime("%H:%M"), R0=n["R0"],
                             pre60=pre, r5=r5, r15=r15, r30=r30, next60=n60, next120=n120, range_before=rng_b,
                             range_after=rng_a, prior_up=prior_up, evening_gap_R=gap, vix=n["vix"], monday=n["day"].dayofweek == 0))
    d = pd.DataFrame(rows)
    d["period"] = d.day.map(period_of)
    out = []
    for (per, ev), g in list(d.groupby(["period", "event"])) + [(("18m", ev), g) for ev, g in d.groupby("event")]:
        g = g.dropna(subset=["r15", "next60"])
        sgn = np.sign(g.r15)
        mom60, mom120 = sgn * g.next60, sgn * g.next120
        out.append(dict(period=per, event=ev, n=len(g), et_times=",".join(sorted(g.et.unique())),
                        first15_abs_pts=g.r15.abs().mean(), first15_mean_pts=g.r15.mean(),
                        cont60_pct=(mom60 > 0).mean(), cont60_pts=mom60.mean(), cont60_t=mom60.mean() / (mom60.std(ddof=1) / np.sqrt(len(g))),
                        cont120_pct=(mom120 > 0).mean(), cont120_pts=mom120.mean(),
                        pre_follow_pct=(np.sign(g.pre60) * g.r15 > 0).mean(),
                        range_ratio_median=(g.range_after / g.range_before).median(), corr15_60=np.corrcoef(g.r15, g.next60)[0, 1]))
    return d, pd.DataFrame(out)


# ---------------------------------------------------------------- layer C
def first_passage(n: dict, i: int, level: float, side_beyond: int, k: float, end_rel: int = 929) -> str:
    """After a touch at bar i: 'reject' if price reaches level -/+ k*R0 back inside first, 'break' if level +/- k*R0
    beyond first, else 'none'. side_beyond = +1 if beyond means above the level."""
    j_end = np.searchsorted(n["rel"], end_rel, side="right")
    h, l = n["h"][i:j_end], n["l"][i:j_end]
    R = k * n["R0"]
    if side_beyond == 1:
        inside, beyond = l <= level - R, h >= level + R
    else:
        inside, beyond = h >= level + R, l <= level - R
    ii = int(np.argmax(inside)) if inside.any() else None
    bb = int(np.argmax(beyond)) if beyond.any() else None
    if ii is None and bb is None:
        return "none"
    if bb is None or (ii is not None and ii < bb):
        return "reject"
    if ii is None or bb < ii:
        return "break"
    return "same bar"


def touch_candle_close(n: dict, i: int, level: float, side_beyond: int, tf: int) -> str:
    """How the tf-minute clock candle containing the touch closed: beyond the level, or back inside (wick)."""
    start = (n["rel"][i] // tf) * tf
    sel = (n["rel"] >= start) & (n["rel"] < start + tf)
    c = n["c"][sel][-1]
    return "closed beyond" if (c > level if side_beyond == 1 else c < level) else "wick only"


def layer_c(nights: list[dict]) -> pd.DataFrame:
    rows = []
    for n in nights:
        asia = (n["rel"] >= 120) & (n["rel"] < 480)   # 20:00-02:00
        ah, al = (n["h"][asia].max(), n["l"][asia].min()) if asia.sum() > 300 else (np.nan, np.nan)
        lv = {"PDH": (n["PDH"], 0), "PDL": (n["PDL"], 0), "PDC": (n["PDC"], 0), "Asia high": (ah, 480), "Asia low": (al, 480)}
        for name, (level, from_rel) in lv.items():
            if not np.isfinite(level):
                continue
            start = np.searchsorted(n["rel"], from_rel)
            if start >= len(n["c"]):
                continue
            ref = n["o"][start]
            if name in ("PDH", "Asia high"):
                side, cond = 1, (ref < level)
            elif name in ("PDL", "Asia low"):
                side, cond = -1, (ref > level)
            else:  # PDC: approached from whichever side the night starts
                side, cond = (1 if ref < level else -1), True
            if not cond:
                rows.append(dict(day=n["day"], level=name, touched=False, opened_beyond=True))
                continue
            hit = (n["h"][start:] >= level) if side == 1 else (n["l"][start:] <= level)
            if not hit.any():
                rows.append(dict(day=n["day"], level=name, touched=False, opened_beyond=False))
                continue
            i = start + int(np.argmax(hit))
            rel = n["rel"][i]
            row = dict(day=n["day"], level=name, touched=True, opened_beyond=False, touch_et=n["t"][i].strftime("%H:%M"),
                       session="asia" if rel < 480 else "london", prior_up=n["PDC"] > n["prev_open"],
                       evening_gap_R=(n["o"][0] - n["PDC"]) / n["R0"], vix=n["vix"], dxy_al=side * n["dxy_chg"],
                       monday=n["day"].dayofweek == 0)
            for k in (0.05, 0.10, 0.20):
                row[f"outcome_{k:.2f}"] = first_passage(n, i, level, side, k)
            for tf in TFS:
                row[f"candle_{tf}m"] = touch_candle_close(n, i, level, side, tf)
            rows.append(row)
    d = pd.DataFrame(rows)
    d["period"] = d.day.map(period_of)
    return d


def summarize_c(d: pd.DataFrame) -> pd.DataFrame:
    out = []
    t = d[d.touched]
    for per in ("first 12m", "last 6m", "18m"):
        x = t if per == "18m" else t[t.period == per]
        nights = d if per == "18m" else d[d.period == per]
        for lvl, g in x.groupby("level"):
            base = dict(period=per, level=lvl, nights=nights[nights.level == lvl].day.nunique(), touches=len(g),
                        opened_beyond=int(nights[(nights.level == lvl)].opened_beyond.sum()))
            for split, sub in [("all", g), ("asia", g[g.session == "asia"]), ("london", g[g.session == "london"])] + \
                              [(f"{tf}m {c}", g[g[f"candle_{tf}m"] == c]) for tf in TFS for c in ("wick only", "closed beyond")]:
                if len(sub) == 0:
                    continue
                r = dict(base, split=split, n=len(sub))
                for k in ("0.05", "0.10", "0.20"):
                    o = sub[f"outcome_{k}"]
                    decided = o.isin(["reject", "break"])
                    r[f"reject_first_{k}"] = (o[decided] == "reject").mean() if decided.any() else np.nan
                out.append(r)
    return pd.DataFrame(out)


# ---------------------------------------------------------------- layer D
def layer_d(c: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    t = c[c.touched].copy()
    t["vix_regime"] = pd.cut(t.vix, [0, 16, 20, 100], labels=["VIX < 16", "VIX 16-20", "VIX > 20"]).astype(str)
    t["gap"] = pd.cut(t.evening_gap_R, [-9, -0.1, 0.1, 9], labels=["evening gap down", "flat", "evening gap up"]).astype(str)
    t["prior"] = np.where(t.prior_up, "prior US day up", "prior US day down")
    t["dxy"] = np.where(t.dxy_al > 0, "DXY with the level side", "DXY against")
    t["dow"] = np.where(t.monday, "Monday", "Tue-Fri")
    out = []
    for per in ("first 12m", "last 6m"):
        x = t[t.period == per]
        for lvl, g in x.groupby("level"):
            for fac in ("prior", "gap", "vix_regime", "dxy", "dow", "session"):
                for val, sub in g.groupby(fac):
                    o = sub["outcome_0.10"]
                    dec = o.isin(["reject", "break"])
                    out.append(dict(period=per, level=lvl, factor=fac, value=val, n=int(dec.sum()),
                                    reject_first_0_10=(o[dec] == "reject").mean() if dec.any() else np.nan))
    bd = b.dropna(subset=["r15", "next60"]).copy()
    bd["mom60"] = np.sign(bd.r15) * bd.next60
    bd["prior"] = np.where(bd.prior_up, "prior US day up", "prior US day down")
    for per in ("first 12m", "last 6m"):
        x = bd[bd.period == per]
        for ev, g in x.groupby("event"):
            for val, sub in g.groupby("prior"):
                out.append(dict(period=per, level=f"kick-off {ev}", factor="prior", value=val, n=len(sub),
                                cont60_pct=(sub.mom60 > 0).mean(), cont60_pts=sub.mom60.mean()))
    return pd.DataFrame(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    nights = load_nights()
    print(f"{len(nights)} nights", flush=True)
    a = layer_a(nights); a.to_csv(OUT / "layer_a_seasonality.csv", index=False); chart_a(a)
    braw, b = layer_b(nights); braw.to_csv(OUT / "layer_b_events_raw.csv", index=False); b.to_csv(OUT / "layer_b_kickoffs.csv", index=False)
    c = layer_c(nights); c.to_csv(OUT / "layer_c_touches_raw.csv", index=False)
    cs = summarize_c(c); cs.to_csv(OUT / "layer_c_levels.csv", index=False)
    dd = layer_d(c, braw); dd.to_csv(OUT / "layer_d_conditions.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    x = a[a.period != "18m"].pivot(index="start", columns="period", values=["mean_pts", "t"])
    x["abs"] = a[a.period == "18m"].set_index("start").abs_bp
    order = a[a.period == "18m"].sort_values("bucket").start
    print("\nLAYER A (mean pts, t) top buckets:")
    xa = a[a.period == "18m"].assign(abs_t=lambda z: z.t.abs()).sort_values("abs_t", ascending=False).head(10)
    print(xa[["start", "n", "mean_pts", "mean_bp", "t", "up_pct", "abs_bp"]].round(2).to_string(index=False))
    print("\nLAYER B:")
    print(b.sort_values(["event", "period"])[["event", "period", "n", "et_times", "first15_abs_pts", "cont60_pct", "cont60_pts", "cont60_t",
                                             "cont120_pct", "cont120_pts", "pre_follow_pct", "range_ratio_median"]].round(2).to_string(index=False))
    print("\nLAYER C (split all / asia / london):")
    print(cs[cs.split.isin(["all", "asia", "london"])].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
