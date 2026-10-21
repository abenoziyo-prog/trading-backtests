"""How well do the live bot's bars match Schwab's final history? Run after a live day (and after the nightly archive).

    python scripts/stream_audit_report.py                 # all audited days
    python scripts/stream_audit_report.py 2026-10-22

Reads state/nqbot/audit/minutes_*.csv and late_*.csv (written by the bot), state/nqbot/bars/ (bars the
engine used) and data/schwab/NQ_1min.parquet (Schwab history archived later = the reference).
Reports per day: where bars came from, how long after each minute they were finalized, how often the raw
stream differed from the bar used, late stream updates, and the key number: minutes where the bar the bot
traded on differs from the archived history bar (US session 09:30-16:00 ET and all hours).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state" / "nqbot"
NY = "America/New_York"


def main() -> None:
    days = sys.argv[1:] or sorted(p.stem.split("_")[1] for p in (STATE / "audit").glob("minutes_*.csv"))
    if not days:
        sys.exit("no audit files yet: the bot writes them once it runs with the settled-bar feed")
    arch_p = ROOT / "data/schwab/NQ_1min.parquet"
    arch = pd.read_parquet(arch_p) if arch_p.exists() else None
    for d in days:
        m = pd.read_csv(STATE / "audit" / f"minutes_{d}.csv")
        late_p = STATE / "audit" / f"late_{d}.csv"
        late = pd.read_csv(late_p) if late_p.exists() else pd.DataFrame(columns=["rx_after_end_s", "diff_vs_used"])
        q = m.finalized_after_end_s.quantile([0.5, 0.9, 0.99]).round(1).tolist()
        print(f"\n=== {d}: {len(m)} minutes")
        print("  source:", m.source.value_counts().to_dict())
        print(f"  finalized after minute end (s): median {q[0]}, p90 {q[1]}, p99 {q[2]}, max {m.finalized_after_end_s.max():.1f}")
        print(f"  stream's last update after minute end (s): median {m.last_rx_after_end_s.median():.1f}, "
              f"p99 {m.last_rx_after_end_s.quantile(0.99):.1f}")
        print(f"  raw stream differed from the bar used: {(m.stream_vs_used > 0).mean():.1%} of minutes "
              f"(first stream message: {(m.first_stream_vs_used > 0).mean():.1%}); max {m.stream_vs_used.max():.2f} pts")
        print(f"  late stream updates after finalizing: {len(late)}, differing {int((late.diff_vs_used.fillna(0) > 0).sum())}")
        bars_p = STATE / "bars" / f"{d}.csv"
        if arch is None or not bars_p.exists():
            print("  vs archived history: not available yet (run bots/schwab_archiver.py after the day)")
            continue
        used = pd.read_csv(bars_p)
        used.index = pd.to_datetime(used.time).dt.tz_convert(NY)
        used = used[used.index.isin(pd.to_datetime(m.minute).dt.tz_convert(NY))]  # only minutes finalized by the new feed
        j = used.join(arch, rsuffix="_h", how="inner")
        cols = ["open", "high", "low", "close"]
        diff = np.abs(j[cols].to_numpy() - j[[c + "_h" for c in cols]].to_numpy()).max(axis=1)
        rth = (j.index.hour * 60 + j.index.minute >= 570) & (j.index.hour * 60 + j.index.minute < 960)
        print(f"  bars used vs archived history: {len(j)} minutes compared; differ {np.mean(diff > 0):.2%} "
              f"(US session {np.mean(diff[rth] > 0) if rth.any() else float('nan'):.2%}); max {diff.max() if len(diff) else 0:.2f} pts")


if __name__ == "__main__":
    main()
