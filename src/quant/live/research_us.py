"""US-session helpers shared by the research scripts (scripts/open_orb.py), the strategy lab and the bot's shadow
book, so all three run the same code. d = one session dict: t (bar times), mod (minute of day), o/h/l/c arrays."""
from __future__ import annotations

import numpy as np

COST, DPP = 1.0, 20.0
LAST_ENTRY = 15 * 60 + 30


def candles_after(d: dict, start_pos: int, tf: int) -> dict:
    """Candles of tf minutes by clock time, starting at the minute start_pos (the end of the opening
    range). Missing minutes do not stretch a candle; last = index of the candle's final minute."""
    mod = d["mod"][start_pos:]
    if len(mod) == 0:
        return dict(o=np.array([]), h=np.array([]), l=np.array([]), c=np.array([]), last=np.array([], dtype=int))
    key = (mod - mod[0]) // tf
    idx = np.arange(start_pos, len(d["c"]))
    starts = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    ends = np.r_[starts[1:], len(key)] - 1
    h = np.maximum.reduceat(d["h"][start_pos:], starts)
    l = np.minimum.reduceat(d["l"][start_pos:], starts)
    return dict(o=d["o"][idx[starts]], h=h, l=l, c=d["c"][idx[ends]], last=idx[ends])


def or_end(d: dict, or_min: int) -> int:
    """Index of the first minute at or after 09:30 + or_min (clock time)."""
    return int(np.searchsorted(d["mod"], 570 + or_min, side="left"))


def exit_trade(d: dict, pos: int, side: int, stop: float, target: float | None) -> tuple[int, float, str]:
    h, l, c = d["h"][pos:], d["l"][pos:], d["c"][pos:]
    s_hit = (l <= stop) if side == 1 else (h >= stop)
    t_hit = np.zeros(len(h), bool) if target is None else ((h >= target) if side == 1 else (l <= target))
    i_s = int(np.argmax(s_hit)) if s_hit.any() else None
    i_t = int(np.argmax(t_hit)) if t_hit.any() else None
    if i_s is not None and (i_t is None or i_s <= i_t):
        return pos + i_s, stop, "stop"
    if i_t is not None:
        return pos + i_t, target, "target"
    return len(d["c"]) - 1, d["c"][-1], "16:00 close"
