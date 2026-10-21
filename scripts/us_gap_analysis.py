"""Gap-day deep dive: US session opens above PDH (gap up) or below PDL (gap down).

    python scripts/us_gap_analysis.py

Windows: last 18 / 12 / 6 months ending 2026-10-09. US session 09:30-16:00 ET. 1-day look-back:
R = previous US session range (PDH - PDL); gap size = distance from the level to the US open, in R.
Per gap day:
  filled           price trades back to the level during the session (low <= PDH / high >= PDL)
  fill_minutes     minutes from 09:30 to the fill
  run_before_fill  largest move away from the open (with the gap) before the fill, in R
  continuation     largest move with the gap over the whole session, in R
  close_with_gap   session closes beyond the open in the gap's direction
  close_beyond     session closes beyond the level (gap held)
Per first candle (1m/3m/5m/10m): its direction (with the gap or against it), and from its close
a race to the level (fill) vs a 0.25R extension away from the open (whichever comes first).
Writes reports/us_session_levels/gaps/.
"""
from __future__ import annotations

import importlib.util
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "us_session_levels"
OUT = SRC / "gaps"
_spec = importlib.util.spec_from_file_location("lr", ROOT / "scripts" / "us_level_reactions_tf.py")
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)
TFS = {"1m": 1, "3m": 3, "5m": 5, "10m": 10}
END = pd.Timestamp("2026-10-09")
WINDOWS = {"18m": pd.Timestamp("2025-04-10"), "12m": pd.Timestamp("2025-10-10"), "6m": pd.Timestamp("2026-04-10")}
SIZE_BINS = [0, 0.10, 0.25, 0.50, np.inf]
SIZE_LABELS = ["< 0.10R", "0.10–0.25R", "0.25–0.50R", "≥ 0.50R"]


def gap_days(name: str) -> pd.DataFrame:
    lv = pd.read_csv(SRC / f"daily_levels_{name}.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    rth = lr.rth_minutes(name)
    by_day = {d: g for d, g in rth.groupby(rth.index.tz_localize(None).normalize())}
    rows = []
    for day, r in lv.iterrows():
        dm = by_day.get(day)
        R = r.PDH - r.PDL
        if dm is None or len(dm) < 200 or not R > 0:
            continue
        o = dm.open.iloc[0]
        if o > r.PDH:
            d, level = 1, r.PDH
        elif o < r.PDL:
            d, level = -1, r.PDL
        else:
            continue
        hi, lo, cl = dm.high.to_numpy(), dm.low.to_numpy(), dm.close.to_numpy()
        hit = (lo <= level) if d == 1 else (hi >= level)
        fi = int(np.argmax(hit)) if hit.any() else None
        away = (hi - o) if d == 1 else (o - lo)
        row = dict(instrument=name, day=day, gap="up" if d == 1 else "down", gap_size=(o - level) * d / R, R=R,
                   filled=fi is not None, fill_minutes=float(fi + 1) if fi is not None else np.nan,
                   run_before_fill=(away[: fi + 1].max() if fi is not None else away.max()) / R,
                   continuation=away.max() / R, close_with_gap=(cl[-1] - o) * d > 0, close_beyond=(cl[-1] - level) * d > 0,
                   session_return=(cl[-1] - o) * d / R)
        for tf, n in TFS.items():
            c = lr.candles(dm, n)
            b = c.iloc[0]
            row[f"first_{tf}"] = "with gap" if (b.close - b.open) * d > 0 else "against gap"
            pos = int(dm.index.get_loc(b["last"])) + 1
            h2, l2 = hi[pos:], lo[pos:]
            to_level = (l2 <= level) if d == 1 else (h2 >= level)
            ext = (h2 >= o + 0.25 * R) if d == 1 else (l2 <= o - 0.25 * R)
            already = (b.low <= level) if d == 1 else (b.high >= level)
            if already:
                row[f"race_{tf}"] = np.nan  # filled inside the first candle
                row[f"filled_in_first_{tf}"] = True
                continue
            row[f"filled_in_first_{tf}"] = False
            i_f = int(np.argmax(to_level)) if to_level.any() else np.inf
            i_e = int(np.argmax(ext)) if ext.any() else np.inf
            row[f"race_{tf}"] = np.nan if i_f == i_e else float(i_f < i_e)  # 1 = fill came first
        rows.append(row)
    t = pd.DataFrame(rows)
    t["size_bucket"] = pd.cut(t.gap_size, SIZE_BINS, labels=SIZE_LABELS, right=False)
    return t


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def prop(x) -> str:
    x = pd.Series(x).dropna().astype(bool)
    if len(x) == 0:
        return "–"
    lo, hi = wilson(x.sum(), len(x))
    return f"{x.mean() * 100:.0f}% ({lo * 100:.0f}–{hi * 100:.0f}), n={len(x)}"


def med(x, fmt="{:.2f}") -> str:
    x = pd.Series(x).dropna()
    return fmt.format(x.median()) if len(x) else "–"


def overview(g: pd.DataFrame) -> list[tuple]:
    return [
        ("Gap days", f"{len(g)}"),
        ("Median gap size (R)", med(g.gap_size)),
        ("Gap fills (back to the level) during the session", prop(g.filled)),
        ("Median minutes to fill (filled days)", med(g.fill_minutes, "{:.0f}")),
        ("Fills within 30 minutes (all gap days)", prop(g.fill_minutes <= 30) if len(g) else "–"),
        ("Median run with the gap before filling (R)", med(g.run_before_fill)),
        ("Median largest run with the gap, whole session (R)", med(g.continuation)),
        ("Session closes further in the gap's direction (close vs open)", prop(g.close_with_gap)),
        ("Session closes beyond the level (gap held)", prop(g.close_beyond)),
    ]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    t = pd.concat([gap_days(n) for n in ("NSX", "QQQ")], ignore_index=True)
    t.to_csv(OUT / "gap_days.csv", index=False)
    rows = []
    for key, start in WINDOWS.items():
        w = t[(t.day >= start) & (t.day <= END)]
        for (inst, gap), g in w.groupby(["instrument", "gap"]):
            for item, val in overview(g):
                rows.append(dict(window=key, instrument=inst, gap=gap, table="overview", item=item, value=val))
            for b in SIZE_LABELS:
                gb = g[g.size_bucket == b]
                rows.append(dict(window=key, instrument=inst, gap=gap, table="by size", item=b,
                                 value=f"days {len(gb)} | fills {prop(gb.filled)} | closes with gap {prop(gb.close_with_gap)} | gap held {prop(gb.close_beyond)}"))
            for tf in TFS:
                for direction in ("with gap", "against gap"):
                    gd = g[g[f"first_{tf}"] == direction]
                    rows.append(dict(window=key, instrument=inst, gap=gap, table="first candle", item=f"{tf} first candle {direction}",
                                     value=f"days {len(gd)} | fills {prop(gd.filled)} | fill before 0.25R extension {prop(gd[f'race_{tf}'])} | "
                                           f"closes with gap {prop(gd.close_with_gap)}"))
    pd.DataFrame(rows).to_csv(OUT / "gap_stats.csv", index=False)
    print(f"{len(t)} gap days; stats written to {OUT}")


if __name__ == "__main__":
    main()
