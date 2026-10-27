"""Expected-move context of each trade: how big the session was expected to be (VIX "rule of 16"), how much of that was
already used when the trade entered, and how its stop and target compare. Record only: no strategy uses these.

  em_pts            the session's expected move in /NQ points = the VIX close before the session / 16 (%) x the
                    session's opening price (09:30 for US trades, 18:00 for overnight trades). VIX/16 is the options'
                    1-day standard deviation; NQ's US range averaged 1.13 x it over the strategies' 18 months
                    (scripts/vix_rule_of_16.py)
  em_used           the session's high - low from its open to the minute before the entry, / em_pts
  em_drift          (entry - session open) x side / em_pts: > 0 = entering in the direction the session already moved
  stop_em           initial risk / em_pts; initial risk = P&L points / R multiple (the ledger's R uses the initial
                    stop, so later breakeven or trailing moves do not change it)
  target_em         |target - entry| / em_pts (live trades only: the shadow ledger keeps no target); blank = no target
  day_range_em      the session's whole range / em_pts: an outcome, known only after the close, for context
Buckets (fixed 2026-10-27): em_used < 0.5 / 0.5-1 / >= 1; em_drift chasing (>= +0.5) / neutral / fading (< -0.5);
stop_em < 0.25 / 0.25-0.5 / >= 0.5; target_em < 0.5 / 0.5-1 / >= 1 / none.
"""
from __future__ import annotations

from datetime import time, timedelta

import numpy as np
import pandas as pd

NY = "America/New_York"
COLS = ["em_pts", "em_used", "em_drift", "stop_em", "target_em", "day_range_em", "em_used_bucket", "em_drift_bucket",
        "stop_bucket", "target_bucket"]
SPLITS = [("Expected move already used at entry", "em_used_bucket"),
          ("Entry vs the session's move so far (in expected moves)", "em_drift_bucket"),
          ("Initial stop size (in expected moves)", "stop_bucket"), ("Target distance (in expected moves)", "target_bucket")]


def session_bounds(t: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(open, close) of the session a trade entering at t belongs to: US 09:30-16:00, else the night from 18:00."""
    d = t.normalize()
    if time(9, 30) <= t.time() < time(16, 0):
        return d + timedelta(hours=9, minutes=30), d + timedelta(hours=16)
    start = d + timedelta(hours=18) if t.time() >= time(18, 0) else d - timedelta(hours=6)   # previous 18:00
    return start, start + timedelta(hours=15, minutes=30)                                  # to 09:30


def bucket(x: float, cuts: list[float], labels: list[str]) -> str:
    if not np.isfinite(x):
        return ""
    for c, lab in zip(cuts, labels):
        if x < c:
            return lab
    return labels[-1]


def used_bucket(x):
    return bucket(x, [0.5, 1.0], ["< 0.5", "0.5-1", ">= 1 (move already done)"])


def drift_bucket(x):
    return bucket(x, [-0.5, 0.5], ["fading (< -0.5)", "neutral", "chasing (>= +0.5)"])


def stop_bucket(x):
    return bucket(x, [0.25, 0.5], ["< 0.25", "0.25-0.5", ">= 0.5"])


def target_bucket(x):
    return "none" if x is None or (isinstance(x, float) and np.isnan(x)) else bucket(x, [0.5, 1.0], ["< 0.5", "0.5-1", ">= 1"])


def features(trades: pd.DataFrame, vix_daily: pd.Series, nq: pd.DataFrame) -> pd.DataFrame:
    """nq: 1-minute open/high/low/close (NY index); vix_daily: VIX closes indexed by naive date. Needs entry_time,
    side ("long"/"short" or +1/-1), pnl_usd, r_multiple; uses entry and target when present (live ledger)."""
    rows = []
    vd = vix_daily.sort_index()
    for _, r in trades.iterrows():
        t = pd.Timestamp(r.entry_time).tz_convert(NY)
        side = 1 if str(r.side).lower() in ("long", "1") else -1
        a, b = session_bounds(t)
        a0 = a.tz_localize(None)
        i = (vd.index + timedelta(hours=16, minutes=15)).searchsorted(a0, side="right") - 1 if len(vd) else -1
        vix = float(vd.iloc[i]) if i >= 0 and a0 - vd.index[i] <= timedelta(days=5) else np.nan   # last close before the open
        sess = nq[(nq.index >= a) & (nq.index < b)] if len(nq) else nq
        before = sess[sess.index < t.floor("min")] if len(sess) else sess
        o = float(sess.open.iloc[0]) if len(sess) and sess.index[0] - a <= timedelta(minutes=5) else np.nan
        em = vix / 16 / 100 * o if np.isfinite(vix) and np.isfinite(o) else np.nan
        ok = np.isfinite(em) and em > 0
        entry = float(r["entry"]) if "entry" in r and pd.notna(r.get("entry")) else (float(before.close.iloc[-1]) if len(before) else np.nan)
        pnl_pts, rm = float(r.pnl_usd) / 20, float(r.r_multiple) if pd.notna(r.r_multiple) else np.nan
        risk = abs(pnl_pts / rm) if np.isfinite(rm) and rm != 0 else np.nan
        tgt = r.get("target") if "target" in r else None
        tgt = float(tgt) if tgt is not None and pd.notna(tgt) and str(tgt) != "" else None
        used = (before.high.max() - before.low.min()) / em if ok and len(before) else (0.0 if ok else np.nan)
        drift = (entry - o) * side / em if ok and np.isfinite(entry) else np.nan
        stop_em = risk / em if ok else np.nan
        target_em = (abs(tgt - entry) / em if tgt is not None and np.isfinite(entry) else None) if ok else np.nan
        day_rng = (sess.high.max() - sess.low.min()) / em if ok and len(sess) else np.nan
        rows.append(dict(em_pts=em, em_used=used, em_drift=drift, stop_em=stop_em,
                         target_em=np.nan if target_em is None else target_em, day_range_em=day_rng,
                         em_used_bucket=used_bucket(used), em_drift_bucket=drift_bucket(drift), stop_bucket=stop_bucket(stop_em),
                         target_bucket=(target_bucket(target_em) if ok and "target" in r else "")))
    f = pd.DataFrame(rows, columns=COLS, index=trades.index)
    num = COLS[:6]
    f[num] = f[num].astype(float).round(3)
    return f


def tag(trades: pd.DataFrame, state_dir, schwab_dir) -> pd.DataFrame:
    if trades.empty:
        return trades.assign(**{k: "" for k in COLS})
    from quant.live import vix_tags
    from quant.live.shadow import load_minutes
    e = pd.to_datetime(trades.entry_time, utc=True)
    since = (e.min() - timedelta(days=10)).date()
    vix = vix_tags.load_live(state_dir, schwab_dir, since).get("vix")
    vd = vix_tags.daily_closes(vix) if vix is not None and len(vix) else pd.Series(dtype=float)
    nq = load_minutes(state_dir, schwab_dir / "NQ_1min.parquet", since)
    return pd.concat([trades, features(trades, vd, nq)], axis=1)
