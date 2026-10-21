"""Render the recent-period report tables and charts from reports/strategies_recent/*.csv.

    python scripts/recent_report.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "reports" / "strategies_recent"
NAMES = {"S1": "Panic breakout fade", "S2": "Stress breakout fade", "S3": "Calm momentum long",
         "S4": "PDH rejection short", "S5": "PDL sweep & reclaim long"}


def fmt_minutes(x: float) -> str:
    if pd.isna(x):
        return "–"
    return f"{int(x // 60)}h {int(x % 60):02d}m" if x >= 60 else f"{int(x)}m"


def usd(x: float) -> str:
    return f"−${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def md_table(t: pd.DataFrame) -> str:
    head = "| Strategy | TF | Trades | Win % | Profit factor | Total R | t-stat | Avg duration | Max drawdown | Net P&L |\n|---|---|---|---|---|---|---|---|---|---|"
    lines = [head]
    for _, r in t.iterrows():
        pf = "∞" if np.isinf(r.profit_factor) else f"{r.profit_factor:.2f}"
        ts = "–" if pd.isna(r.t_stat) else f"{r.t_stat:+.2f}"
        lines.append(f"| {r.strategy} {NAMES[r.strategy]} | {r.tf} | {int(r.trades)} | {r.win_rate * 100:.1f}% | {pf} | {r.total_r:+.1f}R | {ts} | "
                     f"{fmt_minutes(r.avg_minutes)} | {usd(r.max_drawdown)} | {usd(r.net_pnl)} |")
    return "\n".join(lines)


def tables_md() -> str:
    a = pd.read_csv(OUT / "summary_all_windows.csv")
    a = a[a.instrument == "NSX"]
    order = {f"S{i}": i for i in range(1, 6)}
    blocks = []
    for key in ("18m", "12m", "6m"):
        w = a[a.window_key == key]
        blocks.append(f"### {w.window.iloc[0].capitalize()}\n")
        for mode, title in (("fixed", "Fixed VIX thresholds (original rules)"), ("relative", "Relative VIX (trailing 1-year percentile)"),
                            ("none", "No VIX filter (reference)")):
            t = w[w["filter"] == mode].copy()
            t = t.sort_values(["strategy", "tf"], key=lambda c: c.map(order) if c.name == "strategy" else c.map({"3m": 0, "5m": 1, "10m": 2}))
            missing = sorted(set(order) - set(t.strategy))
            note = f"\n*No trades in this window: {', '.join(f'{m} {NAMES[m]}' for m in missing)} (its VIX regime never occurred).*\n" if missing else ""
            blocks.append(f"**{title}**\n\n{md_table(t)}\n{note}")
    return "\n".join(blocks)


def pf_heatmap() -> None:
    a = pd.read_csv(OUT / "summary_all_windows.csv")
    a = a[a.instrument == "NSX"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2), sharey=True)
    rows = [f"S{i} {tf}" for i in range(1, 6) for tf in ("3m", "5m", "10m")]
    for ax, mode in zip(axes, ("fixed", "relative", "none")):
        g = a[a["filter"] == mode].copy()
        g["row"] = g.strategy + " " + g.tf
        mat = g.pivot_table(index="row", columns="window_key", values="profit_factor").reindex(rows)[["18m", "12m", "6m"]]
        n = g.pivot_table(index="row", columns="window_key", values="trades").reindex(rows)[["18m", "12m", "6m"]]
        vals = np.clip(mat.values.astype(float), 0, 3)
        im = ax.imshow(vals, cmap="RdYlGn", vmin=0, vmax=2, aspect="auto")
        for (i, j), v in np.ndenumerate(mat.values):
            if not np.isnan(v):
                ax.text(j, i, f"{'∞' if np.isinf(v) else f'{v:.2f}'}\n({int(n.values[i, j])})", ha="center", va="center", fontsize=7)
        ax.set_xticks(range(3), ["18m", "12m", "6m"]); ax.set_yticks(range(len(rows)), rows)
        ax.set_title({"fixed": "fixed VIX", "relative": "relative VIX", "none": "no VIX filter"}[mode], fontsize=10)
    fig.colorbar(im, ax=axes, fraction=0.02, label="profit factor (trades)")
    fig.suptitle("NSX 3m/5m/10m variants: profit factor by window (blank = no trades)", fontsize=11)
    fig.savefig(OUT / "pf_heatmap.png", dpi=120, bbox_inches="tight"); plt.close(fig)


def equity() -> None:
    t = pd.read_csv(OUT / "trades.csv")
    t = t[t.instrument == "NSX"]
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True).dt.tz_convert("America/New_York")
    t = t[t.entry_time >= pd.Timestamp("2025-04-10", tz="America/New_York")]
    fig, ax = plt.subplots(figsize=(12, 5))
    picks = [("S3", "5m", "relative"), ("S3", "5m", "fixed"), ("S3", "3m", "relative"), ("S2", "5m", "relative"), ("S1", "10m", "none"),
             ("S4", "3m", "none"), ("S5", "5m", "none")]
    for code, tf, mode in picks:
        g = t[(t.code == code) & (t.tf == tf) & (t["mode"] == mode)].sort_values("entry_time")
        if g.empty:
            continue
        ax.step(g.entry_time, g.r_multiple.cumsum(), where="post", label=f"{code} {NAMES[code]} {tf}, {mode} VIX ({len(g)})")
    for m in (12, 6):
        x = pd.Timestamp("2026-10-09", tz="America/New_York") - pd.DateOffset(months=m)
        ax.axvline(x, color="grey", ls=":", lw=0.8); ax.text(x, ax.get_ylim()[1] * 0.92, f" last {m}m", color="grey", fontsize=8)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel("cumulative R (after costs)"); ax.set_title("NSX, last 18 months: cumulative R of selected variants", fontsize=11)
    ax.legend(fontsize=7, loc="upper left"); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "equity_recent_R.png", dpi=120); plt.close(fig)


if __name__ == "__main__":
    pf_heatmap(); equity()
    (OUT / "_tables.md").write_text(tables_md())
    print("written: pf_heatmap.png, equity_recent_R.png, _tables.md")
