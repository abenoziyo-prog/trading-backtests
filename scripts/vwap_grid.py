"""VWAP strategy grid: five families from the literature and trading practice, ~300 configurations across timeframes,
deviations, entries, exits and filters, ranked on 2024 and judged on 2025.

    python scripts/vwap_grid.py                # all families (uses every core)

PRE-REGISTERED 2026-10-27, before any result of the grid was run. Sources behind the families:
  F1 trend     Zarattini & Aziz (2023), "VWAP: The Holy Grail for Day Trading Systems": long above VWAP, short below,
               always in the market during the session. A replication found ~15 flips a day on 1-minute QQQ bars and
               that 1 cent of slippage removed the edge, so slower timeframes and a hysteresis buffer are tested.
  F2 fade      the band mean reversion described on trading forums and indicator pages: after closing beyond +/-k sigma,
               fade the close back INSIDE the band (rejection), not the first touch; practitioners say it works only
               on rotational ("balanced") days, hence the VWAP-crossing filter. A 10-year DAX test of first-touch fades
               lost money (PF 0.64); our own MR2 (first-touch, fixed 3 sigma stop) failed in 2025.
  F3 breakout  the opposite of F2, suggested by our first study: price beyond the bands tends to keep going.
  F4 pullback  trend-day continuation: once the session has stretched to +m sigma, buy the first pullback to VWAP (or
               +0.5 sigma); mirror for shorts.
  F5 noise     Zarattini, Aziz & Barbon (2024), "Beat the Market": breakouts of the time-of-day noise area, VWAP and
               the band as the trailing stop, decisions on 30-minute marks.
Data: QQQ 1-minute with volume (Alpaca, 2024-01 -> 2025-12) gives VWAP and bands; they are mapped onto /NQ minute by
minute (quant.vwap.map_bands; checked vs /NQ's own VWAP: median gap 0.07 sigma) and every trade is executed on NSX
(/NQ) 1-minute prices: 1 NQ, $20/pt, 1 pt round trip, no slippage; a stop and a target in the same minute = stop.
Timing: signals on n-minute bars anchored at 09:30, known at the bar's last minute; entries at the next minute's open;
intrabar stops/targets use levels known at the previous minute; no entries after 15:30; everything flat by 16:00.
Judging: configurations are ranked by their 2024 t-stat of daily P&L. A CANDIDATE needs 2024 t >= 2.0 and, in the 2025
holdout, >= 30 trades, profit factor >= 1.15, daily t >= 2.0 and a positive net in both halves of 2025. With ~300
configurations tried, 2025 is the only honest evidence; the family-level share of configurations positive in both
years is reported to show whether a family has an edge at all.
Writes reports/vwap/VWAP_GRID.md and reports/vwap/grid_results.csv.
"""
from __future__ import annotations

import itertools
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant import vwap as V  # noqa: E402

OUT = ROOT / "reports" / "vwap"
NY = "America/New_York"
DPP, COST = 20.0, 1.0
START, LAST_ENTRY, CLOSE = 600, 930, 960   # 10:00, 15:30, 16:00 (minutes after midnight)
DAYS: list[dict] = []                        # filled per process


# ------------------------------------------------------------------ data

def build_days() -> list[dict]:
    q = pd.read_parquet(ROOT / "data/free/QQQ_1min_2024_2025.parquet")
    q.index = q.index.tz_convert(NY)
    qs = V.vwap_bands(q, "session")
    n = pd.read_parquet(ROOT / "data/free/NSXUSD_1min_histdata.parquet")
    n.index = n.index.tz_convert(NY)
    n = n[(n.index >= "2023-11-01") & (n.index < "2026-01-01")]
    na = V.noise_area(n)
    nr = V.rth(n)
    mb = V.map_bands(qs, nr.close)
    j = nr.join(mb, how="inner").join(na, how="inner")
    j = j[j.index >= "2024-01-01"]
    out = []
    for day, g in j.groupby(j.index.tz_localize(None).normalize()):
        if len(g) < 300:
            continue
        mod = (g.index.hour * 60 + g.index.minute).to_numpy()
        sd = g.sd.to_numpy()
        z = np.where(sd > 0, (g.close.to_numpy() - g.vwap.to_numpy()) / np.where(sd > 0, sd, 1), np.nan)
        out.append(dict(day=day, t=g.index, mod=mod, o=g.open.to_numpy(), h=g.high.to_numpy(), l=g.low.to_numpy(),
                        c=g.close.to_numpy(), vw=g.vwap.to_numpy(), sd=sd, z=z, ub=g.upper.to_numpy(), lb=g.lower.to_numpy()))
    return out


