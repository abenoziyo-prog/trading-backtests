"""Multi-variable optimization of the London strategies on the last 6 months (NSX), checked on the prior 12.

    python scripts/london_model.py          # needs reports/london/trades.parquet (london_research.py)

1. Base: per family, the parameter set x candle size with the highest net P&L in the last 6 months
   (2026-04-10 -> 2026-10-09), with >= MIN_TRADES trades and profit factor > 1.
2. Equation: ridge regression of each trade's R-multiple on the 15 standardized entry features
   (london_research.FEATURES; directional ones are signed by the trade side):
       score = b0 + sum_i w_i * (x_i - mean_i) / sd_i
   fitted on the last 6 months only; a trade is taken when score > theta, theta chosen on the last 6 months
   to maximize net P&L while keeping >= MIN_TRADES trades.
3. Check: the same base settings, weights, means/sds and theta applied unchanged to the prior 12 months
   (2025-04-10 -> 2026-04-09), which the optimization never saw.
4. London system: the filtered strategies combined, one position at a time (priority = 6-month net P&L).
Writes reports/london/.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "london"
_spec = importlib.util.spec_from_file_location("lr", ROOT / "scripts" / "london_research.py")
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)
FEATURES, OPT_START, START, END = lr.FEATURES, lr.OPT_START, lr.START, lr.END
CHECK_END = OPT_START - pd.Timedelta(days=1)
MIN_TRADES = 25
RIDGE = 10.0


def fit_ridge(X: np.ndarray, y: np.ndarray, lam: float) -> tuple[float, np.ndarray]:
    Xc = np.c_[np.ones(len(X)), X]
    P = lam * np.eye(Xc.shape[1])
    P[0, 0] = 0  # no penalty on the intercept
    beta = np.linalg.solve(Xc.T @ Xc + P, Xc.T @ y)
    return beta[0], beta[1:]


def standardize(df: pd.DataFrame, mu: pd.Series, sd: pd.Series) -> np.ndarray:
    return ((df[FEATURES] - mu) / sd).fillna(0.0).to_numpy()


def picker(t: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    t = t.assign(prio=t.cand.map({c: i for i, c in enumerate(order)})).sort_values(["entry_time", "prio"])
    free, keep = None, []
    for i, r in zip(t.index, t.itertuples()):
        if free is None or r.entry_time >= free:
            keep.append(i)
            free = r.exit_time
    return t.loc[keep]


def main() -> None:
    t = pd.read_parquet(OUT / "trades.parquet")
    opt, chk = t[t.day >= OPT_START], t[t.day <= CHECK_END]
    m_opt = opt.groupby("cand").pnl_usd.agg(trades="size", net="sum",
                                            pf=lambda p: p[p > 0].sum() / -p[p <= 0].sum() if (p <= 0).any() else np.inf)
    m_opt["family"] = m_opt.index.str.split("|").str[0]
    ok = m_opt[(m_opt.trades >= MIN_TRADES) & (m_opt.pf > 1)].sort_values("net", ascending=False)
    rows, models, sel_opt, sel_chk, top = [], [], [], [], []
    for fam in ["DRIFT", "ASIA_BO", "ASIA_SWP", "LON_OR", "PD_SWP"]:
        f = ok[ok.family == fam]
        top.append(f.head(5).assign(rank=range(1, min(5, len(f)) + 1)))
        if f.empty:
            continue
        cand = f.index[0]
        a, b = opt[opt.cand == cand], chk[chk.cand == cand]
        mu, sd = a[FEATURES].mean(), a[FEATURES].std(ddof=0).replace(0, np.nan)
        b0, w = fit_ridge(standardize(a, mu, sd), a.r_multiple.to_numpy(), RIDGE)
        sa, sb = b0 + standardize(a, mu, sd) @ w, b0 + standardize(b, mu, sd) @ w
        best = (-np.inf, None)
        for th in np.unique(np.quantile(sa, np.linspace(0, 0.8, 41))):
            k = sa > th - 1e-12
            if k.sum() >= MIN_TRADES and a.pnl_usd[k].sum() > best[0]:
                best = (a.pnl_usd[k].sum(), th)
        th = best[1]
        a_f, b_f = a[sa > th - 1e-12], b[sb > th - 1e-12]
        models.append(dict(family=fam, cand=cand, b0=b0, theta=th, **{f"w_{x}": wi for x, wi in zip(FEATURES, w)},
                           **{f"mu_{x}": mu[x] for x in FEATURES}, **{f"sd_{x}": sd[x] for x in FEATURES}))
        for label, x in (("base", a), ("base", b), ("base + equation", a_f), ("base + equation", b_f)):
            per = "opt: last 6m" if x.day.min() >= OPT_START else "check: prior 12m"
            rows.append(dict(family=fam, cand=cand, version=label, period=per, **lr.metrics(x)))
        sel_opt.append(a_f.assign(cand=cand))
        sel_chk.append(b_f.assign(cand=cand))
    res = pd.DataFrame(rows)
    order = list(m_opt.loc[[m["cand"] for m in models]].sort_values("net", ascending=False).index)
    sys_opt, sys_chk = picker(pd.concat(sel_opt), order), picker(pd.concat(sel_chk), order)
    for per, x in (("opt: last 6m", sys_opt), ("check: prior 12m", sys_chk)):
        rows.append(dict(family="LONDON SYSTEM", cand=" + ".join(c.split("|")[0] for c in order), version="base + equation",
                         period=per, **lr.metrics(x)))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "optimized.csv", index=False)
    pd.DataFrame(models).to_csv(OUT / "equations.csv", index=False)
    pd.concat(top).to_csv(OUT / "top_by_family_6m.csv")
    pd.concat([sys_chk, sys_opt]).to_csv(OUT / "system_trades.csv", index=False)
    # candle-size table for each family's best parameter set
    tfrows = []
    for m in models:
        base = m["cand"].rsplit("|", 1)[0]
        for tf in lr.TFS + [0]:
            x = t[t.cand == f"{base}|{tf}m"]
            if x.empty:
                continue
            for per, (a_, b_) in lr.PERIODS.items():
                tfrows.append(dict(family=m["family"], base=base, tf=f"{tf}m" if tf else "time-based", period=per,
                                   **lr.metrics(x[(x.day >= a_) & (x.day <= b_)])))
    pd.DataFrame(tfrows).to_csv(OUT / "best_by_tf.csv", index=False)
    plot(sel_opt, sel_chk, sys_opt, sys_chk, models)
    pd.set_option("display.width", 250)
    print(res[["family", "version", "period", "trades", "win_rate", "profit_factor", "net_pnl", "max_drawdown", "t_stat"]].round(2).to_string(index=False))
    for m in models:
        ws = sorted(((x, m[f"w_{x}"]) for x in FEATURES), key=lambda z: -abs(z[1]))[:6]
        print(m["family"], m["cand"], "theta", round(m["theta"], 3), [(x, round(v, 3)) for x, v in ws])


def plot(sel_opt, sel_chk, sys_opt, sys_chk, models) -> None:
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
    fig, axes = plt.subplots(1, 2, figsize=(16, 5.6), dpi=150, sharey=True)
    fig.patch.set_facecolor("#fcfcfb")
    for ax, sets, sysx, title, lo in ((axes[0], sel_chk, sys_chk, "Check: prior 12m (never seen by the optimizer)", START),
                                      (axes[1], sel_opt, sys_opt, "Optimized: last 6m (in-sample)", OPT_START)):
        ax.set_facecolor("#fcfcfb")
        for i, (x, m) in enumerate(zip(sets, models)):
            eq = pd.concat([pd.Series([0.0], index=[lo - pd.Timedelta(days=1)]), x.groupby("day").pnl_usd.sum().cumsum()])
            ax.step(eq.index, eq.values / 1000, where="post", color=colors[i], lw=1.4, label=m["family"])
        eq = pd.concat([pd.Series([0.0], index=[lo - pd.Timedelta(days=1)]), sysx.groupby("day").pnl_usd.sum().cumsum()])
        ax.step(eq.index, eq.values / 1000, where="post", color="#1f1f1d", lw=2.4, label="London system (combined)")
        ax.set_title(f"{title}: system {'+' if eq.iloc[-1] >= 0 else '−'}\\${abs(eq.iloc[-1]) / 1000:,.1f}K", loc="left", fontsize=11, color="#1f1f1d")
        ax.axhline(0, color="#8a8984", lw=0.8)
        ax.grid(axis="y", color="#e6e5e0", lw=0.8); ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color("#c3c2b7")
        ax.tick_params(colors="#5f5e5a", labelsize=8)
        ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%b %y"))
    axes[0].set_ylabel("Cumulative net P&L ($K, 1 NQ contract)", color="#3d3d3a", fontsize=9)
    axes[0].legend(loc="upper left", frameon=False, fontsize=8.5, labelcolor="#3d3d3a")
    fig.suptitle("NSX London strategies, base settings + multi-variable equation fitted on the last 6 months  ·  "
                 "\\$20/pt, 1 pt cost, no slippage", x=0.01, ha="left", fontsize=12, color="#1f1f1d")
    fig.tight_layout()
    fig.savefig(OUT / "london_curves.png", facecolor=fig.get_facecolor())


if __name__ == "__main__":
    main()
