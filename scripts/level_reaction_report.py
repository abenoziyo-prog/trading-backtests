"""Charts for the level-reaction study (run after level_reactions.py and the summary steps).

    python scripts/level_reaction_report.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from quant import reactions as rx

OUT = Path(__file__).resolve().parents[1] / "reports" / "level_reactions"


def edge_scatter() -> float:
    j = pd.read_csv(OUT / "consistency_by_level_tf.csv")
    n = j[j.instrument == "NSX"]
    r = np.corrcoef(n.edge_1, n.edge_2)[0, 1]
    fig, ax = plt.subplots(figsize=(6.5, 6))
    colors = {"rejected": "#ef5350", "accepted": "#26a69a"}
    for reac, g in n.groupby("reaction"):
        ax.scatter(g.edge_1 * 100, g.edge_2 * 100, s=np.sqrt(g.decided_2) * 3, alpha=0.7, label=reac, color=colors[reac])
    lim = max(abs(n.edge_1).max(), abs(n.edge_2).max()) * 110
    ax.plot([-lim, lim], [-lim, lim], color="grey", lw=0.8, ls="--")
    ax.axhline(0, color="k", lw=0.5); ax.axvline(0, color="k", lw=0.5)
    ax.set_xlabel("edge vs baseline, 2011-2018 (pp)"); ax.set_ylabel("edge vs baseline, 2019-2026 (pp)")
    ax.set_title(f"NSX: does an edge found in 2011-18 repeat in 2019-26?\ncorrelation r = {r:.2f} (dashed = perfect repeat)", fontsize=10)
    ax.legend(title="touch bar"); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "edge_repeatability.png", dpi=120); plt.close(fig)
    return r


def vix_chart() -> None:
    ev = pd.read_parquet(OUT / "events.parquet")
    ev = ev[(ev.instrument == "NSX") & ev.level.isin(["PDH", "PDL"]) & (ev.reaction != "gap")].dropna(subset=["race_0.25"])
    ev["regime"] = np.where(ev.vix_prev >= 30, "VIX ≥ 30", "VIX < 30")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, reac in zip(axes, ["accepted", "rejected"]):
        x = np.arange(4)
        for off, (reg, col) in zip((-0.18, 0.18), (("VIX < 30", "#90a4ae"), ("VIX ≥ 30", "#c62828"))):
            vals, los, his, ns = [], [], [], []
            for tf in ["15m", "30m", "1h", "4h"]:
                g = ev[(ev.tf == tf) & (ev.reaction == reac) & (ev.regime == reg)]["race_0.25"]
                k, n = g.sum(), len(g)
                lo, hi = rx.wilson(k, n)
                vals.append(k / n); los.append(k / n - lo); his.append(hi - k / n); ns.append(n)
            ax.bar(x + off, np.array(vals) * 100, 0.34, yerr=[np.array(los) * 100, np.array(his) * 100], color=col, capsize=3, label=reg)
            for xi, v, n in zip(x + off, vals, ns):
                ax.text(xi, 22, f"n={n}", ha="center", fontsize=7, rotation=90, color="white")
        ax.axhline(50, color="k", lw=0.8, ls="--")
        ax.set_xticks(x, ["15m", "30m", "1h", "4h"]); ax.set_ylim(20, 85)
        ax.set_title(f"previous-day high/low, touch bar {reac}", fontsize=10); ax.grid(alpha=0.3, axis="y")
    axes[0].set_ylabel("reversal reached first (%), 95% CI")
    axes[0].legend()
    fig.suptitle("NSX 2011-2026: high-fear regimes (prior-day VIX ≥ 30) make level breaks fail more often", fontsize=11)
    fig.tight_layout(); fig.savefig(OUT / "vix_regime_effect.png", dpi=120); plt.close(fig)


def heatmap() -> None:
    s = pd.read_csv(OUT / "summary_by_level_tf.csv")
    s = s[(s.instrument == "NSX") & (s.period == "validation")]
    order = ["PDH", "PDL", "PWH", "PWL", "ASIA_H", "ASIA_L", "LON_H", "LON_L"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
    for ax, reac in zip(axes, ["rejected", "accepted"]):
        t = s[s.reaction == reac].pivot(index="level", columns="tf", values="edge").reindex(order)[["15m", "30m", "1h", "4h"]] * 100
        im = ax.imshow(t.values, cmap="RdBu", vmin=-15, vmax=15, aspect="auto")
        ax.set_xticks(range(4), t.columns); ax.set_yticks(range(len(order)), order)
        for (i, j), v in np.ndenumerate(t.values):
            ax.text(j, i, f"{v:+.1f}", ha="center", va="center", fontsize=8)
        ax.set_title(f"touch bar {reac}", fontsize=10)
    fig.colorbar(im, ax=axes, fraction=0.025, label="reversal edge vs random entries (pp)")
    fig.suptitle("NSX 2019-2026 (out of sample): edge at each level and timeframe", fontsize=11)
    fig.savefig(OUT / "edge_heatmap_validation.png", dpi=120, bbox_inches="tight"); plt.close(fig)


if __name__ == "__main__":
    r = edge_scatter()
    vix_chart()
    heatmap()
    print(f"charts written to {OUT} (edge repeatability r = {r:.2f})")
