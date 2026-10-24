import importlib
import math

import numpy as np
import pandas as pd

from lab import gates


def _trades(days, pnl, cand=None):
    t = pd.DataFrame({"day": pd.to_datetime(days), "side": 1, "pnl_usd": pnl, "r_multiple": np.array(pnl) / 1000.0})
    t["entry_time"] = pd.to_datetime(t.day).dt.tz_localize("America/New_York") + pd.Timedelta(hours=10)
    t["exit_time"] = t.entry_time + pd.Timedelta(minutes=30)
    t["entry_time"], t["exit_time"] = t.entry_time.dt.tz_convert("UTC"), t.exit_time.dt.tz_convert("UTC")
    if cand:
        t["cand"] = cand
    return t


def test_t_required_rises_with_ideas_tested():
    assert gates.t_required(1) == 1.5 and gates.t_required(16) == 2.5
    assert gates.t_required(64) > gates.t_required(16)


def test_consistent_edge_passes_and_luck_fails():
    days = pd.bdate_range("2025-04-14", "2026-10-02", freq="5B")      # weekly trades over the 18 months
    rng = np.random.default_rng(1)
    good = _trades(days, np.where(rng.random(len(days)) < 0.62, 1500.0, -1000.0))
    base = _trades(days[:5], [100.0] * 5, cand="BASE")
    r = gates.evaluate(good, base, ["BASE"], n_tested=1)
    assert r["gates"]["G1_sample"] and r["gates"]["G3_consistency"]
    assert r["addon"]["gain_first12"] > 0 and r["addon"]["idea_trades_taken"] > 0
    # all profit in one lucky block -> fails consistency
    lucky = _trades(days, [5000.0 if d < pd.Timestamp("2025-06-01") else -300.0 for d in days])
    assert not gates.evaluate(lucky, base, ["BASE"], n_tested=1)["gates"]["G3_consistency"]


def test_no_trades_fails_cleanly():
    empty = _trades([], [])
    base = _trades(["2025-05-01"], [100.0], cand="BASE")
    r = gates.evaluate(empty, base, ["BASE"], n_tested=3)
    assert r["verdict"] == "FAIL" and not r["gates"]["G1_sample"]


def test_template_idea_loads():
    from pathlib import Path
    from lab.harness import load_idea
    m = load_idea(Path(__file__).resolve().parents[1] / "ideas" / "_template.py")
    assert m.SESSION in ("us", "overnight") and callable(m.trades)
    assert list(m.trades(None).columns) == ["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]
