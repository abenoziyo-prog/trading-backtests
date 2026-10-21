"""Recent-period backtest of the five PDH/PDL strategies on 3m / 5m / 10m bars.

    python scripts/backtest_recent.py

Windows end at the last NSX bar (2026-10-09): last 18, 12 and 6 months.
Three VIX filter modes, all fixed before testing:
  fixed     the original thresholds on the prior VIX close (S1 >=30, S2 >=25, S3 <20, S4 20-30, S5 >=20)
  relative  the same regimes as percentiles of the trailing 1-year VIX
            (S1 top 10%, S2 top 20%, S3 bottom 50%, S4 60-90th, S5 top 40%)
  none      no VIX filter (reference: what the filter adds)
NSX = 1 NQ contract ($20/pt, 1 pt round trip). QQQ = 100 shares ($0.02/share), data to 2025-12-31 only.
Writes reports/strategies_recent/.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from quant import level_strategies as ls
from quant import reactions as rx
from quant import regime as rg
from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "strategies_recent"
TFS = {"3m": 3, "5m": 5, "10m": 10}
END = pd.Timestamp("2026-10-09 23:59", tz=s.TZ)
WINDOWS = {"18m": 18, "12m": 12, "6m": 6}
RELATIVE = {"S1": (90, 101), "S2": (80, 101), "S3": (0, 50), "S4": (60, 90), "S5": (60, 101)}
SETUP = {"NSX": dict(dpp=20.0, cost=1.0), "QQQ": dict(dpp=100.0, cost=0.02)}


def window_start(months: int) -> pd.Timestamp:
    return (END.normalize() - pd.DateOffset(months=months) + pd.Timedelta(days=1)).tz_convert(s.TZ)


def build_events(name: str, vix: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    inst = s.INSTRUMENTS[name]
    m = s.load(inst)
    m = m[m.index >= pd.Timestamp("2024-01-01", tz=s.TZ)]  # 14-day ATR warm-up well before the 18m window
    daily = rx.daily_table(inst, m)
    lv = rx.reference_levels(inst, m, daily, with_sessions=False)
    lv = lv[lv.level.isin(["PDH", "PDL"])].reset_index(drop=True)
    bars = {tf: rx.tf_bars(inst, m, n) for tf, n in TFS.items()}
    ev = pd.concat([rx.detect_events(lv, bars[tf], tf) for tf in TFS], ignore_index=True)
    entry = pd.DatetimeIndex(ev.entry_time)
    ev["vix_prev"] = rg.lookup_prior(vix["close"], entry, 975)
    ev["vix_pct"] = rg.lookup_prior(rg.vix_percentile(vix), entry, 975)
    ev["instrument"] = name
    return ev, m, bars


def variants() -> list[tuple[str, ls.Strategy]]:
    out = []
    for base in ls.STRATEGIES:
        for tf in TFS:
            for mode in ("fixed", "relative", "none"):
                if mode == "fixed":
                    st = replace(base, tf=tf)
                elif mode == "relative":
                    lo, hi = RELATIVE[base.code]
                    st = replace(base, tf=tf, vix_min=lo, vix_max=hi)
                else:
                    st = replace(base, tf=tf, vix_min=-np.inf, vix_max=np.inf)
                out.append((mode, st))
    return out


def run(name: str, ev: pd.DataFrame, m: pd.DataFrame, bars: dict) -> pd.DataFrame:
    inst = s.INSTRUMENTS[name]
    trades = []
    for mode, st in variants():
        e = ev.copy()
        if mode == "relative":
            e["vix_prev"] = e["vix_pct"]  # filter on the percentile instead of the level
        tr = ls.simulate(ls.signals(st, e, bars[st.tf]), m, inst, cost_pts=SETUP[name]["cost"])
        if tr.empty:
            continue
        tr["mode"], tr["tf"], tr["code"] = mode, st.tf, st.code
        tr["name"] = st.name.split(" (")[0]
        trades.append(tr)
    t = pd.concat(trades, ignore_index=True)
    t.insert(0, "instrument", name)
    t["pnl_usd"] = t.pnl_pts * SETUP[name]["dpp"]
    return t


def table(trades: pd.DataFrame, months: int) -> pd.DataFrame:
    start = window_start(months)
    w = trades[(pd.DatetimeIndex(trades.entry_time) >= start) & (pd.DatetimeIndex(trades.entry_time) <= END)]
    rows = []
    for (inst, mode, code, tf), g in w.groupby(["instrument", "mode", "code", "tf"]):
        st = ls.stats(g.sort_values("entry_time"), SETUP[inst]["dpp"])
        r = g.r_multiple
        t_stat = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) >= 5 and r.std(ddof=1) > 1e-6 else np.nan  # too few trades -> blank
        rows.append(dict(instrument=inst, filter=mode, strategy=code, name=g.name.iloc[0], tf=tf, trades=st["trades"],
                         win_rate=st["win_rate"], profit_factor=st["profit_factor"], total_r=r.sum(), avg_r=r.mean(), t_stat=t_stat,
                         avg_minutes=st["avg_minutes"], max_drawdown=st["max_drawdown"], net_pnl=st["net_pnl"],
                         first_trade=pd.Timestamp(g.entry_time.min()).date(), last_trade=pd.Timestamp(g.entry_time.max()).date()))
    out = pd.DataFrame(rows)
    out.insert(0, "window", f"last {months} months ({start.date()} to {END.date()})")
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    vix = pd.read_parquet(ROOT / "data" / "free" / "VIX_daily_cboe.parquet")
    all_trades = []
    for name in SETUP:
        ev, m, bars = build_events(name, vix)
        print(f"{name}: {len(ev):,} first-touch events on 3m/5m/10m")
        all_trades.append(run(name, ev, m, bars))
    trades = pd.concat(all_trades, ignore_index=True)
    trades.to_csv(OUT / "trades.csv", index=False)
    tables = []
    for label, months in WINDOWS.items():
        t = table(trades, months)
        t.to_csv(OUT / f"summary_{label}.csv", index=False)
        tables.append(t.assign(window_key=label))
    allt = pd.concat(tables, ignore_index=True)
    allt.to_csv(OUT / "summary_all_windows.csv", index=False)
    pd.set_option("display.width", 250)
    for label in WINDOWS:
        t = allt[(allt.window_key == label) & (allt.instrument == "NSX")].copy()
        print(f"\n===== NSX {t.window.iloc[0]} =====")
        t["win_rate"] = (t.win_rate * 100).round(1)
        print(t[["filter", "strategy", "tf", "trades", "win_rate", "profit_factor", "total_r", "t_stat", "avg_minutes", "max_drawdown", "net_pnl"]]
              .round(2).sort_values(["filter", "strategy", "tf"]).to_string(index=False))


if __name__ == "__main__":
    main()
