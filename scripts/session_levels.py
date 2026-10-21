"""Mark the high and low of every day, session and intraday bar.

    python scripts/session_levels.py                      # all instruments, all levels
    python scripts/session_levels.py --only NSX QQQ --levels daily sessions

Writes reports/levels/<INSTRUMENT>/<level>.parquet and .csv, where level is
weekly, daily, sessions, 4h, 1h, 30m or 15m. Columns:
  week / trading_day / session / bar_start   the period
  start, end                          first and last 1-minute bar in it (New York time)
  open, high, low, close
  high_time, low_time                 minute the high / low was made
  bars, coverage                      1-minute bars present (coverage = bars / expected)
  high_swept_at, low_swept_at         weekly (28-day look-ahead), daily + sessions (7 days): when first taken out afterwards
  volume, trade_count, vwap, poc,     only where the source has volume (QQQ)
  poc_volume
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from quant import sessions as s

OUT = Path(__file__).resolve().parents[1] / "reports" / "levels"
LEVELS = ["weekly", "daily", "sessions", *s.TIMEFRAMES]


def save(df, inst: str, level: str) -> Path:
    folder = OUT / inst
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{level}.parquet"
    df.to_parquet(path)
    df.to_csv(path.with_suffix(".csv"), index=False)
    return path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", nargs="+", default=list(s.INSTRUMENTS), choices=list(s.INSTRUMENTS))
    p.add_argument("--levels", nargs="+", default=LEVELS, choices=LEVELS)
    args = p.parse_args()

    for name in args.only:
        inst = s.INSTRUMENTS[name]
        df = s.load(inst)
        print(f"\n{name}: {len(df):,} 1-minute bars, {df.index[0]} -> {df.index[-1]}")
        for level in args.levels:
            t0 = time.time()
            if level == "weekly":
                lv = s.weekly_levels(inst, df)
            elif level == "daily":
                lv = s.daily_levels(inst, df)
            elif level == "sessions":
                lv = s.session_levels(inst, df)
            else:
                lv = s.timeframe_levels(inst, df, s.TIMEFRAMES[level])
            path = save(lv, name, level)
            extra = ""
            if "high_swept_at" in lv:
                extra = f" | high later swept {lv.high_swept_at.notna().mean():.0%}, low {lv.low_swept_at.notna().mean():.0%}"
            print(f"  {level:8s} {len(lv):>9,} rows  ({time.time() - t0:5.1f}s){extra}  -> {path.relative_to(OUT.parents[1])}")


if __name__ == "__main__":
    main()
