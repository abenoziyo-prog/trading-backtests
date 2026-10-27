import re
from datetime import datetime, timedelta

import pandas as pd
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant.live.alerts import Alerter, describe
from quant.live.calendar import Calendar
from quant.live.engine import Bar, DayEngine, DayLevels, Event
from quant.live.schwab_feed import MinuteAggregator, ReadOnlyClient
from quant.live.store import BarStore

NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
DAY = datetime(2026, 3, 2, tzinfo=NY)


def bar(hhmm: str, o, h, l, c) -> Bar:
    hh, mm = map(int, hhmm.split(":"))
    return Bar(DAY.replace(hour=hh, minute=mm), o, h, l, c)


def run(levels, bars):
    eng = DayEngine(DAY, levels)
    ev = []
    for b in bars:
        ev += eng.on_bar(b)
    ev += eng.close_day()
    return eng, ev


def test_gf1_gap_up_red_first_minute_short_to_previous_close():
    lv = DayLevels(pdh=105, pdl=95, pdc=100)
    bars = [bar("09:30", 104, 104.5, 103, 103.2),          # gap +4, closes red, low above PDC
            bar("09:31", 103.2, 103.3, 101, 101.5),
            bar("09:32", 101.5, 101.6, 99.8, 100.2)]       # trades through PDC -> target
    eng, ev = run(lv, bars)
    gf = [t for t in eng.trades if t.strategy == "GF-1"][0]
    assert (gf.side, gf.entry, gf.stop, gf.target) == (-1, 103.2, 108, 100)
    assert gf.entry_time == DAY.replace(hour=9, minute=31) and gf.exit_reason == "target" and gf.taken
    assert gf.pnl_usd == pytest.approx((103.2 - 100 - 1) * 20)
    assert [e.kind for e in ev if e.trade is gf] == ["signal", "entry", "exit"]  # alert before the entry bar


def test_gf1_skips_when_first_candle_already_filled_or_green():
    lv = DayLevels(105, 95, 100)
    eng, _ = run(lv, [bar("09:30", 104, 104.5, 99.9, 103), bar("09:31", 103, 103, 102, 102)])
    assert not [t for t in eng.trades if t.strategy == "GF-1"]
    eng, _ = run(lv, [bar("09:30", 104, 105, 103.5, 104.6), bar("09:31", 104.6, 105, 104, 104)])
    assert not [t for t in eng.trades if t.strategy == "GF-1"]


def test_l2_waits_for_10m_candle_and_holds_to_close_and_picker_blocks_overlap():
    lv = DayLevels(pdh=100, pdl=90, pdc=99, london_high=None)
    bars = [bar(f"09:{30 + i:02d}", 99, 99.5, 98.8, 99.2) for i in range(9)] + [bar("09:39", 99.2, 100.6, 99.1, 100.5)]
    bars += [bar(f"09:{40 + i:02d}", 100.5, 100.8, 100.2, 100.6) for i in range(20)]
    eng, ev = run(lv, bars)
    l2 = [t for t in eng.trades if t.strategy == "L2"]
    assert len(l2) == 1 and l2[0].entry == 100.5 and l2[0].entry_time == DAY.replace(hour=9, minute=40)
    assert l2[0].stop == pytest.approx(100.5 - 2.5) and l2[0].target is None and l2[0].exit_reason == "16:00 close"


def test_candle_closes_on_next_bucket_when_last_minute_missing():
    lv = DayLevels(pdh=100, pdl=90, pdc=99)
    bars = [bar(f"09:{30 + i:02d}", 99, 99.5, 98.8, 99.2) for i in range(8)] + [bar("09:38", 99.2, 100.6, 99.1, 100.5)]
    bars += [bar("09:40", 100.5, 100.8, 100.2, 100.6)]       # 09:39 missing: candle closes when 09:40 arrives
    eng, ev = run(lv, bars)
    l2 = [t for t in eng.trades if t.strategy == "L2"][0]
    assert l2.entry_time == DAY.replace(hour=9, minute=40) and l2.entry == 100.5


