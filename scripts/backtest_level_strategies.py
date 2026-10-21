"""Backtest the five PDH/PDL + VIX-regime strategies (rules in src/quant/level_strategies.py).

    python scripts/backtest_level_strategies.py

NSX 2011-2026 as 1 NQ contract ($20/point, 1 point round-trip cost), plus a
supplementary QQQ 2024-2025 run (100 shares, $0.02/share round trip).
Writes reports/strategies/: trades_<instrument>.csv, summary.csv, equity curves.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from quant import level_strategies as ls
from quant import reactions as rx
from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "strategies"
EVENTS = ROOT / "reports" / "level_reactions" / "events.parquet"
SETUP = {
    "NSX": dict(dollars_per_point=20.0, cost_pts=1.0, start="2011-01-01", split=2018,
                years={"all": 15.8, "first half": 8.0, "second half": 7.8}),
    "QQQ": dict(dollars_per_point=100.0, cost_pts=0.02, start="2024-01-01", split=2024,
                years={"all": 2.0, "first half": 1.0, "second half": 1.0}),
}


def run(name: str, events: pd.DataFrame) -> pd.DataFrame:
    cfg = SETUP[name]
    inst = s.INSTRUMENTS[name]
    m = s.load(inst)
    m = m[m.index >= pd.Timestamp(cfg["start"], tz=s.TZ)]
    ev = events[(events.instrument == name) & events.level.isin(["PDH", "PDL"])]
    bars = {tf: rx.tf_bars(inst, m, rx.TFS[tf]) for tf in {st.tf for st in ls.STRATEGIES}}
    trades = []
    for st in ls.STRATEGIES:
        sig = ls.signals(st, ev, bars[st.tf])
        tr = ls.simulate(sig, m, inst, cost_pts=cfg["cost_pts"])
        if not tr.empty:
            tr["name"] = st.name
            tr["pnl_usd"] = tr.pnl_pts * cfg["dollars_per_point"]
            tr["period"] = np.where(pd.DatetimeIndex(tr.entry_time).year <= cfg["split"], "first half", "second half")
            trades.append(tr)
        print(f"  {name} {st.code}: {len(sig):>4} signals -> {len(tr):>4} trades")
    out = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    out.insert(0, "instrument", name)
    return out


def summary(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (inst, code), g in trades.groupby(["instrument", "strategy"]):
        dpp = SETUP[inst]["dollars_per_point"]
        for label, sub in [("all", g), *list(g.groupby("period"))]:
            st = ls.stats(sub, dpp)
            st["trades_per_year"] = len(sub) / SETUP[inst]["years"][label]  # over the whole period, not first-to-last trade
            st["total_r"] = sub["r_multiple"].sum()
            rows.append(dict(instrument=inst, strategy=code, name=g.name.iloc[0], period=label, **{k: v for k, v in st.items() if k != "exits"},
                             stops=st["exits"].get("stop", 0), targets=st["exits"].get("target", 0), eod=st["exits"].get("end of day", 0)))
    return pd.DataFrame(rows)


def equity_chart(trades: pd.DataFrame, inst: str) -> None:
    t = trades[trades.instrument == inst]
    if t.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    for code, g in t.groupby("strategy"):
        g = g.sort_values("exit_time")
        ax.step(pd.DatetimeIndex(g.exit_time), g.pnl_usd.cumsum(), where="post", label=f"{code} {g.name.iloc[0]} ({len(g)} trades)")
    ax.axhline(0, color="k", lw=0.8)
    if inst == "NSX":
        ax.axvline(pd.Timestamp("2019-01-01", tz=s.TZ), color="grey", ls="--", lw=0.8)
        ax.text(pd.Timestamp("2019-02-01", tz=s.TZ), ax.get_ylim()[1] * 0.9, "2019 →", color="grey", fontsize=8)
    ax.set_ylabel("cumulative P&L, $ (1 NQ contract)" if inst == "NSX" else "cumulative P&L, $ (100 QQQ shares)")
    ax.set_title(f"{inst}: equity curves after costs", fontsize=11)
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / f"equity_{inst}.png", dpi=120); plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    events = pd.read_parquet(EVENTS)
    all_trades = pd.concat([run(n, events) for n in SETUP], ignore_index=True)
    for inst, g in all_trades.groupby("instrument"):
        g.to_csv(OUT / f"trades_{inst}.csv", index=False)
        equity_chart(all_trades, inst)
    summ = summary(all_trades)
    summ.to_csv(OUT / "summary.csv", index=False)
    pd.set_option("display.width", 250)
    show = summ.copy()
    show["win_rate"] = (show.win_rate * 100).round(1)
    for c in ["profit_factor", "avg_r", "trades_per_year", "total_r"]:
        show[c] = show[c].round(2)
    for c in ["avg_trade", "max_drawdown", "net_pnl", "avg_minutes"]:
        show[c] = show[c].round(0)
    print(show[["instrument", "strategy", "period", "trades", "trades_per_year", "win_rate", "profit_factor", "avg_r", "total_r",
                "avg_trade", "avg_minutes", "max_drawdown", "net_pnl", "stops", "targets", "eod"]].to_string(index=False))


if __name__ == "__main__":
    main()
