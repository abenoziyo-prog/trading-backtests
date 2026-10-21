"""Backtest S6 (PDH rejection -> break-up long) exactly as pre-registered in
reports/s6_rejection_breakout/PREREGISTRATION.md.

    python scripts/backtest_s6.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant import level_strategies as ls
from quant import reactions as rx
from quant import regime as rg
from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "s6_rejection_breakout"
TFS = {"3m": 3, "5m": 5, "10m": 10}
MODES = {"none": (-np.inf, np.inf, "vix_prev"), "fixed": (20, 30, "vix_prev"), "relative": (60, 90, "vix_pct")}
END = pd.Timestamp("2026-10-09 23:59", tz=s.TZ)
PERIODS = {
    "2011-2026": (pd.Timestamp("2011-01-01", tz=s.TZ), END),
    "2011-2018": (pd.Timestamp("2011-01-01", tz=s.TZ), pd.Timestamp("2018-12-31 23:59", tz=s.TZ)),
    "2019-2026": (pd.Timestamp("2019-01-01", tz=s.TZ), END),
    "last 18m": (pd.Timestamp("2025-04-10", tz=s.TZ), END),
    "last 12m": (pd.Timestamp("2025-10-10", tz=s.TZ), END),
    "last 6m": (pd.Timestamp("2026-04-10", tz=s.TZ), END),
}


def main() -> None:
    inst = s.INSTRUMENTS["NSX"]
    m = s.load(inst)
    m = m[m.index >= pd.Timestamp("2010-12-01", tz=s.TZ)]
    vix = pd.read_parquet(ROOT / "data" / "free" / "VIX_daily_cboe.parquet")
    daily = rx.daily_table(inst, m)
    lv = rx.reference_levels(inst, m, daily, with_sessions=False)
    lv = lv[lv.level == "PDH"].reset_index(drop=True)
    trades = []
    for tf, n in TFS.items():
        bars = rx.tf_bars(inst, m, n)
        ev = rx.detect_events(lv, bars, tf)
        entry = pd.DatetimeIndex(ev.entry_time)
        ev["vix_prev"] = rg.lookup_prior(vix["close"], entry, 975)
        ev["vix_pct"] = rg.lookup_prior(rg.vix_percentile(vix), entry, 975)
        rej = (ev.reaction == "rejected").sum()
        for mode, (lo, hi, col) in MODES.items():
            e = ev.copy()
            e["vix_prev"] = e[col]
            sig = ls.rejection_breakout_signals(e, bars, tf, vix_min=lo, vix_max=hi)
            tr = ls.simulate(sig, m, inst, cost_pts=1.0)
            if tr.empty:
                continue
            tr["tf"], tr["mode"] = tf, mode
            trades.append(tr)
            print(f"{tf:>3} {mode:8s}: {rej:,} PDH rejections -> {len(sig):,} same-day break-ups -> {len(tr):,} trades")
    t = pd.concat(trades, ignore_index=True)
    t["pnl_usd"] = t.pnl_pts * 20.0
    t.to_csv(OUT / "trades.csv", index=False)

    rows = []
    for period, (a, b) in PERIODS.items():
        w = t[(pd.DatetimeIndex(t.entry_time) >= a) & (pd.DatetimeIndex(t.entry_time) <= b)]
        for (tf, mode), g in w.groupby(["tf", "mode"]):
            st = ls.stats(g.sort_values("entry_time"), 20.0)
            r = g.r_multiple
            tstat = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) >= 5 and r.std(ddof=1) > 1e-6 else np.nan
            rows.append(dict(period=period, tf=tf, filter=mode, trades=st["trades"], win_rate=st["win_rate"],
                             profit_factor=st["profit_factor"], total_r=r.sum(), avg_r=r.mean(), t_stat=tstat,
                             avg_minutes=st["avg_minutes"], max_drawdown=st["max_drawdown"], net_pnl=st["net_pnl"],
                             stops=st["exits"].get("stop", 0), targets=st["exits"].get("target", 0), eod=st["exits"].get("end of day", 0)))
    summ = pd.DataFrame(rows)
    summ.to_csv(OUT / "summary.csv", index=False)

    # pre-registered scoring of the primary test: 5m, no filter
    p = summ[(summ.tf == "5m") & (summ["filter"] == "none")].set_index("period")
    checks = {
        "1. 2011-2026 PF >= 1.2": p.loc["2011-2026", "profit_factor"] >= 1.2,
        "1. 2011-2026 t-stat >= 2.0": p.loc["2011-2026", "t_stat"] >= 2.0,
        "2. 2011-2018 PF > 1": p.loc["2011-2018", "profit_factor"] > 1,
        "2. 2019-2026 PF > 1": p.loc["2019-2026", "profit_factor"] > 1,
        "3. last 18m PF > 1": p.loc["last 18m", "profit_factor"] > 1,
    }
    pd.DataFrame([{"criterion": k, "passed": bool(v)} for k, v in checks.items()]).to_csv(OUT / "primary_test.csv", index=False)
    pd.set_option("display.width", 230)
    show = summ.copy()
    show["win_rate"] = (show.win_rate * 100).round(1)
    print(show[["period", "tf", "filter", "trades", "win_rate", "profit_factor", "total_r", "avg_r", "t_stat", "avg_minutes", "max_drawdown", "net_pnl"]]
          .round(2).to_string(index=False))
    print("\nPRIMARY TEST (5m, no filter):")
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print("  => candidate" if all(checks.values()) else "  => NOT a candidate under the pre-registered criteria")


if __name__ == "__main__":
    main()