def test_no_entries_from_candles_ending_at_or_after_1530():
    lv = DayLevels(pdh=100, pdl=90, pdc=99)
    eng = DayEngine(DAY, lv)
    eng.on_bar(bar("09:30", 99, 99.2, 98.9, 99))
    for b in [bar("15:29", 99, 99.2, 98.9, 99), bar("15:39", 99, 101, 98.9, 100.9)]:
        eng.on_bar(b)
    assert not [t for t in eng.trades if t.strategy == "L2"]


def test_read_only_client_blocks_orders():
    class Fake:
        def place_order(self, *a):
            raise AssertionError("must never be reached")

        def get_user_preferences(self):
            return "prefs"

    c = ReadOnlyClient(Fake())
    assert c.get_user_preferences() == "prefs"
    for name in ("place_order", "replace_order", "cancel_order", "get_account_numbers", "get_orders_for_account"):
        with pytest.raises(PermissionError):
            getattr(c, name)


def test_no_order_code_in_live_bot():
    """The bot is data-only: no order endpoints or order builders anywhere in its code."""
    files = list((ROOT / "src/quant/live").glob("*.py")) + [ROOT / "bots/nq_paper_bot.py", ROOT / "bots/schwab_login.py",
                                                       ROOT / "bots/schwab_archiver.py", ROOT / "bots/seed_levels.py",
                                                       ROOT / "bots/cloud_runner.py", ROOT / "bots/gex_snapshot.py"]
    banned = re.compile(r"place_order|replace_order|cancel_order|schwab\.orders|OrderBuilder|equity_buy|equity_sell|"
                        r"futures_buy|order_spec|preview_order")
    for f in files:
        text = f.read_text()
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        code = re.sub(r'""".*?"""', "", code, flags=re.S)  # docstrings may name what is blocked
        hits = [m.group(0) for m in banned.finditer(code)]
        assert not hits, f"{f.name}: {hits}"


def test_minute_aggregator_waits_to_settle_and_prefers_history():
    t0 = int(datetime(2026, 3, 2, 9, 30, tzinfo=NY).timestamp() * 1000)
    end = t0 / 1000 + 60
    now = [end + 0.5]
    got, audit, hist = [], [], {}
    agg = MinuteAggregator(got.append, settle_s=3, max_wait_s=30, retry_s=3, clock=lambda: now[0],
                           resolver=lambda t: hist.get(t), audit=lambda kind, row: audit.append((kind, row)))
    m0 = datetime(2026, 3, 2, 9, 30, tzinfo=NY)
    agg.update(t0, 1, 2, 0.5, 1.5)
    agg.update(t0 + 60_000, 2, 3, 1.5, 2.5)                 # next minute arrives: no longer finalizes 09:30 early
    agg.update(t0, 1, 2.5, 0.5, 2.0)                        # same minute updated after it ended
    agg.tick(end + 2)
    assert got == []                                        # still settling
    agg.tick(end + 3)
    assert got == []                                        # history not there yet: retry later
    hist[m0] = Bar(m0, 1, 2.6, 0.5, 2.1)
    agg.tick(end + 4)
    assert got == []                                        # next retry is at +6 s
    agg.tick(end + 6)
    assert got == [hist[m0]]                                # history bar used
    row = [r for k, r in audit if k == "minute"][0]
    assert row["source"] == "history" and row["stream_msgs"] == 2 and row["stream_vs_used"] == pytest.approx(0.1)
    agg.tick(end + 60 + 31)                                 # 09:31: history never arrives -> stream fallback
    assert len(got) == 2 and got[1].close == 2.5
    assert [r for k, r in audit if k == "minute"][1]["source"] == "stream (history late)"
    now[0] = end + 100
    agg.update(t0, 9, 9, 9, 9)                              # update after finalization: logged, not used
    late = [r for k, r in audit if k == "late"][0]
    assert late["diff_vs_used"] == pytest.approx(8.5) and len(got) == 2


def test_minute_aggregator_without_resolver_uses_stream_after_settle():
    t0 = int(datetime(2026, 3, 2, 9, 30, tzinfo=NY).timestamp() * 1000)
    got = []
    agg = MinuteAggregator(got.append, settle_s=3, clock=lambda: 0.0)
    agg.update(t0, 1, 2, 0.5, 1.5)
    agg.tick(t0 / 1000 + 62.9)
    assert got == []
    agg.tick(t0 / 1000 + 63)
    assert len(got) == 1 and got[0].close == 1.5


