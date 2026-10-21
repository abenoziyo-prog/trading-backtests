"""London report: robust London system, equation tables, curves. Needs london_research.py and london_model.py outputs.

    python scripts/london_report.py

Robust London system (rules fixed from the research + the both-periods check, no fitted equation):
  1. DRIFT long: buy at 01:00 ET (last close before 01:00), stop 0.25 x previous US range, exit 09:29 ET.
  2. ASIA_BO short: from 03:00 ET, first fresh 5-minute close below the 20:00-02:00 ET low; stop at the
     range midpoint; entries until 08:00; exit 09:29 ET. Taken only when no position is open (one at a time;
     in practice on days the drift trade was stopped out).
Writes reports/london/robust_*.csv, robust_curves.png.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "london"
_spec = importlib.util.spec_from_file_location("lm", ROOT / "scripts" / "london_model.py")
lm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lm)
lr = lm.lr
DRIFT = "DRIFT|in01:00|out09:30|long|stop0.25|0m"
BO = "ASIA_BO|asia_ext|from03:00|until08:00|short|stop_mid|tgt_None|5m"
WINDOWS = {"18m": (lr.START, lr.END), "12m": (pd.Timestamp("2025-10-10"), lr.END), "6m (optimization period)": (lr.OPT_START, lr.END),
           "prior 12m (check)": (lr.START, lm.CHECK_END)}


def full_metrics(g: pd.DataFrame, n_days: int) -> dict:
    m = lr.metrics(g)
    if not m["trades"]:
        return m
    p = g.pnl_usd.to_numpy()
    w, lo = p[p > 0], p[p <= 0]
    daily = g.groupby("day").pnl_usd.sum()
    full = np.r_[daily.to_numpy(), np.zeros(max(0, n_days - len(daily)))]
    return dict(**m, long_trades=int((g.side == 1).sum()), short_trades=int((g.side == -1).sum()),
                trades_per_day=len(p) / n_days, avg_win=w.mean() if len(w) else np.nan, avg_loss=lo.mean() if len(lo) else np.nan,
                largest_win=p.max(), largest_loss=p.min(), sharpe_daily_ann=full.mean() / full.std(ddof=1) * np.sqrt(252),
                profitable_days_pct=(daily > 0).mean())


def main() -> None:
    t = pd.read_parquet(OUT / "trades.parquet")
    days = pd.Series(sorted(t.day.unique()))
    sysx = lm.picker(t[t.cand.isin([DRIFT, BO])], [DRIFT, BO])
    sysx.to_csv(OUT / "robust_trades.csv", index=False)
    rows = []
    for w, (a, b) in WINDOWS.items():
        nd = int(((days >= a) & (days <= b)).sum())
        x = sysx[(sysx.day >= a) & (sysx.day <= b)]
        rows.append(dict(system="Robust London system", window=w, **full_metrics(x, nd)))
        for c, name in ((DRIFT, "  DRIFT long (inside system)"), (BO, "  ASIA_BO short (inside system)")):
            rows.append(dict(system=name, window=w, **full_metrics(x[x.cand == c], nd)))
        for c, name in ((DRIFT, "  DRIFT long (standalone)"), (BO, "  ASIA_BO short (standalone)")):
            y = t[(t.cand == c) & (t.day >= a) & (t.day <= b)]
            rows.append(dict(system=name, window=w, **full_metrics(y, nd)))
    pd.DataFrame(rows).to_csv(OUT / "robust_results.csv", index=False)
    # curves: robust system vs 6m-optimized equation system, across 18 months
    opt_sys = pd.read_csv(OUT / "system_trades.csv", parse_dates=["day"])
    fig, ax = plt.subplots(figsize=(13, 6), dpi=150)
    fig.patch.set_facecolor("#fcfcfb"); ax.set_facecolor("#fcfcfb")
    for x, name, color, lw in ((sysx, "Robust London system (drift long + Asia-low breakout short)", "#2a78d6", 2.4),
                               (opt_sys, "6m-optimized equation system (fitted on the last 6 months)", "#eb6834", 1.6),
                               (t[t.cand == DRIFT], "DRIFT long alone", "#1baf7a", 1.2)):
        eq = pd.concat([pd.Series([0.0], index=[lr.START - pd.Timedelta(days=1)]), x.groupby("day").pnl_usd.sum().cumsum()])
        ax.step(eq.index, eq.values / 1000, where="post", color=color, lw=lw, label=f"{name}: \\${eq.iloc[-1] / 1000:,.1f}K")
        ax.plot(eq.index[-1], eq.iloc[-1] / 1000, "o", ms=5, color=color, mec="#fcfcfb", mew=1.5)
    ax.axvline(lr.OPT_START, color="#8a8984", lw=1, ls=(0, (4, 3)))
    ax.text(lr.OPT_START, ax.get_ylim()[1], "  last 6 months (optimization period) →", va="top", fontsize=9, color="#5f5e5a")
    ax.axhline(0, color="#8a8984", lw=0.8)
    ax.grid(axis="y", color="#e6e5e0", lw=0.8); ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#c3c2b7")
    ax.tick_params(colors="#5f5e5a", labelsize=9)
    ax.set_ylabel("Cumulative net P&L ($K, 1 NQ contract)", color="#3d3d3a")
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor="#3d3d3a")
    ax.set_title("NSX London strategies (01:00–09:29 ET), 2025-04-10 → 2026-10-09  ·  \\$20/pt, 1 pt cost, no slippage",
                 loc="left", fontsize=12, color="#1f1f1d")
    fig.tight_layout()
    fig.savefig(OUT / "robust_curves.png", facecolor=fig.get_facecolor())
    pd.set_option("display.width", 250)
    r = pd.DataFrame(rows)
    print(r[["system", "window", "trades", "win_rate", "profit_factor", "net_pnl", "max_drawdown", "sharpe_daily_ann", "t_stat"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
