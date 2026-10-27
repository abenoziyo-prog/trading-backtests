"""Daily status report (quant.live.status): totals, kill-switch states and the ledger hook."""
import csv
import json
from datetime import date, datetime

from quant.live.ledger import TRADE_FIELDS, Ledger
from quant.live.status import BASELINE, build, drawdown, kill_switch, losing_streak

BASE = {"window": "test", "books": {}, "strategies": {
    "ORB-C": {"win_pct": 58.8, "pf": 1.41, "avg": 361.0, "worst_streak": 4, "max_dd": -20000.0},
    "DRIFT": {"win_pct": 42.0, "pf": 1.37, "avg": 348.0, "worst_streak": 8, "max_dd": -38000.0}}}


def trade(day, strategy, entry, exit_, pnl, book="US", taken=True):
    return dict(day=day, strategy=strategy, side="long", taken=taken, skip_reason="", signal_time=entry, entry_time=entry,
                entry=1, stop=0, target="", exit_time=exit_, exit=2, exit_reason="target", pnl_pts=pnl / 20,
                pnl_usd=pnl, r_multiple=1, book=book)


def write(path, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_helpers():
    assert drawdown([100, -300, 50, -100]) == -350
    assert losing_streak([-1, 5, -2, -3]) == 2
    assert kill_switch([-1000.0] * 3, BASE["strategies"]["DRIFT"])[2] == "✅ OK"
    assert kill_switch([-1000.0] * 6, BASE["strategies"]["DRIFT"])[2] == "⚠️ WATCH"   # 6 of 11 losses
    assert kill_switch([-1000.0] * 12, BASE["strategies"]["DRIFT"])[2] == "⛔ PAUSE"  # streak 12 > 11
    assert kill_switch([-31000.0], BASE["strategies"]["ORB-C"])[2] == "⛔ PAUSE"      # beyond 1.5 x -20,000


def test_build(tmp_path):
    p = tmp_path / "trades.csv"
    write(p, [trade("2026-10-21", "ORB-C", "2026-10-21T09:48:00-04:00", "2026-10-21T12:25:00-04:00", 3055.0),
              trade("2026-10-22", "DRIFT", "2026-10-21T18:00:00-04:00", "2026-10-21T18:03:00-04:00", -1730.0, "overnight-drift"),
              trade("2026-10-22", "S3", "2026-10-22T10:00:00-04:00", "2026-10-22T10:06:00-04:00", 99.0, taken=False)])
    md = build(p, BASE, now=datetime(2026, 10, 22, 16, 5))
    assert "**Final P&L +$1,325.00** over 2 trades (1 W / 1 L) in 2 sessions." in md
    assert "| Wed 10-21 | ORB-C | Long | 2h 37m | ✅ Winner | US | +$3,055.00 |" in md
    assert "| Thu 10-22 | DRIFT | Long | 0h 03m | ❌ Loser | Overnight | −$1,730.00 |" in md
    assert "| DRIFT | −$1,730.00 of −$57,000.00 (3%) | 1 of 11 | ✅ OK |" in md
    assert "S3" not in md.split("## Trade log")[1]  # skipped signals are not trades


def test_ledger_writes_status(tmp_path):
    led = Ledger(tmp_path)
    write(led.trades_path, [trade("2026-10-21", "ORB-C", "2026-10-21T09:48:00-04:00", "2026-10-21T12:25:00-04:00", 3055.0)])
    led.write_day_file(date(2026, 10, 21))
    assert "FINAL +$3,055.00" in (led.dir / "status.md").read_text()


def test_shipped_baseline_covers_live_strategies():
    base = json.loads(BASELINE.read_text())
    assert set(base["strategies"]) == {"GF-1", "ORB-C", "S3", "L2", "DRIFT", "TOKYO", "EU_PRE", "LONDON", "ASIA_BO"}
    assert round(base["books"]["US"]["net"]) == 203365