def test_store_levels_from_recorded_session_and_seed(tmp_path):
    cal = Calendar()
    st = BarStore(tmp_path, NY)
    prev = datetime(2026, 2, 27, 9, 30, tzinfo=NY)          # Friday before Monday 2026-03-02
    for i in range(390):
        st.append(Bar(prev + timedelta(minutes=i), 100 + i * 0.01, 101 + i * 0.01, 99, 100.5))
    for i in range(170):                                    # London 02:00-05:00, 170/180 minutes
        st.append(Bar(datetime(2026, 3, 2, 2, 0, tzinfo=NY) + timedelta(minutes=i), 100, 102 + (i == 50), 99, 100))
    lv, note = st.levels(datetime(2026, 3, 2).date(), cal)
    assert lv.pdh == pytest.approx(101 + 389 * 0.01) and lv.pdl == 99 and lv.pdc == 100.5 and lv.london_high == 103
    empty = BarStore(tmp_path / "x", NY)
    assert empty.levels(datetime(2026, 3, 2).date(), cal)[0] is None
    (tmp_path / "x" / "seed_levels.json").write_text('{"date": "2026-03-02", "pdh": 10, "pdl": 5, "pdc": 7}')
    assert empty.levels(datetime(2026, 3, 2).date(), cal)[0] == DayLevels(10, 5, 7, None)


def test_calendar():
    cal = Calendar()
    d = datetime(2026, 11, 27).date()
    assert not cal.is_trading_day(datetime(2026, 11, 26).date()) and cal.close_time(d).hour == 13
    assert cal.previous_trading_day(datetime(2026, 4, 6).date()).isoformat() == "2026-04-02"  # Good Friday 04-03


def test_alerter_without_channels_only_logs():
    a = Alerter({})
    assert a.channels == []
    lv = DayLevels(105, 95, 100)
    eng, ev = run(lv, [bar("09:30", 104, 104.5, 103, 103.2), bar("09:31", 103.2, 103.3, 101, 101.5)])
    for e in ev:
        a.event(e, "/NQ")
    title, body, prio = describe([e for e in ev if e.kind == "signal"][0], "/NQ")
    assert title == "GF-1: SHORT 1 /NQ now" and "Stop 108.00" in body and prio == "high"


@pytest.mark.skipif(not (ROOT / "reports/optimizer/library.parquet").exists(), reason="needs the backtest library")
def test_live_engine_reproduces_backtest():
    import importlib.util
    spec = importlib.util.spec_from_file_location("chk", ROOT / "scripts/check_live_engine.py")
    chk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(chk)
    assert chk.main() == 0


def test_chart_message_parsing():
    from quant.live.schwab_feed import handle_chart_message
    got = []
    agg = MinuteAggregator(got.append)
    t0 = int(datetime(2026, 3, 2, 9, 30, tzinfo=NY).timestamp() * 1000)
    row = lambda sym, t, c: {"key": sym, "CHART_TIME_MILLIS": t, "OPEN_PRICE": c, "HIGH_PRICE": c + 1, "LOW_PRICE": c - 1,
                             "CLOSE_PRICE": c, "VOLUME": 10}
    handle_chart_message({"service": "CHART_FUTURES", "content": [row("/NQZ26", t0, 100.0), row("/ESZ26", t0, 5.0)]}, "/NQ", agg)
    handle_chart_message({"content": [row("/NQZ26", t0 + 60_000, 101.0)]}, "/NQ", agg)
    agg.tick(t0 / 1000 + 60 + 5)  # default settle: 5 s after the minute ends
    assert len(got) == 1 and got[0].close == 100.0 and got[0].t == datetime(2026, 3, 2, 9, 30, tzinfo=NY)


