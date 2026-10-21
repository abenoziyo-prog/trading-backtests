"""Gap-day strategies from the gap deep dive. Rules fixed 2026-10-20 before testing.

    python scripts/us_gap_strategies.py

US session 09:30-16:00 ET, last 18 / 12 / 6 months, 1m/3m/5m/10m first candle. R = previous US session
range (PDH - PDL). Entry at the first candle's close. Same-minute stop+target = stop. Flat at 16:00.
Costs: NSX 1 NQ contract ($20/pt, 1 pt round trip); QQQ 100 shares ($0.02/share); no slippage.
Opening setups: at most one trade per gap day (by definition of the setup).

  G1 gap-up fade (short)          open > PDH; first candle closes below its open; first candle low > PDH
                                  target PDH, stop session open + 0.25R
  G2 gap-up continuation (long)   open > PDH; first candle closes above its open; first candle low > PDH
                                  target session open + 0.25R, stop PDH
  G3 gap-down continuation (short) open < PDL; first candle high < PDL (either direction)
                                  target session open - 0.25R, stop PDL
No trade if the entry is already at/through the target.
Writes reports/us_session_levels/gap_strategies/.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "us_session_levels"
OUT = SRC / "gap_strategies"
_spec = importlib.util.spec_from_file_location("ul", ROOT / "scripts" / "us_level_strategies.py")
ul = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ul)
lr = ul.lr
TFS, END, WINDOWS, COST = ul.TFS, ul.END, ul.WINDOWS, ul.COST
NAMES = {"G1": "G1 Gap-up fade (short)", "G2": "G2 Gap-up continuation (long)", "G3": "G3 Gap-down continuation (short)"}
SIZE_BINS = [0, 0.10, 0.25, 0.50, np.inf]
SIZE_LABELS = ["< 0.10R", "0.10–0.25R", "0.25–0.50R", "≥ 0.50R"]


def setup(first: pd.Series, session_open: float, pdh: float, pdl: float, R: float) -> tuple | None:
    """Return (code, side, stop, target) for the gap setup on this first candle, or None."""
    entry = first.close
    if session_open > pdh and first.low > pdh:
        if first.close < first.open:  # against the gap -> fade to PDH
            code, side, stop, target = "G1", -1, session_open + 0.25 * R, pdh
        elif first.close > first.open:  # with the gap -> continuation
            code, side, stop, target = "G2", 1, pdh, session_open + 0.25 * R
        else:
            return None
    elif session_open < pdl and first.high < pdl:
        code, side, stop, target = "G3", -1, pdl, session_open - 0.25 * R
    else:
        return None
    if (target - entry) * side <= 0 or (entry - stop) * side <= 0:
        return None  # already at/through the target, or stop on the wrong side
    return code, side, stop, target


def run(name: str) -> pd.DataFrame:
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
        if r.PDL <= o <= r.PDH:
            continue
        gap_size = (o - r.PDH) / R if o > r.PDH else (r.PDL - o) / R
        for tf, n in TFS.items():
            c = lr.candles(dm, n)
            st = setup(c.iloc[0], o, r.PDH, r.PDL, R)
            if st is None:
                continue
            code, side, stop, target = st
            pos = int(dm.index.get_loc(c.iloc[0]["last"])) + 1
            entry = c.iloc[0].close
            res = ul.simulate_day(dm, pos, side, entry, stop, target)
            pts = side * (res["exit"] - entry) - COST[name]["cost"]
            rows.append(dict(instrument=name, strategy=code, tf=tf, day=day, side="long" if side == 1 else "short",
                             gap_size=gap_size, entry_time=dm.index[pos], entry=entry, stop=stop, target=target, **res,
                             pnl_pts=pts, r_multiple=pts / abs(entry - stop), reward_risk=abs(target - entry) / abs(entry - stop),
                             pnl_usd=pts * COST[name]["dpp"]))
    t = pd.DataFrame(rows)
    t["minutes_held"] = (pd.DatetimeIndex(t.exit_time) - pd.DatetimeIndex(t.entry_time)).total_seconds() / 60
    t["size_bucket"] = pd.cut(t.gap_size, SIZE_BINS, labels=SIZE_LABELS, right=False).astype(str)
    return t


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = pd.concat([run(n) for n in ("NSX", "QQQ")], ignore_index=True)
    trades.to_csv(OUT / "trades.csv", index=False)
    rows = []
    for key, start in WINDOWS.items():
        w = trades[(trades.day >= start) & (trades.day <= END)]
        for (inst, code, tf), g in w.groupby(["instrument", "strategy", "tf"]):
            for subset, sg in [("all", g)] + [(f"gap {b}", g[g.size_bucket == b]) for b in SIZE_LABELS]:
                if sg.empty:
                    continue
                m = ul.metrics(sg)
                m["median_reward_risk"] = sg.reward_risk.median()
                rows.append(dict(window=key, instrument=inst, strategy=code, name=NAMES[code], tf=tf, subset=subset, **m))
    summ = pd.DataFrame(rows)
    summ.to_csv(OUT / "summary.csv", index=False)
    pd.set_option("display.width", 250)
    x = summ[summ.subset == "all"].copy(); x["win_rate"] = (x.win_rate * 100).round(1)
    order = {"1m": 0, "3m": 1, "5m": 2, "10m": 3}
    x = x.sort_values(["window", "instrument", "strategy", "tf"], key=lambda c: c.map(order) if c.name == "tf" else c)
    print(x[["window", "instrument", "strategy", "tf", "trades", "win_rate", "median_reward_risk", "profit_factor", "total_r", "t_stat",
             "avg_minutes", "max_drawdown", "net_pnl"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
