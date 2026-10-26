"""Lanto-derived variant found in the 2026-10-26 grid (NOT pre-registered: selected after seeing 3,456 settings).

The canonical framework (ideas/lanto_canonical.py) failed; every setting that held in both halves had the
higher-timeframe bias removed. This idea keeps that cluster's middle settings: the Opening Range Move alone sets the
direction (overnight range 18:00-08:00, confirmed within 30 minutes), the True Day Open (00:00) filter applies
(longs only below it, shorts only above), entry is a 1-minute FVG retest, target PDH/PDL, breakeven at +1R,
1 trade a day, entries until 11:00. Rules: scripts/lanto.py.
"""
import pandas as pd

NAME = "lanto_orm_tdo_fvg"
SESSION = "us"
HYPOTHESIS = ("Trade the direction of the New York Opening Range Move (sweep-and-reclaim or break-and-hold of the "
              "overnight range), only from the discount/premium side of the True Day Open, on a 1-minute FVG retest "
              "toward PDH/PDL. Lanto's higher-timeframe bias is dropped: in the grid it reduced performance.")
PARAMS = dict(bias="orm_only", orm_window=30, entry_tf=1, entry_type="A", min_gap_r=0.0, target="pdhl",
              breakeven=True, max_trades=1, tdo_filter=True, on_end="08:00")
NEIGHBORS = [dict(orm_window=15), dict(orm_window=45), dict(breakeven=False), dict(entry_tf=5), dict(min_gap_r=0.03),
             dict(target="3R"), dict(max_trades=2), dict(on_end="09:29")]


def trades(ctx, **params) -> pd.DataFrame:
    from lab.data import load_script
    lanto = load_script("lab_lanto", "lanto.py")
    t = lanto.run(lanto.Config(**params))
    return t[["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]]
