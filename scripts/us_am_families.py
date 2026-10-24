"""New US-open strategy families (long and short), NSX last 18 months, appended to the optimizer's candidate library.

    python scripts/us_am_families.py      # writes reports/optimizer/library_am.parquet

US session 09:30-16:00 ET, entries until 15:30, flat at 16:00, same-minute stop+target = stop, 1 NQ contract,
$20/pt, 1 pt round trip, no slippage; R0 = previous US range.
  AMDRIVE   opening drive: after the first 5/15/30 minutes, trade in the direction of that move (short if it
            closed red, long if green), optionally only if it closed beyond the previous close (PDC).
            Stop: beyond the first-window extreme + 0.05 R0, or 0.15 / 0.30 R0. Exit: target 0.25 / 0.5 / 1.0 R0
            (else at the close), or no target with a time exit after 60 / 120 minutes or at the close. Once a day.
  AMFAIL    failed opening-range breakout: with a 5/15/30-minute opening range, a candle (1/2/3/5/10 min) that closes
            back inside after the previous candle closed above the range high -> short (mirror: below the low ->
            long). Stop: the session extreme since the range + 0.05 R0. Target: range midpoint / other side /
            1R / 2R. Multiple trades a day, one position at a time.
"""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("o", ROOT / "scripts" / "open_orb.py")
o = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o)
TFS = [1, 2, 3, 5, 10]


def exit_by(d: dict, pos: int, side: int, stop: float, target: float | None, minutes: int | None):
    """Like open_orb.exit_trade, but a time exit after `minutes` (close of the last bar before entry + minutes)."""
    if minutes is None:
        return o.exit_trade(d, pos, side, stop, target)
    end = np.searchsorted(d["mod"], d["mod"][pos] + minutes) - 1
    sub = dict(h=d["h"][:end + 1], l=d["l"][:end + 1], c=d["c"][:end + 1])
    j, px, why = o.exit_trade(sub, pos, side, stop, target)
    return j, px, ("time" if why == "16:00 close" else why)


def add(rows, d, cand, family, tf, side, pos, entry, stop, j, px):
    pts = side * (px - entry) - o.COST
    rows.append(dict(cand=cand, family=family, tf=tf, day=d["day"], side=side, entry_time=d["t"][pos],
                     exit_time=d["t"][j] + pd.Timedelta(minutes=1), pnl_usd=pts * o.DPP, r_multiple=pts / abs(entry - stop)))


def run_day(d: dict, pdc: float, rows: list) -> None:
    R0 = d["R0"]
    for n in (5, 15, 30):
        k = o.or_end(d, n)
        if k == 0 or k >= len(d["c"]):
            continue
        o0, cn = d["o"][0], d["c"][k - 1]
        hi, lo = d["h"][:k].max(), d["l"][:k].min()
        if cn == o0:
            continue
        side_dir = 1 if cn > o0 else -1
        # AMDRIVE
        for filt, dirn, sk, (tg, ex) in itertools.product(
                ("any", "beyond_pdc"), ("both", "long", "short"), ("extreme", 0.15, 0.30),
                [(0.25, None), (0.5, None), (1.0, None), (None, 60), (None, 120), (None, None)]):
            side = side_dir
            if dirn == "long" and side == -1 or dirn == "short" and side == 1:
                continue
            if filt == "beyond_pdc" and not (cn - pdc) * side > 0:
                continue
            stop = ((lo - 0.05 * R0) if side == 1 else (hi + 0.05 * R0)) if sk == "extreme" else cn - side * sk * R0
            if (cn - stop) * side <= 0:
                continue
            target = None if tg is None else cn + side * tg * R0
            j, px, _ = exit_by(d, k, side, stop, target, ex)
            add(rows, d, f"amdrive|first{n}m|{filt}|{dirn}|stop{sk}|tgt{tg}|exit{ex}|0m", "AMDRIVE", 0, side, k, cn, stop, j, px)
        # AMFAIL
        for tf in TFS:
            cd = o.candles_after(d, k, tf)
            if len(cd["c"]) < 2:
                continue
            prev_c = np.r_[d["c"][k - 1], cd["c"][:-1]]
            fail_up = (prev_c > hi) & (cd["c"] <= hi)       # broke above, back inside -> short
            fail_dn = (prev_c < lo) & (cd["c"] >= lo)       # broke below, back inside -> long
            run_hi = np.maximum(np.maximum.accumulate(cd["h"]), hi)
            run_lo = np.minimum(np.minimum.accumulate(cd["l"]), lo)
            for dirn, tg in itertools.product(("both", "long", "short"), ("mid", "opp", 1.0, 2.0)):
                sig = np.flatnonzero((fail_up if dirn != "long" else False) | (fail_dn if dirn != "short" else False))
                busy = -1
                for i in sig:
                    last = cd["last"][i]
                    if last <= busy:
                        continue
                    if d["mod"][last] + 1 > o.LAST_ENTRY:
                        break
                    pos = last + 1
                    if pos >= len(d["c"]):
                        break
                    side = -1 if fail_up[i] and dirn != "long" else 1
                    e = cd["c"][i]
                    stop = (run_hi[i] + 0.05 * R0) if side == -1 else (run_lo[i] - 0.05 * R0)
                    risk = (e - stop) * side
                    if risk <= 0:
                        continue
                    target = {"mid": (hi + lo) / 2, "opp": lo if side == -1 else hi}.get(tg, None)
                    if target is None:
                        target = e + side * tg * risk
                    if (target - e) * side <= 0:
                        continue
                    j, px, _ = o.exit_trade(d, pos, side, stop, target)
                    add(rows, d, f"amfail|OR{n}|{dirn}|tgt{tg}|{tf}m", "AMFAIL", tf, side, pos, e, stop, j, px)
                    busy = j


def main() -> None:
    days = o.load_days()
    lv = pd.read_csv(o.SRC / "daily_levels_NSX.csv", index_col=0, parse_dates=[0])
    rows: list = []
    for d in days:
        run_day(d, lv.loc[d["day"], "PDC"], rows)
    t = pd.DataFrame(rows)
    t = t[(t.day >= o.START) & (t.day <= o.END)]
    t.to_parquet(ROOT / "reports/optimizer/library_am.parquet")
    print(t.groupby("family").agg(candidates=("cand", "nunique"), trades=("cand", "size")))


if __name__ == "__main__":
    main()
