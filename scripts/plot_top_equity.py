"""Equity curves of the top NSX strategies found so far (ranked by 18-month net P&L).

    python scripts/plot_top_equity.py

Last 18 months (2025-04-10 -> 2026-10-09), 1 NQ contract ($20/pt), 1 pt round trip, no slippage.
Writes reports/top_equity_curves.png and reports/top_equity_curves.csv.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"
START, END, TEST_START = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09"), pd.Timestamp("2026-04-10")
DPP = 20.0
# categorical slots 1-6 of the reference palette, fixed order
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]


def level_trades(code: str, tf: str) -> pd.DataFrame:
    t = pd.read_csv(REP / "us_session_levels/strategies_v2/trades.csv")
    t = t[(t.instrument == "NSX") & (t.strategy == code) & (t.tf == tf)]
    return pd.DataFrame({"day": pd.to_datetime(t.day), "pnl": t.pnl_usd})


def gap_fade(stop: str) -> pd.DataFrame:
    t = pd.read_csv(REP / "market_open/gaps/fade_trades.csv", parse_dates=["day"])
    t = t[(t.entry == "confirm_1m") & (t.target == "full") & (t.stop == stop) & (t.gap == "up")]
    return pd.DataFrame({"day": t.day, "pnl": t.pnl_pts * DPP})


def orb(or_min: int, tf: int, stop: str, target: str, direction: str) -> pd.DataFrame:
    t = pd.read_parquet(REP / "market_open/orb/classic_trades.parquet")
    t = t[(t.or_min == or_min) & (t.tf == tf) & (t.stop == stop) & (t.target == target) & (t.direction == direction) & (t.width == "all")]
    return pd.DataFrame({"day": pd.to_datetime(t.day), "pnl": t.pnl_pts * DPP})


STRATEGIES = [
    ("GF-1 Gap-up fade, red 1st minute (stop 1× gap)", lambda: gap_fade("gap")),
    ("ORB-C 15-min ORB long, 3m, 1R", lambda: orb(15, 3, "opposite", "1R", "long")),
    ("ORB-A 5-min ORB both, 5m, mid stop, 2R", lambda: orb(5, 5, "mid", "2R", "both")),
    ("S3 London-high pullback short, 1m", lambda: level_trades("S3", "1m")),
    ("GF-2 Gap-up fade, red 1st minute (stop 0.25R)", lambda: gap_fade("R25")),
    ("L2 PDH break long, hold to close, 10m", lambda: level_trades("L2", "10m")),
]


def stats(p: np.ndarray) -> dict:
    eq = np.cumsum(p)
    wins, losses = p[p > 0], p[p <= 0]
    return dict(trades=len(p), win_rate=len(wins) / len(p), profit_factor=wins.sum() / -losses.sum(),
                net_pnl=p.sum(), max_drawdown=(eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min())


def style(ax) -> None:
    ax.set_facecolor("#fcfcfb")
    ax.axhline(0, color="#8a8984", lw=0.8)
    ax.axvline(TEST_START, color="#8a8984", lw=1, ls=(0, (4, 3)))
    ax.grid(axis="y", color="#e6e5e0", lw=0.8); ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#c3c2b7")
    ax.tick_params(colors="#5f5e5a", labelsize=8)


def main() -> None:
    rows, curves = [], []
    for name, load in STRATEGIES:
        t = load()
        t = t[(t.day >= START) & (t.day <= END)].sort_values("day", kind="stable")
        eq = pd.concat([pd.Series([0.0], index=[START - pd.Timedelta(days=1)]), t.groupby("day").pnl.sum().cumsum()])
        s = stats(t.pnl.to_numpy())
        test = t[t.day >= TEST_START].pnl.to_numpy()
        rows.append(dict(strategy=name, **s, test_6m_net=test.sum(), test_6m_pf=stats(test)["profit_factor"]))
        curves.append(eq.rename(name))
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), dpi=150, sharex=True, sharey=True)
    fig.patch.set_facecolor("#fcfcfb")
    for i, (ax, eq, r, color) in enumerate(zip(axes.flat, curves, rows, COLORS)):
        style(ax)
        for j, other in enumerate(curves):
            if j != i:
                ax.step(other.index, other.values / 1000, where="post", color="#d3d2cc", lw=1)
        ax.step(eq.index, eq.values / 1000, where="post", color=color, lw=2)
        ax.plot(eq.index[-1], eq.iloc[-1] / 1000, "o", ms=6, color=color, mec="#fcfcfb", mew=2)
        ax.set_title(f"#{i + 1}  {r['strategy']}", loc="left", fontsize=10, color="#1f1f1d")
        ax.text(0.02, 0.97, f"Net \\${r['net_pnl'] / 1000:,.1f}K · PF {r['profit_factor']:.2f} · {r['trades']} trades · "
                f"{r['win_rate'] * 100:.0f}% win\nMax DD \\${r['max_drawdown'] / 1000:,.1f}K · last 6m \\${r['test_6m_net'] / 1000:,.1f}K "
                f"(PF {r['test_6m_pf']:.2f})", transform=ax.transAxes, va="top", fontsize=8.5, color="#3d3d3a")
        ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%b %y"))
    for ax in axes[:, 0]:
        ax.set_ylabel("Cumulative net P&L ($K)", color="#3d3d3a", fontsize=9)
    axes[0, 2].text(TEST_START, axes[0, 2].get_ylim()[1], " last 6m →", va="top", fontsize=8, color="#5f5e5a")
    fig.suptitle("NSX top 6 strategies by 18-month net P&L  ·  2025-04-10 → 2026-10-09  ·  1 NQ contract ($20/pt), 1 pt cost, "
                 "no slippage  ·  grey = the other five", x=0.01, ha="left", fontsize=12, color="#1f1f1d")
    fig.tight_layout()
    fig.savefig(REP / "top_equity_curves.png", facecolor=fig.get_facecolor())
    pd.concat(curves, axis=1, sort=True).ffill().fillna(0).to_csv(REP / "top_equity_curves.csv")
    pd.set_option("display.width", 250)
    print(pd.DataFrame(rows).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
