"""Select settings on the train year and judge them on the unseen 12 / 8 / 6 months (protocol:
reports/deep_research_2026-10-28/PROTOCOL.md). Run scripts/deep_research.py first.

    python scripts/deep_research_report.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from lab import baselines  # noqa: E402
from lab.data import Context  # noqa: E402
from lab.gates import picker  # noqa: E402

OUT = ROOT / "reports" / "deep_research_2026-10-28"
WIN = {"train": ("2024-10-10", "2025-10-09"), "12m": ("2025-10-10", "2026-10-09"), "8m": ("2026-02-10", "2026-10-09"),
       "6m": ("2026-04-10", "2026-10-09")}
KEY = ["family", "param", "tf", "dir", "stop", "target", "filter"]
TFS, STOPS, TARGETS = [1, 2, 3, 5, 10], ["sig", "0.1", "0.2", "0.3"], [1.0, 2.0, 3.0, -1.0]
FINAL_T = 3.0


def agg(t: pd.DataFrame) -> pd.DataFrame:
    g = t.assign(win=t.pnl_usd.clip(lower=0), loss=(-t.pnl_usd).clip(lower=0)).groupby(KEY)
    a = g.agg(n=("pnl_usd", "size"), net=("pnl_usd", "sum"), mean=("pnl_usd", "mean"), sd=("pnl_usd", "std"),
              gw=("win", "sum"), gl=("loss", "sum"))
    a["pf"] = np.where(a.gl > 0, a.gw / a.gl.where(a.gl > 0, 1), np.inf)
    a["t"] = a["mean"] / (a.sd / np.sqrt(a.n))
    return a[["n", "net", "mean", "pf", "t"]]


def table(trades: pd.DataFrame, ctx: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for f in ctx.columns:
        days = ctx.index[ctx[f].astype(bool)]
        sub = trades[trades.day.isin(days)].assign(filter=f)
        cols = {}
        for w, (a, b) in WIN.items():
            cols[w] = agg(sub[(sub.day >= a) & (sub.day <= b)]).add_prefix(f"{w}_")
        parts.append(pd.concat(cols.values(), axis=1))
    out = pd.concat(parts)
    for w in WIN:
        out[f"{w}_n"] = out[f"{w}_n"].fillna(0).astype(int)
    return out


def robustness(res: pd.DataFrame) -> pd.Series:
    """Share of neighbouring settings (candle size +-1 step, stop +-1, target +-1) with train PF > 1."""
    r = res.reset_index()
    r["tf_i"], r["st_i"], r["tg_i"] = r.tf.map(TFS.index), r["stop"].map(STOPS.index), r.target.map(TARGETS.index)
    base = ["family", "param", "dir", "filter"]
    look = r.set_index(base + ["tf_i", "st_i", "tg_i"]).train_pf
    ok, tot = np.zeros(len(r)), np.zeros(len(r))
    for col, lim in (("tf_i", len(TFS)), ("st_i", len(STOPS)), ("tg_i", len(TARGETS))):
        for step in (-1, 1):
            k = r[base + ["tf_i", "st_i", "tg_i"]].copy()
            k[col] = k[col] + step
            valid = (k[col] >= 0) & (k[col] < lim)
            vals = look.reindex(pd.MultiIndex.from_frame(k)).to_numpy()
            has = valid.to_numpy() & ~np.isnan(vals)
            tot += valid.to_numpy()
            ok += has & (np.nan_to_num(vals) > 1)
    return pd.Series(np.where(tot > 0, ok / tot, 0), index=res.index)


def addon(trades: pd.DataFrame, key: tuple, ctxd: pd.DataFrame, base: pd.DataFrame, order: list) -> dict:
    fam, p, tf, dr, st, tg, flt = key
    days = ctxd.index[ctxd[flt].astype(bool)]
    t = trades[(trades.family == fam) & (trades.param == p) & (trades.tf == tf) & (trades.dir == dr) &
               (trades["stop"] == st) & (trades.target == tg) & trades.day.isin(days)].copy()
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    out = {}
    for w in ("12m", "6m"):
        a, b = map(pd.Timestamp, WIN[w])
        bw, iw = base[(base.day >= a) & (base.day <= b)], t[(t.day >= a) & (t.day <= b)]
        b0 = picker(bw, order)
        both = picker(pd.concat([bw, iw.assign(cand="IDEA")[bw.columns]], ignore_index=True), order + ["IDEA"])
        eq0, eq1 = b0.sort_values("exit_time").pnl_usd.cumsum(), both.sort_values("exit_time").pnl_usd.cumsum()
        out[f"gain_{w}"] = float(both.pnl_usd.sum() - b0.pnl_usd.sum())
        out[f"dd_base_{w}"] = float((eq0 - eq0.cummax().clip(lower=0)).min()) if len(eq0) else 0.0
        out[f"dd_with_{w}"] = float((eq1 - eq1.cummax().clip(lower=0)).min()) if len(eq1) else 0.0
    eq = t[t.day >= pd.Timestamp(WIN["12m"][0])].sort_values("exit_time").pnl_usd.cumsum()
    out["maxdd_12m"] = float((eq - eq.cummax().clip(lower=0)).min()) if len(eq) else 0.0
    return out


def main() -> None:
    trades = pd.read_parquet(OUT / "trades.parquet")
    ctxd = pd.read_parquet(OUT / "context.parquet")
    res = table(trades, ctxd)
    elig = res[(res.train_n >= 30) & (res.train_pf >= 1.2) & (res.train_mean > 0)].copy()
    elig["robust"] = robustness(res).reindex(elig.index)
    elig = elig[elig.robust >= 0.6].sort_values("train_t", ascending=False)
    res.to_parquet(OUT / "configs.parquet")
    print(f"settings: {len(res):,}; eligible + robust on train: {len(elig):,}")

    # is the search worth anything? (all settings with enough trades on both sides)
    pool = res[(res.train_n >= 30) & (res["12m_n"] >= 10)].copy()
    pool = pool[np.isfinite(pool.train_pf) & np.isfinite(pool["12m_pf"])]
    rho = pool[["train_pf", "12m_pf"]].rank().corr().iloc[0, 1]
    top = pool[pool.train_t >= pool.train_t.quantile(0.99)]
    diag = dict(settings_compared=len(pool), spearman_train_vs_test_pf=round(rho, 3),
                all_test_pf_median=round(pool["12m_pf"].median(), 3), top1pct_test_pf_median=round(top["12m_pf"].median(), 3),
                all_test_mean_trade=round(pool["12m_mean"].mean(), 1), top1pct_test_mean_trade=round(top["12m_mean"].mean(), 1),
                all_share_test_pf_gt1=round((pool["12m_pf"] > 1).mean(), 3), top1pct_share_test_pf_gt1=round((top["12m_pf"] > 1).mean(), 3))
    print(diag)

    # optimal setting per family and candle size
    best = elig.reset_index().groupby(["family", "tf"], as_index=False).first()
    best.to_csv(OUT / "best_per_family_tf.csv", index=False)

    # finalists -> candidates
    fin = elig[elig.train_t >= FINAL_T].reset_index()
    ctx = Context()
    base, order = baselines.us(ctx), list(baselines.ORDER["us"])
    rows = []
    for r in fin.to_dict("records"):
        rows.append({**r, **addon(trades, tuple(r[k] for k in KEY), ctxd, base, order)})
    fin = pd.DataFrame(rows)
    if len(fin):
        fin["candidate"] = ((fin["12m_pf"] >= 1.15) & (fin["8m_pf"] >= 1.15) & (fin["6m_pf"] >= 1.15) & (fin["6m_n"] >= 8)
                            & (fin.gain_12m > 0) & (fin.gain_6m > 0))
    fin.to_csv(OUT / "finalists.csv", index=False)
    pd.Series(diag).to_csv(OUT / "diagnostics.csv")
    print(f"finalists (train t >= {FINAL_T}): {len(fin)}; candidates: {int(fin.candidate.sum()) if len(fin) else 0}")


if __name__ == "__main__":
    main()
