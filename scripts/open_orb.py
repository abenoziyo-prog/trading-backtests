"""Opening Range Breakout (ORB) on NSX: parameter grid, chosen on the first 12 months, checked on the last 6.

    python scripts/open_orb.py

Data: NSX US session 09:30-16:00 ET, 2025-04-10 -> 2026-10-09 (last 18 months). R0 = previous US range
(1-day look-back). Approved rules: entries until 15:30, flat at 16:00, multiple trades per day (one
position at a time; a new trade needs a fresh breakout: previous candle closed inside the range),
same-minute stop+target = stop, 1 NQ contract ($20/pt), 1 pt round trip, no slippage.

Classic ORB grid
  or_min      opening range = first 5 / 15 / 30 / 60 minutes
  tf          confirmation candle 1 / 3 / 5 / 10 min, built from the end of the opening range
  entry       candle closes above the OR high (long) or below the OR low (short); enter at its close
  stop        'opposite' side of the OR, or the OR 'mid'point
  target      1R / 2R / 3R / 10R (R = entry-to-stop distance) or 'eod' (hold to 16:00)
  direction   both / long / short
  width       'all' days, or 'narrow' = OR height < 0.30 x previous US range
Paper variant (Zarattini & Aziz 5-min ORB): first candle of tf minutes sets the direction (no trade if
open == close); enter at its close; stop at its low (long) / high (short); target 1/2/3/10R or eod.

Selection: rank on the first 12 months (2025-04-10 -> 2026-04-09) by total R with >= 40 trades and
profit factor > 1; then report the same configuration on the unseen last 6 months (2026-04-10 -> 2026-10-09).
Writes reports/market_open/orb/.
"""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "us_session_levels"
OUT = ROOT / "reports" / "market_open" / "orb"
_spec = importlib.util.spec_from_file_location("lr", ROOT / "scripts" / "us_level_reactions_tf.py")
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)

START, END = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09")
TRAIN_END = pd.Timestamp("2026-04-09")
WINDOWS = {"18m": pd.Timestamp("2025-04-10"), "12m": pd.Timestamp("2025-10-10"), "6m": pd.Timestamp("2026-04-10")}
OR_MIN = [5, 15, 30, 60]
TFS = [1, 3, 5, 10]
STOPS = ["opposite", "mid"]
TARGETS = [1.0, 2.0, 3.0, 10.0, None]
DIRECTIONS = ["both", "long", "short"]
WIDTHS = ["all", "narrow"]
from quant.live.research_us import COST, DPP, LAST_ENTRY, candles_after, exit_trade, or_end  # noqa: E402,F401  shared with the live bot


