"""Parity check: the live engine (quant.live) must reproduce the backtest trades of system R exactly.

    python scripts/check_live_engine.py

Replays 18 months of NSX US-session bars through quant.live.engine and compares, per strategy, every
shadow trade (entry/exit time, prices, P&L) with reports/optimizer/library.parquet, and the picker's taken
trades with reports/optimizer/trades_R.csv. Exits non-zero on any mismatch.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from quant.live.replay import replay, trades_frame

ROOT = Path(__file__).resolve().parents[1]
CANDS = {"GF-1": "gapfade|confirm|full|gap|up|1m", "ORB-C": "orb|OR15|opposite|1R|long|all|3m", "S3": "level|S3|1m", "L2": "level|L2|10m"}


def main() -> int:
    spec = importlib.util.spec_from_file_location("lr", ROOT / "scripts" / "us_level_reactions_tf.py")
    lr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lr)
    rth = lr.rth_minutes("NSX")
    lv = pd.read_csv(ROOT / "reports/us_session_levels/daily_levels_NSX.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    engines = [e for e, _ in replay(rth, lv)]
    live = trades_frame(engines)
    live["entry_time"] = pd.to_datetime(live.entry_time, utc=True)
    live["exit_time"] = pd.to_datetime(live.exit_time, utc=True)
    lib = pd.read_parquet(ROOT / "reports/optimizer/library.parquet")
    ok = True
    for name, cand in CANDS.items():
        a = live[live.strategy == name].sort_values("entry_time").reset_index(drop=True)
        b = lib[lib.cand == cand].copy()
        b["entry_time"] = pd.to_datetime(b.entry_time, utc=True)
        b["exit_time"] = pd.to_datetime(b.exit_time, utc=True)
        b = b.sort_values("entry_time").reset_index(drop=True)
        same = (len(a) == len(b) and (a.entry_time.values == b.entry_time.values).all()
                and (a.exit_time.values == b.exit_time.values).all() and np.allclose(a.pnl_usd, b.pnl_usd, atol=1e-6))
        print(f"{name:6s} live {len(a):4d} trades ${a.pnl_usd.sum():>10,.0f} | backtest {len(b):4d} trades ${b.pnl_usd.sum():>10,.0f} | {'MATCH' if same else 'MISMATCH'}")
        if not same:
            ok = False
            m = a.merge(b, on="entry_time", how="outer", suffixes=("_live", "_bt"), indicator=True)
            diff = m[(m._merge != "both") | (m.exit_time_live != m.exit_time_bt) | ((m.pnl_usd_live - m.pnl_usd_bt).abs() > 1e-6)]
            print(diff[["entry_time", "_merge", "exit_time_live", "exit_time_bt", "pnl_usd_live", "pnl_usd_bt"]].head(10).to_string(index=False))
    taken = live[live.taken].sort_values("entry_time").reset_index(drop=True)
    ref = pd.read_csv(ROOT / "reports/optimizer/trades_R.csv")
    ref["entry_time"] = pd.to_datetime(ref.entry_time, utc=True)
    ref = ref.sort_values("entry_time").reset_index(drop=True)
    same = len(taken) == len(ref) and (taken.entry_time.values == ref.entry_time.values).all() and np.allclose(taken.pnl_usd, ref.pnl_usd, atol=1e-6)
    print(f"Picker live {len(taken)} trades ${taken.pnl_usd.sum():,.0f} | backtest {len(ref)} trades ${ref.pnl_usd.sum():,.0f} | {'MATCH' if same else 'MISMATCH'}")
    ok &= bool(same)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