def stamps(mod: np.ndarray, tf: int) -> np.ndarray:
    """Indices of the last minute of each tf-minute bar anchored at 09:30."""
    return np.flatnonzero(((mod - 570 + 1) % tf) == 0)


def first(mask: np.ndarray) -> int:
    return int(np.argmax(mask)) if mask.any() else -1


# ------------------------------------------------------------------ exits

def run_exit(d, e: int, side: int, stop: float | None = None, target: float | None = None, vwap_target: bool = False,
             close_exit=None, tf: int = 1) -> tuple[int, float]:
    """From entry minute e: intrabar stop (fixed), intrabar target (fixed, or the VWAP known at the previous minute),
    close-based exit at tf stamps (close_exit(i) -> bool, filled at the next open), else the last close."""
    h, l, o = d["h"][e:], d["l"][e:], d["o"][e:]
    n = len(h)
    s_hit = ((l <= stop) if side == 1 else (h >= stop)) if stop is not None else np.zeros(n, bool)
    if vwap_target:
        pv = d["vw"][e - 1:-1] if e > 0 else np.r_[d["vw"][0], d["vw"][:-1]]
        lvl = pv
    else:
        lvl = np.full(n, target if target is not None else np.nan)
    t_hit = ((h >= lvl) if side == 1 else (l <= lvl)) if (vwap_target or target is not None) else np.zeros(n, bool)
    c_hit = np.zeros(n, bool)
    if close_exit is not None:
        st = stamps(d["mod"], tf)
        for i in st[st >= e]:
            if i + 1 < len(d["o"]) and close_exit(i):
                c_hit[i + 1 - e] = True      # filled at the next minute's open
                break
    cands = []
    i = first(s_hit)
    if i >= 0:
        fill = o[i] if (side * (o[i] - stop) < 0) else stop       # a gap through the stop fills at the open
        cands.append((i, 0, fill))
    i = first(t_hit)
    if i >= 0:
        lv = lvl[i]
        fill = o[i] if (side * (o[i] - lv) > 0) else lv
        cands.append((i, 1, fill))
    i = first(c_hit)
    if i >= 0:
        cands.append((i, -1, o[i]))      # the close exit fills at that minute's open, before its high/low
    if not cands:
        return len(d["c"]) - 1, float(d["c"][-1])
    i, _, px = min(cands, key=lambda x: (x[0], x[1]))
    return e + i, float(px)


# ------------------------------------------------------------------ families

def f1_trend(d, tf, buf, start):
    st = stamps(d["mod"], tf)
    st = st[(d["mod"][st] + 1 >= start) & (d["mod"][st] + 1 < CLOSE)]
    pos, trades, e, side = 0, [], None, 0
    for i in st:
        z = d["z"][i]
        want = 1 if z > buf else -1 if z < -buf else pos
        if d["mod"][i] + 1 > LAST_ENTRY and pos == 0:
            want = 0
        if want != pos and i + 1 < len(d["o"]):
            if pos != 0:
                trades.append((e, i + 1, pos, d["o"][e], d["o"][i + 1]))
            pos, e = (want, i + 1) if d["mod"][i] + 1 <= LAST_ENTRY else (0, None)
    if pos != 0:
        trades.append((e, len(d["c"]) - 1, pos, d["o"][e], d["c"][-1]))
    return trades


