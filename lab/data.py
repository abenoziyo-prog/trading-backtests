"""Data and helpers handed to ideas (the `ctx` argument of an idea's trades() function).

  ctx.us_days     US sessions, last 18 months: dicts with day, R0, t (bar times), mod (minute of day), o/h/l/c arrays
  ctx.nights      nights (D-1 18:00 -> D 09:29 ET): dicts with day, t0, rel (minutes after 18:00), t, o/h/l/c,
                  PDH/PDL/PDC/R0, prev_open, vix
  ctx.levels      daily PDH/PDL/PDC/Asia/London levels per US session (DataFrame indexed by day)
  ctx.features    day conditions known at 09:30 (gap_R, overnight_R, open_loc, prior, vix + boolean gates)
  ctx.us          helper module for US-session ideas (scripts/open_orb.py: or_end, candles_after, exit_trade,
                  COST, DPP, LAST_ENTRY)
  ctx.night       helper module for overnight ideas (scripts/overnight_strategies.py: candles, exit_trade,
                  walk, record, FLAT, COST, DPP)
Every idea gets the same data (NSX 1-minute bars, 2025-04-10 -> 2026-10-09) and the approved trading rules.
"""
from __future__ import annotations

import importlib.util
import sys
from functools import cached_property
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_script(name: str, file: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # registered before running: dataclasses (and caching) need it
    spec.loader.exec_module(mod)
    return mod


class Context:
    @cached_property
    def us(self):
        return load_script("lab_open_orb", "open_orb.py")

    @cached_property
    def night(self):
        return load_script("lab_overnight_strategies", "overnight_strategies.py")

    @cached_property
    def us_days(self) -> list[dict]:
        return self.us.load_days()

    @cached_property
    def nights(self) -> list[dict]:
        return self.night.onr.load_nights()

    @cached_property
    def levels(self) -> pd.DataFrame:
        lv = pd.read_csv(ROOT / "reports/us_session_levels/daily_levels_NSX.csv", index_col=0, parse_dates=[0])
        return lv

    @cached_property
    def features(self) -> pd.DataFrame:
        return load_script("lab_us_conditions", "us_conditions.py").features()
