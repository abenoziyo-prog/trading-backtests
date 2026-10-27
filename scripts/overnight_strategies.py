"""Overnight (Asia + London) strategy families built from scripts/overnight_research.py, NSX last 18 months.

    python scripts/overnight_strategies.py      # writes reports/overnight/strategy_trades.parquet, strategy_grid.csv

Night D: D-1 18:00 -> D 09:29 ET; every trade is flat by 09:29 (no overlap with the US system). R0 = previous US
range. Windows: asia = entries 18:00-02:00, london = entries 02:00-09:00. Multiple trades per night, one position per
strategy; same-minute stop+target = stop; 1 NQ contract, $20/pt, 1 pt round trip, no slippage.

  LVL_BREAK  a candle closes beyond a level after the previous candle closed on the other side -> trade the break
             (PDH long, PDL short, PDC either way, Asia 20:00-02:00 high long / low short in the london window).
             Stop k x R0 from entry; target 0.25 / 0.5 / 1.0 R0 or hold to 09:29.        (layer C: close beyond = break)
  LVL_REJECT a candle trades through the level but closes back on the near side -> fade back inside.
             Stop beyond the candle's extreme + 0.05 R0, or k x R0; target 0.10 / 0.25 / 0.5 R0 or 09:29.
                                                                                      (layer C: wick only = rejection)
  KICKOFF    at a session's exact local open, after its first 5/15/30 minutes, trade WITH or AGAINST that move;
             stop beyond the first-window extreme + 0.05 R0, or k x R0; exit after 60/120 minutes or at 0.25 R0.
                                                                         (layer B: London / EU pre-open reverse)
  DRIFT      long through positive seasonal windows (layer A): 18:00-09:29, 18:00-02:00, 01:00-03:00,
             02:00-03:00; all nights or only after a US down day; stop none / 0.25 / 0.5 R0.
Candles: 1/2/3/5/10 minutes, clock-aligned from the window start.
"""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "overnight"
_spec = importlib.util.spec_from_file_location("onr", ROOT / "scripts" / "overnight_research.py")
onr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(onr)

from quant.live.research_night import COST, DPP, FLAT, candles, exit_trade, record, walk  # noqa: E402,F401  shared with the live bot
TFS = [1, 2, 3, 5, 10]
WINDOWS = {"asia": (0, 480), "london": (480, 900)}  # 18:00-02:00, 02:00-09:00 (entries)
PERIODS = {"first 12m": (onr.START, onr.SPLIT - pd.Timedelta(days=1)), "last 6m": (onr.SPLIT, onr.END),
           "18m": (onr.START, onr.END), "12m": (pd.Timestamp("2025-10-10"), onr.END), "6m": (onr.SPLIT, onr.END)}


def levels(n: dict) -> dict:
    asia = (n["rel"] >= 120) & (n["rel"] < 480)
    lv = {"PDH": (n["PDH"], ("asia", "london")), "PDL": (n["PDL"], ("asia", "london")), "PDC": (n["PDC"], ("asia", "london"))}
    if asia.sum() > 300:
        lv["AsiaH"] = (n["h"][asia].max(), ("london",))
        lv["AsiaL"] = (n["l"][asia].min(), ("london",))
    return lv


