"""US book with stops capped at half the expected move. PRE-REGISTERED 2026-10-27, before any result of this rule was run.

Every US trade keeps its signal and its own target; its initial stop is capped at 0.5 x the day's expected move
(EM = yesterday's VIX / 16 x the 09:30 open, lab/expected_move.py). For L2 this replaces the fixed 60-point cap; its
breakeven at +75 and 50-point trail stay. A replacement of the whole US book's exits (gate G4 replace).
"""
from dataclasses import replace

from quant.live.engine import ExitPolicy

from lab.expected_move import US, l2_live, ready, us_trades  # noqa: F401

NAME = "us_em_stop_cap"
SESSION = "us"
REPLACES = US
HYPOTHESIS = ("A stop wider than half the day's expected move risks more than the day is likely to give back: capping "
              "every US stop at 0.5 EM cuts the large losers on quiet days without cutting the winners on busy ones.")
PARAMS = dict(cap_em=0.5)
NEIGHBORS = [dict(cap_em=0.4), dict(cap_em=0.6), dict(cap_em=0.75)]


def trades(ctx, cap_em=0.5):
    def rules(em):
        r = {s: ExitPolicy(max_loss_pts=cap_em * em) for s in ("GF-1", "ORB-C", "S3")}
        r["L2"] = replace(l2_live(), max_loss_pts=cap_em * em)
        return r
    return us_trades(ctx, rules)
