import numpy as np
import pandas as pd
import pytest

from quant import level_strategies as ls
from quant import sessions as s

INST = s.Instrument("T", "x", "futures", "18:00")


def _minutes(highs, lows, start="2021-01-05 10:00"):
    idx = pd.date_range(start, periods=len(highs), freq="1min", tz=s.TZ)
    close = (np.array(highs) + np.array(lows)) / 2
    return pd.DataFrame({"open": close, "high": highs, "low": lows, "close": close}, index=idx)


def _sig(entry_time, direction, entry=100.0, stop=99.0, target=102.0, touch=None):
    return pd.DataFrame([dict(strategy="T", level="PDH", direction=direction, entry_time=entry_time, entry=entry,
                              stop=stop, target=target, risk_pts=abs(entry - stop), vix_prev=30.0, atr=4.0,
                              test_day=pd.Timestamp("2021-01-05"), touch_bar=entry_time - pd.Timedelta(minutes=15))])


def test_long_hits_target_and_pays_costs():
    m = _minutes([100.5, 101, 102.5, 101], [99.5, 99.5, 100, 100])
    tr = ls.simulate(_sig(m.index[0], 1), m, INST, cost_pts=0.5).iloc[0]
    assert tr.exit_reason == "target" and tr.exit == 102.0
    assert tr.pnl_pts == pytest.approx(2.0 - 0.5)
    assert tr.r_multiple == pytest.approx(1.5)


def test_stop_and_target_in_same_minute_counts_as_stop():
    m = _minutes([102.5, 100], [98.5, 100])
    tr = ls.simulate(_sig(m.index[0], 1), m, INST, cost_pts=0).iloc[0]
    assert tr.exit_reason == "stop" and tr.pnl_pts == -1.0


def test_short_stop_and_end_of_day_exit():
    m = _minutes([100.4, 101.2, 100], [99.6, 99.8, 99.5])
    short = _sig(m.index[0], -1, stop=101.0, target=98.0)
    assert ls.simulate(short, m, INST, cost_pts=0).iloc[0].exit_reason == "stop"
    calm = _minutes([100.4, 100.3, 100.2], [99.6, 99.7, 99.8])
    tr = ls.simulate(short, calm, INST, cost_pts=0).iloc[0]
    assert tr.exit_reason == "end of day" and tr.exit == calm.close.iloc[-1]


def test_entry_uses_only_minutes_after_signal_and_no_overlap():
    m = _minutes([100.5] * 10, [99.5] * 10)
    m.iloc[0, m.columns.get_loc("high")] = 105  # spike in the signal minute itself must be ignored
    sig = _sig(m.index[1], 1)
    tr = ls.simulate(sig, m, INST, cost_pts=0)
    assert tr.iloc[0].exit_reason == "end of day"
    both = pd.concat([sig, _sig(m.index[3], 1)], ignore_index=True)
    assert len(ls.simulate(both, m, INST, cost_pts=0)) == 1  # second signal skipped: position still open


def test_stats():
    tr = pd.DataFrame({"pnl_pts": [2.0, -1.0, 3.0, -1.0], "r_multiple": [2, -1, 3, -1], "exit_reason": ["t", "s", "t", "s"],
                       "minutes_held": [10, 20, 30, 40],
                       "entry_time": pd.date_range("2021-01-01", periods=4, freq="D", tz=s.TZ),
                       "exit_time": pd.date_range("2021-01-01 01:00", periods=4, freq="D", tz=s.TZ)})
    st = ls.stats(tr, dollars_per_point=1)
    assert st["trades"] == 4 and st["win_rate"] == 0.5 and st["profit_factor"] == 2.5
    assert st["net_pnl"] == 3.0 and st["max_drawdown"] == -1.0 and st["avg_minutes"] == 25


def test_signal_rules_for_strong_bar_and_rejection_zone():
    bars = pd.DataFrame({"start": pd.date_range("2021-01-05 09:30", periods=25, freq="15min", tz=s.TZ),
                         "high": 101.0, "low": 100.0})
    t = bars.start.iloc[-1]
    base = dict(tf="15m", level="PDH", side=1, vix_prev=15.0, atr=10.0, touch_bar=t, entry_time=t + pd.Timedelta(minutes=15), test_day=None)
    ev = pd.DataFrame([dict(base, reaction="accepted", bar_high=103.0, bar_low=100.0, entry=102.9),   # strong: range 3 vs avg 1, top of bar
                       dict(base, reaction="accepted", bar_high=101.2, bar_low=100.0, entry=101.1)])  # weak range
    sig = ls.signals(ls.STRATEGIES[2], ev, bars)
    assert len(sig) == 1 and sig.iloc[0].direction == 1 and sig.iloc[0].stop == 100.0
    assert sig.iloc[0].target == pytest.approx(102.9 + 2 * 2.9)
    rej = pd.DataFrame([dict(base, vix_prev=25.0, reaction="rejected", bar_high=103.0, bar_low=100.0, entry=100.5),   # bottom half
                        dict(base, vix_prev=25.0, reaction="rejected", bar_high=103.0, bar_low=100.0, entry=102.5)])  # top half
    s4 = ls.signals(ls.STRATEGIES[3], rej, bars)
    assert len(s4) == 1 and s4.iloc[0].direction == -1 and s4.iloc[0].stop == pytest.approx(103.5)


def _s6_bars():
    # two trading days of 5m bars; PDH = 102
    t1 = pd.date_range("2021-01-05 09:30", periods=6, freq="5min", tz=s.TZ)
    t2 = pd.date_range("2021-01-06 09:30", periods=2, freq="5min", tz=s.TZ)
    idx = t1.append(t2)
    bars = pd.DataFrame({"start": idx, "close_time": idx + pd.Timedelta(minutes=5),
                         "open":  [100, 101, 101, 101, 101, 101, 101, 101],
                         "high":  [101, 103, 101.5, 102.5, 104, 101, 104, 101],
                         "low":   [99, 100.5, 100, 100, 101, 100, 100, 100],
                         "close": [100.5, 101, 101, 102.5, 103.5, 100.5, 103.8, 100]})
    return bars


def _s6_event(bars, **kw):
    base = dict(tf="5m", level="PDH", reaction="rejected", price=102.0, bar_high=103.0, bar_low=100.5, entry=101.0,
                atr=10.0, vix_prev=22.0, touch_bar=bars.start.iloc[1], test_day=pd.Timestamp("2021-01-05"))
    base.update(kw)
    return pd.DataFrame([base])


def test_s6_triggers_on_later_close_above_rejection_high():
    bars = _s6_bars()
    sig = ls.rejection_breakout_signals(_s6_event(bars), bars, "5m")
    r = sig.iloc[0]
    # bar 3 closes 102.5 (< 103, no trigger); bar 4 closes 103.5 > rejection high 103 -> trigger
    assert r.entry_time == bars.close_time.iloc[4] and r.entry == 103.5
    assert r.stop == pytest.approx(102 - 1.0) and r.target == pytest.approx(103.5 + 2 * 2.5)


def test_s6_ignores_next_day_and_top_half_rejections():
    bars = _s6_bars().drop(index=4).reset_index(drop=True)  # no same-day trigger; next-day bar closes 103.8
    assert ls.rejection_breakout_signals(_s6_event(bars), bars, "5m").empty
    bars = _s6_bars()
    top_half = _s6_event(bars, entry=102.5)  # closed in the top half of the rejection bar -> not a setup
    assert ls.rejection_breakout_signals(top_half, bars, "5m").empty
