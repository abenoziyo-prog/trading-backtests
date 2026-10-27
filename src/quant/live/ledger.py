"""Paper ledger under <dir>/ledger/: every trade (taken and skipped), a daily summary, and one file per session of
executed trades (<year>/trades_MMDD.csv). A session D covers the night before it (D-1 18:00 -> D 09:29, books
"overnight-drift" and "overnight-tactical") and the US session (book "US"). Files are rebuilt from trades.csv, so
restarts never duplicate or lose rows."""
from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path

from quant.live.status import write_status

log = logging.getLogger("nqbot.ledger")

TRADE_FIELDS = ["day", "strategy", "side", "taken", "skip_reason", "signal_time", "entry_time", "entry", "stop", "target",
                "exit_time", "exit", "exit_reason", "pnl_pts", "pnl_usd", "r_multiple", "book"]
DAY_FIELDS = ["day", "trades_taken", "wins", "losses", "net_pnl_usd", "cum_pnl_usd", "signals_skipped", "levels"]
# per-session file of executed trades (times ET; P&L for 1 NQ contract per trade at $20/pt incl. the 1 pt round trip)
DAY_FILE_FIELDS = ["date", "book", "strategy", "side", "contracts", "entry_time", "entry_price", "stop", "target", "exit_time",
                   "exit_price", "exit_reason", "pnl_points", "pnl_usd", "r_multiple"]
BOOK_OF = {"DRIFT": "overnight-drift", "TOKYO": "overnight-tactical", "EU_PRE": "overnight-tactical",
           "LONDON": "overnight-tactical", "ASIA_BO": "overnight-tactical"}


def book_of(strategy: str) -> str:
    return BOOK_OF.get(strategy, "US")


class Ledger:
    def __init__(self, root: Path):
        self.dir = root / "ledger"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.trades_path, self.days_path = self.dir / "trades.csv", self.dir / "daily.csv"
        self._migrate()

    def _migrate(self) -> None:
        """Older trades.csv files have no 'book' column: they only hold US-session trades."""
        if not self.trades_path.exists():
            return
        with self.trades_path.open() as f:
            r = csv.DictReader(f)
            if "book" in (r.fieldnames or []):
                return
            rows = list(r)
        with self.trades_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
            w.writeheader()
            w.writerows([{**row, "book": "US"} for row in rows])

    def _append(self, path: Path, fields: list[str], rows: list[dict]) -> None:
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if new:
                w.writeheader()
            w.writerows(rows)

    def _read(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        with path.open() as f:
            return list(csv.DictReader(f))

    def cum_pnl(self) -> float:
        rows = self._read(self.days_path)
        return float(rows[-1]["cum_pnl_usd"]) if rows else 0.0

    def _has(self, d: str, books: set[str]) -> bool:
        return any(r["day"] == d and r.get("book", "US") in books for r in self._read(self.trades_path))

    def _rows(self, trades, d: str) -> list[dict]:
        return [dict(day=d, strategy=t.strategy, side="long" if t.side == 1 else "short", taken=t.taken, skip_reason=t.skip_reason,
                     signal_time=t.signal_time.isoformat(), entry_time=t.entry_time.isoformat(), entry=t.entry, stop=t.stop,
                     target="" if t.target is None else t.target, exit_time=t.exit_time.isoformat() if t.exit_time else "",
                     exit="" if t.exit is None else t.exit, exit_reason=t.exit_reason or "",
                     pnl_pts="" if t.pnl_pts is None else round(t.pnl_pts, 4), pnl_usd="" if t.pnl_usd is None else round(t.pnl_usd, 2),
                     r_multiple="" if t.r_multiple is None else round(t.r_multiple, 4), book=book_of(t.strategy))
                for t in trades]

    def close_night(self, night) -> Path:
        """Record a finished night (NightEngine) once, and refresh that session's day file."""
        d = night.day.date().isoformat()
        if not self._has(d, {"overnight-drift", "overnight-tactical"}):
            self._append(self.trades_path, TRADE_FIELDS, self._rows(night.trades, d))
        return self.write_day_file(night.day.date())

    def close_day(self, eng, levels_note: str) -> dict:
        """Record a finished US session (DayEngine) once; the daily row covers the whole session (night + US)."""
        d = eng.day.date().isoformat()
        if not self._has(d, {"US"}):
            self._append(self.trades_path, TRADE_FIELDS, self._rows(eng.trades, d))
        rows = [r for r in self._read(self.trades_path) if r["day"] == d]
        taken = [float(r["pnl_usd"]) for r in rows if r["taken"] == "True" and r["pnl_usd"] != ""]
        done = [r for r in self._read(self.days_path) if r["day"] == d]
        if done:
            row = done[-1]
        else:
            net = sum(taken)
            row = dict(day=d, trades_taken=len(taken), wins=sum(p > 0 for p in taken), losses=sum(p <= 0 for p in taken),
                       net_pnl_usd=round(net, 2), cum_pnl_usd=round(self.cum_pnl() + net, 2),
                       signals_skipped=sum(r["taken"] != "True" for r in rows), levels=levels_note)
            self._append(self.days_path, DAY_FIELDS, [row])
        self.write_day_file(eng.day.date())
        return row

    def write_day_file(self, day: date) -> Path:
        """Executed trades of one session: ledger/<year>/trades_MMDD.csv (entry, exit, P&L), last row = TOTAL.
        Overnight trades show their date with the time (they start the evening before)."""
        d = day.isoformat()
        rows = [r for r in self._read(self.trades_path) if r["day"] == d and r["taken"] == "True" and r["exit"] != ""]
        rows.sort(key=lambda r: r["entry_time"])
        path = self.dir / str(day.year) / f"trades_{day:%m%d}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)

        def hm(iso: str) -> str:
            return iso[11:16] if iso[:10] == d else f"{iso[5:10]} {iso[11:16]}"

        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=DAY_FILE_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow(dict(date=d, book=r.get("book", "US"), strategy=r["strategy"], side=r["side"].upper(), contracts=1,
                                entry_time=hm(r["entry_time"]), entry_price=r["entry"], stop=round(float(r["stop"]), 2),
                                target="" if r["target"] == "" else round(float(r["target"]), 2), exit_time=hm(r["exit_time"]),
                                exit_price=round(float(r["exit"]), 2), exit_reason=r["exit_reason"],
                                pnl_points=round(float(r["pnl_pts"]), 2), pnl_usd=round(float(r["pnl_usd"]), 2),
                                r_multiple=round(float(r["r_multiple"]), 2)))
            pnl = [float(r["pnl_usd"]) for r in rows]
            w.writerow(dict(date=d, strategy="TOTAL", side=f"{len(rows)} trades",
                            exit_reason=f"{sum(p > 0 for p in pnl)}W / {sum(p <= 0 for p in pnl)}L",
                            pnl_points=round(sum(float(r["pnl_pts"]) for r in rows), 2), pnl_usd=round(sum(pnl), 2)))
        try:  # the report must never stop the bot
            write_status(self.dir)
        except Exception:
            log.exception("status.md not written")
        return path
