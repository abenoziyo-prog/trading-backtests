"""quant.vwap: session/weekly VWAP, standard-deviation bands, resampling and mapping (no look-ahead)."""
import numpy as np
import pandas as pd
import pytest

from quant import vwap as V

NY = "America/New_York"


def bars(day="2026-10-27", n=390, start="09:30", seed=0, vol=None):
    idx = pd.date_range(f"{day} {start}", periods=n, freq="1min", tz=NY)
    c = 100 + np.cumsum(np.random.default_rng(seed).normal(0, 0.1, n))
    return pd.DataFrame(dict(open=c, high=c + 0.05, low=c - 0.05, close=c, volume=vol if vol is not None else np.full(n, 1000.0)), index=idx)


def test_vwap_and_sd_match_the_definition():
    b = pd.concat([bars(start="09:00", n=420)])        # 30 pre-market minutes are dropped
    d = V.vwap_bands(b)
    assert d.index[0].strftime("%H:%M") == "09:30" and len(d) == 390
    p = ((b.high + b.low + b.close) / 3).loc[d.index]
    i = 100
    w = b.volume.loc[d.index][: i + 1]
    vw = (p[: i + 1] * w).sum() / w.sum()
    sd = np.sqrt((w * (p[: i + 1] - vw) ** 2).sum() / w.sum())
    assert d.vwap.iloc[i] == pytest.approx(vw) and d.sd.iloc[i] == pytest.approx(sd)
    assert d.z.iloc[i] == pytest.approx((d.close.iloc[i] - vw) / sd)


def test_source_vwap_column_is_used_and_anchors_reset():
    a, b = bars("2026-10-26", seed=1), bars("2026-10-27", seed=2)
    x = pd.concat([a, b]).assign(vwap=lambda f: f.close + 1)       # a per-minute VWAP column (Alpaca)
    s = V.vwap_bands(x, "session")
    assert s.vwap.loc["2026-10-27 09:30-04:00"] == pytest.approx(b.close.iloc[0] + 1)   # reset at the new session
    w = V.vwap_bands(x, "week")
    assert w.vwap.loc["2026-10-27 09:30-04:00"] != pytest.approx(b.close.iloc[0] + 1)   # same week: carried over


def test_resample_stamps_the_last_minute():
    d = V.vwap_bands(bars())
    r = V.resample(d, 5)
    assert r.index[0].strftime("%H:%M") == "09:34" and r.z.iloc[0] == d.z.iloc[4] and r.high.iloc[0] == d.high.iloc[:5].max()


def test_map_bands():
    d = V.vwap_bands(bars())
    m = V.map_bands(d, d.close * 40)
    assert np.allclose(m.vwap, d.vwap * 40) and np.allclose(m.sd, d.sd * 40)
