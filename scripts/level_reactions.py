"""Build the level-reaction event table for NSX and QQQ.

    python scripts/level_reactions.py

For every reference level (PDH/PDL, PWH/PWL, and for NSX the Asia and London
session highs/lows) and every timeframe (4h, 1h, 30m, 15m), records the first
touch, how the touch bar closed (rejected / accepted / gap), the outcome race
and forward returns, and the DXY/VIX context at entry.

Output: reports/level_reactions/events.parquet (+ .csv)
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from quant import reactions as rx
from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "level_reactions"
DATA = ROOT / "data" / "free"


def period_of(name: str, day: pd.Series) -> pd.Series:
    """Discovery vs validation split."""
    day = pd.DatetimeIndex(day)
    if name == "NSX":  # 2011-2018 (NQ-futures-priced era) to discover, 2019+ to validate
        return pd.Series(["discovery" if d.year <= 2018 else "validation" for d in day])
    return pd.Series(["2024" if d.year == 2024 else "2025" for d in day])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dxy = pd.read_parquet(DATA / "DXY_1min_rebuilt.parquet")
    vix_daily = pd.read_parquet(DATA / "VIX_daily_cboe.parquet")
    vix_min = pd.read_parquet(DATA / "VIX_1min_2024_2025.parquet")
    frames = []
    for name in ("NSX", "QQQ"):
        inst = s.INSTRUMENTS[name]
        m = s.load(inst)
        if name == "NSX":
            m = m[m.index >= pd.Timestamp("2011-01-01", tz=s.TZ)]  # skip the partial first weeks
        t0 = time.time()
        daily = rx.daily_table(inst, m)
        levels = rx.reference_levels(inst, m, daily, with_sessions=(name == "NSX"))
        print(f"{name}: {daily.valid.sum():,} valid trading days ({(~daily.valid).sum()} excluded for low coverage), "
              f"{len(levels):,} level windows")
        for tf, minutes in rx.TFS.items():
            ev = rx.detect_events(levels, rx.tf_bars(inst, m, minutes), tf)
            ev = rx.add_outcomes(ev, m, inst)
            ev.insert(0, "instrument", name)
            frames.append(ev)
            print(f"  {tf:4s} {len(ev):>7,} touches  ({time.time() - t0:5.1f}s)")
    events = pd.concat(frames, ignore_index=True)
    events = rx.add_conditions(events, dxy, vix_daily, vix_min)
    events = rx.bucketize(events)
    events["period"] = pd.concat([period_of(n, g["test_day"]) .set_axis(g.index) for n, g in events.groupby("instrument")]).sort_index()
    events["year"] = pd.DatetimeIndex(events["test_day"]).year
    events.to_parquet(OUT / "events.parquet")
    events.to_csv(OUT / "events.csv", index=False)
    print(f"\nsaved {len(events):,} events -> {OUT / 'events.parquet'}")
    print(events.groupby(["instrument", "reaction"]).size().unstack().to_string())


if __name__ == "__main__":
    main()
