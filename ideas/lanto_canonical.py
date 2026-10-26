"""Lanto Trades framework, canonical configuration — PRE-REGISTERED 2026-10-24 before any results were seen.

Rules: scripts/lanto.py (HTF Daily/4H/1H FVG-IFVG bias with 4H weighted x2, overnight range 18:00-08:00, True Day
Open 00:00, Opening Range Move within 30 minutes of 09:30 agreeing with the bias, 1-minute FVG-retest or IFVG entry
until 11:00 with minimum gap 0.03 x previous range, model stop beyond the gap - 1 pt, target PDH/PDL (2R if PDH/PDL
is less than 1R away), stop to breakeven at +1R, at most 2 trades a day, flat at 16:00).
"""
import pandas as pd

NAME = "lanto_canonical"
SESSION = "us"
HYPOTHESIS = ("Lanto Trades' framework (higher-timeframe FVG/IFVG bias, overnight range, NY Opening Range Move, "
              "1m FVG/IFVG entry to PDH/PDL) codified with the canonical settings fixed before testing.")
PARAMS = {}
NEIGHBORS = [dict(orm_window=15), dict(orm_window=45), dict(entry_tf=5), dict(entry_type="A"), dict(entry_type="B"),
             dict(target="2R"), dict(breakeven=False), dict(max_trades=1)]


def trades(ctx, **params) -> pd.DataFrame:
    from lab.data import load_script
    lanto = load_script("lab_lanto", "lanto.py")
    t = lanto.run(lanto.Config(**params))
    return t[["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]]
