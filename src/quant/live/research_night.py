"""Overnight helpers shared by the research scripts (scripts/overnight_strategies.py), the strategy lab and the bot's
shadow book. n = one night dict: rel (minutes after 18:00), t (bar times), o/h/l/c arrays, R0, day."""
from __future__ import annotations

import numpy as np
import pandas as pd

COST, DPP = 1.0, 20.0
FLAT = 929  # 09:29 ET in minutes after 18:00


def candles(n: dict, a: int, b: int, tf: int) -> dict | None:
    i0, i1 = np.searchsorted(n["rel"], a), np.searchsorted(n["rel"], b)
    if i1 - i0 < 2:
        return None
    key = (n["rel"][i0:i1] - a) // tf
    st = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    en = np.r_[st[1:], len(key)] - 1
    return dict(h=np.maximum.reduceat(n["h"][i0:i1], st), l=np.minimum.reduceat(n["l"][i0:i1], st),
                c=n["c"][i0 + en], o=n["o"][i0 + st], last=i0 + en, i0=i0)


def exit_trade(n: dict, pos: int, side: int, stop: float | None, target: float | None, end: int) -> tuple[int, float, str]:
    j_end = np.searchsorted(n["rel"], end) - 1
    if j_end < pos:
        return pos, n["o"][pos], "time"
    h, l = n["h"][pos:j_end + 1], n["l"][pos:j_end + 1]
    s_hit = np.zeros(len(h), bool) if stop is None else ((l <= stop) if side == 1 else (h >= stop))
    t_hit = np.zeros(len(h), bool) if target is None else ((h >= target) if side == 1 else (l <= target))
    i_s = int(np.argmax(s_hit)) if s_hit.any() else None
    i_t = int(np.argmax(t_hit)) if t_hit.any() else None
    if i_s is not None and (i_t is None or i_s <= i_t):
        return pos + i_s, stop, "stop"
    if i_t is not None:
        return pos + i_t, target, "target"
    return j_end, n["c"][j_end], "time"


def record(rows, n, key, side, pos, entry, stop, target, end):
    j, px, why = exit_trade(n, pos, side, stop, target, end)
    pts = side * (px - entry) - COST
    risk = abs(entry - stop) if stop is not None else 0.25 * n["R0"]
    rows.append(dict(**key, day=n["day"], side=side, entry_time=n["t"][pos], exit_time=n["t"][j] + pd.Timedelta(minutes=1),
                     entry=entry, exit=px, exit_reason=why, pnl_usd=pts * DPP, r_multiple=pts / risk))
    return j


def walk(n, cd, sig_long, sig_short, make, deadline, rows, key):
    busy = -1
    for i in np.flatnonzero(sig_long | sig_short):
        last = cd["last"][i]
        if last <= busy:
            continue
        if n["rel"][last] + 1 > deadline:
            break
        pos = last + 1
        if pos >= len(n["c"]) or n["rel"][pos] >= FLAT:
            break
        side = 1 if sig_long[i] else -1
        st = make(i, side)
        if st is None:
            continue
        stop, target = st
        entry = cd["c"][i]
        if stop is not None and (entry - stop) * side <= 0 or target is not None and (target - entry) * side <= 0:
            continue
        busy = record(rows, n, key, side, pos, entry, stop, target, FLAT)
