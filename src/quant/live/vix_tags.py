"""VIX state at each trade's entry: the same definitions for the live/shadow tags (status.md) and the 18-month backtest
study (scripts/vix_strategy_study.py). Pre-registered 2026-10-27 in docs/GEX_FRAMEWORK.md (section V); a change of a
cut-off is a new dated version there.

Every value is known at the entry (no look-ahead):
  vix_prev        the last official VIX close (16:15 ET) before the entry
  vix_now         the last VIX 1-minute close at or before the entry (at most 30 minutes old)
  vix_chg_pct     vix_now vs vix_prev, %
  spot_vol_corr   correlation of /NQ 1-minute returns with VIX 1-minute changes over the 60 minutes before the entry
                  (at least 40 paired minutes). Normally strongly negative; near zero = VIX and price have decoupled
  vix_vix3m       vix_now / VIX3M: >= 1 = backwardation (stress), < 1 = contango (live only: no VIX3M history here)
  vix9d_vix, vvix kept as numbers, no buckets yet
Buckets:
  vix_level  from vix_prev: < 15, 15-20, 20-25, >= 25
  vix_move   from vix_chg_pct: down (< -5%), flat, up (> +5%)
  coupling   from spot_vol_corr: coupled (< -0.6), loose (-0.6 to -0.2), decoupled (>= -0.2)
  term       from vix_vix3m: contango / backwardation
"""
from __future__ import annotations

from datetime import time, timedelta

import numpy as np
import pandas as pd

NY = "America/New_York"
CLOSE = time(16, 15)
CORR_MIN, CORR_N = 60, 40
COLS = ["vix_prev", "vix_now", "vix_chg_pct", "spot_vol_corr", "vix_vix3m", "vix9d_vix", "vvix",
        "vix_level", "vix_move", "coupling", "term"]
SPLITS = [("VIX level (prior close)", "vix_level"), ("VIX move since prior close", "vix_move"),
          ("NQ / VIX coupling (60-min correlation before entry)", "coupling"), ("VIX term structure (VIX / VIX3M)", "term")]


def daily_closes(vix_min: pd.Series) -> pd.Series:
    """Official-close proxy from 1-minute bars: the last bar at or before 16:15 of each day, indexed by date."""
    m = vix_min[vix_min.index.time <= CLOSE]
    return m.groupby(m.index.tz_localize(None).normalize()).last()


def last_at(s: pd.Series, t: pd.Timestamp, max_age=timedelta(minutes=30)) -> float:
    if s is None or s.empty:
        return np.nan
    i = s.index.searchsorted(t, side="right") - 1
    return float(s.iloc[i]) if i >= 0 and t - s.index[i] <= max_age else np.nan


def level_bucket(v: float) -> str:
    return "" if not np.isfinite(v) else "< 15" if v < 15 else "15-20" if v < 20 else "20-25" if v < 25 else ">= 25"


def move_bucket(p: float) -> str:
    return "" if not np.isfinite(p) else "down > 5%" if p < -5 else "up > 5%" if p > 5 else "flat (±5%)"


def coupling_bucket(c: float) -> str:
    return "" if not np.isfinite(c) else "coupled (< -0.6)" if c < -0.6 else "loose" if c < -0.2 else "decoupled (>= -0.2)"


def term_bucket(r: float) -> str:
    return "" if not np.isfinite(r) else "backwardation" if r >= 1 else "contango"


def spot_vol_corr(nq: pd.Series, vix: pd.Series, t: pd.Timestamp) -> float:
    if nq is None or vix is None or nq.empty or vix.empty:
        return np.nan
    a = t - timedelta(minutes=CORR_MIN)
    x = nq[(nq.index > a) & (nq.index <= t)]
    y = vix[(vix.index > a) & (vix.index <= t)]
    j = pd.concat([x.pct_change(), y.diff()], axis=1, join="inner").dropna()
    if len(j) < CORR_N or j.iloc[:, 0].std() == 0 or j.iloc[:, 1].std() == 0:
        return np.nan
    return float(j.iloc[:, 0].corr(j.iloc[:, 1]))


