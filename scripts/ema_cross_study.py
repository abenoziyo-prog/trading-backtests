"""EMA crossover, stop and reverse: long while the fast EMA is above the slow one, short while below, flip on each cross.

    python scripts/ema_cross_study.py

Grid (fixed 2026-10-27 before running): every pair of EMAs 9, 13, 21, 50, 200 (fast < slow: 10 pairs) x bars of 1, 2, 3,
5, 10, 15 minutes = 60 configurations, in two modes:
  session  the approved rules: US session only. From 09:30 the position follows the EMA state (long if fast > slow),
           flips at each cross, no new position after 15:30 (a cross after 15:30 just exits), flat at 16:00
  24h      always in the market around the clock, holding overnight and over weekends until the next cross
EMAs are computed on /NQ (NSX) bars around the clock (as on a futures chart with the overnight session). A cross is known
at the close of its bar; the trade happens at the next minute's open. 1 NQ, $20/pt, 1 pt round trip per trade, no
slippage (a +1 pt slippage column is reported too).
Windows (all ending 2026-10-09, trades counted by entry date): 18 months (from 2025-04-10), 12 months (2025-10-10),
8 months (2026-02-10), 6 months (2026-04-10). They overlap: the 6 months are inside the 18. The honest out-of-sample
check: pick the best configuration on the first 12 months (2025-04-10 -> 2026-04-09) and look at the last 6.
Writes reports/ema/EMA_CROSS.md and reports/ema/ema_grid.csv.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "reports" / "ema"
NY = "America/New_York"
DPP, COST = 20.0, 1.0
EMAS = [9, 13, 21, 50, 200]
TFS = [1, 2, 3, 5, 10, 15]
END = pd.Timestamp("2026-10-10", tz=NY)
WINDOWS = {"18m": "2025-04-10", "12m": "2025-10-10", "8m": "2026-02-10", "6m": "2026-04-10"}
IS_END = pd.Timestamp("2026-04-10", tz=NY)      # first 12 months = choose; last 6 = check


def load() -> pd.DataFrame:
    n = pd.read_parquet(ROOT / "data/free/NSXUSD_1min_histdata.parquet")
    n.index = n.index.tz_convert(NY)
    return n[(n.index >= "2025-01-01") & (n.index < END)]


def states(m: pd.DataFrame, tf: int, fast: int, slow: int) -> pd.Series:
    """Per minute: +1/-1 = the EMA state known at that minute's OPEN (from the last completed tf bar)."""
    key = m.index.floor(f"{tf}min")
    bars = m.close.groupby(key).last()
    last_min = m.index.to_series().groupby(key).last()          # the bar's last minute (its close is known then)
    f = bars.ewm(span=fast, adjust=False).mean()
    s = bars.ewm(span=slow, adjust=False).mean()
    st = pd.Series(np.sign(f - s).to_numpy(), index=last_min.to_numpy() + pd.Timedelta(minutes=1))   # usable from next minute
    st = st[~st.index.duplicated(keep="last")]
    return st.reindex(m.index, method="ffill").fillna(0)


def trades_from_positions(m: pd.DataFrame, pos: np.ndarray, breaks: np.ndarray | None = None) -> pd.DataFrame:
    """Segments of constant non-zero position. A segment starts at its first minute's open and ends at the open of the
    minute where the position changes; at a forced break (breaks[i] = True: minute i ends a session) it ends at minute
    i's close; at the end of the data, at the last close."""
    o, c, t = m.open.to_numpy(), m.close.to_numpy(), m.index
    n = len(pos)
    brk = np.zeros(n, bool) if breaks is None else breaks
    start = np.r_[True, (pos[1:] != pos[:-1]) | brk[:-1]]
    idx = np.flatnonzero(start)
    rows = []
    for a, b in zip(idx, np.r_[idx[1:], n]):
        side = pos[a]
        if side == 0:
            continue
        if b == n or brk[b - 1]:
            ex, tx = c[b - 1], t[b - 1]
        else:
            ex, tx = o[b], t[b]
        rows.append((t[a], tx, side, o[a], ex))
    d = pd.DataFrame(rows, columns=["entry_time", "exit_time", "side", "entry", "exit"])
    d["pnl_pts"] = d.side * (d.exit - d.entry) - COST
    d["pnl_usd"] = d.pnl_pts * DPP
    return d


