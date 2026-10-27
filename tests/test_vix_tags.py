"""VIX state at entry (quant.live.vix_tags): known-at-entry values and the pre-registered buckets."""
import numpy as np
import pandas as pd
import pytest

from quant.live import vix_tags as V

NY = "America/New_York"


def minutes(start, n, values):
    return pd.Series(values, index=pd.date_range(start, periods=n, freq="1min", tz=NY))


def test_features_use_only_what_is_known_at_entry():
    daily = pd.Series([18.0, 22.0], index=pd.to_datetime(["2026-10-26", "2026-10-27"]))
    rng = np.random.default_rng(0)
    nq_r = rng.normal(0, 1e-4, 120)
    nq = minutes("2026-10-27 09:30", 120, 29700 * np.cumprod(1 + nq_r))
    vix = minutes("2026-10-27 09:30", 120, 19 - 300 * np.cumsum(nq_r))      # VIX moves against price
    vix3m = minutes("2026-10-27 09:30", 120, np.full(120, 20.0))
    e = pd.Series(["2026-10-27T11:20:00-04:00", "2026-10-27T18:00:00-04:00", "2026-10-27T09:35:00-04:00"])
    f = V.features(e, daily, vix, nq, vix3m)
    assert f.vix_prev[0] == 18.0 and f.vix_level[0] == "15-20"            # today's close is not known at 11:20
    assert f.vix_prev[1] == 22.0 and f.vix_level[1] == "20-25"            # but it is at 18:00
    assert f.vix_now[0] == pytest.approx(vix.iloc[110], abs=1e-3) and f.vix_chg_pct[0] == pytest.approx(100 * (vix.iloc[110] / 18 - 1), abs=1e-3)
    assert f.spot_vol_corr[0] < -0.9 and f.coupling[0] == "coupled (< -0.6)"
    assert f.term[0] == ("backwardation" if vix.iloc[110] >= 20 else "contango")
    assert np.isnan(f.spot_vol_corr[2]) and f.coupling[2] == ""          # only 5 minutes of history at 09:35
    assert np.isnan(f.vix_now[1]) and f.vix_move[1] == ""                # no VIX print within 30 minutes of 18:00


def test_buckets():
    assert [V.level_bucket(x) for x in (12, 15, 19.99, 20, 25, np.nan)] == ["< 15", "15-20", "15-20", "20-25", ">= 25", ""]
    assert [V.move_bucket(x) for x in (-6, 0, 6)] == ["down > 5%", "flat (±5%)", "up > 5%"]
    assert [V.coupling_bucket(x) for x in (-0.8, -0.4, 0.1)] == ["coupled (< -0.6)", "loose", "decoupled (>= -0.2)"]


def test_daily_closes_from_minutes():
    m = minutes("2026-10-27 16:10", 10, np.arange(10.0))
    assert V.daily_closes(m).iloc[0] == 5.0                                # 16:15 bar, not the 16:19 one
