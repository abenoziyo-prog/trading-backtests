import numpy as np
import pandas as pd

from quant import sessions as s


def _bars(start, periods, tz=s.TZ, freq="1min"):
    idx = pd.date_range(start, periods=periods, freq=freq, tz=tz)
    base = np.arange(periods, dtype=float)
    return pd.DataFrame({"open": base, "high": base + 0.5, "low": base - 0.5, "close": base + 0.25}, index=idx)


def test_trading_day_futures_rolls_at_17():
    idx = pd.DatetimeIndex(["2025-03-02 18:00", "2025-03-02 20:00", "2025-03-03 16:59", "2025-03-03 17:00"], tz=s.TZ)
    days = s.trading_day(idx, "futures")
    assert [d.strftime("%a %m-%d") for d in days] == ["Mon 03-03", "Mon 03-03", "Mon 03-03", "Tue 03-04"]


def test_session_of_assigns_named_windows():
    idx = pd.DatetimeIndex(["2025-03-03 19:59", "2025-03-03 20:00", "2025-03-03 23:59", "2025-03-04 00:00",
                            "2025-03-04 02:00", "2025-03-04 04:59", "2025-03-04 09:30", "2025-03-04 10:59",
                            "2025-03-04 11:00", "2025-03-04 13:30", "2025-03-04 15:59", "2025-03-04 16:00"], tz=s.TZ)
    assert [None if pd.isna(v) else v for v in s.session_of(idx)] == [None, "asia", "asia", None, "london", "london", "ny_am", "ny_am",
                                           None, "ny_pm", "ny_pm", None]
    assert [s.session_length(n) for n in s.SESSIONS] == [240, 180, 90, 150]


def test_bar_start_anchors_to_origin_across_dst():
    idx = pd.DatetimeIndex(["2025-03-07 09:29", "2025-03-07 09:30", "2025-03-07 13:29", "2025-03-07 13:30",
                            "2025-03-10 09:45"], tz=s.TZ)  # DST starts 2025-03-09
    assert [t.strftime("%H:%M") for t in s.bar_start(idx, 240, "09:30")] == ["05:30", "09:30", "09:30", "13:30", "09:30"]
    assert [t.strftime("%H:%M") for t in s.bar_start(idx, 240, "18:00")] == ["06:00", "06:00", "10:00", "10:00", "06:00"]


def test_aggregate_ohlc_extreme_times_vwap_poc():
    df = _bars("2025-03-04 09:30", 4)
    df["volume"] = [100.0, 300.0, 100.0, 100.0]
    df["vwap"] = [10.00, 10.10, 10.20, 10.10]
    out = s.aggregate(df, [np.zeros(4)], ["k"], poc_bin=0.10).iloc[0]
    assert (out.open, out.high, out.low, out.close) == (0.0, 3.5, -0.5, 3.25)
    assert out.high_time == df.index[3] and out.low_time == df.index[0]
    assert out.vwap == np.average([10.0, 10.1, 10.2, 10.1], weights=[100, 300, 100, 100])
    assert out.poc == 10.1 and out.poc_volume == 400


def test_find_sweeps():
    df = _bars("2025-03-04 09:30", 10)
    df.iloc[7, df.columns.get_loc("high")] = 100  # takes out the earlier high at bar 7
    lv = s.aggregate(df.iloc[:5], [np.zeros(5)], ["k"])
    out = s.find_sweeps(lv, df).iloc[0]
    assert out.high_swept_at == df.index[5]  # bar 5 high 5.5 > period high 4.5
    assert pd.isna(out.low_swept_at)  # prices only rise afterwards


def test_session_levels_coverage_and_day():
    df = _bars("2025-03-03 20:00", 240)  # full Asia session for trading day 03-04
    lv = s.session_levels(s.Instrument("T", "x", "futures", "18:00"), df)
    row = lv.iloc[0]
    assert row.session == "asia" and row.trading_day == pd.Timestamp("2025-03-04") and row.coverage == 1.0