def run(m: pd.DataFrame, tf: int, fast: int, slow: int, mode: str) -> pd.DataFrame:
    st = states(m, tf, fast, slow)
    if mode == "24h":
        return trades_from_positions(m, st.to_numpy())
    mod = m.index.hour * 60 + m.index.minute
    r = (mod >= 570) & (mod < 960) & (m.index.dayofweek < 5)
    mm, pos = m[r], st.to_numpy()[r].copy()
    mod = np.asarray(mod[r])
    day = np.asarray(mm.index.tz_localize(None).normalize())
    last = np.r_[day[1:] != day[:-1], True]                       # each session's last minute: exit at its close
    # no new position from 15:30: after the first change at or after 15:30, flat for the rest of the session
    late = mod >= 930
    first_change = late & np.r_[False, (pos[1:] != pos[:-1]) & (day[1:] == day[:-1])]
    flat = pd.Series(first_change).groupby(day).cummax().to_numpy()
    pos[flat] = 0.0
    return trades_from_positions(mm, pos, breaks=last)


def stats(t: pd.DataFrame, extra_cost: float = 0.0) -> dict:
    if t.empty:
        return dict(n=0, net=0.0, pf=np.nan, win=np.nan, dd=0.0, t=np.nan, per_day=0.0, avg_win=np.nan, avg_loss=np.nan)
    p = t.pnl_usd - extra_cost * DPP
    day = t.entry_time.dt.tz_localize(None).dt.normalize()
    daily = p.groupby(day).sum()
    eq = p.cumsum()
    w, l = p[p > 0], p[p <= 0]
    return dict(n=len(p), net=p.sum(), pf=w.sum() / -l.sum() if l.sum() else np.inf, win=(p > 0).mean() * 100,
                dd=(eq - eq.cummax()).min(), t=daily.mean() / daily.std() * np.sqrt(len(daily)) if daily.std() > 0 else np.nan,
                per_day=len(p) / daily.index.nunique(), avg_win=w.mean(), avg_loss=l.mean())


