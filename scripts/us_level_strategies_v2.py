"""Six US-session level strategies, revised rules (v2): multiple trades per day, gap candles included.

    python scripts/us_level_strategies_v2.py

Changes from v1 (per instruction 2026-10-20):
  - Multiple trades per strategy per day. One position at a time per strategy; after an exit, the
    next fresh signal that day can trade.
  - Gap candles are no longer skipped: a candle that opened beyond the level can be a signal.
  - Costs: NSX 1 NQ contract ($20/pt, 1 pt round trip), QQQ 100 shares ($0.02/share), no slippage.
Unchanged: US session 09:30-16:00 ET, no entries after 15:30, flat at 16:00, same-minute stop+target = stop.

Fresh signal (a new cross of the level; the first candle of the day compares with yesterday's close):
  S1/S2  previous close >= PDL and this candle closes below PDL      -> short
  L2     previous close <= PDH and this candle closes above PDH      -> long
  S3     previous close <= London high and this candle closes above  -> short
  L1     candle closes above the Asia low after either wicking below it or the previous close being below -> long
  L3     opening setup only (London high > PDH, first candle closes below the London high) -> long
Stops/targets as v1 (R = previous US session range): L1/S1 0.10R/0.10R, S3 0.25R/0.25R,
L2/S2 0.25R stop + exit 16:00, L3 target London high with the same distance as stop (min 0.05R).
Each trade is tagged gap_trade (signal candle opened beyond the level) and gap_day (US open beyond PDH/PDL).
Writes reports/us_session_levels/strategies_v2/.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "us_session_levels"
OUT = SRC / "strategies_v2"
_spec = importlib.util.spec_from_file_location("ul", ROOT / "scripts" / "us_level_strategies.py")
ul = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ul)
lr = ul.lr
TFS, END, WINDOWS, COST, NAMES, LAST_ENTRY = ul.TFS, ul.END, ul.WINDOWS, ul.COST, ul.NAMES, ul.LAST_ENTRY

# code: (level column, side, signal kind, stop R, target R or None for hold-to-close)
SPEC = {
    "S1": ("PDL", -1, "cross_below", 0.10, 0.10),
    "S2": ("PDL", -1, "cross_below", 0.25, None),
    "L2": ("PDH", 1, "cross_above", 0.25, None),
    "S3": ("london_high", -1, "cross_above", 0.25, 0.25),
    "L1": ("asia_low", 1, "reclaim", 0.10, 0.10),
}


def fresh(kind: str, prev_close: float, o: float, h: float, l: float, c: float, level: float) -> bool:
    if kind == "cross_below":
        return prev_close >= level and c < level
    if kind == "cross_above":
        return prev_close <= level and c > level
    return c > level and (l < level or prev_close < level)  # reclaim


def day_trades(name: str, day, r, dm: pd.DataFrame, tf: str, n: int) -> list[dict]:
    R = r.PDH - r.PDL
    c = lr.candles(dm, n)
    o_, h_, l_, c_ = (c[x].to_numpy() for x in ("open", "high", "low", "close"))
    last = c["last"].tolist()
    gap_day = dm.open.iloc[0] > r.PDH or dm.open.iloc[0] < r.PDL
    out = []

    def trade(code, i, side, entry, stop, target, level):
        end_t = last[i]
        if end_t.hour * 60 + end_t.minute + 1 > LAST_ENTRY:
            return None
        pos = int(dm.index.get_loc(end_t)) + 1
        if pos >= len(dm):
            return None
        res = ul.simulate_day(dm, pos, side, entry, stop, target)
        pts = side * (res["exit"] - entry) - COST[name]["cost"]
        opened_beyond = (o_[i] < level) if code in ("S1", "S2") else (o_[i] > level) if code in ("L2", "S3") else (o_[i] < level)
        out.append(dict(instrument=name, strategy=code, tf=tf, day=day, side="long" if side == 1 else "short",
                        entry_time=dm.index[pos], entry=entry, stop=stop, target=target, **res, pnl_pts=pts,
                        r_multiple=pts / abs(entry - stop), pnl_usd=pts * COST[name]["dpp"],
                        gap_trade=bool(opened_beyond), gap_day=bool(gap_day)))
        return res["exit_time"]

    for code, (col, side, kind, stop_r, tgt_r) in SPEC.items():
        level = r.get(col)
        if pd.isna(level):
            continue
        free_from = None  # time when the previous trade closed
        for i in range(len(c)):
            if free_from is not None and last[i] < free_from:
                continue  # position still open during this candle
            prev_close = r.PDC if i == 0 else c_[i - 1]
            if not fresh(kind, prev_close, o_[i], h_[i], l_[i], c_[i], level):
                continue
            entry = c_[i]
            stop = entry - side * stop_r * R
            target = None if tgt_r is None else entry + side * tgt_r * R
            t_exit = trade(code, i, side, entry, stop, target, level)
            if t_exit is None:
                break  # past 15:30 or no minutes left
            free_from = t_exit
    # L3: opening setup only
    if not pd.isna(r.get("london_high")) and r.london_high > r.PDH and c_[0] < r.london_high:
        dist = r.london_high - c_[0]
        if dist >= 0.05 * R:
            trade("L3", 0, 1, c_[0], c_[0] - dist, r.london_high, r.london_high)
    return out


def run(name: str) -> pd.DataFrame:
    lv = pd.read_csv(SRC / f"daily_levels_{name}.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    rth = lr.rth_minutes(name)
    by_day = {d: g for d, g in rth.groupby(rth.index.tz_localize(None).normalize())}
    rows = []
    for day, r in lv.iterrows():
        dm = by_day.get(day)
        if dm is None or len(dm) < 200 or not (r.PDH - r.PDL) > 0:
            continue
        for tf, n in TFS.items():
            rows += day_trades(name, day, r, dm, tf, n)
    t = pd.DataFrame(rows)
    t["minutes_held"] = (pd.DatetimeIndex(t.exit_time) - pd.DatetimeIndex(t.entry_time)).total_seconds() / 60
    return t


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = pd.concat([run(n) for n in ("NSX", "QQQ")], ignore_index=True)
    trades.to_csv(OUT / "trades.csv", index=False)
    rows = []
    for key, start in WINDOWS.items():
        w = trades[(trades.day >= start) & (trades.day <= END)]
        for (inst, code, tf), g in w.groupby(["instrument", "strategy", "tf"]):
            for subset, sg in (("all", g), ("gap-candle trades", g[g.gap_trade]), ("non-gap trades", g[~g.gap_trade]),
                               ("gap days (all trades)", g[g.gap_day])):
                if sg.empty:
                    continue
                m = ul.metrics(sg)
                m["trades_per_day"] = len(sg) / max(1, sg.day.nunique())
                rows.append(dict(window=key, instrument=inst, strategy=code, name=NAMES[code], tf=tf, subset=subset, **m))
    summ = pd.DataFrame(rows)
    summ.to_csv(OUT / "summary.csv", index=False)
    pd.set_option("display.width", 250)
    x = summ[summ.subset == "all"].copy(); x["win_rate"] = (x.win_rate * 100).round(1)
    order = {"1m": 0, "3m": 1, "5m": 2, "10m": 3}
    x = x.sort_values(["window", "instrument", "strategy", "tf"], key=lambda c: c.map(order) if c.name == "tf" else c)
    print(x[["window", "instrument", "strategy", "tf", "trades", "trades_per_day", "win_rate", "profit_factor", "total_r", "t_stat",
             "avg_minutes", "max_drawdown", "net_pnl"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
