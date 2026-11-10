"""GEX snapshot: chain parsing, the gamma math, the read-only allowance and the runner's schedule."""
from datetime import datetime, timedelta
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
    assert "574 contracts" in early and "--wait" in early
    assert snap.oi_missing([dict(symbol="$SPX", listed=574, contracts=0), dict(symbol="$NDX", listed=9, contracts=9)])
    assert not snap.oi_missing([dict(symbol="$SPX", listed=574, contracts=300), dict(symbol="$NDX", error="boom")])


def test_futures_levels_take_the_basis_live_or_at_the_last_cash_close():
    from collections import namedtuple
    from quant.live.gex import futures_levels
    B = namedtuple("B", "t close")
    z = dict(symbol="$SPX", spot=7550.0, gamma_flip=7535.0, call_wall=7550.0, put_wall=7500.0)
    # Tuesday 06:34, cash closed: the index price is Monday's close, so the basis comes from Monday's 15:59 bar
    bars = [B(datetime(2026, 11, 9, 15, 58, tzinfo=NY), 7588.0), B(datetime(2026, 11, 9, 15, 59, tzinfo=NY), 7590.0),
            B(datetime(2026, 11, 10, 6, 33, tzinfo=NY), 7610.0)]
    f = futures_levels(z, bars, datetime(2026, 11, 10, 6, 34, tzinfo=NY), "/ES")
    assert f["fut_basis"] == 40.0 and f["fut_basis_kind"] == "close" and f["fut_last"] == 7610.0
    assert (f["fut_gamma_flip"], f["fut_call_wall"], f["fut_put_wall"]) == (7575.0, 7590.0, 7540.0)
    # Monday before the open reaches back over the weekend to Friday's close
    fri = [B(datetime(2026, 11, 6, 15, 59, tzinfo=NY), 7560.0), B(datetime(2026, 11, 9, 9, 19, tzinfo=NY), 7600.0)]
    assert futures_levels(z, fri, datetime(2026, 11, 9, 9, 20, tzinfo=NY), "/ES")["fut_basis"] == 10.0
    # cash open: both prices are live; a stale last bar is refused
    live = bars + [B(datetime(2026, 11, 10, 10, 29, tzinfo=NY), 7585.0)]
    f = futures_levels(z, live, datetime(2026, 11, 10, 10, 30, tzinfo=NY), "/ES")
    assert f["fut_basis"] == 35.0 and f["fut_basis_kind"] == "live" and f["fut_put_wall"] == 7535.0
    assert futures_levels(z, bars, datetime(2026, 11, 10, 10, 30, tzinfo=NY), "/ES") == {}
    # after the close the same day's 15:59 bar is the reference; no bars or no close bar gives nothing
    f = futures_levels(z, live + [B(datetime(2026, 11, 10, 15, 59, tzinfo=NY), 7570.0)], datetime(2026, 11, 10, 16, 20, tzinfo=NY), "/ES")
    assert f["fut_basis"] == 20.0 and f["fut_basis_at"].startswith("2026-11-10T15:59")
    assert futures_levels(z, [], datetime(2026, 11, 10, 6, 34, tzinfo=NY), "/ES") == {}
    assert futures_levels(z, bars[-1:], datetime(2026, 11, 10, 6, 34, tzinfo=NY), "/ES") == {}