def table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def money(x):
    return f"{'−' if x < 0 else ''}${abs(x):,.0f}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    m = load()
    rows, keep = [], {}
    for mode, tf, (fast, slow) in itertools.product(("session", "24h"), TFS, itertools.combinations(EMAS, 2)):
        t = run(m, tf, fast, slow, mode)
        cfg = f"{mode} {tf}m EMA {fast}/{slow}"
        keep[cfg] = t
        row = dict(config=cfg, mode=mode, tf=tf, fast=fast, slow=slow)
        for w, start in WINDOWS.items():
            s = stats(t[t.entry_time >= pd.Timestamp(start, tz=NY)])
            row.update({f"{k}_{w}": v for k, v in s.items()})
        s = stats(t[t.entry_time >= pd.Timestamp(WINDOWS["18m"], tz=NY)], extra_cost=1.0)
        row["net_18m_slip"] = s["net"]
        a = t[(t.entry_time >= pd.Timestamp(WINDOWS["18m"], tz=NY)) & (t.entry_time < IS_END)]
        b = t[t.entry_time >= IS_END]
        sa, sb = stats(a), stats(b)
        row.update(net_is=sa["net"], t_is=sa["t"], dd_is=sa["dd"], net_oos=sb["net"], t_oos=sb["t"], pf_oos=sb["pf"])
        rows.append(row)
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "ema_grid.csv", index=False)

    md = ["# EMA crossover, stop and reverse, on /NQ", "",
          "Built by scripts/ema_cross_study.py (rules in its header). EMAs 9/13/21/50/200, all 10 fast/slow pairs, on 1-15 "
          "minute bars; 1 NQ, $20/pt, 1 pt round trip per trade; windows end 2026-10-09.", ""]
    for mode in ("session", "24h"):
        g = r[r["mode"] == mode]
        md += [f"## {'US session only (approved rules)' if mode == 'session' else 'Around the clock (24h, holds overnight)'}", ""]
        for w in WINDOWS:
            piv = g.pivot_table(index=["fast", "slow"], columns="tf", values=f"net_{w}")
            md += [f"**Net P&L, {w}** (rows: EMA pair; columns: bar minutes)", ""]
            md += table(["EMA pair"] + [f"{c}m" for c in piv.columns],
                        [[f"{a}/{b}"] + [money(v) for v in piv.loc[(a, b)]] for a, b in piv.index]) + [""]
        piv = g.pivot_table(index=["fast", "slow"], columns="tf", values="per_day_18m")
        md += ["**Trades per day, 18 months**", ""]
        md += table(["EMA pair"] + [f"{c}m" for c in piv.columns],
                    [[f"{a}/{b}"] + [f"{v:.1f}" for v in piv.loc[(a, b)]] for a, b in piv.index]) + [""]
        md += ["**Positive in every window (18, 12, 8 and 6 months)**", ""]
        allpos = g[(g.net_18m > 0) & (g.net_12m > 0) & (g.net_8m > 0) & (g.net_6m > 0)].sort_values("net_18m", ascending=False)
        head = ["Configuration", "Trades 18m", "Win %", "PF 18m", "Net 18m", "Net 12m", "Net 8m", "Net 6m", "Max DD 18m",
                "Net 18m +1 pt slip", "t 18m"]
        md += (table(head, [[x.config, x.n_18m, f"{x.win_18m:.0f}%", f"{x.pf_18m:.2f}", money(x.net_18m), money(x.net_12m), money(x.net_8m),
                              money(x.net_6m), money(x.dd_18m), money(x.net_18m_slip), f"{x.t_18m:.2f}"] for x in allpos.itertuples()])
               if len(allpos) else ["None."]) + [""]
        md += ["**Out of sample: the 5 best configurations of the first 12 months (2025-04-10 → 2026-04-09), and their last 6 months**", ""]
        top = g.sort_values("t_is", ascending=False).head(5)
        md += table(["Configuration", "Net, first 12m", "t, first 12m", "Max DD, first 12m", "Net, last 6m", "PF, last 6m", "t, last 6m"],
                    [[x.config, money(x.net_is), f"{x.t_is:.2f}", money(x.dd_is), money(x.net_oos), f"{x.pf_oos:.2f}", f"{x.t_oos:.2f}"]
                     for x in top.itertuples()]) + [""]
        rk = g.t_is.rank().corr(g.t_oos.rank())
        md += [f"Rank correlation of the first-12-month and last-6-month t-stats across the {len(g)} configurations: {rk:.2f}.", ""]
    best = r.sort_values("net_18m", ascending=False).iloc[0]
    t = keep[best.config]
    t = t[t.entry_time >= pd.Timestamp(WINDOWS["18m"], tz=NY)]
    big = t.pnl_usd.sort_values(ascending=False)
    md += ["## How the P&L is made (best 18-month configuration: " + best.config + ")", "",
           f"{len(t)} trades, {(t.pnl_usd > 0).mean() * 100:.0f}% winners. Average win {money(t.pnl_usd[t.pnl_usd > 0].mean())}, "
           f"average loss {money(t.pnl_usd[t.pnl_usd <= 0].mean())}. The best 5% of trades made {money(big.head(int(len(t) * 0.05)).sum())}; "
           f"all others together {money(big.iloc[int(len(t) * 0.05):].sum())}. Costs (1 pt per trade): {money(len(t) * COST * DPP)}.", ""]
    (OUT / "EMA_CROSS.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
