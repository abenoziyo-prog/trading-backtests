"""US book with expected-move brackets. PRE-REGISTERED 2026-10-27, before any result of this rule was run.

Every US trade (GF-1, ORB-C, S3, L2) keeps its signal but exits on a fixed bracket sized by the day's expected move
(EM = yesterday's VIX / 16 x the 09:30 open, lab/expected_move.py): stop 0.5 EM, target 1.0 EM (2:1). L2's live
breakeven/trailing rule is dropped (the bracket replaces it). A replacement of the whole US book's exits: gate G4
replace compares it with system R as it trades now.
"""

from quant.live.engine import ExitPolicy

from lab.expected_move import US, ready, us_trades  # noqa: F401  (ready: used by the shadow book)

NAME = "us_em_bracket"
SESSION = "us"
REPLACES = US
HYPOTHESIS = ("Sizing stops and targets by the options' expected move for the day (VIX/16) fits each day's volatility "
              "better than the strategies' own fixed or previous-day-range exits: stop 0.5 EM, target 1.0 EM.")
PARAMS = dict(stop_em=0.5, target_em=1.0)
NEIGHBORS = [dict(stop_em=0.4, target_em=0.8), dict(stop_em=0.6, target_em=1.2), dict(stop_em=0.5, target_em=0.8),
             dict(stop_em=0.5, target_em=1.2)]


def trades(ctx, stop_em=0.5, target_em=1.0):
    return us_trades(ctx, lambda em: {s: ExitPolicy(stop_pts=stop_em * em, target_pts=target_em * em) for s in US})
