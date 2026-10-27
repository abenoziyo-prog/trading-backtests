"""US book with targets at one expected move. PRE-REGISTERED 2026-10-27, before any result of this rule was run.

Every US trade keeps its signal and its own stop (L2 keeps its live 60-point cap, breakeven and trail); its target
becomes entry +/- 1.0 x the day's expected move (EM = yesterday's VIX / 16 x the 09:30 open, lab/expected_move.py).
A replacement of the whole US book's exits (gate G4 replace).
"""
from dataclasses import replace

from quant.live.engine import ExitPolicy

from lab.expected_move import US, l2_live, ready, us_trades  # noqa: F401

NAME = "us_em_target"
SESSION = "us"
REPLACES = US
HYPOTHESIS = ("A move of one expected move is what the day typically has in it: targeting it takes profit where the "
              "day's range is likely to end instead of at levels unrelated to the day's volatility.")
PARAMS = dict(target_em=1.0)
NEIGHBORS = [dict(target_em=0.8), dict(target_em=1.2), dict(target_em=1.5)]


def trades(ctx, target_em=1.0):
    def rules(em):
        r = {s: ExitPolicy(target_pts=target_em * em) for s in ("GF-1", "ORB-C", "S3")}
        r["L2"] = replace(l2_live(), target_pts=target_em * em)
        return r
    return us_trades(ctx, rules)