def run_night(n: dict, rows: list) -> None:
    R0 = n["R0"]
    lv = levels(n)
    for tf in TFS:
        for wname, (a, b) in WINDOWS.items():
            cd = candles(n, a, FLAT, tf)
            if cd is None:
                continue
            prev_c = np.r_[n["c"][cd["i0"] - 1] if cd["i0"] > 0 else cd["o"][0], cd["c"][:-1]]
            for lname, (L, wins) in lv.items():
                if wname not in wins:
                    continue
                up_x = (prev_c <= L) & (cd["c"] > L)      # fresh close above
                dn_x = (prev_c >= L) & (cd["c"] < L)      # fresh close below
                wick_up = (cd["h"] > L) & (cd["c"] <= L) & (prev_c <= L)   # poked above, closed back below
                wick_dn = (cd["l"] < L) & (cd["c"] >= L) & (prev_c >= L)
                allow_l = lname in ("PDH", "PDC", "AsiaH")
                allow_s = lname in ("PDL", "PDC", "AsiaL")
                z = np.zeros_like(up_x)
                # LVL_BREAK
                for k, tg in itertools.product((0.10, 0.20, 0.30), (0.25, 0.5, 1.0, None)):
                    def mk(i, side, k=k, tg=tg):
                        e = cd["c"][i]
                        return e - side * k * R0, (None if tg is None else e + side * tg * R0)
                    walk(n, cd, up_x if allow_l else z, dn_x if allow_s else z, mk, b, rows,
                         dict(family="LVL_BREAK", config=f"{lname}|{wname}|stop{k}R|tgt{tg}", tf=tf))
                # LVL_REJECT: fade (above-level wick -> short; below-level wick -> long)
                for sk, tg in itertools.product(("wick", 0.10, 0.20), (0.10, 0.25, 0.5, None)):
                    def mk(i, side, sk=sk, tg=tg):
                        e = cd["c"][i]
                        stop = ((cd["h"][i] + 0.05 * R0) if side == -1 else (cd["l"][i] - 0.05 * R0)) if sk == "wick" else e - side * sk * R0
                        return stop, (None if tg is None else e + side * tg * R0)
                    walk(n, cd, wick_dn if allow_s else z, wick_up if allow_l else z, mk, b, rows,
                         dict(family="LVL_REJECT", config=f"{lname}|{wname}|stop{sk}|tgt{tg}", tf=tf))
    # KICKOFF
    for ev, (tz, t, off) in onr.EVENTS.items():
        e = onr.event_rel(n, tz, t, off)
        for w in (5, 15, 30):
            if e < 0 or e + w + 130 > FLAT:
                continue
            i0, i1 = np.searchsorted(n["rel"], e), np.searchsorted(n["rel"], e + w)
            if i1 - i0 < w * 0.8 or i1 >= len(n["c"]):
                continue
            move = n["c"][i1 - 1] - n["o"][i0]
            if move == 0:
                continue
            hi, lo = n["h"][i0:i1].max(), n["l"][i0:i1].min()
            for mode, sk, ex in itertools.product(("fade", "follow"), ("extreme", 0.15, 0.30), (60, 120, "0.25R")):
                side = int(np.sign(move)) * (1 if mode == "follow" else -1)
                entry = n["c"][i1 - 1]
                stop = ((hi + 0.05 * R0) if side == -1 else (lo - 0.05 * R0)) if sk == "extreme" else entry - side * sk * R0
                if (entry - stop) * side <= 0:
                    continue
                target = entry + side * 0.25 * R0 if ex == "0.25R" else None
                end = min(FLAT, e + w + (120 if ex == "0.25R" else ex))
                record(rows, n, dict(family="KICKOFF", config=f"{ev}|first{w}m|{mode}|stop{sk}|exit{ex}", tf=0), side, i1, entry, stop, target, end)
    # DRIFT
    down = n["PDC"] < n["prev_open"]
    for (a, b), cond, sk in itertools.product(((0, FLAT), (0, 480), (420, 540), (480, 540)), ("all", "after_down"), (None, 0.25, 0.5)):
        if cond == "after_down" and not down:
            continue
        i = np.searchsorted(n["rel"], a)
        if i == 0 and a > 0 or i >= len(n["c"]):
            continue
        entry = n["c"][i - 1] if i > 0 else n["o"][0]
        stop = None if sk is None else entry - sk * R0
        lab = lambda m: f"{(18 * 60 + m) // 60 % 24:02d}:{m % 60:02d}"
        record(rows, n, dict(family="DRIFT", config=f"{lab(a)}-{lab(b)}|{cond}|stop{sk}", tf=0), 1, i, entry, stop, None, b)


def metrics(g: pd.DataFrame) -> dict:
    p = g.pnl_usd.to_numpy()
    if not len(p):
        return dict(trades=0)
    w, lo = p[p > 0], p[p <= 0]
    eq = np.cumsum(p)
    r = g.r_multiple.to_numpy()
    return dict(trades=len(p), win_rate=len(w) / len(p), profit_factor=w.sum() / -lo.sum() if lo.sum() < 0 else np.inf,
                net_pnl=p.sum(), avg_trade=p.mean(), max_drawdown=(eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min(),
                t_stat=r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 and r.std(ddof=1) > 0 else np.nan,
                avg_minutes=((g.exit_time - g.entry_time).dt.total_seconds() / 60).mean())


def main() -> None:
    nights = onr.load_nights()
    rows: list = []
    for k, n in enumerate(nights):
        run_night(n, rows)
        if k % 75 == 0:
            print(f"night {k}: {len(rows):,} trades", flush=True)
    t = pd.DataFrame(rows)
    t["cand"] = t.family + "|" + t.config + "|" + t.tf.astype(str) + "m"
    t.to_parquet(OUT / "strategy_trades.parquet")
    grid = [dict(family=f, config=c, tf=tf, period=p, **metrics(g[(g.day >= a) & (g.day <= b)]))
            for (f, c, tf), g in t.groupby(["family", "config", "tf"]) for p, (a, b) in PERIODS.items()]
    pd.DataFrame(grid).to_csv(OUT / "strategy_grid.csv", index=False)
    print(f"{t.cand.nunique()} candidates, {len(t):,} trades")


if __name__ == "__main__":
    main()