def f2_fade(d, tf, k, target, stop_kind, start, balanced):
    st = stamps(d["mod"], tf)
    z, mod = d["z"], d["mod"]
    crosses = np.r_[0, np.cumsum(np.abs(np.diff(np.sign(np.nan_to_num(z)))) > 0)]
    armed = {1: None, -1: None}
    trades, busy_until = [], -1
    for i in st:
        if np.isnan(z[i]):
            continue
        for s in (1, -1):
            if s * z[i] >= k:
                if armed[s] is None:
                    armed[s] = i
            elif armed[s] is not None and s * z[i] < k:
                a = armed[s]
                armed[s] = None
                if i <= busy_until or not (start <= mod[i] + 1 <= LAST_ENTRY) or i + 1 >= len(d["o"]):
                    continue
                if balanced and crosses[i] < 2:
                    continue
                side, e = -s, i + 1
                ext = d["h"][a:i + 1].max() if s == 1 else d["l"][a:i + 1].min()
                stop = ext if stop_kind == "extreme" else d["vw"][i] + s * (k + 1) * d["sd"][i]
                if side * (stop - d["o"][e]) >= 0:
                    continue
                tg = None if target == "vwap" else d["vw"][i] + s * (k / 2) * d["sd"][i]
                x, px = run_exit(d, e, side, stop=stop, target=tg, vwap_target=(target == "vwap"))
                trades.append((e, x, side, d["o"][e], px))
                busy_until = x
    return trades


def f3_breakout(d, tf, k, exit_kind, end):
    st = stamps(d["mod"], tf)
    z, mod = d["z"], d["mod"]
    trades, busy_until, prev = [], -1, 0.0
    for i in st:
        zi = z[i]
        if np.isnan(zi):
            continue
        for s in (1, -1):
            if s * zi >= k and s * prev < k and i > busy_until and START <= mod[i] + 1 <= end and i + 1 < len(d["o"]):
                e = i + 1
                lim = 0.0 if exit_kind == "vwap" else k - 1
                x, px = run_exit(d, e, s, close_exit=lambda j, s=s, lim=lim: s * z[j] < lim, tf=tf)
                trades.append((e, x, s, d["o"][e], px))
                busy_until = x
        prev = zi
    return trades


def f4_pullback(d, m, a, stop_sd, target_kind):
    z, mod, vw, sd = d["z"], d["mod"], d["vw"], d["sd"]
    trades, busy_until = [], -1
    for s in (1, -1):
        reached = np.maximum.accumulate(np.nan_to_num(s * z, nan=-9))
        lvl = vw + s * a * sd
        for i in range(1, len(z)):
            if i <= busy_until or mod[i] < START or mod[i] > LAST_ENTRY or reached[i - 1] < m:
                continue
            L = lvl[i - 1]
            touched = (d["l"][i] <= L) if s == 1 else (d["h"][i] >= L)
            if touched:
                entry = d["o"][i] if s * (d["o"][i] - L) < 0 else L      # limit order: the open if it gapped through
                stop = L - s * stop_sd * sd[i - 1]
                tg = (d["h"][:i].max() if s == 1 else d["l"][:i].min()) if target_kind == "high" else vw[i - 1] + s * (a + 1.0) * sd[i - 1]
                if side_ok := s * (tg - entry) > 0:
                    x, px = run_exit(d, i, s, stop=stop, target=tg)
                    # the entry minute itself: a stop inside the entry bar counts (conservative)
                    trades.append((i, x, s, entry, px))
                    busy_until = x
                break
    return trades


def f5_noise(d, tf, stop_kind, mult):
    st = stamps(d["mod"], tf)
    st = st[(d["mod"][st] + 1 >= START) & (d["mod"][st] + 1 < CLOSE)]
    c, vw = d["c"], d["vw"]
    op = d["o"][0]
    ub = op + (d["ub"] - op) * mult if mult != 1 else d["ub"]
    lb = op - (op - d["lb"]) * mult if mult != 1 else d["lb"]
    pos, e, trades = 0, None, []
    for i in st:
        if np.isnan(ub[i]) or i + 1 >= len(d["o"]):
            continue
        if pos == 1:
            lvl = max(ub[i], vw[i]) if stop_kind == "paper" else ub[i] if stop_kind == "band" else vw[i]
            if c[i] < lvl:
                trades.append((e, i + 1, 1, d["o"][e], d["o"][i + 1]))
                pos = 0
        elif pos == -1:
            lvl = min(lb[i], vw[i]) if stop_kind == "paper" else lb[i] if stop_kind == "band" else vw[i]
            if c[i] > lvl:
                trades.append((e, i + 1, -1, d["o"][e], d["o"][i + 1]))
                pos = 0
        if pos == 0 and d["mod"][i] + 1 <= LAST_ENTRY:
            if c[i] > ub[i]:
                pos, e = 1, i + 1
            elif c[i] < lb[i]:
                pos, e = -1, i + 1
    if pos != 0:
        trades.append((e, len(c) - 1, pos, d["o"][e], c[-1]))
    return trades


