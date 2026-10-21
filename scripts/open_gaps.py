"""Gap fill on NSX at the US open: statistics and fade-to-fill strategy options.

    python scripts/open_gaps.py

Classic gap = US open (09:30) vs previous US close (PDC). Filled = price trades back to PDC during the
session (09:30-16:00 ET). Also reported: the PDH/PDL definition (open beyond yesterday's high/low).
Last 18 months only; R0 = previous US range (1-day look-back). Train = first 12 months, test = last 6.

Fade-to-fill options (one setup per gap day; flat at 16:00; same-minute stop+target = stop;
1 NQ contract, $20/pt, 1 pt round trip, no slippage):
  entry   'open'           close of the first 1-minute candle (09:30)
          'confirm_<tf>'   first <tf>-minute candle closes against the gap -> enter at its close
          'first_<tf>'     enter at the first <tf>-minute candle close regardless of its direction
  target  'full'  = PDC (gap fully filled)        'half' = halfway from the open to PDC
  stop    'gap'   = open + 1 x gap beyond the open  'R25' = open + 0.25 R0 beyond the open
          'candle' = first-candle extreme + 0.05 R0 (confirm/first entries only)
  size    gap in R0: all, < 0.10, 0.10-0.25, 0.25-0.50, >= 0.50, <= 0.50
  side    both / gap-up only (short) / gap-down only (long)
No trade when the entry is already at/through the target.
Writes reports/market_open/gaps/.
"""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "market_open" / "gaps"
_spec = importlib.util.spec_from_file_location("o", ROOT / "scripts" / "open_orb.py")
o = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o)

TFS = [1, 3, 5, 10]
SIZES = {"all": (0, np.inf), "< 0.10R": (0, 0.10), "0.10–0.25R": (0.10, 0.25), "0.25–0.50R": (0.25, 0.50),
         "≥ 0.50R": (0.50, np.inf), "≤ 0.50R": (0, 0.50)}
SIDES = ["both", "gap-up (short)", "gap-down (long)"]


def gap_table(days: list[dict], lv: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for d in days:
        r = lv.loc[d["day"]]
        op, pdc, R0 = d["o"][0], r.PDC, d["R0"]
        gap = op - pdc
        if gap == 0:
            continue
        g = 1 if gap > 0 else -1
        hit = (d["l"] <= pdc) if g == 1 else (d["h"] >= pdc)
        half = op - gap / 2
        hit_half = (d["l"] <= half) if g == 1 else (d["h"] >= half)
        fi = int(np.argmax(hit)) if hit.any() else None
        against = (d["h"] - op) if g == 1 else (op - d["l"])  # move with the gap (against a fade)
        mins = lambda i: (d["t"][i] - d["t"][0]).total_seconds() / 60 + 1
        rows.append(dict(day=d["day"], gap="up" if g == 1 else "down", gap_pts=abs(gap), gap_pct=abs(gap) / pdc * 100,
                         gap_R=abs(gap) / R0, beyond_range=(op > r.PDH) if g == 1 else (op < r.PDL),
                         filled=fi is not None, half_filled=bool(hit_half.any()),
                         fill_min=mins(fi) if fi is not None else np.nan,
                         run_before_fill_R=(against[: fi + 1].max() if fi is not None else against.max()) / R0,
                         run_before_fill_gaps=(against[: fi + 1].max() if fi is not None else against.max()) / abs(gap),
                         close_vs_open_fade=((op - d["c"][-1]) * g) / R0))
    t = pd.DataFrame(rows)
    t["size"] = pd.cut(t.gap_R, [0, 0.10, 0.25, 0.50, np.inf], labels=["< 0.10R", "0.10–0.25R", "0.25–0.50R", "≥ 0.50R"], right=False).astype(str)
    return t


def fade_trades(days: list[dict], lv: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for d in days:
        r = lv.loc[d["day"]]
        op, pdc, R0 = d["o"][0], r.PDC, d["R0"]
        gap = op - pdc
        if gap == 0:
            continue
        g = 1 if gap > 0 else -1
        side = -g  # fade the gap
        gap_R = abs(gap) / R0
        entries = [("open", 1, None)] + [(f"confirm_{tf}m", tf, "confirm") for tf in TFS] + [(f"first_{tf}m", tf, "first") for tf in TFS]
        for name, tf, kind in entries:
            k = o.or_end(d, tf)
            if k == 0 or k >= len(d["c"]):
                continue
            c_close, c_open = d["c"][k - 1], d["o"][0]
            c_hi, c_lo = d["h"][:k].max(), d["l"][:k].min()
            if kind == "confirm" and not ((c_close - c_open) * g < 0):
                continue  # first candle must move against the gap
            if (d["l"][:k].min() <= pdc) if g == 1 else (d["h"][:k].max() >= pdc):
                continue  # already filled inside the first candle
            entry = c_close
            for tgt_kind, stop_kind in itertools.product(["full", "half"], ["gap", "R25", "candle"]):
                if stop_kind == "candle" and kind is None:
                    continue
                target = pdc if tgt_kind == "full" else op - gap / 2
                if stop_kind == "gap":
                    stop = op + gap
                elif stop_kind == "R25":
                    stop = op + g * 0.25 * R0
                else:
                    stop = (c_hi + 0.05 * R0) if g == 1 else (c_lo - 0.05 * R0)
                if (target - entry) * side <= 0 or (entry - stop) * side <= 0:
                    continue
                j, px, why = o.exit_trade(d, k, side, stop, target)
                pts = side * (px - entry) - o.COST
                held = (d["t"][j] - d["t"][k]).total_seconds() / 60 + 1
                rows.append((d["day"], "up" if g == 1 else "down", gap_R, name, tgt_kind, stop_kind, pts, pts / abs(entry - stop),
                             abs(target - entry) / abs(entry - stop), held, why, d["t"][k], d["t"][j] + pd.Timedelta(minutes=1)))
    return pd.DataFrame(rows, columns=["day", "gap", "gap_R", "entry", "target", "stop", "pnl_pts", "r_multiple", "reward_risk", "minutes_held", "exit_reason",
                                       "entry_time", "exit_time"])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    days = o.load_days()
    lv = pd.read_csv(o.SRC / "daily_levels_NSX.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    gt = gap_table(days, lv)
    gt.to_csv(OUT / "gap_days.csv", index=False)
    tr = fade_trades(days, lv)
    rows = []
    for (entry, tgt, stop), g in tr.groupby(["entry", "target", "stop"]):
        for (sz, (a, b)), side in itertools.product(SIZES.items(), SIDES):
            x = g[(g.gap_R >= a) & (g.gap_R < b)]
            if side != "both":
                x = x[x.gap == ("up" if side.startswith("gap-up") else "down")]
            if len(x) == 0:
                continue
            for label, (s, e) in {"train (first 12m)": (o.START, o.TRAIN_END), "test (last 6m, unseen)": (o.TRAIN_END + pd.Timedelta(days=1), o.END),
                                  **{w: (st, o.END) for w, st in o.WINDOWS.items()}}.items():
                y = x[(x.day >= s) & (x.day <= e)]
                if len(y) == 0:
                    continue
                m = o.metrics(y)
                m["median_reward_risk"] = y.reward_risk.median()
                rows.append(dict(entry=entry, target=tgt, stop=stop, size=sz, side=side, period=label, **m))
    grid = pd.DataFrame(rows)
    grid.to_csv(OUT / "fade_grid.csv", index=False)
    tr.to_csv(OUT / "fade_trades.csv", index=False)
    print(f"{len(gt)} gap days; {len(tr)} fade trades; grid rows {len(grid)}")


if __name__ == "__main__":
    main()