def test_snapshot_adds_futures_lines_and_survives_a_failed_price_history():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("snap2", Path(__file__).resolve().parents[1] / "bots/gex_snapshot.py")
    snap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap)
    now = datetime(2026, 11, 10, 6, 34, tzinfo=NY)
    z = dict(asof="2026-11-10T06:34-05:00", symbol="$SPX", spot=7550.0, expiry="2026-11-10", dte=0, contracts=10,
             regime="positive", net_gex=2.4e9, gamma_flip=7535.0, call_wall=7550.0, put_wall=7500.0)
    ms = lambda *a: int(datetime(*a, tzinfo=NY).timestamp() * 1000)  # noqa: E731

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return dict(candles=[dict(datetime=ms(2026, 11, 9, 15, 59), open=1, high=1, low=1, close=7590.0),
                                 dict(datetime=ms(2026, 11, 10, 6, 32), open=1, high=1, low=1, close=7610.0)])

    class Fake:
        def get_price_history_every_minute(self, symbol, **k):
            assert symbol == "/ES"
            return Resp()

    out = snap.add_futures(ReadOnlyClient(Fake(), extra=ReadOnlyClient.MARKET_DATA), z, now)
    assert out["fut_symbol"] == "/ES" and out["fut_call_wall"] == 7590.0
    text = snap.describe(out)
    assert "on /ES" in text and "basis +40.00" in text and "call wall 7,590.00" in text and "last vs flip    +35.00 (above)" in text
    # price history blocked or failing: the row is kept, with the reason
    bad = snap.add_futures(ReadOnlyClient(Fake(), extra=ReadOnlyClient.OPTION_CHAIN), z, now)
    assert bad["net_gex"] == 2.4e9 and "PermissionError" in bad["fut_error"] and "not available" in snap.describe(bad)
    assert snap.add_futures(None, dict(symbol="QQQ", contracts=5), now) == dict(symbol="QQQ", contracts=5)


def test_strike_table_matches_the_summary_and_keeps_volume_without_open_interest():
    import pandas as pd
    from quant.live.gex import strike_table, zero_dte
    extra = {"7850.0": dict(putCall="CALL", strikePrice=7850.0, openInterest=0, totalVolume=900, volatility=20.0, gamma=0.001,
                            delta=0.1, bid=1, ask=1.2, mark=1.1, multiplier=100, daysToExpiration=0,
                            expirationDate="2026-10-27T20:00:00.000+00:00")}
    c = pd.concat([contracts(zero_chain("CALL", extra=extra)), contracts(zero_chain("PUT"))], ignore_index=True)
    now = datetime(2026, 10, 27, 9, 20, tzinfo=NY)
    t, z = strike_table(c, 7722.0, now), zero_dte(c, 7722.0, now, "$SPX")
    assert round(t.call_gex.sum()) == z["call_gex"] and round(t.put_gex.sum()) == z["put_gex"]
    up = (t.call_oi + t.call_volume)[(t.index > 7722.0) & (t.index <= 7722.0 * 1.01)]
    down = (t.put_oi + t.put_volume)[(t.index < 7722.0) & (t.index >= 7722.0 * 0.99)]
    assert up.max() == z["call_wall_size"] and down.max() == z["put_wall_size"]
    assert up.idxmax() == z["call_wall"] and down.idxmax() == z["put_wall"]
    assert t.at[7850.0, "call_volume"] == 900 and t.at[7850.0, "call_gex"] == 0 and (t.put_gex <= 0).all()
    assert t.call_oi.sum() == z["call_oi"] and list(t.index) == sorted(t.index)


def test_live_map_session_reports_new_volume_and_level_moves(tmp_path):
    from quant.live.gex import strike_table, zero_dte
    import pandas as pd
    from quant.live.gex_map import Session, render, write
    c = pd.concat([contracts(zero_chain("CALL")), contracts(zero_chain("PUT"))], ignore_index=True)
    t0 = datetime(2026, 10, 27, 9, 31, tzinfo=NY)
    ses, basis = Session("/ES", 60), 40.0
    d = ses.add(t0, zero_dte(c, 7722.0, t0, "$SPX"), strike_table(c, 7722.0, t0), 7762.0, basis, "test", 7722.0, 1.5)
    assert d["M"]["prev"] is None
    assert d["M"]["snaps"] == 1 and d["movers"] == [] and d["notes"] == [] and all(r["dc1"] == 0 for r in d["R"])
    c2 = c.copy()
    k = float(c2.strike.iloc[0])
    c2.loc[(c2.strike == k) & (c2.put_call == "CALL"), "volume"] += 250      # 250 calls trade at one strike
    t1 = datetime(2026, 10, 27, 9, 32, tzinfo=NY)
    d = ses.add(t1, zero_dte(c2, 7732.0, t1, "$SPX"), strike_table(c2, 7732.0, t1), 7772.0, basis, "test", 7722.0, 1.5)
    assert d["movers"][0]["k"] == k and d["movers"][0]["dc"] == 250 and d["movers"][0]["dp"] == 0
    row = next(r for r in d["R"] if r["k"] == k)
    assert (row["dc1"], row["dc0"], row["dc5"], row["dp1"]) == (250, 250, 0, 0)
    assert any("ES moved +10.00 points since 09:31" in s for s in d["notes"]) and len(d["H"]) == 2
    assert d["M"]["futLast"] == 7772.0 and d["M"]["spot"] == 7732.0 and d["H"][1]["fut"] == 7772.0
    assert d["M"]["prev"]["futLast"] == 7762.0 and d["M"]["prev"]["callVol"] == d["M"]["callVol"] - 250
    html = render(d)
    assert "/*DATA*/null" not in html and "__REFRESH__" not in html and '"futLast":7772.0' in html
    write(tmp_path / "live" / "es_map.html", d)
    assert (tmp_path / "live" / "es_map.html").read_text(encoding="utf-8") == html and not (tmp_path / "live" / "es_map.tmp").exists()


