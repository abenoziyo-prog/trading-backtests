import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant import sessions as s

_spec = importlib.util.spec_from_file_location("ul", Path(__file__).resolve().parents[1] / "scripts" / "us_level_strategies.py")
ul = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ul)


def _c(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_first_signal_rules():
    c = _c([[100, 101, 99.5, 100.5], [100.5, 102.5, 100, 102.2], [102, 103, 101, 102.5]])
    assert ul.first_signal(c, 102.0, "close_above") == 1           # first candle through 102 closes above
    assert ul.first_signal(c, 102.4, "close_above") is None        # first through (bar 1) closes back below -> no long
    low = _c([[100, 100.5, 98.8, 99.4], [99.4, 100, 99, 99.8]])
    assert ul.first_signal(low, 99.0, "reclaim_low") == 0          # wicks below 99, closes above
    assert ul.first_signal(low, 99.6, "close_below") == 0          # first through 99.6 closes below
    gap = _c([[98, 98.5, 97, 97.5]])
    assert ul.first_signal(gap, 99.0, "close_below") is None       # opened beyond the level: no trade


def test_simulate_day_stop_first_and_close_exit():
    idx = pd.date_range("2026-03-02 10:00", periods=4, freq="1min", tz=s.TZ)
    dm = pd.DataFrame({"open": 100.0, "high": [100.5, 101.5, 100.2, 100.1], "low": [99.5, 98.5, 99.9, 99.9],
                       "close": [100.0, 100.0, 100.1, 100.05]}, index=idx)
    r = ul.simulate_day(dm, 0, 1, 100.0, 99.0, 101.0)            # minute 1 touches both -> stop
    assert r["exit_reason"] == "stop" and r["exit"] == 99.0
    r = ul.simulate_day(dm, 2, -1, 100.0, 101.0, None)           # no target: hold to the 16:00 (last) close
    assert r["exit_reason"] == "16:00 close" and r["exit"] == 100.05


def test_metrics():
    g = pd.DataFrame({"entry_time": pd.date_range("2026-01-02", periods=4, freq="D", tz=s.TZ),
                      "pnl_usd": [200.0, -100.0, 300.0, -100.0], "r_multiple": [2, -1, 3, -1],
                      "minutes_held": [10, 20, 30, 40], "exit_reason": ["target", "stop", "target", "stop"]})
    m = ul.metrics(g)
    assert m["trades"] == 4 and m["win_rate"] == 0.5 and m["profit_factor"] == 2.5
    assert m["max_drawdown"] == -100.0 and m["net_pnl"] == 300.0 and m["avg_minutes"] == 25


_spec2 = importlib.util.spec_from_file_location("ul2", Path(__file__).resolve().parents[1] / "scripts" / "us_level_strategies_v2.py")
ul2 = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ul2)


def test_v2_fresh_signals():
    # cross below: previous close at/above the level, this close below (gap candle allowed)
    assert ul2.fresh("cross_below", 100.0, 98.0, 98.5, 97.0, 97.5, 99.0)       # gap-open below, closes below
    assert not ul2.fresh("cross_below", 98.0, 98.0, 98.5, 97.0, 97.5, 99.0)    # already below: not a new cross
    assert ul2.fresh("cross_above", 99.0, 99.5, 101, 99.4, 100.5, 100.0)
    assert ul2.fresh("reclaim", 101.0, 101.0, 101.2, 99.5, 100.5, 100.0)        # wick below, close above
    assert ul2.fresh("reclaim", 99.0, 99.2, 100.8, 99.1, 100.5, 100.0)          # previous close below, close above
    assert not ul2.fresh("reclaim", 101.0, 101.0, 101.2, 100.2, 100.5, 100.0)   # never below


def test_v2_allows_multiple_trades_without_overlap():
    idx = pd.date_range("2026-03-02 09:30", periods=12, freq="1min", tz=s.TZ)
    #   level PDL = 100; R = 10 -> stop/target 1.0 for S1
    close = [101, 99.5, 98.4, 100.5, 99.6, 98.5, 101, 101, 101, 101, 101, 101]
    dm = pd.DataFrame({"open": [101, 101, 99.5, 98.4, 100.5, 99.6, 98.5, 101, 101, 101, 101, 101],
                       "high": np.maximum(close, [101, 101, 99.5, 98.4, 100.5, 99.6, 98.5, 101, 101, 101, 101, 101]) + 0.1,
                       "low": np.minimum(close, [101, 101, 99.5, 98.4, 100.5, 99.6, 98.5, 101, 101, 101, 101, 101]) - 0.1,
                       "close": close}, index=idx)
    r = pd.Series({"PDH": 110.0, "PDL": 100.0, "PDC": 102.0, "asia_low": np.nan, "london_high": np.nan})
    t = pd.DataFrame(ul2.day_trades("NSX", pd.Timestamp("2026-03-02"), r, dm, "1m", 1))
    s1 = t[t.strategy == "S1"]
    assert len(s1) == 2  # two separate crosses below 100, the second after the first trade closed
    assert (pd.DatetimeIndex(s1.entry_time[1:]).values >= pd.DatetimeIndex(s1.exit_time[:-1]).values).all()