FAMILIES = {"F1 trend": f1_trend, "F2 fade": f2_fade, "F3 breakout": f3_breakout, "F4 pullback": f4_pullback, "F5 noise": f5_noise}


def grid() -> list[tuple[str, dict]]:
    g = []
    g += [("F1 trend", dict(tf=tf, buf=b, start=s)) for tf, b, s in itertools.product((1, 3, 5, 10, 15, 30), (0.0, 0.25, 0.5), (575, 600))]
    g += [("F2 fade", dict(tf=tf, k=k, target=t, stop_kind=sk, start=s, balanced=bal))
          for tf, k, t, sk, s, bal in itertools.product((1, 5, 15), (1.5, 2.0, 2.5, 3.0), ("vwap", "half"), ("extreme", "fixed"),
                                                          (600, 720), (False, True))]
    g += [("F3 breakout", dict(tf=tf, k=k, exit_kind=x, end=e))
          for tf, k, x, e in itertools.product((5, 15, 30), (1.0, 1.5, 2.0), ("vwap", "inner"), (720, LAST_ENTRY))]
    g += [("F4 pullback", dict(m=m, a=a, stop_sd=s, target_kind=t))
          for m, a, s, t in itertools.product((1.5, 2.0), (0.0, 0.5), (0.5, 1.0), ("high", "band"))]
    g += [("F5 noise", dict(tf=tf, stop_kind=sk, mult=mu)) for tf, sk, mu in itertools.product((15, 30, 60), ("paper", "band", "vwap"), (1.0, 1.25))]
    return g


# ------------------------------------------------------------------ evaluation

def _init():
    global DAYS
    DAYS = build_days()


def run_config(job) -> tuple[str, dict, pd.DataFrame]:
    fam, p = job
    rows = []
    for d in DAYS:
        for e, x, side, en, ex in FAMILIES[fam](d, **p):
            rows.append((d["day"], d["t"][e], d["t"][x], side, (side * (ex - en) - COST) * DPP))
    return fam, p, pd.DataFrame(rows, columns=["day", "entry_time", "exit_time", "side", "pnl_usd"])


def stats(t: pd.DataFrame, cost_extra: float = 0.0) -> dict:
    if t.empty:
        return dict(n=0, pf=np.nan, net=0.0, t=np.nan, win=np.nan, dd=0.0, per_day=0.0)
    p = t.pnl_usd - cost_extra * DPP
    daily = p.groupby(t.day).sum()
    w, l = p[p > 0].sum(), -p[p <= 0].sum()
    eq = daily.cumsum()
    return dict(n=len(t), pf=w / l if l else np.inf, net=p.sum(), win=(p > 0).mean() * 100,
                t=daily.mean() / daily.std() * np.sqrt(len(daily)) if len(daily) > 2 and daily.std() > 0 else np.nan,
                dd=(eq - eq.cummax()).min(), per_day=len(t) / max(1, len(daily)))


def label(fam, p):
    return fam + " " + " ".join(f"{k}={v}" for k, v in p.items())


