"""Replay historical 1-minute bars through the live engine (for parity checks and dry runs)."""
from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pandas as pd

from .engine import Bar, DayEngine, DayLevels, Event, ExitPolicy


def levels_from_row(r: pd.Series) -> DayLevels:
    lh = r.get("london_high")
    return DayLevels(float(r.PDH), float(r.PDL), float(r.PDC), None if pd.isna(lh) else float(lh))


def bars_of(day_min: pd.DataFrame) -> Iterator[Bar]:
    for t, o, h, l, c in zip(day_min.index, day_min.open.to_numpy(), day_min.high.to_numpy(),
                             day_min.low.to_numpy(), day_min.close.to_numpy()):
        yield Bar(t.to_pydatetime(), float(o), float(h), float(l), float(c))


def replay(rth: pd.DataFrame, levels: pd.DataFrame, min_bars: int = 200,
           exits: dict[str, ExitPolicy] | None = None) -> Iterator[tuple[DayEngine, list[Event]]]:
    """rth: 1-minute US-session bars (tz-aware NY index); levels: daily PDH/PDL/PDC/london_high indexed by day.
    Days with fewer than `min_bars` bars or no previous range are skipped (as in the backtests)."""
    by_day = {d: g for d, g in rth.groupby(rth.index.tz_localize(None).normalize())}
    for day, r in levels.iterrows():
        dm = by_day.get(day)
        if dm is None or len(dm) < min_bars or not (r.PDH - r.PDL) > 0:
            continue
        eng = DayEngine(dm.index[0].to_pydatetime().replace(hour=0, minute=0), levels_from_row(r), exits=exits or {})
        ev: list[Event] = []
        for b in bars_of(dm):
            ev += eng.on_bar(b)
        ev += eng.close_day()
        yield eng, ev


def trades_frame(engines: list[DayEngine]) -> pd.DataFrame:
    rows = [dict(day=pd.Timestamp(e.day.date()), strategy=t.strategy, side=t.side, signal_time=t.signal_time,
                 entry_time=t.entry_time, entry=t.entry, stop=t.stop, target=t.target if t.target is not None else np.nan,
                 exit_time=t.exit_time, exit=t.exit, exit_reason=t.exit_reason, pnl_usd=t.pnl_usd,
                 r_multiple=t.r_multiple, taken=t.taken, skip_reason=t.skip_reason)
            for e in engines for t in e.trades]
    return pd.DataFrame(rows)
