"""Expected-move context at entry (quant.live.em_tags): record-only tags for live and shadow trades."""
import numpy as np
import pandas as pd
import pytest

from quant.live import em_tags as E

NY = "America/New_York"


def session(day="2026-10-27", start="09:30", n=390, o=20000.0, step=1.0):
    idx = pd.date_range(f"{day} {start}", periods=n, freq="1min", tz=NY)
    c = o + step * np.arange(n)
    return pd.DataFrame(dict(open=np.r_[o, c[:-1]], high=c + 0.5, low=np.r_[o, c[:-1]] - 0.5, close=c), index=idx)


def test_us_trade_features():
    nq = session()                                                   # +1 pt a minute from 20,000
    vix = pd.Series([16.0], index=pd.to_datetime(["2026-10-26"]))   # EM = 1% of the 20,000 open = 200 pts
    t = pd.DataFrame([dict(entry_time="2026-10-27T11:10:00-04:00", side="long", entry=20090.0, target=20190.0,
                           pnl_usd=20 * 49.0, r_multiple=0.98)])     # risk = 49 / 0.98 = 50 pts
    f = E.features(t, vix, nq).iloc[0]
    assert f.em_pts == pytest.approx(200.0)
    assert f.em_used == pytest.approx((nq.high[nq.index < "2026-10-27 11:10-04:00"].max() - 19999.5) / 200, abs=1e-3)
    assert f.em_drift == pytest.approx(0.45) and f.em_drift_bucket == "neutral"
    assert f.stop_em == pytest.approx(0.25) and f.stop_bucket == "0.25-0.5"
    assert f.target_em == pytest.approx(0.5) and f.target_bucket == "0.5-1"
    assert f.day_range_em == pytest.approx((nq.high.max() - nq.low.min()) / 200, abs=1e-3)


def test_overnight_short_without_target_and_missing_vix():
    nq = session("2026-10-26", "18:00", 900, step=-0.5)
    vix = pd.Series([20.0], index=pd.to_datetime(["2026-10-26"]))  # the 26th's close is before the 18:00 session
    t = pd.DataFrame([dict(entry_time="2026-10-27T02:00:00-04:00", side="short", pnl_usd=-400.0, r_multiple=-1.0)])
    f = E.features(t, vix, nq).iloc[0]
    assert f.em_pts == pytest.approx(0.2 / 16 * 20000) and f.em_drift > 0       # short after a fall = with the move
    assert f.stop_em == pytest.approx(20 / f.em_pts) and f.target_bucket == ""   # shadow-style row: no target column
    g = E.features(t, pd.Series(dtype=float), nq).iloc[0]
    assert np.isnan(g.em_pts) and g.em_used_bucket == "" and g.stop_bucket == ""


def test_buckets_and_sessions():
    assert [E.used_bucket(x) for x in (0.2, 0.5, 1.0)] == ["< 0.5", "0.5-1", ">= 1 (move already done)"]
    assert [E.drift_bucket(x) for x in (-0.6, 0, 0.6)] == ["fading (< -0.5)", "neutral", "chasing (>= +0.5)"]
    assert E.target_bucket(None) == "none"
    a, b = E.session_bounds(pd.Timestamp("2026-10-27 03:00", tz=NY))
    assert a == pd.Timestamp("2026-10-26 18:00", tz=NY) and b == pd.Timestamp("2026-10-27 09:30", tz=NY)