def load_days() -> list[dict]:
    lv = pd.read_csv(SRC / "daily_levels_NSX.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    rth = lr.rth_minutes("NSX")
    by_day = {d: g for d, g in rth.groupby(rth.index.tz_localize(None).normalize())}
    days = []
    for day, r in lv.iterrows():
        dm = by_day.get(day)
        if dm is None or len(dm) < 200 or not (r.PDH - r.PDL) > 0:
            continue
        mins = dm.index.hour * 60 + dm.index.minute
        days.append(dict(day=day, R0=r.PDH - r.PDL, t=dm.index, mod=np.asarray(mins), o=dm.open.to_numpy(),
                         h=dm.high.to_numpy(), l=dm.low.to_numpy(), c=dm.close.to_numpy()))
    return days


def classic(days: list[dict]) -> pd.DataFrame:
    rows = []
    for d in days:
        for or_min in OR_MIN:
            k = or_end(d, or_min)
            if k == 0 or k >= len(d["c"]):
                continue
            orh, orl = d["h"][:k].max(), d["l"][:k].min()
            height = orh - orl
            if height <= 0:
                continue
            narrow = height < 0.30 * d["R0"]
            mid = (orh + orl) / 2
            for tf in TFS:
                cd = candles_after(d, k, tf)
                for stop_kind, tgt, direction in itertools.product(STOPS, TARGETS, DIRECTIONS):
                    busy_until = -1
                    prev_inside = True  # the opening range ends inside itself
                    for i in range(len(cd["c"])):
                        last = cd["last"][i]
                        cl = cd["c"][i]
                        inside = orl <= cl <= orh
                        if last <= busy_until:
                            prev_inside = inside
                            continue
                        side = 1 if cl > orh else (-1 if cl < orl else 0)
                        fresh = side != 0 and prev_inside
                        prev_inside = inside
                        if not fresh or (direction == "long" and side == -1) or (direction == "short" and side == 1):
                            continue
                        if d["mod"][last] + 1 > LAST_ENTRY:
                            break
                        pos = last + 1
                        if pos >= len(d["c"]):
                            break
                        stop = (orl if side == 1 else orh) if stop_kind == "opposite" else mid
                        risk = (cl - stop) * side
                        if risk <= 0:
                            continue
                        target = None if tgt is None else cl + side * tgt * risk
                        j, px, why = exit_trade(d, pos, side, stop, target)
                        pts = side * (px - cl) - COST
                        held = (d["t"][j] - d["t"][pos]).total_seconds() / 60 + 1
                        rows.append((d["day"], or_min, tf, stop_kind, "eod" if tgt is None else f"{tgt:g}R", direction, narrow,
                                     side, pts, pts / risk, held, why, d["t"][pos], d["t"][j] + pd.Timedelta(minutes=1)))
                        busy_until = j
    cols = ["day", "or_min", "tf", "stop", "target", "direction", "narrow", "side", "pnl_pts", "r_multiple", "minutes_held", "exit_reason",
            "entry_time", "exit_time"]
    return pd.DataFrame(rows, columns=cols)


def paper(days: list[dict]) -> pd.DataFrame:
    rows = []
    for d in days:
        for tf in TFS:
            k = or_end(d, tf)  # the first candle = first tf minutes by clock time
            if k == 0 or k >= len(d["c"]):
                continue
            o, c = d["o"][0], d["c"][k - 1]
            hi, lo = d["h"][:k].max(), d["l"][:k].min()
            if c == o:
                continue
            side = 1 if c > o else -1
            stop = lo if side == 1 else hi
            risk = (c - stop) * side
            if risk <= 0:
                continue
            for tgt in TARGETS:
                target = None if tgt is None else c + side * tgt * risk
                j, px, why = exit_trade(d, k, side, stop, target)
                pts = side * (px - c) - COST
                rows.append((d["day"], tf, "eod" if tgt is None else f"{tgt:g}R", side, pts, pts / risk, j - k + 1, why,
                             d["t"][k], d["t"][j] + pd.Timedelta(minutes=1)))
    return pd.DataFrame(rows, columns=["day", "tf", "target", "side", "pnl_pts", "r_multiple", "minutes_held", "exit_reason",
                                       "entry_time", "exit_time"])


def metrics(g: pd.DataFrame) -> dict:
    p = g.pnl_pts.to_numpy() * DPP
    if len(p) == 0:
        return dict(trades=0)
    eq = np.cumsum(p)
    dd = (eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min()
    wins, losses = p[p > 0], p[p <= 0]
    r = g.r_multiple
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) >= 5 and r.std(ddof=1) > 1e-9 else np.nan
    return dict(trades=len(p), win_rate=len(wins) / len(p), profit_factor=wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
                total_r=r.sum(), avg_r=r.mean(), t_stat=t, avg_minutes=g.minutes_held.mean(), max_drawdown=dd, net_pnl=p.sum())


def evaluate(trades: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    periods = {"train (first 12m)": (START, TRAIN_END), "test (last 6m, unseen)": (TRAIN_END + pd.Timedelta(days=1), END),
               **{w: (s, END) for w, s in WINDOWS.items()}}
    for cfg, g in trades.groupby(keys, sort=False):
        for label, (a, b) in periods.items():
            m = metrics(g[(g.day >= a) & (g.day <= b)])
            rows.append(dict(zip(keys, cfg if isinstance(cfg, tuple) else (cfg,)), period=label, **m))
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    days = load_days()
    print(f"{len(days)} US sessions")
    cl = classic(days)
    # width filter: 'all' uses every day, 'narrow' only narrow-range days
    narrow = cl[cl.narrow].assign(width="narrow")
    cl = pd.concat([cl.assign(width="all"), narrow], ignore_index=True)
    cl.to_parquet(OUT / "classic_trades.parquet")
    keys = ["or_min", "tf", "stop", "target", "direction", "width"]
    ev = evaluate(cl, keys)
    ev.to_csv(OUT / "classic_grid.csv", index=False)
    pp = paper(days)
    pp.to_csv(OUT / "paper_trades.csv", index=False)
    evp = evaluate(pp, ["tf", "target"])
    evp.to_csv(OUT / "paper_grid.csv", index=False)
    print(f"classic configs: {ev.groupby(keys).ngroups}, paper configs: {evp.groupby(['tf', 'target']).ngroups}")


if __name__ == "__main__":
    main()
