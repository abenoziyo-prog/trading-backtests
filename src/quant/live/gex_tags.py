"""GEX state at each trade's entry: tags for live and shadow trades, and P&L by regime for status.md.

Report-time only: trades are tagged from the saved GEX files when status.md is written, so the engines are untouched
and a tag never changes a trade. Every trade gets the state that was known at its entry:
  snapshot  the latest bots/gex_snapshot.py row (data/schwab/gex/gex_daily.csv) at or before the entry, at most
            MAX_AGE old (the 16:20 row covers that evening's overnight trades; Friday's covers Sunday night).
            Regime per symbol (QQQ, NDX, SPX: naive sign convention, see gex.py), QQQ spot vs gamma flip, and the
            QQQ call/put walls converted to /NQ points (x NQ / QQQ at the snapshot) as distances from the entry.
  near-term the latest 5-minute capture (data/schwab/flow/QQQ) at or before the entry, at most NEAR_MAX_AGE old:
            net GEX of the 0-3 day expiries, which moves during the day.
Trades before the first snapshot are tagged "no data" and reported as such.
The tests on whether any of this predicts anything are pre-registered in docs/GEX_FRAMEWORK.md; this only records.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quant.live.gex import summary

ROOT = Path(__file__).resolve().parents[3]
GEX_DIR = ROOT / "data" / "schwab" / "gex"
FLOW_DIR = ROOT / "data" / "schwab" / "flow"
ARCHIVE = ROOT / "data" / "schwab" / "NQ_1min.parquet"
MAX_AGE = timedelta(hours=72)
NEAR_MAX_AGE = timedelta(minutes=10)
NY = "America/New_York"
TAGS = ["gex_asof", "regime_qqq", "regime_ndx", "regime_spx", "regime_combo", "qqq_vs_flip_pct", "flip_side",
        "call_wall_pts", "put_wall_pts", "near_asof", "near_net_gex", "near_regime"]


def snapshots(gex_dir: Path = GEX_DIR) -> pd.DataFrame:
    """One row per snapshot time, with each symbol's columns side by side (qqq_*, ndx_*, spx_*)."""
    p = gex_dir / "gex_daily.csv"
    if not p.exists():
        return pd.DataFrame()
    t = pd.read_csv(p)
    if "error" in t:
        t = t[t.error.isna()]
    t = t[t.net_gex.notna()]
    t["asof"] = pd.to_datetime(t["asof"], utc=True).dt.tz_convert(NY)
    t["sym"] = t.symbol.str.replace("$", "", regex=False).str.lower()
    w = t.pivot_table(index="asof", columns="sym", values=["regime", "net_gex", "spot", "spot_vs_flip_pct", "call_wall", "put_wall"],
                      aggfunc="last")
    w.columns = [f"{s}_{v}" for v, s in w.columns]
    return w.sort_index()


def nq_minutes(state_dir: Path, archive: Path, since) -> pd.Series:
    from quant.live.shadow import load_minutes  # the bot's own bars, topped up from the archive
    m = load_minutes(state_dir, archive, since)
    return m.close if len(m) else pd.Series(dtype=float)


def nq_at(nq: pd.Series, t: pd.Timestamp) -> float:
    """/NQ close of the last minute bar starting at or before t."""
    if nq.empty:
        return np.nan
    i = nq.index.searchsorted(t, side="right") - 1
    return float(nq.iloc[i]) if i >= 0 and t - nq.index[i] <= timedelta(minutes=30) else np.nan


def near_term(flow_dir: Path, t: pd.Timestamp) -> tuple[str, float]:
    """(capture time, net GEX of the 0-3 day QQQ expiries) of the last capture at or before t, if recent enough."""
    d = flow_dir / "QQQ" / t.date().isoformat()
    files = sorted(f for f in d.glob("*.csv.gz") if f.name[:4] <= f"{t:%H%M}") if d.exists() else []
    if not files:
        return "", np.nan
    c = pd.read_csv(files[-1])
    asof = pd.Timestamp(c["asof"].iloc[0]).tz_convert(NY)
    if t - asof > NEAR_MAX_AGE:
        return "", np.nan
    c = c.rename(columns={"gamma": "gamma_schwab"})
    s = summary(c, float(c.spot.iloc[0]), asof.to_pydatetime(), "QQQ")
    return asof.isoformat(timespec="minutes"), float(s.get("net_gex", np.nan))