def test_live_cycle_uses_three_read_only_requests_and_writes_the_page(tmp_path):
    import importlib.util
    import json
    from pathlib import Path
    import pandas as pd
    spec = importlib.util.spec_from_file_location("gex_live", Path(__file__).resolve().parents[1] / "bots/gex_live.py")
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    live.snap.time.sleep = lambda s: None
    calls, asked, no_puts_in_all = [], [], False
    from time import monotonic as time_now
    ms = lambda *a: int(datetime(*a, tzinfo=NY).timestamp() * 1000)  # noqa: E731

    class Resp:
        def __init__(self, d):
            self.d = d

        def raise_for_status(self):
            pass

        def json(self):
            return json.loads(json.dumps(self.d))

    class Fake:
        def get_option_chain(self, symbol, contract_type=None, **k):
            calls.append("chain")
            asked.append(contract_type.value)
            if contract_type.value == "ALL":   # calls and puts in one answer, unless this fake Schwab drops the puts
                both = zero_chain("CALL")
                both["putExpDateMap"] = {} if no_puts_in_all else zero_chain("PUT")["putExpDateMap"]
                return Resp(both)
            return Resp(zero_chain(contract_type.value))

        def get_price_history_every_minute(self, symbol, start_datetime=None, **k):
            calls.append("history")
            return Resp(dict(candles=[dict(datetime=ms(2026, 10, 26, 15, 59), open=1, high=1, low=1, close=7760.0),
                                      dict(datetime=ms(2026, 10, 27, 9, 19), open=1, high=1, low=1, close=7770.0)]))

    client = ReadOnlyClient(Fake(), extra=ReadOnlyClient.MARKET_DATA)
    ses, state = live.Session("/ES", 60), {}
    now = datetime(2026, 10, 27, 9, 20, tzinfo=NY)
    d = live.cycle(client, ses, "$SPX", now, state, tmp_path)
    assert state["basis"] == 38.0 and "cash close, 2026-10-26" in state["note"]     # 7760 - the chain's 7722 close
    assert d["M"]["futLast"] == 7770.0 and d["M"]["spot"] == 7732.0 and calls == ["chain", "chain", "history", "history"]
    calls.clear()
    live.cycle(client, ses, "$SPX", now + timedelta(minutes=1), state, tmp_path)
    assert calls == ["chain", "history"] and asked[-1] == "ALL"   # one request for both sides; the basis is fetched once
    assert (tmp_path / "live" / "es_map.html").exists() and len(pd.read_csv(tmp_path / "gex_live.csv")) == 2
    assert len(list((tmp_path / "SPX_IDX_0DTE" / "live" / "2026-10-27").glob("*.parquet"))) == 2
    assert "ES 7,770.00" in live.line(d) or "/ES 7,770.00" in live.line(d)
    # a third snapshot in the same minute: fetched through a thread pool, no new snapshot file, one more CSV line
    from concurrent.futures import ThreadPoolExecutor
    calls.clear()
    t3 = now + timedelta(minutes=1, seconds=3)
    with ThreadPoolExecutor(2) as pool:
        chain3 = live.fetch(client, "$SPX", t3, state, tmp_path, pool)
    d3 = live.cycle(client, ses, "$SPX", t3, state, tmp_path, chain3, (7771.0, t3), 0.4)
    assert calls == ["chain"] and asked[-1] == "ALL" and d3["M"]["futLast"] == 7771.0 and d3["M"]["snaps"] == 3 and d3["M"]["fetchS"] == 0.4
    assert len(list((tmp_path / "SPX_IDX_0DTE" / "live" / "2026-10-27").glob("*.parquet"))) == 2
    assert len(pd.read_csv(tmp_path / "gex_live.csv")) == 3
    # the page does not reload: it polls the data file written next to it
    page = (tmp_path / "live" / "es_map.html").read_text(encoding="utf-8")
    js = (tmp_path / "live" / "es_map.data.js").read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' not in page and "es_map.data.js" in page and "__DATA_JS__" not in page
    assert js.startswith("__gex({") and json.loads(js[6:-2])["M"]["snaps"] == 3
    # only ES sources; the pace comes from the request budget: 2 requests per symbol and 1 for the future
    assert sorted(live.SOURCES) == ["$SPX", "SPY"] and set(live.SOURCES.values()) == {"/ES"}
    # the pace comes from the request budget: 1 request for the future and 1 per chain (2 when it has to be split)
    assert live.requests_per_cycle([{}]) == 2 and live.requests_per_cycle([{}, {"split": True}]) == 4
    assert round(live.interval(2, 110), 2) == 1.09 and round(live.interval(3, 110), 2) == 1.64
    assert live.interval(2, 500) == 1.0 and live.interval(2, 110, every=10) == 10
    assert live.token_fresh(client) is False   # unknown expiry: the requests of a cycle are not sent together
    # the network part of a snapshot: with a connected price stream it is the chain alone, otherwise price and chain
    from concurrent.futures import ThreadPoolExecutor as Pool
    from quant.live.schwab_feed import LivePrice, handle_quote_message
    lp = LivePrice()
    handle_quote_message(dict(content=[dict(key="/NQZ26", LAST_PRICE=1.0), dict(key="/ESZ26", BID_PRICE=7779.75),
                                       dict(key="/ESZ26", LAST_PRICE=7780.25)]), "/ES", lp)
    assert lp.price == 7780.25 and lp.ticks == 1 and lp.get() is None   # not connected yet: the loop asks for the price itself
    runs, t5 = {"$SPX": (ses, state)}, now + timedelta(minutes=1, seconds=4)
    with Pool(4) as pool:
        calls.clear()
        r = live.grab(client, runs, "/ES", t5, tmp_path, pool, lp)
        assert sorted(calls) == ["chain", "history"] and r["px"][0] == 7770.0 and not r["errors"] and "$SPX" in r["chains"]
        lp.connected = True
        calls.clear()
        r = live.grab(client, runs, "/ES", t5, tmp_path, pool, lp)
        assert calls == ["chain"] and r["px"] == (7780.25, t5)
    # cash hours: the price is asked for every BASIS_EVERY seconds only; in between it is the chain's index price + basis
    t6, ctl = now.replace(hour=10, minute=0), dict(index=True, px_at=-1e9)
    with Pool(4) as pool:
        calls.clear()
        r1 = live.grab(client, runs, "/ES", t6, tmp_path, pool, None, ctl)
        r2 = live.grab(client, runs, "/ES", t6 + timedelta(seconds=1), tmp_path, pool, None, ctl)
        assert r1["asked"] and r1["px"] is not None and not r2["asked"] and r2["px"] is None and calls.count("history") == 1
        ctl["px_at"] -= live.BASIS_EVERY
        assert live.grab(client, runs, "/ES", t6 + timedelta(seconds=11), tmp_path, pool, None, ctl)["asked"]
        early = live.grab(client, runs, "/ES", now, tmp_path, pool, None, dict(index=True, px_at=time_now()))
        assert early["asked"]                                   # 09:20, the cash market is closed: asked every time
    assert live.implied_price(runs, r2["chains"]) == (7722.0 + state["basis"], "$SPX") and live.index_symbol({"SPY": 1}) is None
    # the terminal says why the stream is not used: a failed try is retried and its reason is kept and shown
    from quant.live.schwab_feed import run_price_stream
    import threading
    bad, halt, naps = LivePrice(), threading.Event(), []

    def nap(seconds):
        naps.append(seconds)
        if len(naps) == 3:
            halt.set()
    run_price_stream(ReadOnlyClient(object()), "/ES", bad, halt, sleep=nap)   # this client cannot log in to the stream
    assert naps == [4.0, 8.0, 16.0] and bad.get() is None and "get_user_preferences" in bad.error
    assert "stream failed" in live.stream_status(False, bad) and "get_user_preferences" in live.stream_status(False, bad)
    assert live.stream_status(False, LivePrice()).endswith("while the stream connects") and live.stream_status(True, lp) == "from the stream"
    # a combined answer without one side: the loop goes back to one request per side, for good
    no_puts_in_all = True
    st2, t4 = {"day": state["day"], "saved": "0921"}, now + timedelta(minutes=1, seconds=5)
    c4, _ = live.fetch(client, "$SPX", t4, st2, tmp_path)
    assert st2["split"] is True and asked[-3:] == ["ALL", "CALL", "PUT"] and set(c4.put_call) == {"CALL", "PUT"}
    live.fetch(client, "$SPX", t4, st2, tmp_path)
    assert asked[-2:] == ["CALL", "PUT"] and live.requests_per_cycle([st2]) == 3
    from quant.live.gex_map import write_frame
    write_frame(tmp_path / "live" / "live_map.html", [("/ES", "SPX"), ("/ES", "SPY")])
    frame = (tmp_path / "live" / "live_map.html").read_text(encoding="utf-8")
    assert frame.index("es_map.html") < frame.index("es_spy_map.html")


