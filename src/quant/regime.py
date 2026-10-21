"""Volatility-regime features and robust statistics for the level-reaction study.

Every feature is known before the event (prior-day values only):
  vix_prev     prior official VIX close (already on events)
  vix_pct      percentile of that close within the trailing 252 trading days (0-100)
  rv20         instrument's own 20-day realized volatility, annualized %, at the prior close
  atr_pct      ATR14 as % of the entry price
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import reactions as rx
from . import sessions as s


def vix_percentile(vix_daily: pd.DataFrame, window: int = 252) -> pd.Series:
    c = vix_daily["close"]
    return c.rolling(window, min_periods=200).apply(lambda w: (w <= w[-1]).mean() * 100, raw=True)


def lookup_prior(series: pd.Series, entry: pd.DatetimeIndex, close_minute: int) -> np.ndarray:
    """Value of a daily series at the last date whose close is known at `entry`."""
    local = entry.tz_convert(s.TZ).tz_localize(None)
    d = local.normalize()
    cutoff = d + pd.to_timedelta(((local.hour * 60 + local.minute) >= close_minute).astype(int), unit="D")
    i = series.index.searchsorted(cutoff, side="left") - 1
    return np.where(i >= 0, series.to_numpy()[np.clip(i, 0, None)], np.nan)


def realized_vol(daily: pd.DataFrame, n: int = 20) -> pd.Series:
    """Annualized close-to-close volatility (%) over the prior n days, known before each day starts."""
    r = np.log(daily["close"] / daily["close"].shift())
    return (r.rolling(n, min_periods=15).std() * np.sqrt(252) * 100).shift()


def add_features(ev: pd.DataFrame, vix_daily: pd.DataFrame, daily_by_inst: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = ev.copy()
    entry = pd.DatetimeIndex(out["entry_time"])
    out["vix_pct"] = lookup_prior(vix_percentile(vix_daily), entry, 975)
    out["atr_pct"] = out["atr"] / out["entry"] * 100
    out["rv20"] = np.nan
    for name, daily in daily_by_inst.items():
        m = out["instrument"] == name
        kind = "futures" if s.INSTRUMENTS[name].day == "futures" else "calendar"
        tday = s.trading_day(entry[m.to_numpy()], kind)
        out.loc[m, "rv20"] = realized_vol(daily).reindex(tday).to_numpy()
    return out


# ------------------------------------------------------------------ statistics

def auc(score: np.ndarray, y: np.ndarray) -> float:
    """Probability a random reversal event has a higher score than a random continuation event."""
    ok = ~(np.isnan(score) | np.isnan(y))
    score, y = score[ok], y[ok]
    pos, neg = score[y == 1], score[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    ranks = pd.Series(np.r_[pos, neg]).rank().to_numpy()
    return (ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def block_bootstrap_diff(y: np.ndarray, flag: np.ndarray, blocks: np.ndarray, reps: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """Rate(flag) - rate(~flag) with a 95% CI that resamples whole blocks (e.g. months)."""
    ok = ~np.isnan(y)
    y, flag, blocks = y[ok], flag[ok], blocks[ok]
    diff = y[flag].mean() - y[~flag].mean() if flag.any() and (~flag).any() else np.nan
    ub = pd.unique(blocks)
    idx = {b: np.flatnonzero(blocks == b) for b in ub}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(reps):
        pick = np.concatenate([idx[b] for b in rng.choice(ub, len(ub), replace=True)])
        f, yy = flag[pick], y[pick]
        if f.any() and (~f).any():
            out.append(yy[f].mean() - yy[~f].mean())
    lo, hi = np.percentile(out, [2.5, 97.5]) if out else (np.nan, np.nan)
    return diff, lo, hi


def rate_table(ev: pd.DataFrame, by: list[str], race: str = "race_0.25") -> pd.DataFrame:
    def f(g):
        r = g[race].dropna()
        k, n = r.sum(), len(r)
        lo, hi = rx.wilson(k, n)
        return pd.Series({"n": n, "rate": k / n if n else np.nan, "ci_low": lo, "ci_high": hi,
                          "months": pd.DatetimeIndex(g["test_day"]).to_period("M").nunique()})
    return ev.groupby(by, observed=True).apply(f, include_groups=False).reset_index()