def table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = grid()
    with ProcessPoolExecutor(initializer=_init) as ex:
        results = list(ex.map(run_config, jobs, chunksize=4))
    rows, trades_of = [], {}
    for fam, p, t in results:
        y = t.day.dt.year if len(t) else pd.Series(dtype=int)
        a, b = stats(t[y == 2024]), stats(t[y == 2025])
        h1, h2 = stats(t[(y == 2025) & (t.day.dt.month <= 6)]), stats(t[(y == 2025) & (t.day.dt.month > 6)])
        cand = (a["t"] >= 2.0 and b["n"] >= 30 and b["pf"] >= 1.15 and b["t"] >= 2.0 and h1["net"] > 0 and h2["net"] > 0)
        rows.append(dict(family=fam, config=label(fam, p), **{f"{k}_2024": v for k, v in a.items()},
                         **{f"{k}_2025": v for k, v in b.items()}, net_2025_h1=h1["net"], net_2025_h2=h2["net"], candidate=cand))
        trades_of[label(fam, p)] = t
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "grid_results.csv", index=False)

    md = ["# VWAP strategy grid", "", f"{len(r)} configurations, pre-registered in scripts/vwap_grid.py (sources and rules in its "
          "header). /NQ executed, 1 NQ, $20/pt, 1 pt round trip. Ranked on 2024, judged on 2025.", "",
          "## 1. Does any family have an edge?", "",
          "Positive in both years = net > 0 in 2024 and in 2025. Rank correlation = Spearman correlation of the 2024 and 2025 "
          "daily t-stats across the family's configurations (near 0 = 2024's ranking says nothing about 2025).", ""]
    frows = []
    for fam, g in r.groupby("family"):
        both = ((g.net_2024 > 0) & (g.net_2025 > 0)).mean()
        rc = g.t_2024.rank().corr(g.t_2025.rank())   # Spearman
        frows.append([fam, len(g), f"{(g.net_2024 > 0).mean() * 100:.0f}%", f"{(g.net_2025 > 0).mean() * 100:.0f}%", f"{both * 100:.0f}%",
                      f"{rc:.2f}", f"${g.net_2024.median():,.0f}", f"${g.net_2025.median():,.0f}", f"{g.per_day_2024.median():.1f}"])
    md += table(["Family", "Configs", "Net > 0 in 2024", "Net > 0 in 2025", "Both years", "Rank correlation", "Median net 2024",
                 "Median net 2025", "Trades/day"], frows)

    md += ["", "## 2. The 15 best configurations of 2024 and what they did in 2025", ""]
    top = r.sort_values("t_2024", ascending=False).head(15)
    head = ["Configuration", "Trades 24", "PF 24", "Net 24", "t 24", "Trades 25", "PF 25", "Net 25", "t 25", "Max DD 25", "Candidate"]
    md += table(head, [[x.config, x.n_2024, f"{x.pf_2024:.2f}", f"${x.net_2024:,.0f}", f"{x.t_2024:.2f}", x.n_2025, f"{x.pf_2025:.2f}",
                        f"${x.net_2025:,.0f}", f"{x.t_2025:.2f}", f"${x.dd_2025:,.0f}", "✅" if x.candidate else ""] for x in top.itertuples()])
    for fam, g in r.groupby("family"):
        md += ["", f"**Best 3 of {fam} by 2024**", ""]
        md += table(head, [[x.config, x.n_2024, f"{x.pf_2024:.2f}", f"${x.net_2024:,.0f}", f"{x.t_2024:.2f}", x.n_2025, f"{x.pf_2025:.2f}",
                            f"${x.net_2025:,.0f}", f"{x.t_2025:.2f}", f"${x.dd_2025:,.0f}", "✅" if x.candidate else ""]
                           for x in g.sort_values("t_2024", ascending=False).head(3).itertuples()])

    cands = r[r.candidate]
    md += ["", f"## 3. Candidates (pre-registered rule): {len(cands)}", ""]
    if len(cands):
        rows = []
        for x in cands.itertuples():
            t = trades_of[x.config]
            s = stats(t[t.day.dt.year == 2025], cost_extra=1.0)
            rows.append([x.config, f"${x.net_2025:,.0f}", f"${s['net']:,.0f}", f"{s['pf']:.2f}", f"{s['t']:.2f}"])
        md += ["With 1 extra point of slippage per round trip (2 pts total):", ""]
        md += table(["Configuration", "Net 2025", "Net 2025, +1 pt", "PF 2025, +1 pt", "t 2025, +1 pt"], rows)
    else:
        md += ["None passed."]
    (OUT / "VWAP_GRID.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
