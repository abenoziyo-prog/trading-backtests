"""S3 limited to its first signal of the day. PRE-REGISTERED 2026-10-27, before any result of this rule was run.

Evidence for the idea: in the backtest, S3's first taken trade of each day made +$35.3K over 154 trades, while its
105 later re-entries made +$0.2K. On 2026-10-26 the two re-entries lost $3,430 live. A replacement, not an add-on:
gate G4 replace compares system R with this S3 against system R as it trades now.
"""
import pandas as pd

NAME = "s3_first_signal_only"
SESSION = "us"
REPLACES = "S3"
HYPOTHESIS = ("S3 (short a 1-minute close above the London high) only on its first signal of the day. Later signals "
              "the same day add risk but almost no profit: once the London high has failed to hold price, the "
              "level has lost its meaning.")
PARAMS = dict(max_per_day=1)
NEIGHBORS = [dict(max_per_day=2)]


def trades(ctx, max_per_day=1) -> pd.DataFrame:
    from lab import baselines
    s = baselines.us(ctx)
    s = s[s.cand == "S3"].sort_values("entry_time")
    return s.groupby("day").head(max_per_day)[["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]]
