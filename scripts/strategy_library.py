"""Candidate library for the picker optimizer: every NSX strategy family, on 1/2/3/5/10-minute candles.

    python scripts/strategy_library.py

Last 18 months only (2025-04-10 -> 2026-10-09); approved rules (09:30-16:00 ET, entries until 15:30, flat
at 16:00, multiple trades per day, gap candles included, same-minute stop+target = stop, 1 NQ contract,
$20/pt, 1 pt round trip, no slippage). Every trade carries entry/exit timestamps so the picker can enforce
one position at a time.

Families (all rules as in their own scripts; only the candle size is extended to 2m)
  level   L1 L2 L3 S1 S2 S3          scripts/us_level_strategies_v2.py
  gapday  G1 G2 G3                   scripts/us_gap_strategies.py
  orb     classic ORB grid           scripts/open_orb.py (OR 5/15/30/60, stop opposite/mid, target 1/2/3/10R/eod,
                                     direction both/long/short, all or narrow-range days)
  orbpaper first-candle ORB          scripts/open_orb.py paper()
  gapfade gap fade to previous close scripts/open_gaps.py (entry open/confirm/first, target full/half,
                                     stop gap/R25/candle, gap up / gap down / both)
Writes reports/optimizer/library.parquet (one row per trade, `cand` = candidate id).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "optimizer"
TZ = "America/New_York"
START, END = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09")
TF_LIST = [1, 2, 3, 5, 10]
DPP = 20.0


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _std(df: pd.DataFrame, cand: pd.Series, family: str, tf, pnl_usd, side) -> pd.DataFrame:
    return pd.DataFrame({"cand": cand.to_numpy(), "family": family, "tf": np.asarray(tf).astype(int) if np.ndim(tf) else int(tf),
                         "day": pd.to_datetime(df.day).to_numpy(),
                         "side": np.asarray(side, int),
                         "entry_time": pd.to_datetime(df.entry_time, utc=True).dt.tz_convert(TZ).to_numpy(),
                         "exit_time": pd.to_datetime(df.exit_time, utc=True).dt.tz_convert(TZ).to_numpy(),
                         "pnl_usd": np.asarray(pnl_usd, float), "r_multiple": df.r_multiple.to_numpy(float)})


def level(ul2) -> pd.DataFrame:
    ul2.TFS = {f"{n}m": n for n in TF_LIST}
    t = ul2.run("NSX")
    tf = t.tf.str.rstrip("m").astype(int)
    return _std(t, "level|" + t.strategy + "|" + t.tf, "level", tf, t.pnl_usd, np.where(t.side == "long", 1, -1))


def gapday(gs) -> pd.DataFrame:
    gs.TFS = {f"{n}m": n for n in TF_LIST}
    t = gs.run("NSX")
    tf = t.tf.str.rstrip("m").astype(int)
    return _std(t, "gapday|" + t.strategy + "|" + t.tf, "gapday", tf, t.pnl_usd, np.where(t.side == "long", 1, -1))


def orb(o, days) -> pd.DataFrame:
    o.TFS = TF_LIST
    t = o.classic(days)
    t = pd.concat([t.assign(width="all"), t[t.narrow].assign(width="narrow")], ignore_index=True)
    cand = ("orb|OR" + t.or_min.astype(str) + "|" + t.stop + "|" + t.target + "|" + t.direction + "|" + t.width
            + "|" + t.tf.astype(str) + "m")
    return _std(t, cand, "orb", t.tf, t.pnl_pts * DPP, t.side)


def orbpaper(o, days) -> pd.DataFrame:
    o.TFS = TF_LIST
    t = o.paper(days)
    return _std(t, "orbpaper|" + t.target + "|" + t.tf.astype(str) + "m", "orbpaper", t.tf, t.pnl_pts * DPP, t.side)


def gapfade(o, g, days) -> pd.DataFrame:
    lv = pd.read_csv(o.SRC / "daily_levels_NSX.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    g.TFS = TF_LIST
    t = g.fade_trades(days, lv)
    tf = t.entry.str.extract(r"(\d+)m")[0].fillna(1).astype(int)
    kind = t.entry.str.replace(r"_\d+m", "", regex=True)
    base = "gapfade|" + kind + "|" + t.target + "|" + t.stop + "|"
    side = np.where(t.gap == "up", -1, 1)
    both = _std(t, base + "both|" + tf.astype(str) + "m", "gapfade", tf, t.pnl_pts * DPP, side)
    one = _std(t, base + np.where(t.gap == "up", "up", "down") + "|" + tf.astype(str) + "m", "gapfade", tf, t.pnl_pts * DPP, side)
    return pd.concat([both, one], ignore_index=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    o, g = _load("o", "open_orb.py"), _load("g", "open_gaps.py")
    ul2, gs = _load("ul2", "us_level_strategies_v2.py"), _load("gs", "us_gap_strategies.py")
    days = o.load_days()
    parts = [level(ul2), gapday(gs), orb(o, days), orbpaper(o, days), gapfade(o, g, days)]
    lib = pd.concat(parts, ignore_index=True)
    lib = lib[(lib.day >= START) & (lib.day <= END)].reset_index(drop=True)
    lib.to_parquet(OUT / "library.parquet")
    print(lib.groupby("family").agg(candidates=("cand", "nunique"), trades=("cand", "size")))


if __name__ == "__main__":
    main()
