"""GEX snapshot: chain parsing, the gamma math, the read-only allowance and the runner's schedule."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quant.live.calendar import Calendar
from quant.live.gex import contracts, spot_of, summary
from quant.live.schwab_feed import ReadOnlyClient

NY = ZoneInfo("America/New_York")


def chain(spot=600.0, call_oi=None, put_oi=None):
    """A small QQQ-like chain: strikes 560-640, one expiry 10 days out, IV 20%."""
    def side(oi, pc):
        return {"2026-11-06:10": {f"{k:.1f}": [dict(putCall=pc, strikePrice=k, expirationDate="2026-11-06T21:00:00.000+00:00",
                                                      daysToExpiration=10, openInterest=oi.get(k, 0), totalVolume=5,
                                                      volatility=20.0, gamma=0.01, delta=0.5, bid=1, ask=1.1, mark=1.05,
                                                      multiplier=100.0)]
                                  for k in range(560, 641, 10)}}
    return dict(symbol="QQQ", underlyingPrice=spot, callExpDateMap=side(call_oi or {}, "CALL"), putExpDateMap=side(put_oi or {}, "PUT"))


def test_parse_and_spot():
    c = contracts(chain(call_oi={600: 1000}))
    assert len(c) == 18 and set(c.put_call) == {"CALL", "PUT"} and c.expiry.iloc[0] == "2026-11-06"
    assert spot_of(chain()) == 600.0


def test_signs_walls_and_flip():
    asof = datetime(2026, 10, 27, 9, 20, tzinfo=NY)
    # calls stacked above spot, puts stacked below: positive gamma at spot, negative far below -> flip below spot
    ch = chain(call_oi={600: 20000, 620: 50000}, put_oi={580: 30000, 560: 80000})
    s = summary(contracts(ch), 600.0, asof, "QQQ")
    assert s["net_gex"] > 0 and s["regime"] == "positive"
    assert s["call_wall"] == 620.0 or s["call_wall"] == 600.0
    assert s["put_wall"] in (560.0, 580.0)
    assert s["gamma_flip"] is not None and s["gamma_flip"] < 600 and s["spot_vs_flip_pct"] > 0
    # only puts: negative everywhere, no flip
    s2 = summary(contracts(chain(put_oi={600: 1000})), 600.0, asof, "QQQ")
    assert s2["net_gex"] < 0 and s2["regime"] == "negative" and s2["gamma_flip"] is None and s2["call_wall"] is None


def test_gex_scale():
    asof = datetime(2026, 10, 27, 9, 20, tzinfo=NY)
    s = summary(contracts(chain(call_oi={600: 1000})), 600.0, asof, "QQQ")
    # ATM gamma at 20% IV, 10.3 days: ~0.0198 per $; x 1000 OI x 100 x 600^2 x 1% = ~$7.1M per 1% move
    assert 6.0e6 < s["net_gex"] < 8.5e6


def test_option_chain_allowed_orders_still_blocked():
    class Fake:
        def get_option_chain(self, *a, **k):
            return "chain"

        def place_order(self, *a):
            raise AssertionError("must never be reachable")
    with pytest.raises(PermissionError):
        ReadOnlyClient(Fake()).get_option_chain
    c = ReadOnlyClient(Fake(), extra=ReadOnlyClient.OPTION_CHAIN)
    assert c.get_option_chain("QQQ") == "chain"
    with pytest.raises(PermissionError):
        c.place_order
    with pytest.raises(PermissionError):
        ReadOnlyClient(Fake(), extra=frozenset({"place_order"}) | ReadOnlyClient.OPTION_CHAIN).place_order


def test_runner_gex_schedule():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("runner", Path(__file__).resolve().parents[1] / "bots/cloud_runner.py")
    r = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r)
    cal, done = Calendar(), set()
    assert r.gex_due(datetime(2026, 10, 27, 9, 19, tzinfo=NY), done, cal) is None
    k = r.gex_due(datetime(2026, 10, 27, 9, 25, tzinfo=NY), done, cal)
    assert k == "2026-10-27 09:20"
    done.add(k)
    assert r.gex_due(datetime(2026, 10, 27, 9, 30, tzinfo=NY), done, cal) is None
    assert r.gex_due(datetime(2026, 10, 27, 16, 19, tzinfo=NY), done, cal) is None
    assert r.gex_due(datetime(2026, 10, 27, 16, 25, tzinfo=NY), done, cal) == "2026-10-27 16:20"
    assert r.gex_due(datetime(2026, 10, 27, 12, 0, tzinfo=NY), done, cal) is None   # 09:20 slot expired, no catch-up
    assert r.gex_due(datetime(2026, 10, 31, 9, 25, tzinfo=NY), set(), cal) is None   # Saturday
    assert r.flow_due(datetime(2026, 10, 27, 9, 25, tzinfo=NY), cal) and r.flow_due(datetime(2026, 10, 27, 16, 14, tzinfo=NY), cal)
    assert not r.flow_due(datetime(2026, 10, 27, 9, 24, tzinfo=NY), cal) and not r.flow_due(datetime(2026, 10, 27, 16, 15, tzinfo=NY), cal)
    assert not r.flow_due(datetime(2026, 10, 31, 12, 0, tzinfo=NY), cal)


def test_snapshot_through_read_only_client(tmp_path):
    """The snapshot job works through ReadOnlyClient (only get_option_chain reachable) and fetches in small chunks."""
    import importlib.util
    import json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("snap", Path(__file__).resolve().parents[1] / "bots/gex_snapshot.py")
    snap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap)
    snap.OUT, snap.time.sleep = tmp_path, (lambda s: None)
    seen = []

    class Resp:
        def __init__(self, d):
            self.d = d

        def raise_for_status(self):
            pass

        def json(self):
            return json.loads(json.dumps(self.d))

    class Fake:
        def get_option_chain(self, symbol, contract_type=None, from_date=None, to_date=None, **k):
            seen.append((contract_type.value, from_date, to_date))
            c = chain(call_oi={600: 20000, 620: 50000}, put_oi={580: 30000, 560: 80000})
            keep = "callExpDateMap" if contract_type.value == "CALL" else "putExpDateMap"
            return Resp({k_: (v if k_ in (keep, "symbol", "underlyingPrice") else {}) for k_, v in c.items()})

        def place_order(self, *a):
            raise AssertionError("must never be reachable")

    client = ReadOnlyClient(Fake(), extra=ReadOnlyClient.OPTION_CHAIN)
    s = snap.snapshot(client, "QQQ", datetime(2026, 10, 27, 15, 50, tzinfo=NY), days=8, strikes=60)
    assert {t for t, _, _ in seen} == {"CALL", "PUT"} and all((b - a).days < snap.CHUNK_DAYS for _, a, b in seen)
    assert s["net_gex"] > 0 and s["call_wall"] in (600.0, 620.0)
    assert len(list((tmp_path / "QQQ").glob("*.parquet"))) == 1


def test_empty_chunks_keep_numeric_types():
    """A request window with no expiries returns an empty map: it must not break the math (2026-10-27 first run)."""
    import pandas as pd
    asof = datetime(2026, 10, 27, 11, 37, tzinfo=NY)
    empty = contracts(dict(symbol="QQQ", underlyingPrice=600.0, callExpDateMap={}, putExpDateMap={}))
    assert len(empty) == 0 and empty.strike.dtype == float
    c = pd.concat([empty, contracts(chain(call_oi={600: 1000}))], ignore_index=True)
    assert summary(c, 600.0, asof, "QQQ")["net_gex"] > 0


def test_expired_contracts_are_left_out_after_the_close():
    ch = chain(call_oi={600: 1000})
    today = {"2026-10-27:0": {"600.0": [dict(putCall="CALL", strikePrice=600.0, expirationDate="2026-10-27T20:00:00.000+00:00",
                                              daysToExpiration=0, openInterest=50000, totalVolume=5, volatility=20.0, gamma=0.5,
                                              delta=0.5, bid=1, ask=1.1, mark=1.05, multiplier=100.0)]}}
    ch["callExpDateMap"].update(today)
    c = contracts(ch)
    before = summary(c, 600.0, datetime(2026, 10, 27, 15, 50, tzinfo=NY), "QQQ")
    after = summary(c, 600.0, datetime(2026, 10, 27, 16, 20, tzinfo=NY), "QQQ")
    alone = summary(contracts(chain(call_oi={600: 1000})), 600.0, datetime(2026, 10, 27, 16, 20, tzinfo=NY), "QQQ")
    assert before["net_gex_0dte"] > 0 and before["net_gex"] > 10 * after["net_gex"]
    assert after["net_gex_0dte"] == 0 and after["net_gex"] == alone["net_gex"] and after["expiries"] == 1


def zero_chain(pc, today="2026-10-27", extra=None):
    """One side of an SPX-like chain: a same-day expiry (strikes 7600-7800 by 50) and one 10 days out."""
    oi = {"CALL": {7700: 900, 7750: 4000, 7800: 2500}, "PUT": {7700: 1200, 7650: 6000, 7600: 3000}}[pc]

    def opt(k, exp, dte, n, **kw):
        return dict(putCall=pc, strikePrice=k, expirationDate=f"{exp}T20:00:00.000+00:00", daysToExpiration=dte,
                    openInterest=n, totalVolume=7, volatility=15.0, gamma=0.01, delta=0.5, bid=1, ask=1.1, mark=1.05,
                    multiplier=100.0, **kw)
    m = {f"{today}:0": {f"{k:.1f}": [opt(k, today, 0, oi.get(k, 0))] for k in range(7600, 7801, 50)},
         "2026-11-06:10": {"7700.0": [opt(7700, "2026-11-06", 10, 99999)]}}
    for k, row in (extra or {}).items():
        m[f"{today}:0"].setdefault(k, []).append(row)
    key = "callExpDateMap" if pc == "CALL" else "putExpDateMap"
    return {"symbol": "$SPX", "underlyingPrice": 7722.0, key: m, "putExpDateMap" if pc == "CALL" else "callExpDateMap": {}}


def test_zero_dte_summary_uses_only_the_nearest_live_expiry():
    import pandas as pd
    from quant.live.gex import zero_dte
    c = pd.concat([contracts(zero_chain("CALL")), contracts(zero_chain("PUT"))], ignore_index=True)
    z = zero_dte(c, 7722.0, datetime(2026, 10, 27, 9, 20, tzinfo=NY), "$SPX", top=2)
    assert z["expiry"] == "2026-10-27" and z["dte"] == 0 and z["contracts"] == 6     # the 10-day expiry is ignored
    assert (z["max_call_oi_strike"], z["max_call_oi"]) == (7750.0, 4000)
    assert (z["max_put_oi_strike"], z["max_put_oi"]) == (7650.0, 6000)
    assert z["top_call_oi"] == "7750:4000|7800:2500" and z["top_put_oi"] == "7650:6000|7600:3000"
    assert z["call_oi"] == 7400 and z["put_oi"] == 10200
    assert z["regime"] in ("positive", "negative") and z["net_gex"] == z["call_gex"] + z["put_gex"]
    assert z["call_gex"] > 0 > z["put_gex"]
    # after the close today's expiry is dead: the next one is described, with its distance in days
    z2 = zero_dte(c, 7722.0, datetime(2026, 10, 27, 16, 20, tzinfo=NY), "$SPX")
    assert z2["expiry"] == "2026-11-06" and z2["dte"] == 10 and z2["max_call_oi_strike"] == 7700.0
    # more call gamma than put gamma near spot -> positive; the mirror image -> negative
    calls_only = contracts(zero_chain("CALL"))
    assert zero_dte(calls_only, 7722.0, datetime(2026, 10, 27, 9, 20, tzinfo=NY), "$SPX")["regime"] == "positive"
    puts_only = contracts(zero_chain("PUT"))
    assert zero_dte(puts_only, 7722.0, datetime(2026, 10, 27, 9, 20, tzinfo=NY), "$SPX")["regime"] == "negative"
    assert zero_dte(c.iloc[:0], 7722.0, datetime(2026, 10, 27, 9, 20, tzinfo=NY), "$SPX")["contracts"] == 0


def test_am_settled_contracts_are_dropped():
    from quant.live.gex import drop_am_settled
    am = dict(putCall="CALL", strikePrice=7750.0, expirationDate="2026-10-27T20:00:00.000+00:00", daysToExpiration=0,
              openInterest=50000, totalVolume=0, volatility=15.0, multiplier=100.0, settlementType="A")
    ch = zero_chain("CALL", extra={"7750.0": am, "7900.0": am})
    c = contracts(drop_am_settled(ch))
    assert c[c.expiry == "2026-10-27"].open_interest.max() == 4000 and 7900.0 not in set(c.strike)


def test_zero_dte_snapshot_asks_for_every_strike_and_skips_days_without_expiry(tmp_path):
    import importlib.util
    import json
    from datetime import date
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("snap0", Path(__file__).resolve().parents[1] / "bots/gex_snapshot.py")
    snap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap)
    snap.OUT, snap.time.sleep = tmp_path, (lambda s: None)
    seen = []

    class Resp:
        def __init__(self, d):
            self.d = d

        def raise_for_status(self):
            pass

        def json(self):
            return json.loads(json.dumps(self.d))

    class Fake:
        def get_option_chain(self, symbol, contract_type=None, from_date=None, to_date=None, **k):
            seen.append((contract_type.value, from_date, to_date, k))
            if from_date != date(2026, 10, 27):       # only the 27th has an expiry
                return Resp(dict(symbol=symbol, status="FAILED", underlyingPrice=7722.0, callExpDateMap={}, putExpDateMap={}))
            return Resp(zero_chain(contract_type.value))

    client = ReadOnlyClient(Fake(), extra=ReadOnlyClient.OPTION_CHAIN)
    z = snap.snapshot_0dte(client, "$SPX", datetime(2026, 10, 27, 9, 20, tzinfo=NY))
    assert z["expiry"] == "2026-10-27" and z["max_put_oi_strike"] == 7650.0 and z["regime"] in ("positive", "negative")
    assert all(a == b and "strike_count" not in k for _, a, b, k in seen) and len(seen) == 2
    assert len(list((tmp_path / "SPX_IDX_0DTE").glob("*.parquet"))) == 1
    # a Friday 16:20 run starts at the next day and walks forward to the first expiry
    seen.clear()
    z = snap.snapshot_0dte(client, "$SPX", datetime(2026, 10, 23, 16, 20, tzinfo=NY))
    assert z["expiry"] == "2026-10-27" and z["dte"] == 4 and seen[0][1] == date(2026, 10, 24)


def test_local_env_defaults_and_readable_0dte_lines(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("snap1", Path(__file__).resolve().parents[1] / "bots/gex_snapshot.py")
    snap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap)
    (tmp_path / ".env").write_text("SCHWAB_API_KEY=from_file\nSCHWAB_APP_SECRET=s\n")
    env = {}
    snap.load_env(tmp_path, env)
    assert env["SCHWAB_TOKEN_PATH"] == str(tmp_path / "state" / "nqbot" / "schwab_token.json")
    env = {"SCHWAB_TOKEN_PATH": "/x/token.json"}
    snap.load_env(tmp_path, env)
    assert env["SCHWAB_TOKEN_PATH"] == "/x/token.json"          # a value that is already set wins
    z = dict(asof="2026-10-27T09:20-04:00", symbol="$SPX", spot=7722.6, expiry="2026-10-27", dte=0, contracts=6,
             regime="negative", net_gex=-1.5e9, gamma_flip=7730.0, call_wall=7750.0, put_wall=7650.0,
             top_call_oi="7750:4000|7800:2500", top_put_oi="7650:6000", call_oi=7400, put_oi=10200)
    text = snap.describe(z)
    assert "NEGATIVE" in text and "-1.50bn" in text and "7750 (4,000), 7800 (2,500)" in text and "7650 (6,000)" in text
    assert "no 0DTE data" in snap.describe(dict(symbol="$NDX", error="boom"))
    early = snap.describe(dict(symbol="$SPX", expiry="2026-11-10", dte=0, listed=574, contracts=0))
    assert "574 contracts" in early and "run again" in early