def features(entries: pd.Series, vix_daily: pd.Series, vix_min: pd.Series | None = None, nq_min: pd.Series | None = None,
             vix3m: pd.Series | None = None, vix9d: pd.Series | None = None, vvix: pd.Series | None = None) -> pd.DataFrame:
    """One row per entry time (tz-aware, any zone): the COLS above. vix_daily is indexed by naive dates."""
    rows = []
    for t in pd.to_datetime(entries, utc=True).dt.tz_convert(NY):
        d = t.tz_localize(None).normalize()
        known = d if t.time() >= CLOSE else d - timedelta(days=1)          # today's close counts only after 16:15
        i = vix_daily.index.searchsorted(known, side="right") - 1 if len(vix_daily) else -1
        prev = float(vix_daily.iloc[i]) if i >= 0 and known - vix_daily.index[i] <= timedelta(days=5) else np.nan
        now = last_at(vix_min, t)
        chg = 100 * (now / prev - 1) if np.isfinite(now) and np.isfinite(prev) else np.nan
        corr = spot_vol_corr(nq_min, vix_min, t)
        r3 = now / last_at(vix3m, t) if np.isfinite(now) else np.nan
        rows.append(dict(vix_prev=prev, vix_now=now, vix_chg_pct=chg, spot_vol_corr=corr, vix_vix3m=r3,
                         vix9d_vix=last_at(vix9d, t) / now if np.isfinite(now) else np.nan, vvix=last_at(vvix, t),
                         vix_level=level_bucket(prev), vix_move=move_bucket(chg), coupling=coupling_bucket(corr), term=term_bucket(r3)))
    f = pd.DataFrame(rows, columns=COLS, index=entries.index)
    num = COLS[:7]
    f[num] = f[num].astype(float).round(4)
    return f


def load_live(state_dir, schwab_dir, since) -> dict[str, pd.Series]:
    """VIX family 1-minute closes for the live tags: the daily archive (bots/schwab_archiver.py) topped up with today's
    bars from the 5-minute capture (data/schwab/vix/<day>/<SYM>.csv); /NQ from the bot's bars."""
    from quant.live.gex_tags import nq_minutes  # noqa: avoid a cycle at import time
    out = {}
    for key, sym in (("vix", "VIX"), ("vix3m", "VIX3M"), ("vix9d", "VIX9D"), ("vvix", "VVIX")):
        parts = []
        p = schwab_dir / f"{sym}_1min.parquet"
        if p.exists():
            parts.append(pd.read_parquet(p, columns=["close"]).close)
        for f in sorted((schwab_dir / "vix").glob(f"*/{sym}.csv")):
            if f.parent.name >= str(since):
                c = pd.read_csv(f)
                parts.append(pd.Series(c.close.to_numpy(), index=pd.to_datetime(c["time"], utc=True).dt.tz_convert(NY)))
        if parts:
            s = pd.concat(parts)
            s.index = s.index.tz_convert(NY)
            out[key] = s[~s.index.duplicated(keep="last")].sort_index()
    out["nq"] = nq_minutes(state_dir, schwab_dir / "NQ_1min.parquet", since)
    return out


def tag(trades: pd.DataFrame, state_dir, schwab_dir) -> pd.DataFrame:
    if trades.empty:
        return trades.assign(**{k: "" for k in COLS})
    e = pd.to_datetime(trades.entry_time, utc=True)
    src = load_live(state_dir, schwab_dir, (e.min() - timedelta(days=10)).date())
    vix = src.get("vix", pd.Series(dtype=float))
    f = features(trades.entry_time, daily_closes(vix) if len(vix) else pd.Series(dtype=float), vix, src.get("nq"),
                 src.get("vix3m"), src.get("vix9d"), src.get("vvix"))
    return pd.concat([trades, f], axis=1)