def test_read_only_client_price_history_only_when_granted():
    class Fake:
        def get_price_history_every_minute(self, *a, **k):
            return "bars"

        def place_order(self, *a):
            raise AssertionError("must never be reached")

    with pytest.raises(PermissionError):
        ReadOnlyClient(Fake()).get_price_history_every_minute
    c = ReadOnlyClient(Fake(), extra=ReadOnlyClient.PRICE_HISTORY)
    assert c.get_price_history_every_minute("/NQ") == "bars"
    with pytest.raises(PermissionError):
        c.place_order
    with pytest.raises(PermissionError):  # only market-data calls can be granted
        ReadOnlyClient(Fake(), extra=frozenset({"place_order"})).place_order


def test_archiver_chunks_merges_and_drops_incomplete_minute(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("arch", ROOT / "bots/schwab_archiver.py")
    arch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(arch)
    monkeypatch.setattr(arch, "OUT", tmp_path)
    monkeypatch.setattr(arch.time, "sleep", lambda s: None)
    now = datetime(2026, 10, 21, 10, 0, 30, tzinfo=NY)
    calls = []

    class Resp:
        def __init__(self, c):
            self.c = c

        def raise_for_status(self):
            pass

        def json(self):
            return {"candles": self.c}

    class Fake:
        def get_price_history_every_minute(self, sym, start_datetime, end_datetime, need_extended_hours_data):
            calls.append((start_datetime, end_datetime))
            out, t = [], max(start_datetime, now - timedelta(minutes=5)).replace(second=0, microsecond=0)
            while t <= end_datetime and t <= now:
                out.append(dict(datetime=int(t.timestamp() * 1000), open=1.0, high=2.0, low=0.5, close=1.5, volume=10))
                t += timedelta(minutes=1)
            return Resp(out)

    r = arch.update(Fake(), "/NQ", 45, now)
    assert len(calls) == 3                                      # 45 days in 20-day chunks
    df = pd.read_parquet(tmp_path / "NQ_1min.parquet")
    assert df.index.max() == pd.Timestamp("2026-10-21 09:58", tz=NY)   # 09:59 still forming at 10:00:30 -> dropped
    r2 = arch.update(Fake(), "/NQ", 45, now + timedelta(minutes=2))
    assert r2["added"] == 2 and r2["bars"] == r["bars"] + 2


def test_bot_catch_up_after_restart_matches_uninterrupted_day(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("nqbot", ROOT / "bots/nq_paper_bot.py")
    nb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nb)
    seed = '{"date": "2026-03-02", "pdh": 100, "pdl": 90, "pdc": 99}'
    hm = lambda i: f"{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}"
    bars = [bar(hm(i), 99, 99.5, 98.8, 99.2) for i in range(9)] + [bar("09:39", 99.2, 100.6, 99.1, 100.5)]
    bars += [bar(hm(10 + i), 100.5, 100.8, 100.2, 100.6) for i in range(30)]
    for name in ("full", "restart"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "seed_levels.json").write_text(seed)
    full = nb.Bot(tmp_path / "full", "/NQ", Alerter({}))
    for b in bars:
        full.on_bar(b)
    first = nb.Bot(tmp_path / "restart", "/NQ", Alerter({}))
    for b in bars[:20]:
        first.on_bar(b)                                     # first process stops after 09:49
    again = nb.Bot(tmp_path / "restart", "/NQ", Alerter({}))  # restart: same state dir, fresh engine
    for b in bars[20:]:
        again.on_bar(b)
    key = lambda e: [(t.strategy, t.entry_time, t.taken) for t in e.trades]
    assert key(again.engine) == key(full.engine) and len(key(full.engine)) >= 1
    assert again.engine.taken_open is not None and again.engine.taken_open.strategy == "L2"


def test_backfill_after_crash_rebuilds_day_quietly(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("nqbot2", ROOT / "bots/nq_paper_bot.py")
    nb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nb)
    seed = '{"date": "2026-03-02", "pdh": 100, "pdl": 90, "pdc": 99}'
    hm = lambda i: f"{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}"
    bars = [bar(hm(i), 99, 99.5, 98.8, 99.2) for i in range(9)] + [bar("09:39", 99.2, 100.6, 99.1, 100.5)]
    bars += [bar(hm(10 + i), 100.5, 100.8, 100.2, 100.6) for i in range(30)]
    for name in ("full", "crash"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "seed_levels.json").write_text(seed)
    full = nb.Bot(tmp_path / "full", "/NQ", Alerter({}))
    for b in bars:
        full.on_bar(b)
    first = nb.Bot(tmp_path / "crash", "/NQ", Alerter({}))
    for b in bars[:12]:
        first.on_bar(b)                                     # dies after 09:41 (L2 entered 09:40)
    sent = []
    again = nb.Bot(tmp_path / "crash", "/NQ", Alerter({}))
    again.alerter.send = lambda title, body, prio="default": sent.append(title)
    again.backfill(bars[:35])                               # restart at 10:05: Schwab history has every minute
    for b in bars[35:]:
        again.on_bar(b)
    key = lambda e: [(t.strategy, t.entry_time, t.taken, t.exit_time) for t in e.trades]
    assert key(again.engine) == key(full.engine)
    assert any("resumed: L2 LONG open" in s for s in sent) and not any(" now" in s for s in sent)


def _runner():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cloud_runner", ROOT / "bots/cloud_runner.py")
    cr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cr)
    return cr


