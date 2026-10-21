import numpy as np
import pandas as pd
import pytest

from quant import engine, metrics
from quant.params import parse_params
from quant.strategies import buy_and_hold, sma_cross
from quant.walkforward import walk_forward


@pytest.fixture
def prices() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2010-01-01", periods=2600)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(idx))))
    return pd.DataFrame({"close": close}, index=idx)


def test_buy_and_hold_matches_price_return(prices):
    res = engine.run(prices, buy_and_hold(prices), cost_bps=0, slippage_bps=0)
    # delay=1: the first bar's return is missed, so compare from bar 0's close.
    expected = prices["close"].iloc[-1] / prices["close"].iloc[0] - 1
    assert res.equity.iloc[-1] / 100_000 - 1 == pytest.approx(expected)


@pytest.mark.parametrize("delay", [1, 2, 5])
def test_position_lags_signal(prices, delay):
    signal = pd.Series(np.sign(np.sin(np.arange(len(prices)) / 20)), index=prices.index)
    res = engine.run(prices, signal, delay=delay)
    pd.testing.assert_series_equal(
        res.position.iloc[delay:], signal.iloc[:-delay].set_axis(prices.index[delay:]), check_names=False
    )
    assert (res.position.iloc[:delay] == 0).all()


def test_costs_equal_turnover_times_bps(prices):
    signal = pd.Series(0.0, index=prices.index)
    signal.iloc[10:20] = 1.0
    signal.iloc[20:30] = -1.0
    res = engine.run(prices, signal, cost_bps=3, slippage_bps=2)
    # 0->1 (1 unit), 1->-1 (2 units), -1->0 (1 unit) = 4 units at 5 bps
    assert res.costs.sum() == pytest.approx(4 * 5 / 1e4)


def test_delay_below_one_raises(prices):
    with pytest.raises(ValueError):
        engine.run(prices, buy_and_hold(prices), delay=0)


def test_lookahead_signal_earns_nothing_special(prices):
    # A signal built from *today's* return must not get to trade today's return.
    peek = np.sign(prices["close"].pct_change().fillna(0))
    res = engine.run(prices, peek, cost_bps=0, slippage_bps=0)
    assert res.stats()["sharpe"] < 3


def test_sma_cross_flat_during_warmup(prices):
    sig = sma_cross(prices, fast=20, slow=100)
    assert (sig.iloc[:99] == 0).all()
    assert set(sig.unique()) <= {0.0, 1.0}


def test_metrics_drawdown():
    r = pd.Series([0.1, -0.5, 0.2], index=pd.bdate_range("2020-01-01", periods=3))
    assert metrics.max_drawdown(r) == pytest.approx(-0.5)


def test_walk_forward_is_out_of_sample(prices):
    wf = walk_forward(prices, sma_cross, {"fast": [10, 20], "slow": [50, 100]}, train_years=3, test_years=1)
    assert wf.returns.index.min() >= prices.index[0] + pd.DateOffset(years=3)
    assert wf.returns.index.is_unique
    assert len(wf.windows) >= 5


def test_parse_params():
    assert parse_params(["fast=50", "slow=200.5", "allow_short=true", "name=x"]) == {
        "fast": 50, "slow": 200.5, "allow_short": True, "name": "x",
    }