def test_live_session_keeps_a_long_fast_run_small():
    from quant.live.gex import strike_table, zero_dte
    from quant.live.gex_map import HIST, Session
    import pandas as pd
    c = pd.concat([contracts(zero_chain("CALL")), contracts(zero_chain("PUT"))], ignore_index=True)
    ses, t0 = Session("/ES", 3), datetime(2026, 10, 27, 10, 0, tzinfo=NY)
    z, table = zero_dte(c, 7722.0, t0, "$SPX"), strike_table(c, 7722.0, t0)
    for i in range(1200):   # an hour at one snapshot every 3 seconds
        t = t0 + timedelta(seconds=3 * i)
        d = ses.add(t, z, table, 7760.0 + i * 0.01, 38.0, "x")
    assert d["M"]["snaps"] == 1200 and len(ses.snaps) <= 1 + 420 // 3 + 1 and ses.snaps[0]["t"] == t0
    assert len(d["H"]) <= HIST and d["H"][-1]["t"] == "10:59:57" and d["M"]["win"] == "5 minutes"


def test_live_cycle_moves_etf_strikes_onto_the_future_by_the_price_ratio(tmp_path):
    import importlib.util
    import json
    from pathlib import Path
    import pandas as pd
    spec = importlib.util.spec_from_file_location("gex_live2", Path(__file__).resolve().parents[1] / "bots/gex_live.py")
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    live.snap.time.sleep = lambda s: None
    ms = lambda *a: int(datetime(*a, tzinfo=NY).timestamp() * 1000)  # noqa: E731

    class Resp:
        def __init__(self, d):
            self.d = d

        def raise_for_status(self):
            pass

        def json(self):
            return json.loads(json.dumps(self.d))

    def side(pc):   # a SPY-like same-day chain: strikes 765-779, underlying 772
        oi = {"CALL": {772: 900, 775: 4000}, "PUT": {770: 1200, 768: 6000}}[pc]
        m = {"2026-10-27:0": {f"{k:.1f}": [dict(putCall=pc, strikePrice=k, expirationDate="2026-10-27T20:00:00.000+00:00",
                                               daysToExpiration=0, openInterest=oi.get(k, 0), totalVolume=5, volatility=15.0,
                                               gamma=0.1, delta=0.5, bid=1, ask=1.1, mark=1.05, multiplier=100.0)]
                              for k in range(765, 780)}}
        key = "callExpDateMap" if pc == "CALL" else "putExpDateMap"
        return {"symbol": "SPY", "underlyingPrice": 772.0, key: m, "putExpDateMap" if pc == "CALL" else "callExpDateMap": {}}

    class Fake:
        def get_option_chain(self, symbol, contract_type=None, **k):
            return Resp(side(contract_type.value))

        def get_price_history_every_minute(self, symbol, **k):
            px = {"SPY": 772.0, "/ES": 7758.6}[symbol]      # ratio 10.05
            return Resp(dict(candles=[dict(datetime=ms(2026, 10, 27, 9, 18), open=px, high=px, low=px, close=px)]))

    client = ReadOnlyClient(Fake(), extra=ReadOnlyClient.MARKET_DATA)
    ses, state = live.Session("/ES", 60, index="SPY"), {}
    d = live.cycle(client, ses, "SPY", datetime(2026, 10, 27, 9, 20, tzinfo=NY), state, tmp_path)
    m = d["M"]
    assert state["basis"] == 0.0 and round(state["ratio"], 4) == 10.05 and m["ratio"] == 10.05 and m["index"] == "SPY"
    assert m["spot"] == 772.0 and m["futLast"] == 7758.6 and m["callWall"] in (772.0, 775.0)
    assert d["H"][0]["cw"] == round(m["callWall"] * 10.05, 2)                       # history is on the future's scale
    assert (tmp_path / "live" / "es_spy_map.html").exists()
    row = pd.read_csv(tmp_path / "gex_live.csv").iloc[-1]
    assert row.fut_call_wall == round(m["callWall"] * 10.05, 2) and row.ratio == 10.05


