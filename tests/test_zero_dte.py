"""0DTE dealer gamma (quant.live.flow.dealer_gamma_0dte) and the pre-registered study script (scripts/zero_dte_study.py)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.live import flow

ROOT = Path(__file__).resolve().parents[1]
NY = "America/New_York"


def capture_module():
    spec = importlib.util.spec_from_file_location("cap", ROOT / "bots/option_flow_capture.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def write_day(root, day, sym="QQQ", buy_calls=0, n=80, spot0=600.0, oi_call=1000, oi_put=1000, am=False, root_name="QQQ"):
    """Captures every 5 minutes from 09:30; contracts 595-605 expiring today (+ an AM-settled one if am)."""
    cap = capture_module()
    d = root / flow.folder(sym) / day
    d.mkdir(parents=True, exist_ok=True)
    vol = 0
    for i in range(n):
        ts = pd.Timestamp(f"{day} 09:30", tz=NY) + pd.Timedelta(minutes=5 * i)
        spot = spot0 + np.sin(i / 5)
        rows = []
        for k in range(595, 606):
            for pc in ("C", "P"):
                is_buy = pc == "C" and k == 600 and buy_calls
                v = vol if is_buy else 0
                last, bid, ask = (1.2, 1.0, 1.2) if is_buy else (1.1, 1.0, 1.2)
                rows.append([ts.isoformat(), sym, spot, spot - .01, spot + .01, 1, "CALL" if pc == "C" else "PUT",
                             f"{root_name}{day[2:4]}{day[5:7]}{day[8:]}{pc}{k:05d}000", day, 0, float(k), bid, ask, last, 1.1, 1, 1, 1,
                             v, oi_call if pc == "C" else oi_put, 20.0, .5, .05, -1, .1, 1, (i if is_buy else 0), 100])
        if am:
            rows.append([ts.isoformat(), sym, spot, 0, 0, 1, "CALL", f"SPX2{day[2:4]}C00600000", day, 0, 600.0, 1, 1.2, 1.1, 1.1, 1, 1, 1,
                         0, 10**7, 20.0, .5, .05, -1, .1, 1, 0, 100])
        pd.DataFrame(rows, columns=cap.FIELDS).to_csv(d / f"{ts:%H%M}.csv.gz", index=False)
        vol += buy_calls


def test_dealer_gamma_signs(tmp_path):
    write_day(tmp_path, "2026-10-27", buy_calls=50, oi_call=5000, oi_put=1000, n=20, am=True)
    g = flow.dealer_gamma_0dte(flow.load_day(tmp_path, "QQQ", "2026-10-27"))
    assert (g.g_oi > 0).all()                                 # more call than put open interest: naive sign positive
    assert g.g_tick.iloc[0] == 0 and (g.g_tick.iloc[2:] < 0).all() and g.g_tick.iloc[-1] < g.g_tick.iloc[5]   # customers buy
    assert g.pin.mode()[0] == 600.0                           # the AM-settled contract (10M OI) is ignored
    assert np.allclose(g.g_total, g.g_oi + g.g_tick)


def test_study_runs_end_to_end(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("z", ROOT / "scripts/zero_dte_study.py")
    z = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(z)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-10-01", periods=5)]
    for i, d in enumerate(days):
        write_day(tmp_path / "flow", d, oi_call=1000 + 3000 * (i % 2), oi_put=3000, buy_calls=10)
    idx = pd.date_range(f"{days[0]} 09:00", f"{days[-1]} 16:30", freq="1min", tz=NY)
    c = 25000 + np.cumsum(np.random.default_rng(0).normal(0, 3, len(idx)))
    pd.DataFrame(dict(open=c, high=c + 2, low=c - 2, close=c, volume=1), index=idx).to_parquet(tmp_path / "nq.parquet")
    monkeypatch.setattr(z, "FLOW", tmp_path / "flow")
    monkeypatch.setattr(z, "NQ", tmp_path / "nq.parquet")
    monkeypatch.setattr(z, "OUT", tmp_path / "out")
    z.main()
    md = (tmp_path / "out" / "ZERO_DTE_STUDY.md").read_text()
    assert "## QQQ: 5 sessions" in md and "Z1 range30" in md and "Verdicts wait for 40 sessions (35 to go)" in md