def test_cloud_state_sync_round_trip(tmp_path):
    cr = _runner()
    db = f"sqlite:///{tmp_path / 'state.db'}"
    a = tmp_path / "a"
    (a / "state/nqbot/bars").mkdir(parents=True)
    (a / "data/schwab").mkdir(parents=True)
    (a / "state/nqbot/schwab_token.json").write_text('{"token": 1}')
    (a / "state/nqbot/bars/2026-10-21.csv").write_text("time,open\n")
    (a / "data/schwab/NQ_1min.parquet").write_bytes(b"\x00\x01")
    (a / "data/schwab/x.tmp").write_text("half-written")
    s = cr.StateSync(cr.FileStore(db), root=a)
    assert s.save() == 3 and s.save() == 0                       # unchanged files are not saved again
    (a / "state/nqbot/schwab_token.json").write_text('{"token": 2}')
    assert s.save() == 1                                          # token refresh is picked up
    b = tmp_path / "b"                                            # a fresh container
    assert cr.StateSync(cr.FileStore(db), root=b).restore() == 3
    assert (b / "state/nqbot/schwab_token.json").read_text() == '{"token": 2}'
    assert (b / "data/schwab/NQ_1min.parquet").read_bytes() == b"\x00\x01" and not (b / "data/schwab/x.tmp").exists()
    assert oct((b / "state/nqbot/schwab_token.json").stat().st_mode)[-3:] == "600"


def test_cloud_archive_schedule():
    cr = _runner()
    assert cr.archive_due(datetime(2026, 10, 21, 18, 30, tzinfo=NY), None)
    assert not cr.archive_due(datetime(2026, 10, 21, 18, 29, tzinfo=NY), None)
    assert not cr.archive_due(datetime(2026, 10, 21, 20, 0, tzinfo=NY), "2026-10-21")
    assert not cr.archive_due(datetime(2026, 10, 25, 19, 0, tzinfo=NY), None)   # Sunday


def test_dockerignore_keeps_secrets_and_data_out():
    ignored = (ROOT / ".dockerignore").read_text().split()
    for p in ("data/", "state/", ".env", "deploy/nq_paper_bot.env", "reports/"):
        assert p in ignored


def test_cloud_db_url_from_northflank_link():
    cr = _runner()
    assert cr.db_url({"NF_NQBOT_DB_POSTGRES_URI": "postgresql://in", "NF_NQBOT_DB_EXTERNAL_POSTGRES_URI": "postgresql://out"}) == "postgresql://in"
    assert cr.db_url({"STATE_DB_URL": "sqlite:///x", "POSTGRES_URI": "postgresql://in"}) == "sqlite:///x"
    assert cr.db_url({"JDBC_POSTGRES_URI": "jdbc:x"}) is None


