"""Charts for the high-VIX deep dive (run after the regime analysis steps).

    python scripts/vix_regime_report.py
"""
from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "reports" / "level_reactions"


def _ci(s: str) -> tuple[float, float]:
    a, b = (float(x) for x in re.findall(r"[-+]?\d+\.?\d*", s))
    return a, b


def threshold_chart() -> None:
    t = pd.read_csv(OUT / "vix_threshold_sweep.csv")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.3), sharey=True)
    for ax, reac in zip(axes, ["accepted", "both"]):
        for tf, col, off in (("15m", "#90caf9", -0.2), ("30m", "#42a5f5", 0), ("1h", "#0d47a1", 0.2)):
            g = t[(t.tf == tf) & (t.reaction == reac)].sort_values("threshold")
            x = np.arange(len(g)) + off
            ax.errorbar(x, g["diff"] * 100, yerr=[(g["diff"] - g.ci_low) * 100, (g.ci_high - g["diff"]) * 100],
                        fmt="o", color=col, capsize=3, label=tf)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xticks(range(4), ["VIX ≥ 20", "≥ 25", "≥ 30", "≥ 35"])
        ax.set_title(f"touch bar: {'accepted (closed beyond)' if reac == 'accepted' else 'accepted + rejected'}", fontsize=10)
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("extra reversal vs below threshold (pp)\n95% CI resampling whole months")
    axes[0].legend(title="timeframe")
    fig.suptitle("NSX previous-day high/low (full session), 2011-2026: the effect switches on around VIX 30", fontsize=11)
    fig.tight_layout(); fig.savefig(OUT / "vix_threshold_sweep.png", dpi=120); plt.close(fig)


def dose_chart() -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    names = {"vix_prev": "VIX level", "vix_pct": "VIX 1-yr percentile", "rv20": "NSX 20d realized vol", "atr_pct": "ATR % of price"}
    for f, label in names.items():
        d = pd.read_csv(OUT / f"dose_{f}_1h.csv")
        ax.plot(range(len(d)), d.rate * 100, marker="o", label=label)
    ax.axhline(50, color="k", lw=0.8, ls="--")
    ax.set_xticks(range(6), ["0-20%\n(calm)", "20-40%", "40-60%", "60-80%", "80-90%", "top 10%\n(stress)"])
    ax.set_ylabel("reversal reached first (%)"); ax.set_xlabel("bucket of the volatility measure (cut points from 2011-18)")
    ax.set_title("NSX previous-day levels, 1h: flat until the top 10% of stress", fontsize=10)
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "vix_dose_response.png", dpi=120); plt.close(fig)


def control_chart() -> None:
    c = pd.read_csv(OUT / "control_fake_levels_summary.csv")
    r = pd.read_csv(OUT / "nsx_rth_levels_vix.csv")
    r = r[(r.threshold == 30) & r.reaction.isin(["accepted", "rejected"]) & r.tf.isin(["30m", "1h"])].copy()
    r["variant"] = "real level, regular-session day"
    c = c.copy()
    c["variant"] = c["variant"].replace({"real level": "real level, full 23h day"})
    allr = pd.concat([c[["variant", "tf", "reaction", "lift", "ci"]], r[["variant", "tf", "reaction", "lift", "ci"]]])
    order = ["real level, full 23h day", "real level, regular-session day", "fake: 0.25 ATR outside", "fake: 0.25 ATR inside"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.3), sharey=True)
    for ax, reac in zip(axes, ["accepted", "rejected"]):
        for k, tf in enumerate(["30m", "1h"]):
            g = allr[(allr.tf == tf) & (allr.reaction == reac)].set_index("variant").reindex(order)
            x = np.arange(len(order)) + (k - 0.5) * 0.3
            lo = np.array([_ci(s)[0] for s in g.ci]); hi = np.array([_ci(s)[1] for s in g.ci])
            ax.errorbar(x, g.lift, yerr=[g.lift - lo, hi - g.lift], fmt="o", capsize=3, label=tf, color=["#42a5f5", "#0d47a1"][k])
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xticks(range(len(order)), [o.replace(", ", "\n") for o in order], fontsize=8)
        ax.set_title(f"touch bar {reac}", fontsize=10); ax.grid(alpha=0.3)
    axes[0].set_ylabel("extra reversal at VIX ≥ 30 (pp), 95% CI by month")
    axes[0].legend(title="timeframe")
    fig.suptitle("Is it the level? Real full-session levels vs regular-session levels vs fake levels (NSX 2011-2026)", fontsize=11)
    fig.tight_layout(); fig.savefig(OUT / "vix_control_tests.png", dpi=120); plt.close(fig)


if __name__ == "__main__":
    threshold_chart(); dose_chart(); control_chart()
    print("charts written to", OUT)