def test_a_slowdown_after_429_is_taken_back_step_by_step():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("gex_live_ease", Path(__file__).resolve().parents[1] / "bots" / "gex_live.py")
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    assert live.ease(1.5 ** 4, live.RECOVER_S - 1) == 1.5 ** 4          # not yet
    assert live.ease(1.5 ** 4, live.RECOVER_S) == 1.5 ** 3              # one step back
    assert live.ease(1.2, live.RECOVER_S) == 1.0 and live.ease(1.0, 9999) == 1.0


def test_walls_are_the_largest_positions_near_the_price_and_follow_todays_volume():
    import pandas as pd
    from quant.live.gex import walls
    call_oi = pd.Series({7475.0: 1416, 7490.0: 659, 7500.0: 1525, 7505.0: 390, 7510.0: 506, 7550.0: 3119, 7690.0: 8085})
    put_oi = pd.Series({7450.0: 3501, 7475.0: 2093, 7490.0: 1389, 7500.0: 3725, 7510.0: 1111, 6900.0: 7034})
    w = walls(call_oi, put_oi, 7500.72)                                                   # 7690 and 6900 are outside 1%
    assert w == dict(call_wall=7550.0, put_wall=7500.0, call_wall_size=3119, put_wall_size=3725)
    assert walls(call_oi, put_oi, 7499.0)["put_wall"] == 7450.0                           # below the price only
    far = walls(call_oi, put_oi, 7499.0, band=1.0)
    assert (far["call_wall"], far["put_wall"]) == (7690.0, 6900.0)
    # today's trading moves a wall: 60,000 calls traded at 7510 overtake the 7550 position
    moved = walls(call_oi.add(pd.Series({7510.0: 60000}), fill_value=0), put_oi, 7500.72)
    assert (moved["call_wall"], moved["call_wall_size"]) == (7510.0, 60506)
    tie = walls(pd.Series({7510.0: 5, 7520.0: 5}), pd.Series({7480.0: 5, 7490.0: 5}), 7500.0)
    assert (tie["call_wall"], tie["put_wall"]) == (7510.0, 7490.0)                        # the nearer strike on a tie
    assert walls(call_oi, put_oi, 7800.0)["call_wall"] is None
    assert walls(pd.Series(dtype=float), pd.Series(dtype=float), 7500.0)["put_wall_size"] is None


def test_the_levels_basis_is_the_median_of_the_recent_readings():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("gex_live_basis", Path(__file__).resolve().parents[1] / "bots" / "gex_live.py")
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    seen: list = []
    assert live.steady_basis(seen, 22.7) == 22.7
    out = [live.steady_basis(seen, b) for b in (21.8, 23.2, 22.5, 20.2, 22.6, 23.4, 22.4)]
    assert max(out[2:]) - min(out[2:]) <= 0.2           # the readings span 3.2 points, the basis in use hardly moves
    for b in [30.0] * live.BASIS_KEEP:
        last = live.steady_basis(seen, b)
    assert last == 30.0 and len(seen) == live.BASIS_KEEP  # old readings drop out
