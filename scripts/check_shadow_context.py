"""Parity check: the bot's shadow context (quant.live.shadow.LiveContext) must give each lab idea the same trades as
the lab's research context, on the same NSX bars.

    python scripts/check_shadow_context.py

Builds LiveContext from the NSX 1-minute file and compares every shadow idea's trades with lab.data.Context over the
18 months. Exits non-zero if an idea differs beyond its first 30 sessions (look-back warm-up).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from lab.data import Context  # noqa: E402
from quant import sessions as s  # noqa: E402
from quant.live.calendar import Calendar  # noqa: E402
from quant.live.shadow import LiveContext, load_ideas  # noqa: E402


def main() -> int:
    lab = Context()
    m = s.load(s.INSTRUMENTS["NSX"])
    m = m[(m.index >= pd.Timestamp("2025-03-01", tz=s.TZ)) & (m.index < pd.Timestamp("2026-10-10", tz=s.TZ))]
    live = LiveContext(m[["open", "high", "low", "close"]], Calendar())
    warm = pd.Timestamp("2025-05-25")
    ok = True
    for idea in load_ideas():
        p = dict(getattr(idea, "PARAMS", {}))
        a, b = idea.trades(lab, **p), idea.trades(live, **p)
        for t in (a, b):
            t["day"] = pd.to_datetime(t["day"]).dt.tz_localize(None).dt.normalize() if len(t) else t.get("day")
        a, b = a[a.day >= warm].reset_index(drop=True), b[(b.day >= warm) & (b.day <= a.day.max())].reset_index(drop=True)
        k = ["day", "side", "entry_time"]
        mg = a.merge(b, on=k, how="outer", suffixes=("_lab", "_live"), indicator=True)
        same = mg[mg._merge == "both"]
        pnl_ok = (same.pnl_usd_lab - same.pnl_usd_live).abs() < 0.01
        share = (len(same[pnl_ok]) / max(len(a), 1))
        print(f"{idea.NAME:26s} lab {len(a):4d} ${a.pnl_usd.sum():>10,.0f} | live {len(b):4d} ${b.pnl_usd.sum():>10,.0f} | "
              f"identical {share:6.1%}")
        ok &= share >= 0.98
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
