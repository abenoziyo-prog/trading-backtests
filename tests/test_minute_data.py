import pandas as pd
import pytest

from quant import minute_data as md

NOW = pd.Timestamp("2026-10-16 18:00", tz="UTC")


def test_month_ranges_splits_on_month_boundaries():
    chunks = list(md.month_ranges(pd.Timestamp("2025-01-15"), pd.Timestamp("2025-03-10")))
    assert [(a.strftime("%m-%d"), b.strftime("%m-%d")) for a, b in chunks] == [
        ("01-15", "02-01"), ("02-01", "03-01"), ("03-01", "03-10"),
    ]


def test_rth_only_keeps_regular_session():
    idx = pd.DatetimeIndex(
        ["2025-03-03 09:29", "2025-03-03 09:30", "2025-03-03 15:59", "2025-03-03 16:00", "2025-03-01 10:00"],
        tz=md.TZ,
    )
    df = pd.DataFrame({"close": range(5)}, index=idx)
    assert list(md.rth_only(df)["close"]) == [1, 2]


# ----------------------------------------------------------------- cache

def _fake_fetch(calls):
    def fetch(a, b):
        calls.append(a.strftime("%Y-%m"))
        idx = pd.date_range(a, b, freq="1D", inclusive="left").tz_convert(md.TZ)
        return pd.DataFrame({c: 1.0 for c in md.COLUMNS}, index=idx)
    return fetch


def test_cache_reuses_complete_months_and_filters_start(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "MINUTE_DIR", tmp_path)
    start, end = pd.Timestamp("2025-01-15", tz="UTC"), pd.Timestamp("2025-03-10", tz="UTC")
    calls = []
    df = md._cached_months("test", "QQQ", start, end, _fake_fetch(calls), False, lambda _: None)
    assert calls == ["2025-01", "2025-02", "2025-03"]  # whole first month fetched
    assert df.index.min() >= start.tz_convert(md.TZ)

    calls.clear()
    md._cached_months("test", "QQQ", start, end, _fake_fetch(calls), False, lambda _: None)
    assert calls == []  # complete months and same-end partial month come from cache

    calls.clear()
    md._cached_months("test", "QQQ", start, pd.Timestamp("2025-03-20", tz="UTC"), _fake_fetch(calls), False, lambda _: None)
    assert calls == ["2025-03"]  # only the partial month is refreshed
    assert len(list((tmp_path / "test" / "QQQ").glob("2025-03_to_*.parquet"))) == 1


def test_cache_can_keep_same_timestamp_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "MINUTE_DIR", tmp_path)
    ts = pd.Timestamp("2025-02-03 17:00", tz=md.TZ)

    def fetch(a, b):
        return pd.DataFrame({"stat_type": [3, 9]}, index=[ts, ts])

    args = ("ev", "NQ", pd.Timestamp("2025-02-01", tz="UTC"), pd.Timestamp("2025-03-01", tz="UTC"), fetch, False, lambda _: None)
    assert len(md._cached_months(*args, dedupe=False)) == 2
    assert len(md._cached_months(*args)) == 1


# ----------------------------------------------------------------- Alpaca plan

def test_plan_alpaca_defaults_are_within_limits():
    plan = md.plan_alpaca("qqq", months=24, now=NOW)
    assert plan.symbol == "QQQ"
    assert plan.end == NOW - pd.Timedelta(minutes=16)  # outside the free-plan SIP window
    assert plan.start == pd.Timestamp("2024-10-01", tz="UTC")  # month-aligned
    assert plan.start >= md.ALPACA_EARLIEST
    assert {"volume", "trade_count", "vwap"} <= set(plan.fields)
    # ~25 months x ~21 days x 960 bars, 10k-bar pages -> ~2-3 requests a month
    assert 400_000 < plan.bars < 600_000
    assert 50 <= plan.requests <= 80
    assert plan.requests < md.ALPACA_CALLS_PER_MIN


def test_plan_alpaca_clamps_recent_end():
    plan = md.plan_alpaca("QQQ", start="2026-10-01", end=NOW, now=NOW)
    assert plan.end < NOW
    assert any("clamped" in n for n in plan.notes)
    iex = md.plan_alpaca("QQQ", start="2026-10-01", end=NOW, feed="iex", now=NOW)
    assert iex.end == NOW


@pytest.mark.parametrize("kwargs", [
    {"symbol": "^NDX"},
    {"symbol": "not a ticker"},
    {"symbol": "QQQ", "start": "2015-06-01"},
    {"symbol": "QQQ", "start": "2026-10-10", "end": "2026-10-01"},
    {"symbol": "QQQ", "feed": "otc"},
    {"symbol": "QQQ", "months": 0},
])
def test_plan_alpaca_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        md.plan_alpaca(now=NOW, **kwargs)


