"""Day conditions known at the US open (09:30 ET), for gating US strategies. NSX, last 18 months.

    python scripts/us_conditions.py        # writes reports/us_conditions/day_features.csv

Per US session D (all values known at 09:30, R0 = previous US range):
  gap_R        (09:30 open - PDC) / R0
  overnight_R  (09:30 open - price at 18:00 the evening before) / R0
  open_loc     where the open sits: above_PDH / inside / below_PDL
  prior        previous US session up (close > open) or down
  vix          VIX previous close
Gates (each a boolean column): all, gap_down (gap_R < -0.10), gap_up (> 0.10), flat_open (|gap_R| <= 0.10),
overnight_down (overnight_R < -0.10), overnight_up (> 0.10), below_PDL, above_PDH, inside_range, prior_down,
prior_up, vix_high (> 20), vix_low (<= 20).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "us_conditions"
GATES = ["all", "gap_down", "gap_up", "flat_open", "overnight_down", "overnight_up", "below_PDL", "above_PDH",
         "inside_range", "prior_down", "prior_up", "vix_high", "vix_low"]


def features() -> pd.DataFrame:
    lv = pd.read_csv(ROOT / "reports/us_session_levels/daily_levels_NSX.csv", index_col=0, parse_dates=[0])
    m = s.load(s.INSTRUMENTS["NSX"])
    m = m[(m.index >= pd.Timestamp("2025-03-25", tz=s.TZ)) & (m.index < pd.Timestamp("2026-10-10", tz=s.TZ))]
    mod = m.index.hour * 60 + m.index.minute
    rth = m[(mod >= 570) & (mod < 960)]
    us_open = rth.open.groupby(rth.index.tz_localize(None).normalize()).first()
    vix = pd.read_parquet(ROOT / "data/free/VIX_daily_cboe.parquet").close
    vix.index = pd.to_datetime(vix.index).normalize()
    rows = []
    for day, r in lv.iterrows():
        R0 = r.PDH - r.PDL
        if day not in us_open.index or not R0 > 0:
            continue
        o = us_open.loc[day]
        t18 = pd.Timestamp(day - pd.Timedelta(days=1), tz=s.TZ) + pd.Timedelta(hours=18)
        i = m.index.searchsorted(t18)
        p18 = m.open.iloc[i] if i < len(m) and m.index[i] < pd.Timestamp(day, tz=s.TZ) + pd.Timedelta(hours=9, minutes=30) else np.nan
        prev = pd.Timestamp(r.prev_day)
        vp = vix.loc[:prev]
        rows.append(dict(day=day, gap_R=(o - r.PDC) / R0, overnight_R=(o - p18) / R0 if np.isfinite(p18) else np.nan,
                         open_loc="above_PDH" if o > r.PDH else ("below_PDL" if o < r.PDL else "inside"),
                         prior="up" if prev in us_open.index and r.PDC > us_open.loc[prev] else "down",
                         vix=vp.iloc[-1] if len(vp) else np.nan))
    f = pd.DataFrame(rows).set_index("day")
    f["all"] = True
    f["gap_down"], f["gap_up"], f["flat_open"] = f.gap_R < -0.10, f.gap_R > 0.10, f.gap_R.abs() <= 0.10
    f["overnight_down"], f["overnight_up"] = f.overnight_R < -0.10, f.overnight_R > 0.10
    f["below_PDL"], f["above_PDH"], f["inside_range"] = f.open_loc == "below_PDL", f.open_loc == "above_PDH", f.open_loc == "inside"
    f["prior_down"], f["prior_up"] = f.prior == "down", f.prior == "up"
    f["vix_high"], f["vix_low"] = f.vix > 20, f.vix <= 20
    return f


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f = features()
    f.to_csv(OUT / "day_features.csv")
    print(f[GATES].mean().round(2).to_string())
    print(len(f), "days")


if __name__ == "__main__":
    main()