def test_daily_trades_file(tmp_path):
    import csv
    from quant.live.ledger import Ledger
    lv = DayLevels(105, 95, 100)
    eng, _ = run(lv, [bar("09:30", 104, 104.5, 103, 103.2), bar("09:31", 103.2, 103.3, 101, 101.5),
                      bar("09:32", 101.5, 101.6, 99.8, 100.2)])          # GF-1 short 103.2 -> target 100
    led = Ledger(tmp_path)
    led.close_day(eng, "test")
    led.close_day(eng, "test")                                           # closing twice must not duplicate
    rows = list(csv.DictReader((tmp_path / "ledger/2026/trades_0302.csv").open()))
    assert [r["strategy"] for r in rows] == ["GF-1", "TOTAL"] and rows[0]["book"] == "US"
    t = rows[0]
    assert (t["side"], t["entry_time"], t["exit_time"], t["exit_reason"]) == ("SHORT", "09:31", "09:33", "target")
    assert float(t["entry_price"]) == 103.2 and float(t["exit_price"]) == 100.0
    assert float(t["pnl_points"]) == pytest.approx(2.2) and float(t["pnl_usd"]) == pytest.approx(44.0)
    assert float(rows[1]["pnl_usd"]) == 44.0 and rows[1]["side"] == "1 trades" and rows[1]["exit_reason"] == "1W / 0L"
    assert len(list(csv.DictReader((tmp_path / "ledger/daily.csv").open()))) == 1
    quiet, _ = run(DayLevels(105, 95, 100), [bar("09:30", 99, 99.5, 98.8, 99.2)])
    led2 = Ledger(tmp_path / "q")
    led2.close_day(quiet, "test")                                        # no trades: file still written, TOTAL 0
    rows = list(csv.DictReader((tmp_path / "q/ledger/2026/trades_0302.csv").open()))
    assert [r["strategy"] for r in rows] == ["TOTAL"] and float(rows[0]["pnl_usd"]) == 0


def test_ledger_migrates_old_trades_csv(tmp_path):
    import csv
    from quant.live.ledger import Ledger
    (tmp_path / "ledger").mkdir()
    old = "day,strategy,side,taken,skip_reason,signal_time,entry_time,entry,stop,target,exit_time,exit,exit_reason,pnl_pts,pnl_usd,r_multiple\n"
    old += "2026-10-21,ORB-C,long,True,,t,2026-10-21T10:03:00-04:00,1,0,2,2026-10-21T12:40:00-04:00,2,target,1,20,1\n"
    (tmp_path / "ledger/trades.csv").write_text(old)
    Ledger(tmp_path)
    rows = list(csv.DictReader((tmp_path / "ledger/trades.csv").open()))
    assert rows[0]["book"] == "US" and rows[0]["strategy"] == "ORB-C"


def _trade(side=1, entry=100.0, stop=90.0, target=None, policy=None):
    from quant.live.engine import Trade
    t = Trade("S3", side, DAY.replace(hour=10), entry, stop, target, DAY.replace(hour=10))
    t.attach(policy)
    return t


def test_exit_policy_cap_breakeven_trail_time():
    from quant.live.engine import ExitPolicy
    t = _trade(policy=ExitPolicy(max_loss_pts=5))
    assert t.stop == 95 and t.risk0 == 10                       # stop capped at 5 pts; R stays on the original 10
    assert t.check(bar("10:00", 100, 101, 94.5, 95)) and t.exit == 95 and t.exit_reason == "stop"
    assert t.r_multiple == pytest.approx((95 - 100 - 1) / 10)
    t = _trade(policy=ExitPolicy(be_trigger_pts=3))
    assert not t.check(bar("10:00", 100, 103.5, 99, 103))      # +3.5 reached: stop moves to entry + 1 for the NEXT bar
    assert t.stop == 101
    assert t.check(bar("10:01", 103, 103, 100.5, 101)) and t.exit == 101 and t.exit_reason == "dynamic stop"
    t = _trade(policy=ExitPolicy(be_trigger_pts=2, trail_pts=3))
    t.check(bar("10:00", 100, 108, 100, 107))                  # best 108 -> stop max(101, 105)
    assert t.stop == 105
    t.check(bar("10:01", 107, 106, 105.5, 106))                 # no new high: stop stays 105, not hit
    assert t.exit is None and t.stop == 105
    t = _trade(side=-1, entry=100, stop=110, policy=ExitPolicy(time_stop_min=2))
    assert not t.check(bar("10:00", 100, 101, 99, 100.5))      # 1 minute in, losing: keep
    assert t.check(bar("10:01", 100.5, 101, 100, 100.25))      # 2 minutes in, not in profit -> exit at close
    assert t.exit == 100.25 and t.exit_reason == "time stop"
    t = _trade(side=-1, entry=100, stop=110, policy=ExitPolicy(time_stop_min=1))
    assert not t.check(bar("10:00", 100, 100.5, 98, 99))       # in profit at the time limit: keeps running