# ----------------------------------------------------------------- Databento plan

class FakeMetadata:
    def __init__(self):
        self.calls = []

    def get_dataset_range(self, dataset):
        assert dataset == "GLBX.MDP3"
        return {
            "start": "2010-06-06T00:00:00Z", "end": "2026-10-16T00:00:00Z",
            "schema": {"ohlcv-1m": {"start": "2010-06-06T00:00:00Z", "end": "2026-10-16T00:00:00Z"}},
        }

    def get_cost(self, **kw):
        self.calls.append(("cost", kw))
        assert kw["stype_in"] == "continuous" and kw["symbols"] == ["NQ.v.0"]
        return {"ohlcv-1m": 1.25, "statistics": 0.10, "trades": 40.0}[kw["schema"]]

    def get_record_count(self, **kw):
        self.calls.append(("count", kw))
        return {"ohlcv-1m": 500_000, "trades": 300_000_000}[kw["schema"]]


class FakeClient:
    def __init__(self):
        self.metadata = FakeMetadata()


def test_plan_databento_prices_each_schema_and_clamps_end():
    client = FakeClient()
    plan = md.plan_databento("nq", end="2027-01-01", with_trades=True, client=client)
    assert plan.symbol == "NQ.v.0"
    assert plan.end == pd.Timestamp("2026-10-16", tz="UTC")
    assert any("clamped" in n for n in plan.notes)
    assert plan.costs == {"ohlcv-1m": 1.25, "statistics": 0.10, "trades": 40.0}
    assert plan.total_cost == pytest.approx(41.35)
    assert {"trade_count", "buy_volume", "sell_volume"} <= set(plan.fields)
    # cost is priced on the same month-aligned window that will be downloaded
    assert all(kw["start"] == plan.start.isoformat() for kind, kw in client.metadata.calls)


def test_plan_databento_defaults_skip_trades():
    plan = md.plan_databento("NQ", client=FakeClient())
    assert set(plan.costs) == {"ohlcv-1m", "statistics"}
    assert "trade_count" not in plan.fields


@pytest.mark.parametrize("kwargs", [
    {"root": "NQ!"},
    {"root": "NQ", "roll": "weekly"},
    {"root": "NQ", "start": "2009-01-01"},
    {"root": "NQ", "months": 0},
])
def test_plan_databento_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        md.plan_databento(client=FakeClient(), **kwargs)


def test_databento_symbol():
    assert md.databento_symbol("nq") == "NQ.v.0"
    assert md.databento_symbol("ES", "calendar") == "ES.c.0"
    assert md.databento_symbol("MNQ", "open_interest") == "MNQ.n.0"


# ----------------------------------------------------------------- aggregation

def test_aggregate_trades_counts_and_aggressor_volume():
    ts = pd.DatetimeIndex(["2025-03-03 14:30:01", "2025-03-03 14:30:59", "2025-03-03 14:31:10"], tz="UTC")
    chunk1 = pd.DataFrame({"size": [2, 3], "side": ["B", "A"]}, index=ts[:2])
    chunk2 = pd.DataFrame({"size": [5], "side": ["N"]}, index=ts[2:])
    out = md.aggregate_trades([chunk1, chunk2])
    assert list(out["trade_count"]) == [2, 1]
    assert list(out["buy_volume"]) == [2, 0]
    assert list(out["sell_volume"]) == [3, 0]
    assert str(out.index.tz) == md.TZ


def test_pivot_statistics_daily_table():
    ref = pd.Timestamp("2025-03-03 00:00", tz=md.TZ).tz_convert("UTC")
    raw = pd.DataFrame({
        "stat_type": [3, 9, 6, 3, 7],
        "price": [21000.25, float("nan"), float("nan"), 20999.0, 1.0],
        "quantity": [0, 250_000, 400_000, 0, 0],
        "ts_ref": [ref] * 5,
        "update_action": [1, 1, 1, 2, 1],  # the delete and the unmapped stat (7) are dropped
    })
    table = md.pivot_statistics(raw)
    row = table.loc[pd.Timestamp("2025-03-03")]
    assert row["settlement_price"] == 21000.25
    assert row["open_interest"] == 250_000
    assert row["cleared_volume"] == 400_000


def test_missing_key_exits(monkeypatch):
    monkeypatch.setattr(md, "ROOT", md.ROOT / "does-not-exist")
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        md._env("DATABENTO_API_KEY")
