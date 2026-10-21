"""Render the S6 report (tables + charts) from reports/s6_rejection_breakout/*.csv.

    python scripts/s6_report.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "reports" / "s6_rejection_breakout"
PERIODS = ["2011-2026", "2011-2018", "2019-2026", "last 18m", "last 12m", "last 6m"]
LABEL = {"none": "no filter (primary)", "fixed": "VIX 20–30", "relative": "VIX pct 60–90"}


def usd(x: float) -> str:
    return f"−${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def dur(x: float) -> str:
    return f"{int(x // 60)}h {int(x % 60):02d}m" if x >= 60 else f"{int(x)}m"


def tables() -> str:
    sm = pd.read_csv(OUT / "summary.csv")
    blocks = []
    for per in PERIODS:
        t = sm[sm.period == per].copy()
        t["o1"] = t.tf.map({"3m": 0, "5m": 1, "10m": 2}); t["o2"] = t["filter"].map({"none": 0, "fixed": 1, "relative": 2})
        t = t.sort_values(["o1", "o2"])
        lines = ["| TF | VIX filter | Trades | Win % | Profit factor | Total R | t-stat | Avg duration | Max drawdown | Net P&L |",
                 "|---|---|---|---|---|---|---|---|---|---|"]
        for _, r in t.iterrows():
            pf = "∞" if np.isinf(r.profit_factor) else f"{r.profit_factor:.2f}"
            ts = "–" if pd.isna(r.t_stat) else f"{r.t_stat:+.2f}"
            bold = "**" if (r.tf == "5m" and r["filter"] == "none") else ""
            lines.append(f"| {bold}{r.tf}{bold} | {bold}{LABEL[r['filter']]}{bold} | {int(r.trades)} | {r.win_rate * 100:.1f}% | {pf} | "
                         f"{r.total_r:+.1f}R | {ts} | {dur(r.avg_minutes)} | {usd(r.max_drawdown)} | {usd(r.net_pnl)} |")
        blocks.append(f"### {per}\n\n" + "\n".join(lines) + "\n")
    return "\n".join(blocks)


def charts() -> None:
    t = pd.read_csv(OUT / "trades.csv")
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True).dt.tz_convert("America/New_York")
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.8))
    for ax, start, title in ((axes[0], "2011-01-01", "2011–2026"), (axes[1], "2025-04-10", "last 18 months")):
        for tf, col in (("3m", "#90caf9"), ("5m", "#0d47a1"), ("10m", "#42a5f5")):
            g = t[(t.tf == tf) & (t["mode"] == "none") & (t.entry_time >= pd.Timestamp(start, tz="America/New_York"))].sort_values("entry_time")
            ax.step(g.entry_time, g.r_multiple.cumsum(), where="post", color=col, lw=2.0 if tf == "5m" else 1.2,
                    label=f"{tf}, no filter ({len(g)} trades){' – primary' if tf == '5m' else ''}")
        ax.axhline(0, color="k", lw=0.8)
        if start == "2011-01-01":
            ax.axvline(pd.Timestamp("2019-01-01", tz="America/New_York"), color="grey", ls="--", lw=0.8)
        ax.set_title(f"S6 cumulative R after costs, {title}", fontsize=10); ax.grid(alpha=0.3); ax.legend(fontsize=8)
    axes[0].set_ylabel("cumulative R")
    fig.tight_layout(); fig.savefig(OUT / "s6_cumulative_R.png", dpi=120); plt.close(fig)


if __name__ == "__main__":
    charts()
    (OUT / "_tables.md").write_text(tables())
    print("written s6_cumulative_R.png and _tables.md")
