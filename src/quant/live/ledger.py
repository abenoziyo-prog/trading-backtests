"""Paper ledger: every trade (taken and skipped) and a daily summary, as CSV under <dir>/ledger/."""
from __future__ import annotations

import csv
from pathlib import Path

from .engine import DayEngine

TRADE_FIELDS = ["day", "strategy", "side", "taken", "skip_reason", "signal_time", "entry_time", "entry", "stop", "target",
                "exit_time", "exit", "exit_reason", "pnl_pts", "pnl_usd", "r_multiple"]
DAY_FIELDS = ["day", "trades_taken", "wins", "losses", "net_pnl_usd", "cum_pnl_usd", "signals_skipped", "levels"]


class Ledger:
    def __init__(self, root: Path):
        self.dir = root / "ledger"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.trades_path, self.days_path = self.dir / "trades.csv", self.dir / "daily.csv"

    def _append(self, path: Path, fields: list[str], rows: list[dict]) -> None:
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if new:
                w.writeheader()
            w.writerows(rows)

    def cum_pnl(self) -> float:
        if not self.days_path.exists():
            return 0.0
        with self.days_path.open() as f:
            rows = list(csv.DictReader(f))
        return float(rows[-1]["cum_pnl_usd"]) if rows else 0.0

    def close_day(self, eng: DayEngine, levels_note: str) -> dict:
        trades = eng.trades
        d = eng.day.date().isoformat()
        self._append(self.trades_path, TRADE_FIELDS, [dict(
            day=d, strategy=t.strategy, side="long" if t.side == 1 else "short", taken=t.taken, skip_reason=t.skip_reason,
            signal_time=t.signal_time.isoformat(), entry_time=t.entry_time.isoformat(), entry=t.entry, stop=t.stop,
            target="" if t.target is None else t.target, exit_time=t.exit_time.isoformat() if t.exit_time else "",
            exit="" if t.exit is None else t.exit, exit_reason=t.exit_reason or "",
            pnl_pts="" if t.pnl_pts is None else round(t.pnl_pts, 4), pnl_usd="" if t.pnl_usd is None else round(t.pnl_usd, 2),
            r_multiple="" if t.r_multiple is None else round(t.r_multiple, 4)) for t in trades])
        taken = [t for t in trades if t.taken and t.pnl_usd is not None]
        net = sum(t.pnl_usd for t in taken)
        row = dict(day=d, trades_taken=len(taken), wins=sum(t.pnl_usd > 0 for t in taken), losses=sum(t.pnl_usd <= 0 for t in taken),
                   net_pnl_usd=round(net, 2), cum_pnl_usd=round(self.cum_pnl() + net, 2),
                   signals_skipped=sum(not t.taken for t in trades), levels=levels_note)
        self._append(self.days_path, DAY_FIELDS, [row])
        return row