def test_exit_policy_never_loosens_and_stop_first():
    from quant.live.engine import ExitPolicy
    t = _trade(stop=97, policy=ExitPolicy(max_loss_pts=5, trail_pts=10))
    assert t.stop == 97                                         # cap only tightens; trail 10 behind would be looser
    t.check(bar("10:00", 100, 100.5, 99, 100))
    assert t.stop == 97
    t = _trade(target=102, policy=ExitPolicy(be_trigger_pts=1))
    assert t.check(bar("10:00", 100, 103, 89, 101)) and t.exit_reason == "stop"   # stop and target same bar: stop


def test_live_l2_exit_reports_stop_moves():
    from quant.live.engine import LIVE_EXITS
    lv = DayLevels(pdh=100, pdl=0, pdc=99)                        # R = 100: L2's own stop would be 25 below entry
    bars = [bar(f"09:{30 + i:02d}", 99, 99.5, 98.8, 99.2) for i in range(9)] + [bar("09:39", 99.2, 100.6, 99.1, 100.5)]
    bars += [bar("09:40", 100.5, 180, 100.2, 179), bar("09:41", 179, 200, 170, 199), bar("09:42", 199, 199, 140, 150)]
    eng = DayEngine(DAY, lv, exits=LIVE_EXITS)
    moves = []                                                     # alerts are formatted as each event happens
    for b in bars:
        moves += [describe(e, "/NQ")[0] for e in eng.on_bar(b) if e.kind == "stop"]
    l2 = [t for t in eng.trades if t.strategy == "L2"][0]
    assert moves == ["L2: move stop to 130.00", "L2: move stop to 150.00"]  # best 180 -> trail 130; best 200 -> 150
    assert l2.exit_reason == "dynamic stop" and l2.exit == 150.0   # hit at 09:42


def test_night_routing_weekends_and_holidays(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("nqbot3", ROOT / "bots/nq_paper_bot.py")
    nb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nb)
    bot = nb.Bot(tmp_path, "/NQ", Alerter({}))
    at = lambda y, m, d, hh, mm: datetime(y, m, d, hh, mm, tzinfo=NY)
    assert bot.night_of(at(2026, 10, 18, 18, 5)).isoformat() == "2026-10-19"   # Sunday evening -> Monday session
    assert bot.night_of(at(2026, 10, 21, 3, 0)).isoformat() == "2026-10-21"    # Wednesday 03:00 -> Wednesday
    assert bot.night_of(at(2026, 10, 21, 12, 0)) is None                       # US session hours
    assert bot.night_of(at(2026, 11, 25, 20, 0)) is None                       # Wed evening before Thanksgiving: no night
    assert bot.night_of(at(2026, 11, 26, 18, 30)).isoformat() == "2026-11-27"  # Thanksgiving evening -> Friday session


def test_night_engine_books():
    from quant.live.overnight import NightEngine, NightLevels
    D = datetime(2026, 10, 21)                                    # night starts 2026-10-20 18:00 ET
    eng = NightEngine(D, NightLevels(110, 90, 100))               # R0 = 20 -> drift stop 5 pts below entry
    first = Bar(datetime(2026, 10, 20, 18, 0, tzinfo=NY), 100, 100.5, 99.5, 100)
    ev = eng.on_bar(first)
    drift = [t for t in eng.trades if t.strategy == "DRIFT"][0]
    assert drift.entry == 100 and drift.stop == 95 and drift.taken and ev[0].kind == "entry"
    eng.on_bar(Bar(datetime(2026, 10, 20, 18, 1, tzinfo=NY), 100, 100, 94, 95))
    assert drift.exit == 95 and drift.exit_reason == "stop"
    ev = eng.close()
    assert ev[-1].kind == "night_end" and eng.closed
