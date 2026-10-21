"""Six US-session level strategies (3 long, 3 short) on 1m/3m/5m/10m candles, last 18/12/6 months.

    python scripts/us_level_strategies.py

Levels from reports/us_session_levels/daily_levels_<INST>.csv (1-day look-back).
R = previous US session range (PDH - PDL). Signal = first candle of the given size in the
US session (09:30-16:00 ET) meeting the rule; entry at its close; no entries after 15:30.

  L1 Asia-low reclaim long      candle trades below Asia low, closes back above  -> long,  stop/target 0.10R
  L2 PDH break, hold to close   candle closes above PDH                          -> long,  stop 0.25R, exit 16:00
  L3 London-high magnet long    London high > PDH; first candle closes below the London high
                                -> long, target London high, stop same distance below (min 0.05R), else 16:00
  S1 PDL breakdown short        candle closes below PDL                          -> short, stop/target 0.10R
  S2 PDL break, hold to close   candle closes below PDL                          -> short, stop 0.25R, exit 16:00
  S3 London-high pullback short candle closes above London high                  -> short, stop/target 0.25R

No trade when the signal candle opened beyond the level. Same-minute stop and target = stop.
NSX: 1 NQ contract ($20/pt, 1 pt round trip). QQQ: 100 shares ($0.02/share), data to 2025-12-31.
Writes reports/us_session_levels/strategies/.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "us_session_levels"
OUT = SRC / "strategies"
_spec = importlib.util.spec_from_file_location("lr", ROOT / "scripts" / "us_level_reactions_tf.py")
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)

TFS = {"1m": 1, "3m": 3, "5m": 5, "10m": 10}
END = pd.Timestamp("2026-10-09")
WINDOWS = {"18m": pd.Timestamp("2025-04-10"), "12m": pd.Timestamp("2025-10-10"), "6m": pd.Timestamp("2026-04-10")}
COST = {"NSX": dict(cost=1.0, dpp=20.0), "QQQ": dict(cost=0.02, dpp=100.0)}
LAST_ENTRY = 15 * 60 + 30
NAMES = {"L1": "Asia-low reclaim long", "L2": "PDH break, hold to close (long)", "L3": "London-high magnet long",
         "S1": "PDL breakdown short", "S2": "PDL break, hold to close (short)", "S3": "London-high pullback short"}
SIDE = {"L1": 1, "L2": 1, "L3": 1, "S1": -1, "S2": -1, "S3": -1}


def first_signal(c: pd.DataFrame, level: float, cond: str) -> int | None:
    """Index of the first candle meeting the condition, skipping candles that opened beyond the level.

    cond: 'reclaim_low'   low < level and close > level (and open >= level)
          'close_above'   close > level (and open <= level)
          'close_below'   close < level (and open >= level)
    """
    o, h, l, cl = (c[x].to_numpy() for x in ("open", "high", "low", "close"))
    if cond == "reclaim_low":
        through = l < level
    elif cond == "close_above":
        through = h > level
    else:
        through = l < level
    if not through.any():
        return None
    i = int(np.argmax(through))  # first candle that trades through: decide on it, as in the reaction study
    if cond == "reclaim_low":
        ok = o[i] >= level and cl[i] > level
    elif cond == "close_above":
        ok = o[i] <= level and cl[i] > level
    else:
        ok = o[i] >= level and cl[i] < level
    return i if ok else None


def simulate_day(dm: pd.DataFrame, pos: int, side: int, entry: float, stop: float, target: float | None) -> dict:
    """Walk 1-minute bars from pos to the end of the US session."""
    hi, lo, cl = dm.high.to_numpy()[pos:], dm.low.to_numpy()[pos:], dm.close.to_numpy()[pos:]
    stop_hit = (lo <= stop) if side == 1 else (hi >= stop)
    tgt_hit = np.zeros_like(stop_hit) if target is None else ((hi >= target) if side == 1 else (lo <= target))
    i_s = int(np.argmax(stop_hit)) if stop_hit.any() else None
    i_t = int(np.argmax(tgt_hit)) if tgt_hit.any() else None
    if i_s is not None and (i_t is None or i_s <= i_t):
        j, px, why = i_s, stop, "stop"
    elif i_t is not None:
        j, px, why = i_t, target, "target"
    else:
        j, px, why = len(cl) - 1, cl[-1], "16:00 close"
    return dict(exit_time=dm.index[pos + j] + pd.Timedelta(minutes=1), exit=px, exit_reason=why)


def run(name: str) -> pd.DataFrame:
    lv = pd.read_csv(SRC / f"daily_levels_{name}.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    rth = lr.rth_minutes(name)
    by_day = {d: g for d, g in rth.groupby(rth.index.tz_localize(None).normalize())}
    has_sessions = "asia_low" in lv.columns
    rows = []
    for day, r in lv.iterrows():
        dm = by_day.get(day)
        R = r.PDH - r.PDL
        if dm is None or len(dm) < 200 or not R > 0:
            continue
        for tf, n in TFS.items():
            c = lr.candles(dm, n)
            plans = []
            # (code, candle index, side, stop, target)
            i = first_signal(c, r.PDH, "close_above")
            if i is not None:
                plans.append(("L2", i, 1, -0.25, None))
            i = first_signal(c, r.PDL, "close_below")
            if i is not None:
                plans.append(("S1", i, -1, 0.10, 0.10))
                plans.append(("S2", i, -1, 0.25, None))
            if has_sessions and not pd.isna(r.asia_low):
                i = first_signal(c, r.asia_low, "reclaim_low")
                if i is not None:
                    plans.append(("L1", i, 1, 0.10, 0.10))
            if has_sessions and not pd.isna(r.london_high):
                i = first_signal(c, r.london_high, "close_above")
                if i is not None:
                    plans.append(("S3", i, -1, 0.25, 0.25))
                if r.london_high > r.PDH and c.close.iloc[0] < r.london_high:
                    plans.append(("L3", 0, 1, None, "london_high"))
            for code, i, side, stop_r, tgt_r in plans:
                bar = c.iloc[i]
                end_t = bar["last"]
                if end_t.hour * 60 + end_t.minute + 1 > LAST_ENTRY:
                    continue
                pos = int(dm.index.get_loc(end_t)) + 1
                if pos >= len(dm):
                    continue
                entry = bar.close
                if code == "L3":
                    dist = r.london_high - entry
                    if dist < 0.05 * R:
                        continue
                    stop, target = entry - dist, r.london_high
                elif code in ("L2",):
                    stop, target = entry - 0.25 * R, None
                elif code == "S2":
                    stop, target = entry + 0.25 * R, None
                else:
                    stop = entry - side * stop_r * R
                    target = entry + side * tgt_r * R
                res = simulate_day(dm, pos, side, entry, stop, target)
                pts = side * (res["exit"] - entry) - COST[name]["cost"]
                risk = abs(entry - stop)
                rows.append(dict(instrument=name, strategy=code, tf=tf, day=day, side="long" if side == 1 else "short",
                                 entry_time=dm.index[pos], entry=entry, stop=stop, target=target, **res,
                                 pnl_pts=pts, r_multiple=pts / risk, pnl_usd=pts * COST[name]["dpp"]))
    t = pd.DataFrame(rows)
    t["minutes_held"] = (pd.DatetimeIndex(t.exit_time) - pd.DatetimeIndex(t.entry_time)).total_seconds() / 60
    return t


def metrics(g: pd.DataFrame) -> dict:
    g = g.sort_values("entry_time")
    p = g.pnl_usd.to_numpy()
    eq = np.cumsum(p)
    dd = (eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min()
    wins, losses = p[p > 0], p[p <= 0]
    r = g.r_multiple
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) >= 5 and r.std(ddof=1) > 1e-9 else np.nan
    return dict(trades=len(p), win_rate=len(wins) / len(p), profit_factor=wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
                total_r=r.sum(), avg_r=r.mean(), t_stat=t, avg_minutes=g.minutes_held.mean(), max_drawdown=dd, net_pnl=p.sum(),
                stops=int((g.exit_reason == "stop").sum()), targets=int((g.exit_reason == "target").sum()),
                closes=int((g.exit_reason == "16:00 close").sum()))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = pd.concat([run(n) for n in ("NSX", "QQQ")], ignore_index=True)
    trades.to_csv(OUT / "trades.csv", index=False)
    rows = []
    for key, start in WINDOWS.items():
        w = trades[(trades.day >= start) & (trades.day <= END)]
        for (inst, code, tf), g in w.groupby(["instrument", "strategy", "tf"]):
            rows.append(dict(window=key, instrument=inst, strategy=code, name=NAMES[code], tf=tf, **metrics(g)))
    summ = pd.DataFrame(rows)
    summ.to_csv(OUT / "summary.csv", index=False)
    pd.set_option("display.width", 250)
    x = summ.copy(); x["win_rate"] = (x.win_rate * 100).round(1)
    order = {"1m": 0, "3m": 1, "5m": 2, "10m": 3}
    x = x.sort_values(["window", "instrument", "strategy", "tf"], key=lambda c: c.map(order) if c.name == "tf" else c)
    print(x[["window", "instrument", "strategy", "tf", "trades", "win_rate", "profit_factor", "total_r", "t_stat", "avg_minutes", "max_drawdown", "net_pnl"]]
          .round(2).to_string(index=False))


if __name__ == "__main__":
    main()
