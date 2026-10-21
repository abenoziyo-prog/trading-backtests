"""Session-box charts in the style of a TradingView sessions indicator.

    python scripts/plot_sessions.py --start 2025-12-08 --end 2025-12-11 --tf 15
    python scripts/plot_sessions.py --only NSX --start 2025-03-10 --end 2025-03-12

Draws candles at the chosen timeframe, a shaded box over each session (Asia,
London, NY AM, NY PM) from its low to its high, and extends the session's
high and low lines until price takes them out. Charts go to reports/levels/charts/.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from quant import sessions as s

OUT = Path(__file__).resolve().parents[1] / "reports" / "levels" / "charts"
COLORS = {"asia": "#e91e63", "london": "#00acc1", "ny_am": "#ff6d00", "ny_pm": "#2962ff"}
LABELS = {"asia": "Asian", "london": "London", "ny_am": "New York AM", "ny_pm": "New York PM"}


def plot(name: str, start: str, end: str, tf: int, trades: pd.DataFrame | None = None, tag: str = "") -> Path:
    inst = s.INSTRUMENTS[name]
    df = s.load(inst)
    lo, hi = pd.Timestamp(start, tz=s.TZ), pd.Timestamp(end, tz=s.TZ) + pd.Timedelta(days=1)
    window = df[(df.index >= lo) & (df.index < hi)]
    if window.empty:
        raise SystemExit(f"{name}: no data between {start} and {end}")
    bars = s.timeframe_levels(inst, window, tf).dropna(subset=["bar_start"]).reset_index(drop=True)
    x_of = pd.Series(np.arange(len(bars)), index=pd.DatetimeIndex(bars["bar_start"]))

    def x_at(ts) -> float:
        """x position of a timestamp (bar index, interpolated inside a bar)."""
        i = x_of.index.searchsorted(ts, side="right") - 1
        i = int(np.clip(i, 0, len(x_of) - 1))
        frac = (ts - x_of.index[i]) / pd.Timedelta(minutes=tf)
        return i + min(max(frac, 0.0), 1.0) - 0.5

    fig, ax = plt.subplots(figsize=(15, 7))
    up = bars.close >= bars.open
    for i, b in bars.iterrows():
        color = "#26a69a" if up[i] else "#ef5350"
        ax.vlines(i, b.low, b.high, color="#555555", lw=0.6, zorder=2)
        body = max(abs(b.close - b.open), (bars.high.max() - bars.low.min()) * 0.0008)
        ax.add_patch(Rectangle((i - 0.35, min(b.open, b.close)), 0.7, body, color=color, zorder=3))

    sess = s.session_levels(inst, df)
    sess = sess[(sess.start >= lo) & (sess.start < hi)]
    right = len(bars) - 0.5
    for _, r in sess.iterrows():
        c = COLORS[r.session]
        x0, x1 = x_at(r.start), x_at(r.end + pd.Timedelta(minutes=1))
        ax.add_patch(Rectangle((x0, r.low), x1 - x0, r.high - r.low, color=c, alpha=0.12, lw=0, zorder=1))
        for level, swept in ((r.high, r.high_swept_at), (r.low, r.low_swept_at)):
            xe = x_at(swept) if pd.notna(swept) and swept < hi else right
            ax.hlines(level, x0, xe, color=c, lw=1.3, zorder=4)
        ax.text((x0 + x1) / 2, r.high, LABELS[r.session], color=c, ha="center", va="bottom", fontsize=10, zorder=5)
        if "vwap" in r and pd.notna(r.get("vwap")):  # instruments with volume
            ax.hlines(r.vwap, x0, x1, color=c, lw=1.1, ls="--", zorder=4)
            ax.hlines(r.poc, x0, x1, color="#212121", lw=1.4, ls=":", zorder=4)

    day = s.daily_levels(inst, df)
    for _, r in day[(pd.DatetimeIndex(day.trading_day) >= pd.Timestamp(start)) & (pd.DatetimeIndex(day.trading_day) <= pd.Timestamp(end))].iterrows():
        x0 = x_at(r.start)
        for level in (r.high, r.low):
            ax.hlines(level, x0, right, color="#9e9e9e", lw=0.8, ls=":", zorder=1)

    wk = s.weekly_levels(inst, df)
    for _, r in wk[(wk.end >= lo) & (wk.start < hi)].iterrows():  # weeks visible in the window
        x0 = max(x_at(r.start), -0.5)
        for level, wtag in ((r.high, "WH"), (r.low, "WL")):
            if bars.low.min() * 0.97 <= level <= bars.high.max() * 1.03:
                ax.hlines(level, x0, right, color="#6a1b9a", lw=1.0, ls="--", zorder=1)
                ax.text(right, level, f" {wtag} {r.week:%m-%d}", color="#6a1b9a", fontsize=7, va="center")

    if trades is not None and not trades.empty:
        tw = trades[(trades.entry_time >= lo) & (trades.entry_time < hi)]
        for _, t in tw.iterrows():
            x0, x1 = x_at(t.entry_time), x_at(min(t.exit_time, hi - pd.Timedelta(minutes=1)))
            col = "#1b5e20" if t.pnl_pts > 0 else "#b71c1c"
            ax.hlines(t.stop, x0, x1, color="#b71c1c", lw=1, ls="--", zorder=6)
            ax.hlines(t.target, x0, x1, color="#1b5e20", lw=1, ls="--", zorder=6)
            ax.plot([x0, x1], [t.entry, t.exit], color=col, lw=1.6, zorder=7)
            ax.scatter([x0], [t.entry], marker="^" if t.direction == "long" else "v", s=90, color="#212121", zorder=8)
            ax.scatter([x1], [t.exit], marker="x", s=60, color=col, zorder=8)
            ax.annotate(f"{t.strategy} {t.direction} {t.level}\n{t.exit_reason}: {t.r_multiple:+.2f}R",
                        (x0, t.entry), xytext=(6, 12 if t.direction == "short" else -24), textcoords="offset points",
                        fontsize=8, color="#212121", zorder=9)

    step = max(1, len(bars) // 12)
    ticks = list(range(0, len(bars), step))
    ax.set_xticks(ticks, [bars.bar_start[i].strftime("%a %H:%M") for i in ticks], fontsize=8)
    ax.set_xlim(-1, len(bars))
    pad = (bars.high.max() - bars.low.min()) * 0.05
    ax.set_ylim(bars.low.min() - pad, bars.high.max() + pad * 2)
    ax.grid(alpha=0.25)
    extra = " | in boxes: dashed = session VWAP, black dotted = POC" if inst.poc_bin else ""
    ax.set_title(f"{name} {tf}-min, {start} → {end} (New York time) | boxes = session high/low, lines run until taken out, dotted = day high/low, purple dashed = week high/low{extra}", fontsize=10)
    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}_{start}_{end}_{tf}m{tag}.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", nargs="+", default=list(s.INSTRUMENTS), choices=list(s.INSTRUMENTS))
    p.add_argument("--start", default="2025-12-09")
    p.add_argument("--end", default="2025-12-10")
    p.add_argument("--tf", type=int, default=15, help="candle minutes")
    p.add_argument("--trades", default=None, help="trades CSV from backtest_level_strategies.py to overlay")
    p.add_argument("--strategy", default=None, help="only overlay this strategy code, e.g. S1")
    args = p.parse_args()
    for name in args.only:
        trades, tag = None, ""
        if args.trades:
            trades = pd.read_csv(args.trades)
            for c in ("entry_time", "exit_time"):
                trades[c] = pd.to_datetime(trades[c], utc=True).dt.tz_convert(s.TZ)
            if args.strategy:
                trades = trades[trades.strategy == args.strategy]
                tag = f"_{args.strategy}"
        print("saved", plot(name, args.start, args.end, args.tf, trades, tag))


if __name__ == "__main__":
    main()
