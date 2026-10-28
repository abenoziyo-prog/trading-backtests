"""S&P dealer gamma (SqueezeMetrics GEX, and DIX) against what price does next: direction, volatility, drawdowns,
regime flips and lead-lag. SPX and /NQ, 2011-2026. Exploratory: no rule is fixed in advance, so every pattern is
shown separately for 2011-2020 and 2021-2026, and only one that holds in both is worth a pre-registered test.

    python scripts/gex_price_study.py

Known-at-the-time convention: GEX/DIX dated D (published after D's close) vs returns from D's close onward.
Level = percentile of GEX within the previous 252 sessions (incl. D), in quintiles (Q1 = lowest gamma).
Forward k-day returns overlap from day to day, so their t-stats use every k-th day only (non-overlapping samples).
Data: data/free/SPX_GEX_squeezemetrics.csv (bots/fetch_spx_gex.py URL), SPX close from the same file, /NQ = NSX 16:00
closes. Writes reports/gex/GEX_PRICE_STUDY.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "reports" / "gex"
NY = "America/New_York"
SPLIT = pd.Timestamp("2021-01-01")
HORIZONS = (1, 5, 20)


def table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def pctile(s: pd.Series, n: int = 252) -> pd.Series:
    return s.rolling(n, min_periods=200).apply(lambda w: (w <= w[-1]).mean() * 100, raw=True)


def load() -> pd.DataFrame:
    g = pd.read_csv(ROOT / "data/free/SPX_GEX_squeezemetrics.csv", parse_dates=["date"]).set_index("date").sort_index()
    for p in (ROOT / "data/schwab/gex/spx_gex_squeezemetrics.csv",):
        if p.exists():
            h = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
            g = h if len(h) > len(g) else g
    n = pd.read_parquet(ROOT / "data/free/NSXUSD_1min_histdata.parquet", columns=["close"])
    n.index = n.index.tz_convert(NY)
    n = n[(n.index.hour * 60 + n.index.minute) <= 959]
    n = n[(n.index.hour * 60 + n.index.minute) >= 570]
    nq = n.close.groupby(n.index.tz_localize(None).normalize()).last()
    d = g.rename(columns={"price": "spx"}).join(nq.rename("nq"), how="left")
    d["gex_pct"], d["dix_pct"] = pctile(d.gex), pctile(d.dix)
    d["gex_q"] = pd.cut(d.gex_pct, [-1, 20, 40, 60, 80, 101], labels=["Q1 low", "Q2", "Q3", "Q4", "Q5 high"]).astype(str)
    d["dix_q"] = pd.cut(d.dix_pct, [-1, 20, 40, 60, 80, 101], labels=["Q1 low", "Q2", "Q3", "Q4", "Q5 high"]).astype(str)
    for a in ("spx", "nq"):
        r = np.log(d[a]).diff()
        d[f"{a}_r"] = r
        for k in HORIZONS:
            d[f"{a}_f{k}"] = 100 * (np.log(d[a]).shift(-k) - np.log(d[a]))
        d[f"{a}_vol5"] = r.shift(-1).rolling(5).std().shift(-4) * np.sqrt(252) * 100           # realized vol of days D+1..D+5
        fwd_min = np.log(d[a])[::-1].rolling(20, min_periods=5).min()[::-1].shift(-1)            # lowest close D+1..D+20
        d[f"{a}_dd20"] = 100 * (fwd_min - np.log(d[a]))
    return d[d.gex_pct.notna()]


def tstat(x: pd.Series, k: int) -> float:
    x = x.dropna().iloc[::k]
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else np.nan


def by_bucket(d: pd.DataFrame, col: str, a: str) -> list[list]:
    rows = []
    order = sorted(d[col].unique(), key=lambda s: s)
    for q in order:
        x = d[d[col] == q]
        rows.append([q, len(x)] + [f"{x[f'{a}_f{k}'].mean():+.2f}% (t {tstat(x[f'{a}_f{k}'], k):+.1f})" for k in HORIZONS]
                    + [f"{(x[f'{a}_f1'] > 0).mean() * 100:.0f}%", f"{x[f'{a}_vol5'].mean():.1f}%", f"{x[f'{a}_dd20'].mean():.2f}%"])
    allr = [f"{d[f'{a}_f{k}'].mean():+.2f}%" for k in HORIZONS]
    rows.append(["all days", len(d)] + allr + [f"{(d[f'{a}_f1'] > 0).mean() * 100:.0f}%", f"{d[f'{a}_vol5'].mean():.1f}%",
                                              f"{d[f'{a}_dd20'].mean():.2f}%"])
    return rows


HEAD = ["Bucket", "Days", "Next day", "Next 5 days", "Next 20 days", "Next day up", "Realized vol, next 5 days",
        "Avg worst drop, next 20 days"]


def episodes(d: pd.DataFrame, a: str) -> list[list]:
    """GEX turning negative after >= 20 positive days, and turning back positive after >= 5 negative days."""
    s = np.sign(d.gex)
    pos_run = s.gt(0).astype(int).groupby((s <= 0).cumsum()).cumsum().shift()
    neg_run = s.lt(0).astype(int).groupby((s >= 0).cumsum()).cumsum().shift()
    to_neg = d[(s < 0) & (pos_run >= 20)]
    to_pos = d[(s > 0) & (neg_run >= 5)]
    rows = []
    for name, x in (("GEX turns negative (after >= 20 positive days)", to_neg), ("GEX turns positive (after >= 5 negative days)", to_pos)):
        rows.append([name, len(x)] + [f"{x[f'{a}_f{k}'].mean():+.2f}% ({(x[f'{a}_f{k}'] > 0).mean() * 100:.0f}% up)" for k in HORIZONS]
                    + [f"{x[f'{a}_dd20'].mean():.2f}%"])
    rows.append(["all days", len(d)] + [f"{d[f'{a}_f{k}'].mean():+.2f}% ({(d[f'{a}_f{k}'] > 0).mean() * 100:.0f}% up)" for k in HORIZONS]
                + [f"{d[f'{a}_dd20'].mean():.2f}%"])
    return rows


def lead_lag(d: pd.DataFrame, a: str) -> list[list]:
    """Correlation of the daily change in GEX percentile with the return k days later (k < 0: price moves first)."""
    dg = d.gex_pct.diff()
    r = d[f"{a}_r"] * 100
    return [[f"{k:+d}", f"{dg.corr(r.shift(-k)):+.3f}"] for k in range(-5, 6)]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    d = load()
    md = ["# S&P GEX and DIX vs what price does next (SPX and /NQ, 2011-2026)", "",
          "Exploratory, built by scripts/gex_price_study.py. GEX/DIX dated D vs returns from D's close. Quintiles of the "
          "252-day percentile (Q1 = lowest). t-stats use non-overlapping samples. Every table is split 2011-2020 / "
          "2021-2026: a pattern that is not in both is not a pattern.", ""]
    for a, name in (("spx", "SPX"), ("nq", "/NQ")):
        md += [f"## {name}: by GEX level", ""]
        for lab, w in (("2011-2020", d.index < SPLIT), ("2021-2026", d.index >= SPLIT)):
            md += [f"**{lab}**", ""] + table(HEAD, by_bucket(d[w].dropna(subset=[f"{a}_f1"]), "gex_q", a)) + [""]
        md += [f"**{name}: by GEX sign, both periods**", ""]
        rows = []
        for lab, w in (("2011-2020", d.index < SPLIT), ("2021-2026", d.index >= SPLIT)):
            x = d[w].dropna(subset=[f"{a}_f1"]).assign(sign=lambda f: np.where(f.gex < 0, "negative", "positive"))
            rows += [[f"{lab}, {r[0]}"] + r[1:] for r in by_bucket(x, "sign", a)[:-1]]
        md += table(HEAD, rows) + [""]
        md += [f"## {name}: by DIX level (dark-pool short volume share; SqueezeMetrics reads high = buying)", ""]
        for lab, w in (("2011-2020", d.index < SPLIT), ("2021-2026", d.index >= SPLIT)):
            md += [f"**{lab}**", ""] + table(HEAD, by_bucket(d[w].dropna(subset=[f"{a}_f1"]), "dix_q", a)) + [""]
        md += [f"## {name}: GEX regime flips", ""]
        for lab, w in (("2011-2020", d.index < SPLIT), ("2021-2026", d.index >= SPLIT)):
            md += [f"**{lab}**", ""] + table(["Event", "Days", "Next day", "Next 5 days", "Next 20 days", "Avg worst drop, next 20 days"],
                                             episodes(d[w].dropna(subset=[f"{a}_f1"]), a)) + [""]
        md += [f"## {name}: lead-lag of daily GEX changes and returns (full period)", "",
               "k < 0: the return k days BEFORE the GEX change; k > 0: the return k days AFTER it.", ""]
        md += table(["k (days)", "Correlation"], lead_lag(d.dropna(subset=[f"{a}_r"]), a)) + [""]
    (OUT / "GEX_PRICE_STUDY.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
