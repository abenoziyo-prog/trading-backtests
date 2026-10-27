"""Intraday option capture (bots/option_flow_capture.py) and the flow analysis built on it (quant.live.flow)."""
import gzip
import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from quant.live import flow
from quant.live.schwab_feed import ReadOnlyClient

NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]


def _capture_module():
    spec = importlib.util.spec_from_file_location("option_flow_capture", ROOT / "bots/option_flow_capture.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def chain(spot, side, vol, last, bid, ask, iv, trade_ms, oi=1000):
    key = "callExpDateMap" if side == "CALL" else "putExpDateMap"
    o = dict(symbol="QQQ   261027C00720000" if side == "CALL" else "QQQ   261027P00720000", expirationDate="2026-10-27T20:00:00.000+00:00",
             daysToExpiration=0, strikePrice=720.0, bid=bid, ask=ask, last=last, mark=(bid + ask) / 2, bidSize=10, askSize=10,
             lastSize=1, totalVolume=vol, openInterest=oi, volatility=iv, delta=0.5 if side == "CALL" else -0.5, gamma=0.05,
             theta=-1, vega=0.1, quoteTimeInLong=trade_ms, tradeTimeInLong=trade_ms, multiplier=100)
    return {"underlyingPrice": spot, "underlying": {"bid": spot - 0.01, "ask": spot + 0.01, "totalVolume": 1},
            key: {"2026-10-27:0": {"720.0": [o]}}}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_next_mark():
    m = _capture_module()
    assert m.next_mark(datetime(2026, 10, 27, 9, 0, tzinfo=NY)) == datetime(2026, 10, 27, 9, 30, tzinfo=NY)
    assert m.next_mark(datetime(2026, 10, 27, 10, 0, tzinfo=NY)) == datetime(2026, 10, 27, 10, 0, tzinfo=NY)
    assert m.next_mark(datetime(2026, 10, 27, 10, 0, 1, tzinfo=NY)) == datetime(2026, 10, 27, 10, 5, tzinfo=NY)
    assert m.next_mark(datetime(2026, 10, 27, 10, 3, 30, tzinfo=NY)) == datetime(2026, 10, 27, 10, 5, tzinfo=NY)


def test_capture_through_read_only_client(tmp_path, monkeypatch):
    m = _capture_module()
    monkeypatch.setattr(m, "OUT", tmp_path)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    calls = []

    class Fake:
        def get_option_chain(self, symbol, contract_type, **kw):
            calls.append((symbol, contract_type, kw["from_date"], kw["to_date"]))
            side = "CALL" if "CALL" in str(contract_type) else "PUT"
            return FakeResponse(chain(720.5, side, 100, 1.2, 1.1, 1.2, 25.0, 1))

        def get_price_history_every_minute(self, symbol, start_datetime, end_datetime, **kw):
            calls.append((symbol, "bars"))
            ms = int(datetime(2026, 10, 27, 10, 3, tzinfo=NY).timestamp() * 1000)
            return FakeResponse({"candles": [dict(datetime=ms + 60000 * k, open=17, high=17.2, low=16.9, close=17.1) for k in range(3)]})

        def place_order(self, *a):
            raise AssertionError("never")

    monkeypatch.setattr(m, "VIX_OUT", tmp_path / "vix")
    c = ReadOnlyClient(Fake(), extra=ReadOnlyClient.OPTION_CHAIN | ReadOnlyClient.PRICE_HISTORY)
    with pytest.raises(PermissionError):
        c.place_order
    log = m.capture_all(c, ["QQQ", "$SPX"], datetime(2026, 10, 27, 10, 5, tzinfo=NY), vix=["$VIX"])
    assert [r["contracts"] for r in log] == [2, 2, 3] and len(calls) == 5
    v = pd.read_csv(tmp_path / "vix/2026-10-27/VIX.csv")
    assert len(v) == 2 and v["time"].iloc[-1].startswith("2026-10-27T10:04")   # the 10:05 minute is not complete yet
    rows = pd.read_csv(gzip.open(tmp_path / "QQQ/2026-10-27/1005.csv.gz"))
    assert list(rows.columns) == m.FIELDS and set(rows.put_call) == {"CALL", "PUT"} and rows.occ[0] == "QQQ261027C00720000"
    assert (tmp_path / "SPX_IDX/2026-10-27/1005.csv.gz").exists() and len(pd.read_csv(tmp_path / "capture_log.csv")) == 3


def write_captures(root, m, snaps):
    for ts, spot, call, put in snaps:
        rows = m.rows_of(chain(spot, "CALL", *call), ts.isoformat(), "QQQ") + m.rows_of(chain(spot, "PUT", *put), ts.isoformat(), "QQQ")
        d = root / "QQQ" / ts.date().isoformat()
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=m.FIELDS).to_csv(d / f"{ts:%H%M}.csv.gz", index=False)


def test_flow_intervals_and_side_proxies(tmp_path):
    m = _capture_module()
    t = [datetime(2026, 10, 27, 10, k, tzinfo=NY) for k in (0, 5, 10)]
    #                 vol, last, bid, ask, iv, trade_ms
    write_captures(tmp_path, m, [
        (t[0], 720.0, (100, 1.20, 1.10, 1.20, 25.0, 1), (50, 1.00, 1.00, 1.10, 25.0, 1)),
        (t[1], 721.0, (160, 1.70, 1.60, 1.70, 26.0, 2), (50, 1.00, 0.90, 1.00, 25.0, 1)),   # 60 calls at the ask, IV up
        (t[2], 720.0, (160, 1.70, 1.10, 1.20, 26.0, 2), (80, 1.00, 1.00, 1.10, 24.0, 3))])  # 30 puts at the bid, IV down
    d = flow.load_day(tmp_path, "QQQ", "2026-10-27")
    f = flow.contract_flow(d)
    c5 = f[(f["asof"] == pd.Timestamp(t[1])) & (f.put_call == "CALL")].iloc[0]
    assert c5.traded == 60 and c5.side_tick == 1 and c5.side_iv == 1 and c5.delta_usd == 60 * 0.5 * 100 * 721.0
    p10 = f[(f["asof"] == pd.Timestamp(t[2])) & (f.put_call == "PUT")].iloc[0]
    assert p10.traded == 30 and p10.side_tick == -1 and p10.side_iv == -1
    iv = flow.intervals(d)
    assert list(iv.call_traded) == [100, 60, 0] and list(iv.put_traded) == [50, 0, 30]
    assert iv.delta_usd_tick.iloc[1] > 0 and iv.delta_usd_tick.iloc[2] > 0   # calls bought, puts sold: both add customer delta
    assert iv.dte0_share.iloc[1] == 1 and iv.net_gex.notna().all()
    assert iv.dealer_hedge_usd.iloc[1] == pytest.approx(-iv.net_gex.iloc[0] * iv.ret_pct.iloc[1])


def test_oi_change_opening_ratio(tmp_path):
    m = _capture_module()
    write_captures(tmp_path, m, [(datetime(2026, 10, 27, 15, 55, tzinfo=NY), 720.0, (400, 1, 1, 1.1, 25, 1), (200, 1, 1, 1.1, 25, 1))])
    a = flow.load_day(tmp_path, "QQQ", "2026-10-27")
    b = a.assign(open_interest=[1300, 900], asof=a["asof"] + pd.Timedelta(days=1))
    oi = flow.oi_change(a, b)
    assert oi.loc["QQQ261027C00720000", "opening_ratio"] == pytest.approx(300 / 400)
    assert oi.loc["QQQ261027P00720000", "opening_ratio"] == pytest.approx(-100 / 200)
