"""How price behaves around its VWAP and VWAP standard-deviation bands, on several timeframes, and a pre-registered
VWAP-band mean-reversion entry test.

    python scripts/vwap_study.py

Data with real volume: QQQ 1-minute from Alpaca, 2024-01-03 -> 2025-12-31 (per-minute VWAP and volume); /NQ and QQQ
1-minute from the Schwab archive (2026-09-10 -> 2026-10-21 locally). NSX (the strategies' /NQ data) has no volume, so
/NQ's VWAP cannot be computed before Schwab: the entry test computes VWAP and bands on QQQ and trades them on /NQ prices
(NSX), mapped by the minute's NQ/QQQ price ratio (quant.vwap.map_bands). Part 6 checks that proxy against /NQ's own VWAP.

Descriptive parts (1-5). For a race between two levels, the random-walk benchmark is the gambler's-ruin probability:
from +k sigma, VWAP (k sigma away) is reached before +(k+1) sigma (1 sigma away) with probability 1 / (k + 1)
(50% at 1 sigma, 33% at 2, 25% at 3). Beating that is the mean-reversion evidence; matching it means no pull.

PRE-REGISTERED ENTRY TEST (fixed 2026-10-27, before any result of it was run). Rule MR2:
  bands       QQQ session VWAP and standard-deviation bands (quant.vwap), mapped onto /NQ minute by minute
  signal      a 5-minute bar (anchored 09:30) closes beyond +/-2 sigma, its close between 10:00 and 15:30;
              above -> short, below -> long; the first signal per side per day only
  entry       the next minute's /NQ open
  stop        the +/-3 sigma band at the signal (fixed price)
  target      the session VWAP, moving: hit when a minute trades through the VWAP known at the previous minute
  exit        16:00 at the latest; stop and target in the same minute = stop; 1 NQ, $20/pt, 1 pt round trip
  windows     design 2024 (may be looked at), holdout 2025 (judged once)
  passes if   the holdout has >= 30 trades, profit factor >= 1.15, t-stat >= 2.0, and net > 0 in both halves of 2025
  neighbours  1- and 15-minute bars; 2.5 sigma signals (reported, not used to choose)
Writes reports/vwap/VWAP_STUDY.md and reports/vwap/mr2_trades.csv.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant import vwap as V  # noqa: E402

OUT = ROOT / "reports" / "vwap"
NY = "America/New_York"
DPP, COST = 20.0, 1.0


def table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def load_qqq() -> pd.DataFrame:
    q = pd.read_parquet(ROOT / "data/free/QQQ_1min_2024_2025.parquet")
    q.index = q.index.tz_convert(NY)
    return q


def load_schwab(name: str) -> pd.DataFrame | None:
    p = ROOT / "data/schwab" / f"{name}_1min.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    d.index = d.index.tz_convert(NY)
    return d


def load_nsx() -> pd.Series:
    n = pd.read_parquet(ROOT / "data/free/NSXUSD_1min_histdata.parquet")
    n.index = n.index.tz_convert(NY)
    return V.rth(n[(n.index >= "2024-01-01") & (n.index < "2026-01-01")])


# ------------------------------------------------------------------ descriptive

def occupancy(d: pd.DataFrame) -> list:
    z = d.z[(d.index.hour * 60 + d.index.minute) >= 600].dropna()      # from 10:00: the bands need 30 minutes to form
    return [f"{(z.abs() > k).mean() * 100:.1f}%" for k in (1, 2, 3)]


def first_true(*masks) -> tuple[int, int]:
    """(which mask fired first, index), checking masks in order at each step (earlier mask wins ties); (-1, -1) if none."""
    best = (-1, -1)
    for w, m in enumerate(masks):
        i = int(np.argmax(m)) if m.any() else -1
        if i >= 0 and (best[1] < 0 or i < best[1]):
            best = (w, i)
    return best


def by_day(d: pd.DataFrame) -> dict:
    return {k: g for k, g in d.groupby(d.index.tz_localize(None).normalize())}


def races(d1: pd.DataFrame, tf: int, k: float, start=600, end=930, fixed: bool = False) -> pd.DataFrame:
    """First close beyond +/-k sigma per side per day on tf-minute bars (close between start and end, minutes after
    midnight); then on 1-minute bars: does price reach the VWAP (back) before the (k+1) sigma band (beyond)?
    Levels are those known at the previous minute's close; a minute touching both counts as beyond. fixed=True freezes
    the (k+1) sigma level at its price when the signal closes (a stop you could actually place). One row per event."""
    bars = V.resample(d1, tf)
    days = by_day(d1)
    out = []
    for day, b in bars.groupby(bars.index.tz_localize(None).normalize()):
        m = days.get(day)
        if m is None:
            continue
        mod = b.index.hour * 60 + b.index.minute + 1
        b = b[(mod >= start) & (mod <= end)]
        for side in (1, -1):
            hit = b[(side * b.z) >= k]
            if hit.empty:
                continue
            t0 = hit.index[0]
            i0 = m.index.searchsorted(t0, side="right")
            if i0 >= len(m):
                continue
            hi, lo = m.high.to_numpy()[i0:], m.low.to_numpy()[i0:]
            pv, ps = m.vwap.to_numpy()[i0 - 1:-1], m.sd.to_numpy()[i0 - 1:-1]
            far = np.full_like(pv, pv[0] + side * (k + 1) * ps[0]) if fixed else pv + side * (k + 1) * ps
            beyond = (hi >= far) if side == 1 else (lo <= far)
            back = (lo <= pv) if side == 1 else (hi >= pv)
            w, i = first_true(beyond, back)
            res = {0: "beyond", 1: "vwap"}.get(w, "neither")
            out.append(dict(day=day, side=side, t=t0, hour=t0.hour, result=res,
                            minutes=None if i < 0 else (m.index[i0 + i] - t0).total_seconds() / 60))
    return pd.DataFrame(out)


def race_row(name, r: pd.DataFrame, k: float) -> list:
    n = len(r)
    if not n:
        return [name, 0, "–", "–", "–", "–"]
    back = (r.result == "vwap").mean()
    se = np.sqrt(back * (1 - back) / n)
    med = r[r.result == "vwap"].minutes.median()
    return [name, n, f"{back * 100:.0f}%", f"{100 / (k + 1):.0f}%", f"{(back - 1 / (k + 1)) / se:+.1f}" if se else "–",
            f"{med:.0f} min" if np.isfinite(med) else "–"]


def vwap_retests(d1: pd.DataFrame) -> pd.DataFrame:
    """Price returns to the session VWAP after being at least 1 sigma away (from 10:00): does it bounce back to 1 sigma
    on the same side before going 1 sigma through? (random walk: 50%)"""
    out = []
    for day, m in d1.groupby(d1.index.tz_localize(None).normalize()):
        m = m[(m.index.hour * 60 + m.index.minute) >= 600]
        if len(m) < 30:
            continue
        z, prev = m.z.to_numpy(), m.shift()
        armed, side, i = False, 0, 0
        while i < len(m):
            if not armed and abs(z[i]) >= 1:
                armed, side = True, int(np.sign(z[i]))
            elif armed and side * z[i] <= 0:      # closed at/through the VWAP: a retest from `side`
                res = "neither"
                for j in range(i + 1, len(m)):
                    if side * z[j] >= 1:
                        res = "bounce"
                        break
                    if side * z[j] <= -1:
                        res = "through"
                        break
                out.append(dict(day=day, side=side, t=m.index[i], result=res))
                armed, i = False, j if res != "neither" else len(m)
                continue
            i += 1
    return pd.DataFrame(out)


def trend_days(d1: pd.DataFrame) -> list[list]:
    g = d1.groupby(d1.index.tz_localize(None).normalize())
    close_z = g.z.last()
    z11 = g.apply(lambda m: m.z[m.index.hour * 60 + m.index.minute <= 660].iloc[-1] if len(m) else np.nan, include_groups=False)
    side_frac = g.apply(lambda m: max((m.z[(m.index.hour >= 12)] > 0).mean(), (m.z[(m.index.hour >= 12)] < 0).mean()),
                        include_groups=False)
    j = pd.concat([close_z.rename("c"), z11.rename("z11")], axis=1).dropna()
    return [["Sessions closing beyond ±1σ", f"{(close_z.abs() > 1).mean() * 100:.0f}%"],
            ["Sessions on one side of VWAP the whole afternoon (12:00-16:00)", f"{(side_frac >= 0.999).mean() * 100:.0f}%"],
            ["Same side of VWAP at 11:00 and at the close", f"{(np.sign(j.c) == np.sign(j.z11)).mean() * 100:.0f}%"],
            ["Correlation of z at 11:00 with z at the close", f"{j.c.corr(j.z11):.2f}"]]


# ------------------------------------------------------------------ entry test

def mr_trades(q1: pd.DataFrame, nq: pd.DataFrame, tf: int = 5, k: float = 2.0, stop_k: float = 3.0) -> pd.DataFrame:
    """Rule MR2 (see the header): QQQ bands mapped onto /NQ, executed on /NQ 1-minute bars."""
    mb = V.map_bands(q1, nq.close)
    nqb = nq.join(mb, how="inner")
    bars = V.resample(q1, tf)
    rows = []
    days_nq = by_day(nqb)
    for day, b in bars.groupby(bars.index.tz_localize(None).normalize()):
        m = days_nq.get(day)
        if m is None or len(m) < 300:
            continue
        mod = b.index.hour * 60 + b.index.minute + 1
        b = b[(mod >= 600) & (mod <= 930)]
        for side_sig in (1, -1):
            hit = b[(side_sig * b.z) >= k]
            if hit.empty:
                continue
            t0 = hit.index[0]
            side = -side_sig                                   # fade
            i0 = m.index.searchsorted(t0, side="right")
            if i0 == 0 or i0 >= len(m) or m.index[i0 - 1] != t0 and t0 - m.index[i0 - 1] > pd.Timedelta(minutes=2):
                continue
            s = m.iloc[i0 - 1]
            stop = s.vwap + side_sig * stop_k * s.sd
            entry, t_in = float(m.open.iloc[i0]), m.index[i0]
            if side * (stop - entry) >= 0:                    # opened at or beyond the stop already
                continue
            hi, lo, op = m.high.to_numpy()[i0:], m.low.to_numpy()[i0:], m.open.to_numpy()[i0:]
            pv = m.vwap.to_numpy()[i0 - 1:-1]
            stop_hit = (hi >= stop) if side == -1 else (lo <= stop)
            tgt_hit = (lo <= pv) if side == -1 else (hi >= pv)
            w, i = first_true(stop_hit, tgt_hit)
            if w == 0:
                ex, why = (op[i] if side * (op[i] - stop) < 0 else stop), "stop"   # a gap through the stop fills at the open
            elif w == 1:
                # target: the VWAP, or the open if the minute opened already past it (gap through the level)
                ex, why = (op[i] if side * (op[i] - pv[i]) > 0 else pv[i]), "vwap"
            else:
                ex, why, i = float(m.close.iloc[-1]), "time", len(hi) - 1
            pts = side * (ex - entry) - COST
            rows.append(dict(day=day, side="long" if side == 1 else "short", signal=t0, entry_time=t_in, entry=entry, stop=stop,
                             exit_time=m.index[i0 + i], exit=ex, why=why, pnl_pts=pts, pnl_usd=pts * DPP, r=pts / abs(entry - stop)))
    return pd.DataFrame(rows)


def stats(t: pd.DataFrame) -> dict:
    p = t.pnl_usd
    w, l = p[p > 0].sum(), -p[p <= 0].sum()
    return dict(n=len(t), win=(p > 0).mean() * 100 if len(t) else np.nan, pf=w / l if l else np.inf, net=p.sum(),
                avg=p.mean() if len(t) else np.nan, t=p.mean() / p.std() * np.sqrt(len(t)) if len(t) > 2 and p.std() > 0 else np.nan,
                dd=(p.cumsum() - p.cumsum().cummax()).min() if len(t) else 0.0)


def stat_row(name, t):
    s = stats(t)
    return [name, s["n"], f"{s['win']:.0f}%", f"{s['pf']:.2f}", f"${s['net']:,.0f}", f"${s['avg']:,.0f}", f"{s['t']:.2f}", f"${s['dd']:,.0f}"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    q = load_qqq()
    qs, qw = V.vwap_bands(q, "session"), V.vwap_bands(q, "week")
    nq_s, qq_s = load_schwab("NQ"), load_schwab("QQQ")
    md = ["# VWAP and its standard-deviation bands", "",
          "Built by scripts/vwap_study.py (definitions: src/quant/vwap.py). QQQ = Alpaca 1-minute with real volume, "
          "2024-01 → 2025-12 (501 sessions). /NQ and QQQ (Schwab) = 2026-09-10 → 2026-10-21 (about 30 sessions). "
          "All from 10:00 ET (the bands need 30 minutes to form).", "",
          "## 1. How often price sits beyond the bands", "", "A normal distribution would give 32% / 4.6% / 0.3%.", ""]
    rows = [["QQQ session VWAP (2024-25)"] + occupancy(qs), ["QQQ weekly VWAP (2024-25)"] + occupancy(qw)]
    sch = {}
    for name, d in (("/NQ session VWAP (Schwab)", nq_s), ("QQQ session VWAP (Schwab)", qq_s)):
        if d is not None:
            sch[name] = V.vwap_bands(d, "session")
            rows.append([name] + occupancy(sch[name]))
    md += table(["Series", "|z| > 1", "|z| > 2", "|z| > 3"], rows)

    md += ["", "## 2. After the first close beyond a band: back to VWAP, or one band further?", "",
           "First close beyond ±kσ per side per day (closes 10:00-15:30), on bars of each timeframe; then minute by minute, "
           "which comes first. Random walk = 1/(k+1). z = (share back − random walk) / standard error.", ""]
    head = ["Series / timeframe / band", "Events", "Back to VWAP first", "Random walk", "z vs random walk", "Median time to VWAP"]
    for label, d, tfs in (("QQQ 2024-25", qs, (1, 5, 15, 30)), ("QQQ weekly VWAP 2024-25", qw, (5, 30))):
        rows = []
        for tf in tfs:
            for k in (1.0, 2.0, 3.0):
                rows.append(race_row(f"{label}, {tf}m, {k:g}σ", races(d, tf, k), k))
        md += table(head, rows) + [""]
    rows = []
    for name, d in sch.items():
        for k in (1.0, 2.0):
            rows.append(race_row(f"{name}, 5m, {k:g}σ", races(d, 5, k), k))
    if rows:
        md += table(head, rows) + [""]
    md += ["**The same race with the far band frozen at its price when the signal closes** (what a resting stop order "
           "sees: the bands keep widening as the session's volatility accumulates, so a moving band is harder to reach)", ""]
    md += table(head, [race_row(f"QQQ 2024-25, {tf}m, {k:g}σ, far band fixed", races(qs, tf, k, fixed=True), k)
                       for tf in (1, 5, 15) for k in (1.0, 2.0)]) + [""]
    sd_ratio = qs.groupby(qs.index.tz_localize(None).normalize()).apply(
        lambda m: m.sd[m.index.hour * 60 + m.index.minute == 720].mean() / m.sd[m.index.hour * 60 + m.index.minute == 600].mean(),
        include_groups=False).median()
    md += [f"Median σ at 12:00 / σ at 10:00 on the same day: {sd_ratio:.2f}.", ""]
    r2 = races(qs, 5, 2.0)
    md += ["**QQQ, 5m, 2σ, by time of the signal**", ""]
    md += table(head, [race_row(f"{h}:00-{h}:59", r2[r2.hour == h], 2.0) for h in range(10, 16)]) + [""]

    md += ["## 3. Retests of the VWAP after a 1σ excursion: bounce or break?", "",
           "Price was ≥1σ away, came back to the VWAP; then which comes first, 1σ back on the same side (bounce) or 1σ "
           "through (break). Random walk: 50%.", ""]
    rows = []
    for name, d in [("QQQ 2024-25", qs)] + list(sch.items()):
        rt = vwap_retests(d)
        done = rt[rt.result != "neither"]
        b = (done.result == "bounce").mean() if len(done) else np.nan
        rows.append([name, len(done), f"{b * 100:.0f}%", f"{(b - 0.5) / np.sqrt(0.25 / len(done)):+.1f}" if len(done) else "–"])
    md += table(["Series", "Retests", "Bounce first", "z vs 50%"], rows) + [""]

    md += ["## 4. Trend days: does the session pick a side of VWAP?", ""]
    md += table(["QQQ 2024-25", "Value"], trend_days(qs)) + [""]

    # proxy check
    md += ["## 5. QQQ's VWAP mapped onto /NQ vs /NQ's own VWAP (Schwab, same minutes)", ""]
    if nq_s is not None and qq_s is not None:
        a, b = sch["/NQ session VWAP (Schwab)"], sch["QQQ session VWAP (Schwab)"]
        mp = V.map_bands(b, a.close).join(a[["vwap", "sd"]].rename(columns={"vwap": "nq_vwap", "sd": "nq_sd"}), how="inner").dropna()
        mp = mp[(mp.index.hour * 60 + mp.index.minute) >= 600]
        diff = (mp.vwap - mp.nq_vwap).abs()
        md += table(["Measure", "Value"], [["Minutes compared", len(mp)], ["Median |difference|, /NQ points", f"{diff.median():.1f}"],
                                            ["95th percentile, points", f"{diff.quantile(0.95):.1f}"],
                                            ["Median |difference| in /NQ σ", f"{(diff / mp.nq_sd).median():.2f}"],
                                            ["σ ratio (mapped QQQ / NQ), median", f"{(mp.sd / mp.nq_sd).median():.2f}"]]) + [""]

    # entry test
    nsx = load_nsx()
    md += ["## 6. Pre-registered entry test MR2 (fade a 2σ close back to VWAP, stop at 3σ)", "",
           "Rule fixed before this run (see the script header). QQQ bands mapped onto /NQ, trades on NSX 1-minute prices, "
           "1 NQ, $20/pt, 1 pt round trip. A code bug in the first run (an inverted check that skipped every normal "
           "entry) was fixed before reading any result; the rule is unchanged.", ""]
    head = ["Variant / window", "Trades", "Win %", "PF", "Net", "Avg", "t", "Max DD"]
    t = mr_trades(qs, nsx)
    t.to_csv(OUT / "mr2_trades.csv", index=False)
    d24, d25 = t[t.day.dt.year == 2024], t[t.day.dt.year == 2025]
    h1, h2 = d25[d25.day.dt.month <= 6], d25[d25.day.dt.month > 6]
    rows = [stat_row("MR2 design 2024", d24), stat_row("MR2 HOLDOUT 2025", d25), stat_row("  2025 H1", h1),
            stat_row("  2025 H2", h2), stat_row("  longs (both years)", t[t.side == "long"]), stat_row("  shorts (both years)", t[t.side == "short"])]
    for name, kw in (("1-minute bars", dict(tf=1)), ("15-minute bars", dict(tf=15)), ("2.5σ signal", dict(k=2.5))):
        n = mr_trades(qs, nsx, **kw)
        rows += [stat_row(f"neighbour {name}, 2024", n[n.day.dt.year == 2024]), stat_row(f"neighbour {name}, 2025", n[n.day.dt.year == 2025])]
    md += table(head, rows)
    s25 = stats(d25)
    ok = s25["n"] >= 30 and s25["pf"] >= 1.15 and s25["t"] >= 2.0 and stats(h1)["net"] > 0 and stats(h2)["net"] > 0
    md += ["", f"**Verdict: {'PASS' if ok else 'FAIL'}** against the pre-registered holdout rule (≥ 30 trades, PF ≥ 1.15, "
           "t ≥ 2.0, net > 0 in both halves of 2025).", "",
           f"Exits: {t.why.value_counts().to_dict()}."]
    (OUT / "VWAP_STUDY.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
