"""GEX state at entry (quant.live.gex_tags) and the P&L-by-regime section of status.md."""
import csv
from datetime import datetime

import pandas as pd
import pytest

from quant.live.gex_tags import tag
from quant.live.ledger import TRADE_FIELDS
from quant.live.status import gex_section, write_status


def snapshot_rows(asof, qqq_regime, spx_regime, flip_pct):
    base = dict(asof=asof, contracts=100, net_gex_0dte=0, gamma_flip=1, expiries=5, call_volume=1, put_volume=1, max_oi_strike=1)
    return [dict(base, symbol="QQQ", spot=720.0, net_gex=1e9 if qqq_regime == "positive" else -1e9, regime=qqq_regime,
                 spot_vs_flip_pct=flip_pct, call_wall=725.0, put_wall=715.0),
            dict(base, symbol="$NDX", spot=29600.0, net_gex=1e9, regime="positive", spot_vs_flip_pct=None, call_wall=29700.0,
                 put_wall=29500.0),
            dict(base, symbol="$SPX", spot=6850.0, net_gex=1e9 if spx_regime == "positive" else -1e9, regime=spx_regime,
                 spot_vs_flip_pct=0.5, call_wall=6900.0, put_wall=6800.0)]


@pytest.fixture
def world(tmp_path):
    gex = tmp_path / "gex"
    gex.mkdir()
    rows = (snapshot_rows("2026-10-27T09:20-04:00", "positive", "positive", 0.4)
            + [dict(asof="2026-10-27T09:20-04:00", symbol="/NQ", error="400")]
            + snapshot_rows("2026-10-27T16:20-04:00", "negative", "positive", -0.6))
    pd.DataFrame(rows).to_csv(gex / "gex_daily.csv", index=False)
    state = tmp_path / "state"
    (state / "bars").mkdir(parents=True)
    idx = pd.date_range("2026-10-27 09:00", "2026-10-27 20:00", freq="1min", tz="America/New_York")
    bars = pd.DataFrame(dict(time=[t.isoformat() for t in idx], open=29700.0, high=29701.0, low=29699.0, close=29700.0))
    bars.to_csv(state / "bars" / "2026-10-27.csv", index=False)
    flow = tmp_path / "flow" / "QQQ" / "2026-10-27"
    flow.mkdir(parents=True)
    pd.DataFrame([dict(asof="2026-10-27T10:00:00-04:00", symbol="QQQ", spot=720.0, put_call=pc, occ=f"Q{pc}", expiry="2026-10-27",
                       dte=0, strike=720.0, bid=1, ask=1.1, last=1, mark=1.05, volume=10, open_interest=oi, iv=25.0, delta=0.5,
                       gamma=0.05, multiplier=100) for pc, oi in (("CALL", 100), ("PUT", 5000))]).to_csv(flow / "1000.csv.gz", index=False)
    return dict(state=state, gex=gex, flow=tmp_path / "flow", archive=tmp_path / "none.parquet")


def trades(times):
    return pd.DataFrame(dict(entry_time=times, pnl_usd=[100.0, -50.0, 30.0][:len(times)]))


def test_tags_use_the_state_known_at_entry(world):
    t = tag(trades(["2026-10-27T09:10:00-04:00", "2026-10-27T10:02:00-04:00", "2026-10-27T18:00:00-04:00"]),
            world["state"], world["gex"], world["flow"], world["archive"])
    assert t.loc[0, "regime_qqq"] == "" and t.loc[0, "gex_asof"] == ""            # before the first snapshot
    assert t.loc[1, "regime_qqq"] == "positive" and t.loc[1, "flip_side"] == "above flip"
    assert t.loc[1, "regime_combo"] == "QQQ pos / SPX pos" and t.loc[1, "regime_ndx"] == "positive"
    assert t.loc[1, "call_wall_pts"] == pytest.approx(725 * 29700 / 720 - 29700, abs=0.01)   # QQQ wall in NQ points
    assert t.loc[1, "put_wall_pts"] < 0
    assert t.loc[1, "near_regime"] == "negative" and t.loc[1, "near_asof"].startswith("2026-10-27T10:00")
    assert t.loc[2, "regime_qqq"] == "negative" and t.loc[2, "flip_side"] == "below flip"   # 16:20 row for the evening
    assert t.loc[2, "near_regime"] == ""                                            # no capture within 10 minutes


def test_status_has_regime_section_and_tagged_csv(world, tmp_path):
    led = world["state"] / "ledger"
    led.mkdir()
    with (led / "trades.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        w.writeheader()
        for et, pnl, taken in (("2026-10-27T10:02:00-04:00", 300.0, True), ("2026-10-27T10:30:00-04:00", -100.0, True),
                               ("2026-10-27T11:00:00-04:00", 50.0, False)):
            w.writerow(dict(day="2026-10-27", strategy="ORB-C", side="long", taken=taken, entry_time=et,
                            exit_time="2026-10-27T12:00:00-04:00", pnl_usd=pnl, book="US"))
    md = gex_section(led, gex_dir=world["gex"], flow_dir=world["flow"], archive=world["archive"])
    assert "## P&L by GEX regime" in md and "| positive | 2 | 1 / 1 | +$200.00 |" in md
    assert "### Shadow book" in md and "No trades yet." in md
    g = pd.read_csv(led / "trades_gex.csv")
    assert len(g) == 2 and set(g.regime_qqq) == {"positive"}


def test_status_survives_missing_gex_files(tmp_path):
    led = tmp_path / "ledger"
    led.mkdir()
    with (led / "trades.csv").open("w", newline="") as f:
        csv.DictWriter(f, fieldnames=TRADE_FIELDS).writeheader()
    md = write_status(led, now=datetime(2026, 10, 27, 16, 5)).read_text()
    assert "## P&L by GEX regime" in md and "## Shadow book" in md
