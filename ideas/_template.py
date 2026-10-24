"""Template for a new idea: copy to ideas/<short_name>.py and fill in. Files starting with '_' are not run.

Rules every idea follows (approved): US session 09:30-16:00 ET (entries until 15:30, flat at 16:00) or the night
18:00-09:29 ET (flat by 09:29); multiple trades a day but one position at a time per idea; same-minute stop and
target = stop; 1 NQ contract, $20/pt, 1 pt round trip (subtract ctx.us.COST or ctx.night.COST), no slippage.
Use only information available at the time of each decision (no look-ahead).
"""
import pandas as pd

NAME = "template"
SESSION = "us"            # "us" or "overnight"
HYPOTHESIS = "What the idea trades and why it should work."
PARAMS = {}               # default parameters passed to trades()
NEIGHBORS = []            # nearby parameter sets, e.g. [{"tf": 3}, {"tf": 10}] (optional, enables gate G5)


def trades(ctx, **params) -> pd.DataFrame:
    """Return one row per trade: day, side (+1/-1), entry_time, exit_time, pnl_usd, r_multiple."""
    rows = []
    # for d in ctx.us_days: ...   (see lab/data.py for what ctx provides)
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"])
