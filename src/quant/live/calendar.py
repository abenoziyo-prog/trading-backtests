"""US equity-market trading calendar for the live bot (NYSE/Nasdaq holidays and 13:00 early closes).

Hard-coded from the exchanges' published schedules; extend EXTRA_HOLIDAYS / EARLY_CLOSES in
state/calendar_overrides.json (keys "holidays", "early_closes", ISO dates) for unscheduled closures.
"""
from __future__ import annotations

import json
from datetime import date, time, timedelta
from pathlib import Path

HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
}
EARLY_CLOSES = {"2025-07-03", "2025-11-28", "2025-12-24", "2026-11-27", "2026-12-24", "2027-11-26"}


class Calendar:
    def __init__(self, overrides: Path | None = None):
        self.holidays = {date.fromisoformat(d) for d in HOLIDAYS}
        self.early = {date.fromisoformat(d) for d in EARLY_CLOSES}
        if overrides is not None and overrides.exists():
            o = json.loads(overrides.read_text())
            self.holidays |= {date.fromisoformat(d) for d in o.get("holidays", [])}
            self.early |= {date.fromisoformat(d) for d in o.get("early_closes", [])}

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def previous_trading_day(self, d: date) -> date:
        p = d - timedelta(days=1)
        while not self.is_trading_day(p):
            p -= timedelta(days=1)
        return p

    def close_time(self, d: date) -> time:
        return time(13, 0) if d in self.early else time(16, 0)
