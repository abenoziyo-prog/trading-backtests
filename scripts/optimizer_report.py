"""Backtest tables, P&L curves and the VIX / DXY / time filter study for the finalized picker systems.

    python scripts/optimizer_report.py    # needs reports/optimizer/library.parquet and greedy_steps.csv

Systems (all: one position at a time, 1 NQ contract, $20/pt, 1 pt round trip, no slippage; last 18 months)
  R  revised picker: GF-1 (1m), ORB-C (3m), S3 (1m), L2 (10m)          (reports/picker)
  A  optimizer, selected on the first 12 months (out-of-sample check on the last 6)
  B  optimizer, selected on all 18 months (in-sample maximum)

Filter study (no look-ahead; every value is known at the entry minute)
  vix_open      VIX official opening value of the day (Cboe daily open, 09:30)
  vix_change    VIX open vs previous VIX close (%)
  dxy_overnight DXY 09:29 vs previous 16:00 (%)
  dxy_intraday  DXY at entry vs 09:30 (%)
  entry_hour    entry time bucket
  weekday       day of week
  gap_R         |US open - previous close| / previous US range
  range_pct     previous US range as % of price
Buckets come from the first 12 months (terciles or fixed bands); a filter that skips a bucket is chosen on
the first 12 months and checked on the last 6, re-running the picker so freed slots can be used.
Writes reports/optimizer/.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "optimizer"
DATA = ROOT / "data" / "free"
TZ = "America/New_York"
START, END = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09")
TRAIN_END, TEST_START = pd.Timestamp("2026-04-09"), pd.Timestamp("2026-04-10")
PERIODS = {"18m": (START, END), "12m": (pd.Timestamp("2025-10-10"), END), "6m": (TEST_START, END),
           "first 12m (selection)": (START, TRAIN_END), "last 6m (unseen by A)": (TEST_START, END)}
R_SYSTEM = ["gapfade|confirm|full|gap|up|1m", "orb|OR15|opposite|1R|long|all|3m", "level|S3|1m", "level|L2|10m"]
NAMED = {  # strategy name -> library candidate id without the candle size
    "L1 Asia-low reclaim long": "level|L1", "L2 PDH break long (hold to close)": "level|L2", "L3 London-high magnet long": "level|L3",
    "S1 PDL breakdown short": "level|S1", "S2 PDL break short (hold to close)": "level|S2", "S3 London-high pullback short": "level|S3",
    "G1 gap-up fade short (PDH)": "gapday|G1", "G2 gap-up continuation long": "gapday|G2", "G3 gap-down continuation short": "gapday|G3",
    "GF-1 gap-up fade to PDC, stop 1x gap": "gapfade|confirm|full|gap|up", "GF-2 gap-up fade to PDC, stop 0.25R": "gapfade|confirm|full|R25|up",
    "GF-3 gap-up fade to PDC, stop first candle": "gapfade|confirm|full|candle|up",
    "ORB-A 5-min OR both, mid stop, 2R": "orb|OR5|mid|2R|both|all", "ORB-B 15-min OR both, mid stop, 3R": "orb|OR15|mid|3R|both|all",
    "ORB-C 15-min OR long, opposite stop, 1R": "orb|OR15|opposite|1R|long|all",
    "ORB paper (first candle), 10R": "orbpaper|10R", "ORB paper (first candle), hold to close": "orbpaper|eod",
}
TF_LIST = [1, 2, 3, 5, 10]


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


op = _load("op", "optimize_picker.py")


def picker(lib: pd.DataFrame, chosen: list[str]) -> pd.DataFrame:
    """Taken trades of the picker over `lib` (chosen order = priority)."""
    sub = lib[lib.cand.isin(chosen)]
    chosen = [k for k in chosen if k in set(sub.cand)]
    if not chosen:
        return sub.iloc[:0]
    arrs = op.Arrays(sub)
    taken = op.run_picker(arrs, chosen)
    parts = [sub[sub.cand == k].sort_values("entry_time", kind="stable") for k in chosen]
    return pd.concat(parts)[taken]  # keeps the library row index (features are looked up by it)


def metrics(t: pd.DataFrame, n_days: int) -> dict:
    t = t.sort_values("exit_time")
    p = t.pnl_usd.to_numpy()
    if len(p) == 0:
        return dict(trades=0)
    eq = np.cumsum(p)
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    dd = eq - peak
    # drawdown duration in calendar days: longest stretch below a previous peak
    under, longest, start = dd < 0, 0, None
    ex = t.exit_time.to_numpy()
    for i, u in enumerate(under):
        if u and start is None:
            start = ex[i - 1] if i else ex[i]
        if not u and start is not None:
            longest = max(longest, (ex[i] - start) / np.timedelta64(1, "D")); start = None
    if start is not None:
        longest = max(longest, (ex[-1] - start) / np.timedelta64(1, "D"))
    wins, losses = p[p > 0], p[p <= 0]
    daily = t.groupby("day").pnl_usd.sum()
    streak = cur = 0
    for x in p:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    r = t.r_multiple.to_numpy()
    full_daily = daily.reindex(range(n_days), fill_value=0) if False else np.r_[daily.to_numpy(), np.zeros(max(0, n_days - len(daily)))]
    return dict(trades=len(p), trades_per_day=len(p) / n_days, days_traded_pct=t.day.nunique() / n_days,
                long_trades=int((t.side == 1).sum()), short_trades=int((t.side == -1).sum()),
                win_rate=len(wins) / len(p), profit_factor=wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
                net_pnl=p.sum(), gross_profit=wins.sum(), gross_loss=losses.sum(), avg_trade=p.mean(),
                avg_win=wins.mean() if len(wins) else np.nan, avg_loss=losses.mean() if len(losses) else np.nan,
                payoff_ratio=(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else np.nan,
                avg_r=r.mean(), largest_win=p.max(), largest_loss=p.min(), max_drawdown=dd.min(), max_dd_days=longest,
                net_to_max_dd=p.sum() / -dd.min() if dd.min() < 0 else np.inf,
                sharpe_daily_ann=full_daily.mean() / full_daily.std(ddof=1) * np.sqrt(252) if full_daily.std(ddof=1) > 0 else np.nan,
                t_stat=r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 else np.nan,
                max_consec_losses=streak, best_day=daily.max(), worst_day=daily.min(), profitable_days_pct=(daily > 0).mean(),
                avg_minutes=((t.exit_time - t.entry_time).dt.total_seconds() / 60).mean())


def day_counts(lib: pd.DataFrame) -> dict:
    days = pd.Series(np.sort(lib.day.unique()))
    return {k: int(((days >= a) & (days <= b)).sum()) for k, (a, b) in PERIODS.items()}


# ---------------------------------------------------------------- features
def features(lib: pd.DataFrame) -> pd.DataFrame:
    vix = pd.read_parquet(DATA / "VIX_daily_cboe.parquet")
    vix.index = pd.to_datetime(vix.index).normalize()
    vix["prev_close"] = vix.close.shift(1)
    lv = pd.read_csv(ROOT / "reports/us_session_levels/daily_levels_NSX.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    lr = _load("lr", "us_level_reactions_tf.py")
    rth = lr.rth_minutes("NSX")
    us_open = rth.open.groupby(rth.index.tz_localize(None).normalize()).first()
    dxy = pd.read_parquet(DATA / "DXY_1min_rebuilt.parquet", columns=["close"])
    dxy = dxy[(dxy.index >= pd.Timestamp("2025-04-01", tz=TZ)) & (dxy.index < pd.Timestamp("2026-10-10", tz=TZ))].close
    dxy = dxy.tz_convert(TZ)

    def asof(ts: pd.Series) -> np.ndarray:
        idx = dxy.index.searchsorted(pd.DatetimeIndex(ts), side="right") - 1
        out = dxy.to_numpy()[np.clip(idx, 0, None)]
        out[idx < 0] = np.nan
        return out

    days = pd.DatetimeIndex(np.sort(lib.day.unique()))
    d = pd.DataFrame(index=days)
    d["vix_open"] = vix.open.reindex(days).to_numpy()
    d["vix_change"] = (vix.open / vix.prev_close - 1).reindex(days).to_numpy() * 100
    d["gap_R"] = ((us_open.reindex(days) - lv.PDC.reindex(days)).abs() / (lv.PDH - lv.PDL).reindex(days)).to_numpy()
    d["range_pct"] = ((lv.PDH - lv.PDL) / lv.PDC).reindex(days).to_numpy() * 100
    loc = days.tz_localize(TZ)
    prev = pd.Series(days).shift(1)
    d["dxy_0929"] = asof(pd.Series(loc + pd.Timedelta(hours=9, minutes=29)))
    d["dxy_prev_close"] = asof(pd.Series(prev.dt.tz_localize(TZ) + pd.Timedelta(hours=16)))
    d["dxy_overnight"] = (d.dxy_0929 / d.dxy_prev_close - 1) * 100
    f = lib.join(d[["vix_open", "vix_change", "gap_R", "range_pct", "dxy_overnight", "dxy_0929"]], on="day")
    f["dxy_intraday"] = (asof(f.entry_time) / f.dxy_0929 - 1) * 100
    mins = f.entry_time.dt.hour * 60 + f.entry_time.dt.minute
    f["entry_hour"] = pd.cut(mins, [570, 600, 660, 720, 840, 931], right=False,
                             labels=["09:30-10:00", "10:00-11:00", "11:00-12:00", "12:00-14:00", "14:00-15:30"]).astype(str)
    f["weekday"] = f.day.dt.day_name().str[:3]
    return f.drop(columns="dxy_0929")


FEATURES = ["vix_open", "vix_change", "dxy_overnight", "dxy_intraday", "entry_hour", "weekday", "gap_R", "range_pct"]
FIXED_BANDS = {"vix_open": [0, 15, 18, 22, 100]}


def bucketize(f: pd.DataFrame, feat: str, train_mask: pd.Series) -> pd.Series:
    if not pd.api.types.is_numeric_dtype(f[feat]):
        return f[feat]
    edges = FIXED_BANDS.get(feat) or list(np.unique(np.r_[-np.inf, f.loc[train_mask, feat].quantile([1 / 3, 2 / 3]).to_numpy(), np.inf]))
    lab = [f"{a:.2f} to {b:.2f}" if np.isfinite(a) and np.isfinite(b) else (f"< {b:.2f}" if np.isfinite(b) else f">= {a:.2f}")
           for a, b in zip(edges[:-1], edges[1:])]
    return pd.cut(f[feat], edges, labels=lab, right=False).astype(str)


def filter_study(lib: pd.DataFrame, systems: dict[str, list[str]], nd: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    f = features(lib[lib.cand.isin(set(sum(systems.values(), [])))])
    train_mask = f.day <= TRAIN_END
    buckets, effects = [], []
    for sname, chosen in systems.items():
        base = picker(f, chosen)
        base_tr, base_te = base[base.day <= TRAIN_END].pnl_usd.sum(), base[base.day >= TEST_START].pnl_usd.sum()
        for feat in FEATURES:
            b_all = bucketize(f, feat, train_mask)
            taken_b = b_all.loc[base.index]
            for b in sorted(taken_b.unique()):
                x = base[taken_b == b]
                row = dict(system=sname, feature=feat, bucket=b)
                for per, lo, hi in (("first 12m", START, TRAIN_END), ("last 6m", TEST_START, END)):
                    y = x[(x.day >= lo) & (x.day <= hi)].pnl_usd
                    row.update({f"{per} trades": len(y), f"{per} net": y.sum(),
                                f"{per} PF": y[y > 0].sum() / -y[y <= 0].sum() if (y <= 0).any() and y[y <= 0].sum() < 0 else np.nan})
                buckets.append(row)
                # filter: skip this bucket for every strategy in the system, re-run the picker
                kept = f[b_all != b]
                filt = picker(kept, chosen)
                tr, te = filt[filt.day <= TRAIN_END], filt[filt.day >= TEST_START]
                effects.append(dict(system=sname, filter=f"skip {feat} = {b}",
                                    train_net_change=tr.pnl_usd.sum() - base_tr, test_net_change=te.pnl_usd.sum() - base_te,
                                    train_trades_per_day=len(tr) / nd["first 12m (selection)"], test_trades_per_day=len(te) / nd["last 6m (unseen by A)"],
                                    chosen_on_train=row["first 12m net"] < 0))
    return pd.DataFrame(buckets), pd.DataFrame(effects)


# ---------------------------------------------------------------- output
def plot_curves(curves: dict[str, pd.DataFrame], path: Path) -> None:
    colors = {"R": "#2a78d6", "A": "#eb6834", "B": "#1baf7a"}
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.6), dpi=150, sharey=True)
    fig.patch.set_facecolor("#fcfcfb")
    for ax, w in zip(axes, ("18m", "12m", "6m")):
        lo, hi = PERIODS[w]
        ax.set_facecolor("#fcfcfb")
        txt = []
        for name, t in curves.items():
            x = t[(t.day >= lo) & (t.day <= hi)].sort_values("exit_time")
            eq = pd.concat([pd.Series([0.0], index=[lo - pd.Timedelta(days=1)]), x.groupby("day").pnl_usd.sum().cumsum()])
            c = colors[name[0]]
            ax.step(eq.index, eq.values / 1000, where="post", color=c, lw=2.2 if name[0] == "R" else 1.6, label=name)
            ax.plot(eq.index[-1], eq.iloc[-1] / 1000, "o", ms=5, color=c, mec="#fcfcfb", mew=1.5)
            txt.append(f"{name[0]}: \\${eq.iloc[-1] / 1000:,.1f}K")
        ax.set_title(f"{w}: {lo:%Y-%m-%d} → {hi:%Y-%m-%d}", loc="left", fontsize=11, color="#1f1f1d")
        ax.text(0.02, 0.97, "   ".join(txt), transform=ax.transAxes, va="top", fontsize=9, color="#3d3d3a")
        ax.axhline(0, color="#8a8984", lw=0.8)
        ax.grid(axis="y", color="#e6e5e0", lw=0.8); ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color("#c3c2b7")
        ax.tick_params(colors="#5f5e5a", labelsize=8)
        ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%b %y"))
    axes[0].axvline(TEST_START, color="#8a8984", lw=1, ls=(0, (4, 3)))
    axes[0].set_ylabel("Cumulative net P&L ($K, 1 NQ contract)", color="#3d3d3a", fontsize=9)
    axes[0].legend(loc="lower right", frameon=False, fontsize=8.5, labelcolor="#3d3d3a")
    fig.suptitle("NSX picker systems  ·  one position at a time  ·  \\$20/pt, 1 pt cost, no slippage  ·  "
                 "dashed line = start of the last 6 months (unseen by A)", x=0.01, ha="left", fontsize=12, color="#1f1f1d")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())


def main() -> None:
    lib = pd.read_parquet(OUT / "library.parquet")
    nd = day_counts(lib)
    steps = pd.read_csv(OUT / "greedy_steps.csv")
    systems = {"R: revised picker (4 strategies)": R_SYSTEM,
               "A: optimizer, selected on first 12m": steps[steps.selection.str.startswith("A") & steps.chosen].added.tolist(),
               "B: optimizer, selected on all 18m": steps[steps.selection.str.startswith("B") & steps.chosen].added.tolist()}
    trades = {k: picker(lib, v) for k, v in systems.items()}
    rows = [dict(system=k, period=p, **metrics(t[(t.day >= a) & (t.day <= b)], nd[p]))
            for k, t in trades.items() for p, (a, b) in PERIODS.items()]
    pd.DataFrame(rows).to_csv(OUT / "systems_backtest.csv", index=False)
    comp = []
    for k, t in trades.items():
        for i, c in enumerate(systems[k]):
            for p in ("18m", "12m", "6m"):
                a, b = PERIODS[p]
                x = t[(t.cand == c) & (t.day >= a) & (t.day <= b)]
                comp.append(dict(system=k, priority=i + 1, strategy=c, period=p, **metrics(x, nd[p])))
    pd.DataFrame(comp).to_csv(OUT / "systems_components.csv", index=False)
    for k, t in trades.items():
        t.to_csv(OUT / f"trades_{k[0]}.csv", index=False)
    # every named strategy on 1/2/3/5/10m, standalone
    tfrows = []
    for name, base in NAMED.items():
        for tf in TF_LIST:
            x = lib[lib.cand == f"{base}|{tf}m"]
            for p in ("18m", "12m", "6m", "first 12m (selection)", "last 6m (unseen by A)"):
                a, b = PERIODS[p]
                tfrows.append(dict(strategy=name, tf=f"{tf}m", period=p, **metrics(x[(x.day >= a) & (x.day <= b)], nd[p])))
    pd.DataFrame(tfrows).to_csv(OUT / "strategies_by_tf.csv", index=False)
    plot_curves({"R: revised picker": trades[next(iter(trades))], "A: optimizer (first 12m)": trades[list(trades)[1]],
                 "B: optimizer (all 18m)": trades[list(trades)[2]]}, OUT / "systems_curves.png")
    bk, ef = filter_study(lib, {"R": systems[list(systems)[0]], "B": systems[list(systems)[2]]}, nd)
    bk.to_csv(OUT / "filter_buckets.csv", index=False)
    ef.to_csv(OUT / "filter_effects.csv", index=False)
    pd.set_option("display.width", 260)
    print(pd.DataFrame(rows)[["system", "period", "trades", "trades_per_day", "days_traded_pct", "win_rate", "profit_factor", "net_pnl",
                              "max_drawdown", "sharpe_daily_ann", "t_stat"]].round(2).to_string(index=False))
    print(ef[ef.chosen_on_train].round(0).to_string(index=False))


if __name__ == "__main__":
    main()
