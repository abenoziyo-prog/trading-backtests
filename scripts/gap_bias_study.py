"""Gap-fill bias study (design data only: the first 12 months, 2025-04-10 -> 2026-04-09; the last 6 months stay unseen
until the lab runs the pre-registered rules). NSX US session; gap = 09:30 open - previous close (PDC); R0 = previous
US range. Writes reports/gaps_bias/gap_days.csv and prints the bias tables.

Questions:
  1. Fill-or-go: when a gap is still unfilled at 10:00 / 10:30, which way does the rest of the day go?
  2. After the fill: once price reaches the previous close, does it reverse back toward the open or run through?
  3. Unfilled gaps as magnets: how often does a gap left open at the close get filled in the next 1-5 sessions?
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lab.data import Context  # noqa: E402

SPLIT = pd.Timestamp("2026-04-10")


def at(d, m):
    k = int(np.searchsorted(d["mod"], m)) - 1
    return k if k >= 0 and d["mod"][k] >= m - 5 else None


def main():
    ctx = Context()
    lv = ctx.levels
    rows = []
    days = [d for d in ctx.us_days]
    for i, d in enumerate(days):
        day = d["day"]
        pdc = lv.loc[day, "PDC"]
        op, R0 = d["o"][0], d["R0"]
        gap = op - pdc
        g = np.sign(gap)
        if g == 0:
            continue
        h, l, c, mod = d["h"], d["l"], d["c"], d["mod"]
        fill_hit = (l <= pdc) if g > 0 else (h >= pdc)
        fi = int(np.argmax(fill_hit)) if fill_hit.any() else None
        row = dict(day=day, gap_R=gap / R0, gap_pts=gap, side=int(g), filled=fi is not None,
                   fill_min=None if fi is None else int(mod[fi] - 570), close_vs_open_R=(c[-1] - op) / R0 * g)
        for m in (600, 630):
            k = at(d, m)
            if k is None:
                continue
            unfilled = fi is None or fi > k
            row[f"unfilled_{m}"] = unfilled
            # how much of the gap was retraced by then (0 = none, 1 = filled)
            ext = l[:k + 1].min() if g > 0 else h[:k + 1].max()
            row[f"retrace_{m}"] = min(1.0, max(0.0, (op - ext) / gap))
            row[f"pos_{m}_R"] = (c[k] - op) / R0 * g          # + = price moved further in the gap direction
            row[f"rest_{m}_R"] = (c[-1] - c[k]) / R0 * g      # rest of day, in the gap direction
        if fi is not None and fi + 1 < len(c):
            after = slice(fi, min(len(c), fi + 60))
            # after the fill: max excursion back toward the open vs through PDC, in the next 60 minutes
            row["after_back_R"] = ((h[after].max() - pdc) if g > 0 else (pdc - l[after].min())) / R0
            row["after_through_R"] = ((pdc - l[after].min()) if g > 0 else (h[after].max() - pdc)) / R0
            row["after_close_R"] = (c[-1] - pdc) / R0 * g      # + = closed back on the gap side
        # unfilled at the close: filled within the next 1-5 sessions?
        if fi is None:
            for n in (1, 3, 5):
                fut = days[i + 1:i + 1 + n]
                row[f"later_fill_{n}"] = any(((x["l"] <= pdc) if g > 0 else (x["h"] >= pdc)).any() for x in fut) if len(fut) == n else np.nan
        rows.append(row)
    t = pd.DataFrame(rows)
    t.to_csv(ROOT / "reports/gaps_bias/gap_days.csv", index=False)
    tr = t[t.day < SPLIT].copy()
    tr["size"] = pd.cut(tr.gap_R.abs(), [0, 0.1, 0.25, 0.5, 9], labels=["<0.10R", "0.10-0.25R", "0.25-0.50R", ">0.50R"])
    tr["dir"] = np.where(tr.side > 0, "up", "down")
    pd.set_option("display.width", 220)
    print("DESIGN WINDOW:", tr.day.min().date(), "->", tr.day.max().date(), len(tr), "gap days\n")
    print("1. Fill rate and fill time by size and direction")
    print(tr.groupby(["dir", "size"], observed=True).agg(n=("filled", "size"), fill=("filled", "mean"),
          med_fill_min=("fill_min", "median"), close_vs_open_R=("close_vs_open_R", "mean")).round(2), "\n")
    for m in (600, 630):
        u = tr[tr[f"unfilled_{m}"] == True]  # noqa: E712
        print(f"2. Still unfilled at {m // 60}:{m % 60:02d}: rest of day in the gap direction (R), by how much was retraced so far")
        u = u.assign(retr=pd.cut(u[f"retrace_{m}"], [-0.01, 0.25, 0.5, 1.0], labels=["<25%", "25-50%", "50-99%"]),
                     ext=np.where(u[f"pos_{m}_R"] > 0, "beyond open", "inside gap"))
        print(u.groupby(["dir", "retr", "ext"], observed=True)[f"rest_{m}_R"].agg(["size", "mean", "median", lambda s: (s > 0).mean()])
              .rename(columns={"<lambda_0>": "share_up"}).round(3), "\n")
        print(u.groupby(["size"], observed=True)[f"rest_{m}_R"].agg(["size", "mean", lambda s: (s > 0).mean()]).round(3), "\n")
    f = tr[tr.filled & tr.after_back_R.notna()]
    print("3. After the fill (next 60 min, R): back toward the open vs through the previous close; close vs PDC")
    print(f.groupby(["dir", "size"], observed=True)[["after_back_R", "after_through_R", "after_close_R"]]
          .agg(["size", "mean"]).round(3), "\n")
    print(f.assign(early=f.fill_min <= 30).groupby(["dir", "early"])[["after_back_R", "after_through_R", "after_close_R"]].mean().round(3), "\n")
    o = tr[~tr.filled]
    print("4. Gaps left open at the close: filled within the next 1 / 3 / 5 sessions")
    print(o.groupby(["dir", "size"], observed=True)[["later_fill_1", "later_fill_3", "later_fill_5"]].agg(["size", "mean"]).round(2))


if __name__ == "__main__":
    main()