def tag(trades: pd.DataFrame, state_dir: Path, gex_dir: Path = GEX_DIR, flow_dir: Path = FLOW_DIR,
        archive: Path = ARCHIVE) -> pd.DataFrame:
    """The trades with the TAGS columns added (entry_time must be an ISO time with offset)."""
    out = trades.copy()
    for k in TAGS:
        out[k] = pd.Series([""] * len(out), index=out.index, dtype=object)  # mixed text and numbers
    if out.empty:
        return out
    snaps = snapshots(gex_dir)
    entries = pd.to_datetime(out.entry_time, utc=True).dt.tz_convert(NY)
    nq = nq_minutes(state_dir, archive, (entries.min() - timedelta(days=4)).date()) if not snaps.empty else pd.Series(dtype=float)
    for i, t in zip(out.index, entries):
        near_asof, near = near_term(flow_dir, t)
        out.loc[i, ["near_asof", "near_net_gex", "near_regime"]] = [
            near_asof, "" if np.isnan(near) else round(near), "" if np.isnan(near) else "positive" if near > 0 else "negative"]
        if snaps.empty:
            continue
        j = snaps.index.searchsorted(t, side="right") - 1
        if j < 0 or t - snaps.index[j] > MAX_AGE:
            continue
        s, at = snaps.iloc[j], snaps.index[j]
        reg = {k: s.get(f"{k}_regime") if isinstance(s.get(f"{k}_regime"), str) else "" for k in ("qqq", "ndx", "spx")}
        vs = s.get("qqq_spot_vs_flip_pct")
        out.loc[i, ["gex_asof", "regime_qqq", "regime_ndx", "regime_spx"]] = [at.isoformat(timespec="minutes"), reg["qqq"], reg["ndx"], reg["spx"]]
        if reg["qqq"] and reg["spx"]:
            out.loc[i, "regime_combo"] = f"QQQ {reg['qqq'][:3]} / SPX {reg['spx'][:3]}"
        if pd.notna(vs):
            out.loc[i, ["qqq_vs_flip_pct", "flip_side"]] = [round(float(vs), 2), "above flip" if vs >= 0 else "below flip"]
        ratio = nq_at(nq, at) / s.get("qqq_spot", np.nan)
        entry_nq = nq_at(nq, t)
        for side in ("call", "put"):
            wall = s.get(f"qqq_{side}_wall", np.nan)
            d = wall * ratio - entry_nq
            out.loc[i, f"{side}_wall_pts"] = "" if not np.isfinite(d) else round(float(d), 2)
    return out


SPLITS = [("QQQ regime (09:20 / 16:20 snapshot)", "regime_qqq"), ("QQQ × SPX regime", "regime_combo"),
          ("QQQ vs gamma flip", "flip_side"), ("Near-term QQQ regime (0-3 day expiries, 5-min capture)", "near_regime")]


def regime_tables(tagged: pd.DataFrame, splits: list[tuple[str, str]] = SPLITS, pnl: str = "pnl_usd") -> list[str]:
    """One P&L table per (title, column) split; empty tags count as "no data"."""
    from quant.live.status import money, table  # status imports this module lazily: no cycle at import time
    out = []
    p = tagged[pnl].astype(float)
    for title, col in splits:
        key = tagged[col].fillna("").astype(str).replace("", "no data")
        rows = []
        for k in sorted(key.unique(), key=lambda x: (x == "no data", x)):
            x = p[key == k]
            rows.append([k, len(x), f"{(x > 0).sum()} / {(x <= 0).sum()}", money(x.sum()), money(x.mean()),
                         f"{100 * (x > 0).mean():.0f}%"])
        out += [f"**{title}**", ""] + table(["State at entry", "Trades", "W / L", "Net", "Avg", "Win %"], rows) + [""]
    return out
