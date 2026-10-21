"""Minute-bar store for the live bot: every streamed /NQ bar is saved, and the day's levels are computed
from it exactly as in reports/us_session_levels:

  PDH / PDL / PDC   high / low / last close of the previous trading day's US session (09:30-16:00 ET)
  london_high       02:00-05:00 ET high of the current day, only if >= 80% of its 180 minutes are present
Files: <dir>/bars/YYYY-MM-DD.csv (one per calendar day, NY time) and <dir>/seed_levels.json (manual seed for
the first day, when the previous session was not recorded).
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from .calendar import Calendar
from .engine import Bar, DayLevels

FIELDS = ["time", "open", "high", "low", "close"]


class BarStore:
    def __init__(self, root: Path, tz):
        self.dir = root / "bars"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.root, self.tz = root, tz
        self._seen: set[str] = set()
        for p in sorted(self.dir.glob("*.csv"))[-3:]:  # bars already saved by an earlier run (restart / back-fill)
            with p.open() as f:
                self._seen |= {r["time"] for r in csv.DictReader(f)}

    def _path(self, d: date) -> Path:
        return self.dir / f"{d.isoformat()}.csv"

    def append(self, bar: Bar) -> bool:
        """Save a finalized bar (ignores duplicates). Returns True if new."""
        key = bar.t.isoformat()
        if key in self._seen:
            return False
        self._seen.add(key)
        p = self._path(bar.t.date())
        new = not p.exists()
        with p.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(FIELDS)
            w.writerow([key, bar.open, bar.high, bar.low, bar.close])
        return True

    def bars(self, d: date) -> list[Bar]:
        p = self._path(d)
        if not p.exists():
            return []
        out, seen = [], set()
        with p.open() as f:
            for r in csv.DictReader(f):
                if r["time"] in seen:
                    continue
                seen.add(r["time"])
                out.append(Bar(datetime.fromisoformat(r["time"]).astimezone(self.tz), float(r["open"]), float(r["high"]),
                               float(r["low"]), float(r["close"])))
        out.sort(key=lambda b: b.t)
        return out

    def levels(self, today: date, cal: Calendar) -> tuple[DayLevels | None, str]:
        """Levels for `today`, or (None, reason). The seed file is used when the previous session is missing."""
        prev = cal.previous_trading_day(today)
        rth = [b for b in self.bars(prev) if 9 * 60 + 30 <= b.mod < 16 * 60]
        london = [b for b in self.bars(today) if 2 * 60 <= b.mod < 5 * 60]
        lh = max(b.high for b in london) if len(london) >= 0.8 * 180 else None
        if len(rth) >= 200:
            return DayLevels(max(b.high for b in rth), min(b.low for b in rth), rth[-1].close, lh), \
                f"from recorded {prev} session ({len(rth)} bars); London {'ok' if lh else 'coverage < 80%'}"
        seed = self.root / "seed_levels.json"
        if seed.exists():
            s = json.loads(seed.read_text())
            if s.get("date") == today.isoformat():
                return DayLevels(float(s["pdh"]), float(s["pdl"]), float(s["pdc"]), lh if lh is not None else s.get("london_high")), \
                    "from seed_levels.json"
        return None, f"previous session {prev} has only {len(rth)} recorded bars and no seed for {today}"