_spec3 = importlib.util.spec_from_file_location("gs", Path(__file__).resolve().parents[1] / "scripts" / "us_gap_strategies.py")
gs = importlib.util.module_from_spec(_spec3)
_spec3.loader.exec_module(gs)


def test_gap_setups():
    # PDH 100, PDL 90, R 10; gap up open 103
    against = pd.Series({"open": 103.0, "high": 103.5, "low": 101.0, "close": 101.5})
    assert gs.setup(against, 103.0, 100.0, 90.0, 10.0) == ("G1", -1, 105.5, 100.0)      # fade to PDH, stop open+0.25R
    withg = pd.Series({"open": 103.0, "high": 104.5, "low": 102.8, "close": 104.0})
    assert gs.setup(withg, 103.0, 100.0, 90.0, 10.0) == ("G2", 1, 100.0, 105.5)         # continuation to open+0.25R
    filled = pd.Series({"open": 103.0, "high": 103.2, "low": 99.5, "close": 101.0})
    assert gs.setup(filled, 103.0, 100.0, 90.0, 10.0) is None                           # first candle already filled
    ran = pd.Series({"open": 103.0, "high": 106.0, "low": 102.9, "close": 105.8})
    assert gs.setup(ran, 103.0, 100.0, 90.0, 10.0) is None                              # already past the G2 target
    down = pd.Series({"open": 87.0, "high": 88.0, "low": 86.0, "close": 87.5})
    assert gs.setup(down, 87.0, 100.0, 90.0, 10.0) == ("G3", -1, 90.0, 84.5)            # gap-down continuation


_spec4 = importlib.util.spec_from_file_location("sp", Path(__file__).resolve().parents[1] / "scripts" / "strategy_picker.py")
sp = importlib.util.module_from_spec(_spec4)
_spec4.loader.exec_module(sp)


def _cand(rows):
    t = pd.DataFrame(rows, columns=["strategy", "entry", "exit", "r"])
    ts = lambda x: pd.to_datetime("2026-03-02 " + x).tz_localize(s.TZ)
    return pd.DataFrame({"strategy": t.strategy, "book": "short", "day": pd.Timestamp("2026-03-02"), "side": -1,
                         "entry_time": t.entry.map(ts), "exit_time": t.exit.map(ts), "pnl_usd": t.r * 100, "r_multiple": t.r})


def test_picker_one_position_and_priority():
    c = _cand([("S3", "09:31", "09:40", 1.0), ("GF-1", "09:31", "09:50", 1.0),   # same minute: GF-1 has priority
               ("ORB-C", "09:45", "10:00", 1.0),                                  # GF-1 still open -> skipped
               ("L2", "09:50", "10:30", -1.0)])                                   # starts as GF-1 exits -> taken
    p = sp.pick(c, gate=False).set_index("strategy")
    assert p.taken.to_dict() == {"GF-1": True, "S3": False, "ORB-C": False, "L2": True}
    assert p.loc["ORB-C", "skip_reason"] == "position open"


def test_picker_gate_uses_only_closed_trades():
    rows = [("S3", f"10:{2 * i:02d}", f"10:{2 * i + 1:02d}", -1.0) for i in range(3)] + [("S3", "11:00", "11:01", 1.0)]
    p = sp.pick(_cand(rows), gate=True, warmup=3)
    assert p.taken.tolist() == [True, True, True, False]                          # last 3 shadow trades sum to -3R
    assert p.skip_reason.iloc[-1] == "gated off"


_spec5 = importlib.util.spec_from_file_location("opt", Path(__file__).resolve().parents[1] / "scripts" / "optimize_picker.py")
opt = importlib.util.module_from_spec(_spec5)
_spec5.loader.exec_module(opt)


def test_fast_picker_matches_rules():
    c = _cand([("S3", "09:31", "09:40", 1.0), ("GF-1", "09:31", "09:50", 1.0), ("ORB-C", "09:45", "10:00", 1.0),
               ("L2", "09:50", "10:30", -1.0)]).rename(columns={"strategy": "cand"})
    arrs = opt.Arrays(c)
    taken = opt.run_picker(arrs, ["GF-1", "ORB-C", "S3", "L2"])        # concatenated in this order
    assert taken.tolist() == [True, False, False, True]                 # GF-1 wins the tie; ORB-C and S3 blocked
    net, n, days = opt.score(arrs, ["GF-1", "ORB-C", "S3", "L2"])
    assert (net, n, days) == (0.0, 2, 1)
