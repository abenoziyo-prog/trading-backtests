"""Reactions at PDH/PDL and Asia/London highs/lows during the US session, on 1m/3m/5m/10m candles.

    python scripts/us_level_reactions_tf.py

Windows: last 18 / 12 / 6 months ending 2026-10-09. US session 09:30-16:00 ET only.
Levels come from reports/us_session_levels/daily_levels_<INST>.csv (1-day look-back).
Touch candle = first US-session candle of that timeframe trading through the level:
  rejected  = traded through, closed back inside
  accepted  = closed beyond
  gap       = opened beyond the level (no test; excluded from reaction stats)
After the touch candle closes (no look-ahead), measured on 1-minute bars, in units of the
previous US session's range R = PDH - PDL:
  rev_N / cont_N   largest move back inside / further beyond within the next N candles (N = 1, 3, 5)
  race_10 / race_25  1 if a 0.10R / 0.25R move back inside comes before the same move beyond
                     (until 16:00), 0 if the reverse; ties and no-hit days are left out
Reference row: random candles from the same sessions, judged as if a high had been touched.
Writes reports/us_session_levels/tf_reactions/.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "us_session_levels"
OUT = SRC / "tf_reactions"
TFS = {"1m": 1, "3m": 3, "5m": 5, "10m": 10}
END = pd.Timestamp("2026-10-09")
WINDOWS = {"18m": pd.Timestamp("2025-04-10"), "12m": pd.Timestamp("2025-10-10"), "6m": pd.Timestamp("2026-04-10")}
LEVELS = {"NSX": [("PDH", 1), ("PDL", -1), ("asia_high", 1), ("asia_low", -1), ("london_high", 1), ("london_low", -1)],
          "QQQ": [("PDH", 1), ("PDL", -1)]}
NAMES = {"PDH": "PDH", "PDL": "PDL", "asia_high": "Asia high", "asia_low": "Asia low", "london_high": "London high", "london_low": "London low"}


def rth_minutes(name: str) -> pd.DataFrame:
    m = s.load(s.INSTRUMENTS[name])
    m = m[(m.index >= pd.Timestamp("2025-04-10", tz=s.TZ)) & (m.index < pd.Timestamp("2026-10-10", tz=s.TZ))]
    mod = s._minutes(m.index.tz_convert(s.TZ))
    return m[(mod >= 570) & (mod < 960) & (m.index.dayofweek < 5)]


def candles(day_min: pd.DataFrame, n: int) -> pd.DataFrame:
    """US-session candles of n minutes for one day, anchored at 09:30."""
    if n == 1:
        c = day_min[["open", "high", "low", "close"]].copy()
        c["last"] = day_min.index
        return c
    key = s.bar_start(day_min.index, n, "09:30")
    g = day_min.groupby(np.asarray(key))
    c = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last()})
    c["last"] = g.apply(lambda x: x.index[-1], include_groups=False)
    return c


def reaction(day_min: pd.DataFrame, entry_pos: int, entry: float, side: int, R: float, n_tf: int) -> dict:
    """Moves after the touch candle, from 1-minute bars starting at entry_pos."""
    hi, lo = day_min.high.to_numpy()[entry_pos:], day_min.low.to_numpy()[entry_pos:]
    rev = (entry - lo) if side == 1 else (hi - entry)  # back inside
    cont = (hi - entry) if side == 1 else (entry - lo)  # further beyond
    out = {}
    for k in (1, 3, 5):
        span = k * n_tf
        out[f"rev_{k}"] = rev[:span].max() / R if len(rev[:span]) else np.nan
        out[f"cont_{k}"] = cont[:span].max() / R if len(cont[:span]) else np.nan
    for pct, thr in (("10", 0.10), ("25", 0.25)):
        r_hit = np.flatnonzero(rev >= thr * R)
        c_hit = np.flatnonzero(cont >= thr * R)
        r0 = r_hit[0] if r_hit.size else np.inf
        c0 = c_hit[0] if c_hit.size else np.inf
        out[f"race_{pct}"] = np.nan if (r0 == c0) else float(r0 < c0)
    return out


def events(name: str, rng: np.random.Generator) -> pd.DataFrame:
    lv = pd.read_csv(SRC / f"daily_levels_{name}.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    rth = rth_minutes(name)
    by_day = {d: g for d, g in rth.groupby(rth.index.tz_localize(None).normalize())}
    rows = []
    for day, r in lv.iterrows():
        dm = by_day.get(day)
        if dm is None or len(dm) < 200:
            continue
        R = r.PDH - r.PDL
        if not R > 0:
            continue
        mins = dm.index
        for tf, n in TFS.items():
            c = candles(dm, n)
            for lvl, side in LEVELS[name]:
                level = r.get(lvl)
                if pd.isna(level):
                    continue
                through = (c.high > level) if side == 1 else (c.low < level)
                if not through.any():
                    rows.append(dict(instrument=name, day=day, tf=tf, level=NAMES[lvl], reaction="not touched"))
                    continue
                i = int(np.argmax(through.to_numpy()))
                bar = c.iloc[i]
                opened_beyond = bar.open > level if side == 1 else bar.open < level
                closed_beyond = bar.close > level if side == 1 else bar.close < level
                cls = "gap" if opened_beyond else ("accepted" if closed_beyond else "rejected")
                row = dict(instrument=name, day=day, tf=tf, level=NAMES[lvl], reaction=cls, touch_time=c.index[i],
                           overshoot=((bar.high - level) if side == 1 else (level - bar.low)) / R,
                           close_vs_level=((bar.close - level) * side) / R)
                if cls != "gap":
                    pos = int(mins.get_loc(bar["last"])) + 1  # first minute after the touch candle
                    if pos < len(dm):
                        row.update(reaction(dm, pos, bar.close, side, R, n))
                rows.append(row)
            # reference: one random candle per day per timeframe, treated as a "high" touch
            if len(c) > 6:
                j = int(rng.integers(0, len(c) - 5))
                pos = int(mins.get_loc(c.iloc[j]["last"])) + 1
                rows.append(dict(instrument=name, day=day, tf=tf, level="reference: random candle", reaction="random",
                                 **reaction(dm, pos, c.iloc[j].close, 1, R, n)))
    return pd.DataFrame(rows)


def summarize(ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for key, start in WINDOWS.items():
        w = ev[(ev.day >= start) & (ev.day <= END)]
        for (inst, level, tf), g in w.groupby(["instrument", "level", "tf"], sort=False):
            if level.startswith("reference"):
                parts = [("random", g)]
            else:
                tested = g[g.reaction.isin(["rejected", "accepted"])]
                parts = [("rejected", g[g.reaction == "rejected"]), ("accepted", g[g.reaction == "accepted"]), ("all tested", tested)]
            days = g.day.nunique()
            for label, x in parts:
                rr10, rr25 = x.race_10.dropna() if "race_10" in x else pd.Series(dtype=float), x.race_25.dropna() if "race_25" in x else pd.Series(dtype=float)
                rows.append(dict(window=key, instrument=inst, level=level, tf=tf, group=label, days=days,
                                 touched=int(g.reaction.isin(["rejected", "accepted", "gap"]).sum()) if not level.startswith("reference") else np.nan,
                                 gap=int((g.reaction == "gap").sum()) if not level.startswith("reference") else np.nan,
                                 events=len(x), share_of_tested=(len(x) / max(1, g.reaction.isin(["rejected", "accepted"]).sum())) if not level.startswith("reference") else np.nan,
                                 rev_1=x.get("rev_1", pd.Series(dtype=float)).median(), cont_1=x.get("cont_1", pd.Series(dtype=float)).median(),
                                 rev_5=x.get("rev_5", pd.Series(dtype=float)).median(), cont_5=x.get("cont_5", pd.Series(dtype=float)).median(),
                                 race10_n=len(rr10), race10=rr10.mean() if len(rr10) else np.nan,
                                 race25_n=len(rr25), race25=rr25.mean() if len(rr25) else np.nan))
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    ev = pd.concat([events(n, rng) for n in ("NSX", "QQQ")], ignore_index=True)
    ev.to_csv(OUT / "touch_events.csv", index=False)
    summ = summarize(ev)
    summ.to_csv(OUT / "summary.csv", index=False)
    print(f"{len(ev):,} rows; summary {len(summ):,} rows")


if __name__ == "__main__":
    main()
