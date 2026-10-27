"""Does VIX / 16 predict the S&P 500's daily move? ("rule of 16": sqrt(252) ~ 15.9, so VIX / 16 = the 1-day standard
deviation the options imply, in %.)

    python scripts/vix_rule_of_16.py                 # downloads SPX daily OHLC (Yahoo ^GSPC) once, caches it

What the rule claims, read strictly: with yesterday's VIX close V, today's close-to-close move r has a standard deviation
of V/16 %, so |r| < V/16 on about 68% of days and E|r| = 0.80 x V/16 (if moves were normal). It says nothing directly
about the high-low range; for a random walk the expected high-low range of a session is about 1.6 x its standard
deviation, but the cash session misses the overnight gap. Checked here:
  1. calibration: realized SD, share of days inside +/-V/16, mean |r| and mean range, overall and by VIX bucket
  2. accuracy day by day: how often the range lands within +/-25% of a range forecast scaled from V/16
  3. against simple alternatives known the same morning: 20-day realized volatility, 10-day average range
  4. the same for /NQ (NSX US session, the strategies' 18 months), with VIX and with VIX x NQ's beta
Windows: since 1993 (Yahoo SPX opens before 1993 are not real), last 10 years, and the strategies' 18 months.
Writes reports/vix/RULE_OF_16.md.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "reports" / "vix"
CACHE = ROOT / "data" / "free" / "SPX_daily_yahoo.parquet"
WINDOWS = [("1993-01-01 → 2026-10-26", "1993-01-01", "2026-10-27"), ("last 10 years", "2016-10-27", "2026-10-27"),
           ("strategies' 18 months", "2025-04-10", "2026-10-10")]


def spx() -> pd.DataFrame:
    if not CACHE.exists():
        import yfinance as yf
        d = yf.download("^GSPC", start="1990-01-01", end="2026-10-27", auto_adjust=False, progress=False)  # complete days only
        d.columns = [c[0].lower() for c in d.columns]
        d[["open", "high", "low", "close"]].to_parquet(CACHE)
    d = pd.read_parquet(CACHE)
    d.index = pd.to_datetime(d.index).normalize()
    return d


def frame(px: pd.DataFrame, vix: pd.Series) -> pd.DataFrame:
    d = px.copy()
    d["sig"] = vix.shift().reindex(d.index) / 16                     # yesterday's VIX close, known before the open
    d["r"] = 100 * np.log(d.close / d.close.shift())                  # close to close (includes the overnight gap)
    d["rng"] = 100 * (d.high - d.low) / d.close.shift()               # cash-session high-low, % of prior close
    d["rv20"] = d.r.rolling(20).std().shift()                         # 20-day realized SD, known the same morning
    d["ar10"] = d.rng.rolling(10).mean().shift()                      # 10-day average range
    return d.dropna(subset=["sig", "r", "rng", "rv20", "ar10"])


def fit(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    """Scale k (y ~ k x, through the origin) and R² of log y on log x (how well x ranks the days)."""
    k = float((x * y).sum() / (x * x).sum())
    lx, ly = np.log(x), np.log(y.clip(lower=1e-4))
    return k, float(np.corrcoef(lx, ly)[0, 1] ** 2)


def table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def calibration(d: pd.DataFrame) -> list:
    return [len(d), f"{d.sig.mean():.2f}%", f"{d.r.std():.2f}%", f"{d.r.std() / d.sig.mean():.2f}",
            f"{(d.r.abs() < d.sig).mean() * 100:.0f}%", f"{(d.r.abs() / d.sig).mean():.2f}", f"{(d.rng / d.sig).mean():.2f}",
            f"{(d.r.abs() > 2 * d.sig).mean() * 100:.1f}%"]


CAL_HEAD = ["Days", "Avg VIX/16", "Realized SD", "Realized / implied", "Days |move| < VIX/16 (normal: 68%)",
            "Avg |move| / (VIX/16) (normal: 0.80)", "Avg range / (VIX/16)", "Days |move| > 2×VIX/16 (normal: 4.6%)"]


def accuracy(d: pd.DataFrame, k: float, col: str = "sig") -> list:
    """Range forecast = k x predictor (k fitted on the window): share of days within +/-25% and median abs error."""
    f = k * d[col]
    err = (d.rng - f) / f
    return [f"{(err.abs() <= 0.25).mean() * 100:.0f}%", f"{err.abs().median() * 100:.0f}%", f"{(d.rng > 2 * f).mean() * 100:.1f}%"]


def compare(d: pd.DataFrame) -> list[list]:
    rows = []
    for name, col in (("VIX / 16", "sig"), ("20-day realized SD", "rv20"), ("10-day average range", "ar10")):
        k_r, r2_r = fit(d[col], d.rng)
        k_m, r2_m = fit(d[col], d.r.abs())
        rows.append([name, f"{k_r:.2f}", f"{r2_r:.2f}", f"{r2_m:.2f}"] + accuracy(d, k_r, col))
    both = np.column_stack([np.log(d.sig), np.log(d.ar10)])
    b, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(d)), both]), np.log(d.rng.clip(lower=1e-4)), rcond=None)
    pred = np.column_stack([np.ones(len(d)), both]) @ b
    r2 = 1 - ((np.log(d.rng.clip(lower=1e-4)) - pred) ** 2).sum() / ((np.log(d.rng.clip(lower=1e-4)) - np.log(d.rng.clip(lower=1e-4)).mean()) ** 2).sum()
    rows.append(["VIX/16 and 10-day range together", "–", f"{r2:.2f}", "–", "–", "–", "–"])
    return rows


CMP_HEAD = ["Predictor (known at the open)", "Range = k × predictor, k", "R² for range (log)", "R² for |move| (log)",
            "Days range within ±25% of forecast", "Median miss", "Days range > 2× forecast"]


def nq_section(vix: pd.Series) -> list[str]:
    spec = importlib.util.spec_from_file_location("lr", ROOT / "scripts" / "us_level_reactions_tf.py")
    lr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lr)
    m = lr.rth_minutes("NSX")
    g = m.groupby(m.index.tz_localize(None).normalize())
    px = pd.DataFrame(dict(open=g.open.first(), high=g.high.max(), low=g.low.min(), close=g.close.last()))
    d = frame(px, vix)
    s = frame(spx(), vix)
    j = pd.concat([d.r.rename("nq"), s.r.rename("spx")], axis=1, join="inner").dropna()
    beta = float(np.polyfit(j.spx, j.nq, 1)[0])
    out = [f"NQ's beta to SPX over the window: {beta:.2f} (close to close).", ""]
    rows = [["VIX / 16"] + calibration(d)]
    d2 = d.assign(sig=d.sig * beta)
    rows.append([f"VIX / 16 × {beta:.2f}"] + calibration(d2))
    out += table(["Predictor"] + CAL_HEAD, rows)
    out += ["", "NQ range forecasts (k fitted on the window):", ""] + table(CMP_HEAD, compare(d))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    vix = pd.read_parquet(ROOT / "data/free/VIX_daily_cboe.parquet").close
    vix.index = pd.to_datetime(vix.index).normalize()
    all_ = frame(spx(), vix)
    md = ["# VIX / 16 and the S&P 500's daily move", "",
          "VIX = yesterday's Cboe close. Move = SPX close to close (log %). Range = cash-session high − low, % of the prior "
          "close. Built by scripts/vix_rule_of_16.py.", "", "## 1. Calibration: is VIX/16 the right size?", ""]
    md += table(["Window"] + CAL_HEAD, [[name] + calibration(all_[(all_.index >= a) & (all_.index < b)]) for name, a, b in WINDOWS])
    ten = all_[all_.index >= "2016-10-27"]
    ten = ten.assign(b=pd.cut(ten.sig * 16, [0, 12, 15, 20, 25, 30, 40, 200], labels=["< 12", "12-15", "15-20", "20-25", "25-30", "30-40", ">= 40"]))
    md += ["", "**By VIX level, last 10 years**", ""]
    md += table(["VIX"] + CAL_HEAD, [[k] + calibration(g) for k, g in ten.groupby("b", observed=True)])
    md += ["", "## 2. Day by day: does it predict *that day's* range, and better than simpler gauges?", ""]
    for name, a, b in WINDOWS[1:]:
        md += [f"**{name}**", ""] + table(CMP_HEAD, compare(all_[(all_.index >= a) & (all_.index < b)])) + [""]
    md += ["## 3. /NQ (NSX US session, strategies' 18 months)", ""] + nq_section(vix)
    (OUT / "RULE_OF_16.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
