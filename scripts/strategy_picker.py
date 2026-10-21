"""Strategy picker: the top NSX strategies, grouped into a long and a short book, combined into one algorithm.

    python scripts/strategy_picker.py

Last 18 months only (2025-04-10 -> 2026-10-09); results for the 18m / 12m / 6m windows are slices of one
continuous run, each P&L curve starting at 0 at the window start. Approved rules: US session 09:30-16:00 ET,
entries until 15:30, flat at 16:00, same-minute stop+target = stop, 1 NQ contract ($20/pt), 1 pt round trip,
no slippage.

Pool (priority order = rank by 18-month net P&L, from reports/top_equity_curves.png)
  short book   GF-1  gap-up, first 1m candle closes red -> short to previous close, stop open + 1 x gap
               ORB-A short leg (5-min OR, 5m close below, stop OR mid, 2R)
               S3    London-high pullback short (1m)
  long book    ORB-C 15-min OR, 3m close above, stop OR low, 1R, long only
               ORB-A long leg (5-min OR, 5m close above, stop OR mid, 2R)
               L2    PDH break long, hold to close (10m)
  GF-2 (GF-1 with a 0.25R stop) is the same signal as GF-1, so the picker takes GF-1's version only.

Picker rules (fixed before running, not tuned)
  1. One position at a time, 1 contract. A signal is taken only if no position is open at its entry minute.
  2. Signals at the same minute: the higher-priority strategy wins.
  3. Gate: each strategy keeps a shadow record of all its own signals (taken or not). A strategy may trade
     while it has fewer than WARMUP closed shadow trades; after that only if the sum of R over its last
     WARMUP closed shadow trades is > 0. Only trades closed before the new entry count (no look-ahead).
Writes reports/picker/.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"
OUT = REP / "picker"
TZ = "America/New_York"
START, END = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09")
WINDOWS = {"18m": pd.Timestamp("2025-04-10"), "12m": pd.Timestamp("2025-10-10"), "6m": pd.Timestamp("2026-04-10")}
DPP = 20.0
WARMUP = 20


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _ts(x) -> pd.Series:
    return pd.to_datetime(pd.Series(x), utc=True).dt.tz_convert(TZ).reset_index(drop=True)


def _frame(code: str, book: str, day, side, entry_time, exit_time, pnl_pts, r) -> pd.DataFrame:
    return pd.DataFrame({"strategy": code, "book": book, "day": pd.to_datetime(pd.Series(day)).reset_index(drop=True),
                         "side": np.asarray(side), "entry_time": _ts(entry_time), "exit_time": _ts(exit_time),
                         "pnl_usd": np.asarray(pnl_pts, float) * DPP, "r_multiple": np.asarray(r, float)})


def strategy_trades() -> pd.DataFrame:
    o = _load("o", "open_orb.py")
    g = _load("g", "open_gaps.py")
    days = o.load_days()
    # ORB: only the two selected configurations (globals read by classic())
    o.OR_MIN, o.TFS, o.STOPS, o.TARGETS, o.DIRECTIONS = [5, 15], [3, 5], ["opposite", "mid"], [1.0, 2.0], ["both", "long"]
    orb = o.classic(days)
    a = orb[(orb.or_min == 5) & (orb.tf == 5) & (orb.stop == "mid") & (orb.target == "2R") & (orb.direction == "both")]
    c = orb[(orb.or_min == 15) & (orb.tf == 3) & (orb.stop == "opposite") & (orb.target == "1R") & (orb.direction == "long")]
    lv = pd.read_csv(o.SRC / "daily_levels_NSX.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    g.TFS = [1]
    f = g.fade_trades(days, lv)
    gf1 = f[(f.entry == "confirm_1m") & (f.target == "full") & (f.stop == "gap") & (f.gap == "up")]
    lt = pd.read_csv(REP / "us_session_levels/strategies_v2/trades.csv")
    lt = lt[lt.instrument == "NSX"]
    s3 = lt[(lt.strategy == "S3") & (lt.tf == "1m")]
    l2 = lt[(lt.strategy == "L2") & (lt.tf == "10m")]
    al, ash = a[a.side == 1], a[a.side == -1]
    parts = [
        _frame("GF-1", "short", gf1.day, -1, gf1.entry_time, gf1.exit_time, gf1.pnl_pts, gf1.r_multiple),
        _frame("ORB-C", "long", c.day, 1, c.entry_time, c.exit_time, c.pnl_pts, c.r_multiple),
        _frame("ORB-A long", "long", al.day, 1, al.entry_time, al.exit_time, al.pnl_pts, al.r_multiple),
        _frame("ORB-A short", "short", ash.day, -1, ash.entry_time, ash.exit_time, ash.pnl_pts, ash.r_multiple),
        _frame("S3", "short", s3.day, -1, s3.entry_time, s3.exit_time, s3.pnl_pts, s3.r_multiple),
        _frame("L2", "long", l2.day, 1, l2.entry_time, l2.exit_time, l2.pnl_pts, l2.r_multiple),
    ]
    t = pd.concat(parts, ignore_index=True)
    return t[(t.day >= START) & (t.day <= END)].reset_index(drop=True)


PRIORITY = ["GF-1", "ORB-C", "ORB-A long", "ORB-A short", "S3", "L2"]
REVISED = "Revised picker (no ORB-A, no gate)"


def pick(trades: pd.DataFrame, gate: bool = True, warmup: int = WARMUP) -> pd.DataFrame:
    """Run the picker over all candidate trades; returns every candidate with taken / skip_reason."""
    t = trades.assign(prio=trades.strategy.map(PRIORITY.index)).sort_values(["entry_time", "prio"], kind="stable").reset_index(drop=True)
    shadow = {s: g.sort_values("exit_time")[["exit_time", "r_multiple"]] for s, g in t.groupby("strategy")}
    free_at = None
    taken, reason = np.zeros(len(t), bool), np.full(len(t), "", object)
    for i, r in t.iterrows():
        if free_at is not None and r.entry_time < free_at:
            reason[i] = "position open"
            continue
        if gate:
            h = shadow[r.strategy]
            closed = h.r_multiple[h.exit_time.values <= r.entry_time.to_datetime64()]
            if len(closed) >= warmup and closed.iloc[-warmup:].sum() <= 0:
                reason[i] = "gated off"
                continue
        taken[i] = True
        free_at = r.exit_time
    return t.assign(taken=taken, skip_reason=reason).drop(columns="prio")


def metrics(g: pd.DataFrame) -> dict:
    p = g.pnl_usd.to_numpy()
    if len(p) == 0:
        return dict(trades=0)
    eq = np.cumsum(p)
    wins, losses = p[p > 0], p[p <= 0]
    r = g.r_multiple.to_numpy()
    return dict(trades=len(p), long_trades=int((g.side == 1).sum()), short_trades=int((g.side == -1).sum()),
                win_rate=len(wins) / len(p), profit_factor=wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
                net_pnl=p.sum(), max_drawdown=(eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min(),
                avg_minutes=((g.exit_time - g.entry_time).dt.total_seconds() / 60).mean(),
                t_stat=r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 else np.nan,
                trading_days=g.day.nunique())


def curve(g: pd.DataFrame, start: pd.Timestamp) -> pd.Series:
    s = g.sort_values("exit_time").groupby("day").pnl_usd.sum().cumsum()
    return pd.concat([pd.Series([0.0], index=[start - pd.Timedelta(days=1)]), s])


def plot(variants: dict[str, pd.DataFrame]) -> None:
    rev = variants[REVISED]
    lines = [("Revised picker (no ORB-A, no gate)", rev, "#2a78d6", 2.4, "-"),
             ("  its long book", rev[rev.book == "long"], "#1baf7a", 1.4, "-"),
             ("  its short book", rev[rev.book == "short"], "#eb6834", 1.4, "-"),
             ("Current picker (gated)", variants["Picker (gated)"], "#8a8984", 1.6, "-"),
             ("Current picker without gate", variants["Picker without gate"], "#8a8984", 1.2, (0, (4, 3)))]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.6), dpi=150, sharey=True)
    fig.patch.set_facecolor("#fcfcfb")
    for ax, (w, start) in zip(axes, WINDOWS.items()):
        ax.set_facecolor("#fcfcfb")
        for label, g, color, lw, ls in lines:
            x = g[g.day >= start]
            eq = curve(x, start)
            ax.step(eq.index, eq.values / 1000, where="post", color=color, lw=lw, ls=ls, label=label)
            ax.plot(eq.index[-1], eq.iloc[-1] / 1000, "o", ms=5, color=color, mec="#fcfcfb", mew=1.5)
        m = metrics(rev[rev.day >= start])
        m0 = metrics(variants["Picker (gated)"][variants["Picker (gated)"].day >= start])
        ax.set_title(f"{w}: {start:%Y-%m-%d} → {END:%Y-%m-%d}", loc="left", fontsize=11, color="#1f1f1d")
        ax.text(0.02, 0.97, f"Revised: net \\${m['net_pnl'] / 1000:,.1f}K · PF {m['profit_factor']:.2f} · {m['trades']} trades · "
                f"{m['win_rate'] * 100:.0f}% win · max DD \\${m['max_drawdown'] / 1000:,.1f}K\n"
                f"Current: net \\${m0['net_pnl'] / 1000:,.1f}K · PF {m0['profit_factor']:.2f} · max DD \\${m0['max_drawdown'] / 1000:,.1f}K",
                transform=ax.transAxes, va="top", fontsize=9, color="#3d3d3a")
        ax.axhline(0, color="#8a8984", lw=0.8)
        ax.grid(axis="y", color="#e6e5e0", lw=0.8); ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color("#c3c2b7")
        ax.tick_params(colors="#5f5e5a", labelsize=8)
        ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%b %y"))
    axes[0].set_ylabel("Cumulative net P&L ($K, 1 NQ contract)", color="#3d3d3a", fontsize=9)
    axes[0].legend(loc="lower right", frameon=False, fontsize=8.5, labelcolor="#3d3d3a")
    fig.suptitle("NSX strategy picker: revised (no ORB-A, no gate) vs current  ·  one position at a time  ·  "
                 "\\$20/pt, 1 pt cost, no slippage", x=0.01, ha="left", fontsize=12, color="#1f1f1d")
    fig.tight_layout()
    fig.savefig(OUT / "picker_curves.png", facecolor=fig.get_facecolor())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cand = strategy_trades()
    revised = cand[~cand.strategy.str.startswith("ORB-A")]
    picked = {"Picker (gated)": pick(cand, True), "Picker without gate": pick(cand, False),
              REVISED: pick(revised, False)}
    picked["Picker (gated)"].to_csv(OUT / "candidates.csv", index=False)
    picked[REVISED].to_csv(OUT / "candidates_revised.csv", index=False)
    variants = {name: p[p.taken] for name, p in picked.items()}
    variants["Picker (gated)"].to_csv(OUT / "trades.csv", index=False)
    variants[REVISED].to_csv(OUT / "trades_revised.csv", index=False)
    rows, contrib = [], []
    for w, start in WINDOWS.items():
        for name, t in variants.items():
            rows.append(dict(window=w, period=f"{start:%Y-%m-%d} → {END:%Y-%m-%d}", system=name, **metrics(t[t.day >= start])))
            if name == "Picker without gate":
                continue
            for book in ("long", "short"):
                rows.append(dict(window=w, period=f"{start:%Y-%m-%d} → {END:%Y-%m-%d}", system=f"  {book} book ({name})",
                                 **metrics(t[(t.day >= start) & (t.book == book)])))
        for system in ("Picker (gated)", REVISED):
            allc = picked[system]
            allc = allc[allc.day >= start]
            for s in PRIORITY:
                c = allc[allc.strategy == s]
                if c.empty:
                    continue
                m = metrics(c[c.taken])
                contrib.append(dict(window=w, system=system, strategy=s, book=c.book.iloc[0], signals=len(c), taken=int(c.taken.sum()),
                                    skipped_position_open=int((c.skip_reason == "position open").sum()),
                                    skipped_gated=int((c.skip_reason == "gated off").sum()),
                                    standalone_net=c.pnl_usd.sum(), net_in_picker=m.get("net_pnl", 0.0),
                                    pf_in_picker=m.get("profit_factor", np.nan)))
    res, con = pd.DataFrame(rows), pd.DataFrame(contrib)
    res.to_csv(OUT / "results.csv", index=False)
    con.to_csv(OUT / "contribution.csv", index=False)
    plot(variants)
    pd.set_option("display.width", 250)
    print(res.round(3).to_string(index=False))
    print(con.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
