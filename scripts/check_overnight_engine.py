"""Parity check: the live overnight engine (quant.live.overnight) must reproduce the overnight backtests exactly.

    python scripts/check_overnight_engine.py

Replays 18 months of NSX nights (18:00 -> 09:29 ET) bar by bar and compares every trade per component with
reports/overnight/strategy_trades.parquet (DRIFT, TOKYO, EU_PRE, LONDON) and reports/london/trades.parquet
(ASIA_BO), and the tactical book's taken trades with reports/overnight/system_two_books_trades.csv.
Exits non-zero on any mismatch.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant.live.engine import Bar  # noqa: E402
from quant.live.overnight import NightEngine, NightLevels  # noqa: E402

CANDS = {"DRIFT": "DRIFT|18:00-09:29|all|stop0.25|0m",
         "TOKYO": "KICKOFF|tokyo 09:00 JST|first5m|fade|stopextreme|exit120|0m",
         "EU_PRE": "KICKOFF|eu pre-open 07:00 UK|first5m|fade|stop0.3|exit120|0m",
         "LONDON": "KICKOFF|london 08:00 UK|first15m|fade|stopextreme|exit120|0m",
         "ASIA_BO": "ASIA_BO|asia_ext|from03:00|until08:00|short|stop_mid|tgt_None|5m"}


def replay_nights():
    spec = importlib.util.spec_from_file_location("onr", ROOT / "scripts" / "overnight_research.py")
    onr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(onr)
    rows = []
    for n in onr.load_nights():
        day = n["day"].to_pydatetime()
        eng = NightEngine(datetime(day.year, day.month, day.day), NightLevels(n["PDH"], n["PDL"], n["PDC"]),
                          t0=n["t0"].to_pydatetime())
        for t, o, h, l, c in zip(n["t"], n["o"], n["h"], n["l"], n["c"]):
            eng.on_bar(Bar(t.to_pydatetime(), float(o), float(h), float(l), float(c)))
        eng.close()
        rows += [dict(day=n["day"], strategy=t.strategy, side=t.side, entry_time=t.entry_time, exit_time=t.exit_time,
                      pnl_usd=t.pnl_usd, taken=t.taken) for t in eng.trades]
    t = pd.DataFrame(rows)
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    return t


def compare(name: str, a: pd.DataFrame, b: pd.DataFrame) -> bool:
    a = a.sort_values("entry_time").reset_index(drop=True)
    b = b.sort_values("entry_time").reset_index(drop=True)
    same = (len(a) == len(b) and (a.entry_time.values == b.entry_time.values).all()
            and (a.exit_time.values == b.exit_time.values).all() and np.allclose(a.pnl_usd, b.pnl_usd, atol=1e-6))
    print(f"{name:22s} live {len(a):4d} trades ${a.pnl_usd.sum():>10,.0f} | backtest {len(b):4d} trades ${b.pnl_usd.sum():>10,.0f} | "
          f"{'MATCH' if same else 'MISMATCH'}")
    if not same:
        m = a.merge(b, on="entry_time", how="outer", suffixes=("_live", "_bt"), indicator=True)
        bad = m[(m._merge != "both") | (m.exit_time_live != m.exit_time_bt) | ((m.pnl_usd_live - m.pnl_usd_bt).abs() > 1e-6)]
        print(bad[["entry_time", "_merge", "exit_time_live", "exit_time_bt", "pnl_usd_live", "pnl_usd_bt"]].head(8).to_string(index=False))
    return same


def main() -> int:
    live = replay_nights()
    st = pd.read_parquet(ROOT / "reports/overnight/strategy_trades.parquet")
    lon = pd.read_parquet(ROOT / "reports/london/trades.parquet")
    ok = True
    for name, cand in CANDS.items():
        bt = (lon if name == "ASIA_BO" else st)
        bt = bt[bt.cand == cand][["day", "entry_time", "exit_time", "pnl_usd"]].copy()
        for c in ("entry_time", "exit_time"):
            bt[c] = pd.to_datetime(bt[c], utc=True)
        lv = live[live.strategy == name]
        days = set(lv.day) & set(pd.to_datetime(bt.day)) if name == "ASIA_BO" else set(lv.day) | set(pd.to_datetime(bt.day))
        ok &= compare(name, lv[lv.day.isin(days)], bt[pd.to_datetime(bt.day).isin(days)])
    sysb = pd.read_csv(ROOT / "reports/overnight/system_two_books_trades.csv")
    tb = sysb[sysb.book == "tactical"].copy()
    tb["day"] = pd.to_datetime(tb.day)
    for c in ("entry_time", "exit_time"):
        tb[c] = pd.to_datetime(tb[c], utc=True)
    taken = live[(live.strategy != "DRIFT") & live.taken]
    days = set(taken.day) & set(pd.to_datetime(lon.day))  # ASIA_BO only exists on nights the London study covered
    ok &= compare("tactical book (taken)", taken[taken.day.isin(days)], tb[tb.day.isin(days)])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
