import numpy as np
import pandas as pd
import pytest

from quant import reactions as rx
from quant import sessions as s

INST = s.Instrument("T", "x", "futures", "18:00")


def _minutes(days: int, seed: int = 0, vol: float = 0.0006) -> pd.DataFrame:
    """Random-walk 1-minute bars on a 23h futures schedule (18:00 -> 17:00 New York)."""
    rng = np.random.default_rng(seed)
    stamps = []
    for d in pd.bdate_range("2021-01-04", periods=days):
        start = (d - pd.Timedelta(days=1)).replace(hour=18)
        stamps.append(pd.date_range(start, periods=23 * 60, freq="1min"))
    idx = pd.DatetimeIndex(np.concatenate(stamps)).tz_localize(s.TZ)
    steps = rng.normal(0, vol, (len(idx), 4))
    close = 100 * np.exp(np.cumsum(steps[:, 0]))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + np.abs(steps[:, 1]) / 3)
    lo = np.minimum(open_, close) * (1 - np.abs(steps[:, 2]) / 3)
    return pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close}, index=idx)


def test_classification_and_entry_after_bar_close():
    idx = pd.date_range("2021-01-05 09:30", periods=8, freq="15min", tz=s.TZ)
    bars = pd.DataFrame({"start": idx, "end": idx + pd.Timedelta(minutes=14), "close_time": idx + pd.Timedelta(minutes=15),
                         "open": [100, 100, 100, 104, 100, 100, 100, 100], "high": [101, 101, 103, 105, 101, 101, 101, 101],
                         "low": [99] * 8, "close": [100, 100, 101, 104, 100, 100, 100, 100]})
    lv = pd.DataFrame([dict(level="PDH", side=1, price=102.0, ref_day=None, test_day=None, win_start=idx[0], win_end=idx[-1], atr=4.0)])
    ev = rx.detect_events(lv, bars, "15m").iloc[0]
    assert ev.touch_bar == idx[2] and ev.reaction == "rejected"  # high 103 > 102, closed 101 back inside
    assert ev.entry_time == idx[2] + pd.Timedelta(minutes=15) and ev.entry == 101
    accepted = bars.astype({"close": float})
    accepted.loc[2, "close"] = 102.5  # same bar, but it closes above the 102 level
    assert rx.detect_events(lv, accepted, "15m").iloc[0].reaction == "accepted"
    lv.loc[0, "price"] = 103.5
    assert rx.detect_events(lv, bars, "15m").iloc[0].reaction == "gap"  # bar 3 opens 104 above 103.5


def test_race_direction_for_high_and_low():
    idx = pd.date_range("2021-01-05 10:00", periods=6, freq="1min", tz=s.TZ)
    m = pd.DataFrame({"open": 100.0, "high": [100, 100, 100.6, 100, 100, 100], "low": [100, 99.4, 100, 100, 100, 100],
                      "close": 100.0}, index=idx)
    base = dict(entry_time=idx[0], touch_bar=idx[0] - pd.Timedelta(minutes=15), entry=100.0, atr=2.0)
    ev = pd.DataFrame([dict(base, side=1), dict(base, side=-1)])
    out = rx.add_outcomes(ev, m, INST, ks=(0.25,))
    # threshold 0.5: price drops to 99.4 (minute 1) before rising to 100.6 (minute 2)
    assert out["race_0.25"].tolist() == [1.0, 0.0]  # high: drop = reversal; low: drop = continuation


def test_no_edge_on_random_walk():
    m = _minutes(260, seed=7)
    daily = rx.daily_table(INST, m)
    lv = rx.reference_levels(INST, m, daily, with_sessions=False)
    rates = []
    for tf, minutes in rx.TFS.items():
        ev = rx.add_outcomes(rx.detect_events(lv, rx.tf_bars(INST, m, minutes), tf), m, INST)
        r = ev["race_0.25"].dropna()
        assert len(r) > 150
        rates.append(r.mean())
    # a coin flip: every timeframe within a few points of 50%
    assert all(abs(x - 0.5) < 0.07 for x in rates), rates


def test_reference_levels_are_known_before_window():
    m = _minutes(40, seed=1)
    daily = rx.daily_table(INST, m)
    lv = rx.reference_levels(INST, m, daily, with_sessions=True)
    pd_rows = lv[lv.level.isin(["PDH", "PDL"])]
    ends = daily["end"]
    assert (pd.DatetimeIndex(pd_rows.win_start) > pd.DatetimeIndex(pd_rows.ref_day.map(ends))).all()
    sess = lv[lv.level.str.startswith(("ASIA", "LON"))]
    hours = pd.DatetimeIndex(sess.win_start).hour
    assert set(hours) <= {0, 5}


def test_wilson_and_bh():
    lo, hi = rx.wilson(50, 100)
    assert lo < 0.5 < hi and round(hi - lo, 2) == 0.19
    q = rx.bh(pd.Series([0.01, 0.04, 0.03, 0.5]))
    assert q.round(3).tolist() == [0.04, 0.053, 0.053, 0.5]
