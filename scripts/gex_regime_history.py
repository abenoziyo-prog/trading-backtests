"""Does the S&P 500's dealer-gamma regime predict the /NQ US session? 15 years, SqueezeMetrics GEX (free, daily).

    python scripts/gex_regime_history.py           # data/free/SPX_GEX_squeezemetrics.csv (download: see DATA below)

PRE-REGISTERED 2026-10-28, before any outcome was looked at (only the GEX series itself was inspected: 3,896 days from
2011-05-02, negative on 9% of them, 41% of 2022). Tests hypotheses R1/R2 of docs/GEX_FRAMEWORK.md with S&P GEX standing
in for the Nasdaq (no free QQQ/NDX history exists).
  regime (known before session D's open: GEX dated before D; SqueezeMetrics publishes after each close)
    sign   negative / positive
    level  percentile of GEX within the previous 252 sessions: low (< 33), mid, high (> 67); the level grows over the
           years, so the percentile, not the raw value, is compared
  outcomes (/NQ = NSX, US session 09:30-16:00 ET)
    O1  range / expected move: (high - low) / (VIX previous close / 16 % x open). PRIMARY: does GEX say anything that
        VIX does not?
    O2  range / ATR20: (high - low) / the average range of the previous 20 sessions
    O3  variance ratio VR(12) of 5-minute returns (var of 60-minute returns / 12 x var of 5-minute): > 1 trending,
        < 1 mean-reverting
    O4  trendiness |close - open| / (high - low)
  predicted: low / negative GEX -> larger O1-O4 (dealers short gamma amplify moves); high / positive -> smaller
  windows  design 2011-05 -> 2020-12 (may be inspected), holdout 2021-01 -> 2026-10-09 (judged once)
  passes   in the holdout, the predicted direction with |t| >= 3.0 (Welch, low vs high tercile; negative vs positive)
           -- 3.0 is the framework's bar for its 20 hypotheses
Exploratory (not judged): the live strategies' 18-month backtest trades split by the same regime.
DATA: curl -A "Mozilla/5.0" -o data/free/SPX_GEX_squeezemetrics.csv https://squeezemetrics.com/monitor/static/DIX.csv
Writes reports/gex/GEX_REGIME_HISTORY.md and reports/gex/daily_regime.csv.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "reports" / "gex"
NY = "America/New_York"
HOLDOUT = pd.Timestamp("2021-01-01")
END = pd.Timestamp("2026-10-10")
OUTCOMES = {"o1_range_em": "O1 range / expected move (VIX/16)", "o2_range_atr": "O2 range / ATR20",
            "o3_vr12": "O3 variance ratio VR(12)", "o4_trend": "O4 |close-open| / range"}


def table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def nq_days() -> pd.DataFrame:
    n = pd.read_parquet(ROOT / "data/free/NSXUSD_1min_histdata.parquet")
    n.index = n.index.tz_convert(NY)
    mod = n.index.hour * 60 + n.index.minute
    r = n[(mod >= 570) & (mod < 960) & (n.index.dayofweek < 5) & (n.index >= "2011-04-01") & (n.index < str(END.date()))]
    day = r.index.tz_localize(None).normalize()
    g = r.groupby(day)
    d = pd.DataFrame(dict(open=g.open.first(), high=g.high.max(), low=g.low.min(), close=g.close.last(), bars=g.close.size()))
    c5 = r.close.groupby([day, (r.index.hour * 60 + r.index.minute - 570) // 5]).last()
    r5 = np.log(c5).groupby(level=0).diff()
    r60 = np.log(c5).groupby(level=0).apply(lambda x: x.iloc[::12].diff())
    v5 = r5.groupby(level=0).var()
    v60 = r60.groupby(level=0).var() if r60.index.nlevels == 1 else r60.groupby(level=0).var()
    d["o3_vr12"] = (v60 / (12 * v5)).reindex(d.index)
    d = d[d.bars >= 300]
    rng = d.high - d.low
    d["o2_range_atr"] = rng / rng.rolling(20).mean().shift()
    d["o4_trend"] = (d.close - d.open).abs() / rng
    return d


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    g = pd.read_csv(ROOT / "data/free/SPX_GEX_squeezemetrics.csv", parse_dates=["date"]).set_index("date").gex
    vix = pd.read_parquet(ROOT / "data/free/VIX_daily_cboe.parquet").close
    vix.index = pd.to_datetime(vix.index).normalize()
    d = nq_days()
    gp = g.shift().reindex(d.index, method="ffill")                       # the last value dated before session D
    pct = g.rolling(252, min_periods=200).apply(lambda w: (w <= w[-1]).mean() * 100, raw=True).shift().reindex(d.index, method="ffill")
    d["gex_prev"], d["gex_pct"] = gp, pct
    d["sign"] = np.where(gp < 0, "negative", "positive")
    d["level"] = pd.cut(pct, [-1, 33, 67, 101], labels=["low", "mid", "high"]).astype(str)
    vp = vix.shift().reindex(d.index, method="ffill")
    d["o1_range_em"] = (d.high - d.low) / (vp / 16 / 100 * d.open)
    d = d.dropna(subset=["gex_prev", "gex_pct", "o1_range_em", "o2_range_atr", "o3_vr12"])
    d.to_csv(OUT / "daily_regime.csv")

    def welch(a, b):
        a, b = a.dropna(), b.dropna()
        se = np.sqrt(a.var() / len(a) + b.var() / len(b))
        return (a.mean() - b.mean()) / se if se > 0 else np.nan

    md = ["# S&P dealer gamma (SqueezeMetrics GEX) and the /NQ US session, 2011-2026", "",
          f"{len(d)} sessions (NSX 09:30-16:00), GEX dated before each session. Pre-registered rules in "
          "scripts/gex_regime_history.py. Design 2011-05 → 2020-12, holdout 2021-01 → 2026-10-09.", ""]
    verdicts = []
    for title, col, lo, hi in (("Level: low vs high GEX percentile (previous 252 sessions)", "level", "low", "high"),
                               ("Sign: negative vs positive GEX", "sign", "negative", "positive")):
        md += [f"## {title}", ""]
        rows = []
        for name, w in (("design", d.index < HOLDOUT), ("HOLDOUT", d.index >= HOLDOUT)):
            x = d[w]
            for o, label in OUTCOMES.items():
                a, b = x.loc[x[col] == lo, o], x.loc[x[col] == hi, o]
                t = welch(a, b)
                mids = [f"{x.loc[x[col] == k, o].mean():.3f}" for k in (["low", "mid", "high"] if col == "level" else ["negative", "positive"])]
                ok = name == "HOLDOUT" and t >= 3.0
                if name == "HOLDOUT":
                    verdicts.append((f"{col}: {label}", t, ok))
                rows.append([name, label, f"{len(a)} / {len(b)}", *mids, f"{a.mean() / b.mean():.2f}×", f"{t:+.1f}",
                             ("✅ PASS" if ok else "❌") if name == "HOLDOUT" else ""])
        heads = ["low", "mid", "high"] if col == "level" else ["negative", "positive"]
        md += table(["Window", "Outcome", f"Days ({lo} / {hi})", *[f"Mean, {h}" for h in heads], f"{lo} ÷ {hi}", "t (Welch)", "Verdict"], rows) + [""]

    # by year: does the low-vs-high gap in O1 hold every year?
    yr = []
    for y, x in d.groupby(d.index.year):
        a, b = x.loc[x.level == "low", "o1_range_em"], x.loc[x.level == "high", "o1_range_em"]
        yr.append([y, len(x), f"{a.mean():.2f}", f"{b.mean():.2f}", f"{a.mean() / b.mean():.2f}×", f"{welch(a, b):+.1f}",
                   f"{(x.sign == 'negative').mean() * 100:.0f}%"])
    md += ["## O1 by year (range / VIX expected move, low vs high GEX percentile)", ""]
    md += table(["Year", "Sessions", "Low GEX", "High GEX", "Low ÷ high", "t", "Negative-GEX days"], yr) + [""]

    # exploratory: live strategies by regime
    spec = importlib.util.spec_from_file_location("mlb", ROOT / "scripts" / "make_live_baseline.py")
    mlb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mlb)
    t = mlb.backtest_trades().reset_index(drop=True)
    entry = pd.to_datetime(t.entry_time, utc=True).dt.tz_convert(NY)
    known = entry.dt.tz_localize(None).dt.normalize() - pd.Timedelta(days=1)   # GEX dated before the entry's calendar day
    t["gex_pct"] = pct.reindex(pct.index.union(known.unique())).sort_index().ffill().reindex(known).to_numpy()
    t["gex_sign"] = np.where(g.shift(0).reindex(g.index.union(known.unique())).sort_index().ffill().reindex(known).to_numpy() < 0,
                             "negative", "positive")
    t["level"] = pd.cut(t.gex_pct, [-1, 33, 67, 101], labels=["low", "mid", "high"]).astype(str)
    md += ["## Exploratory: the live strategies' 18-month backtest by GEX regime at entry", "",
           "Not judged (the strategies were built on these 18 months). GEX = the last value dated before the entry day.", ""]
    rows = []
    for (s, lv), x in t.groupby(["strategy", "level"]):
        p = x.pnl_usd
        rows.append([s, lv, len(p), f"{(p > 0).mean() * 100:.0f}%", f"${p.sum():,.0f}", f"${p.mean():,.0f}",
                     f"{p[p > 0].sum() / -p[p <= 0].sum():.2f}" if (p <= 0).any() else "∞"])
    md += table(["Strategy", "GEX level", "Trades", "Win %", "Net", "Avg", "PF"], rows) + [""]
    rows = []
    for (b, lv), x in t.groupby(["book", "level"]):
        p = x.pnl_usd
        rows.append([b, lv, len(p), f"${p.sum():,.0f}", f"${p.mean():,.0f}", f"{p[p > 0].sum() / -p[p <= 0].sum():.2f}"])
    for (b, sg), x in t.groupby(["book", "gex_sign"]):
        p = x.pnl_usd
        rows.append([b, sg, len(p), f"${p.sum():,.0f}", f"${p.mean():,.0f}", f"{p[p > 0].sum() / -p[p <= 0].sum():.2f}" if (p <= 0).any() else "∞"])
    md += table(["Book", "GEX regime", "Trades", "Net", "Avg", "PF"], rows) + [""]
    md += ["## Verdicts (holdout 2021-2026, |t| >= 3.0 in the predicted direction)", ""]
    md += table(["Test", "t", "Result"], [[n, f"{tt:+.1f}", "✅ PASS" if ok else "❌ FAIL"] for n, tt, ok in verdicts])
    (OUT / "GEX_REGIME_HISTORY.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
