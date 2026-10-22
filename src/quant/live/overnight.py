"""Bar-by-bar overnight engine (night D: D-1 18:00 ET -> D 09:29 ET), two books (reports/overnight/REPORT.md):

  drift book     DRIFT     long at the night's first bar (open), stop 0.25 R0, exit at the 09:28 bar's close
  tactical book  one position at a time over four components (priority on same-minute ties, in this order):
                 TOKYO     09:00 JST: after 5 minutes trade against the move; stop beyond the 5-min extreme + 0.05 R0;
                           exit 120 minutes after entry
                 EU_PRE    07:00 UK: after 5 minutes trade against the move; stop 0.30 R0; exit after 120 minutes
                 LONDON    08:00 UK: after 15 minutes trade against the move; stop beyond the 15-min extreme + 0.05 R0;
                           exit after 120 minutes
                 ASIA_BO   from 03:00 ET, a 5-minute candle (clock-aligned at 03:00) closes below the 20:00-02:00 low
                           after the previous candle closed inside that range -> short; stop at the range midpoint;
                           entries until 08:00; exit at the 09:29 bar's close; re-entries after an exit
R0 = previous US session range. Fills and exits reproduce scripts/overnight_strategies.py and
scripts/london_research.py exactly (scripts/check_overnight_engine.py): entry at the signal close, exits checked
on 1-minute bars from the entry bar, same-minute stop + target = stop, time exits at the close of the last bar
before the exit time. 1 contract per book, $20/pt, 1 pt round trip. Pure logic: no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .engine import Bar, Candles, Event, ONE_MIN, Trade

NY = ZoneInfo("America/New_York")
FLAT_REL = 929   # 09:29 ET in minutes after 18:00: drift / kick-offs exit at the close of the last bar before it
ABO_END_REL = 930  # 09:30 ET: ASIA_BO exits at the close of the last bar before it (the 09:29 bar)
LAST_REL = 929    # the night's last bar (09:29); the US session starts at 09:30
TACTICAL = ["TOKYO", "EU_PRE", "LONDON", "ASIA_BO"]
KICKOFFS = {  # name: (timezone, local open, first-window minutes, stop kind)
    "TOKYO": ("Asia/Tokyo", time(9, 0), 5, "extreme"),
    "EU_PRE": ("Europe/London", time(7, 0), 5, 0.30),
    "LONDON": ("Europe/London", time(8, 0), 15, "extreme"),
}


@dataclass(frozen=True)
class NightLevels:
    pdh: float
    pdl: float
    pdc: float

    @property
    def R(self) -> float:
        return self.pdh - self.pdl


def night_start(day: datetime) -> datetime:
    """18:00 ET on the calendar day before trading day `day`."""
    d = day.date() - timedelta(days=1)
    return datetime(d.year, d.month, d.day, 18, 0, tzinfo=NY)


class Timed:
    """A trade with a time exit: the close of the last bar that starts before `end` (minutes after 18:00)."""

    def __init__(self, trade: Trade, end_rel: int):
        self.t, self.end = trade, end_rel
        self.last: Bar | None = None

    def on_bar(self, bar: Bar, rel: int) -> bool:
        """True when the trade closed (on this bar, or at the previous bar's close if this bar is past the end)."""
        if rel >= self.end:
            self.t._close(self.last.t + ONE_MIN, self.last.close, "time")
            return True
        if self.t.check(bar):
            return True
        self.last = bar
        if rel == self.end - 1:
            self.t._close(bar.t + ONE_MIN, bar.close, "time")
            return True
        return False


class Kickoff:
    def __init__(self, name: str, day: datetime, t0: datetime, R: float):
        tz, t, self.w, self.stop_kind = KICKOFFS[name]
        local = datetime.combine(day.date(), t, tzinfo=ZoneInfo(tz))
        self.name, self.R = name, R
        self.e = int((local.astimezone(NY) - t0).total_seconds() // 60)
        self.n = 0
        self.first_open = self.hi = self.lo = self.last_close = None
        self.done = self.e < 0 or self.e + self.w + 130 > FLAT_REL

    def on_bar(self, bar: Bar, rel: int) -> tuple[Trade, int] | None:
        """Returns (trade, end_rel) when the entry is decided; the trade's first bar is the NEXT bar (or this bar
        if this bar is already past the first window)."""
        if self.done:
            return None
        if self.e <= rel < self.e + self.w:
            if self.n == 0:
                self.first_open, self.hi, self.lo = bar.open, bar.high, bar.low
            self.hi, self.lo = max(self.hi, bar.high), min(self.lo, bar.low)
            self.n, self.last_close = self.n + 1, bar.close
            return None
        if rel >= self.e + self.w:
            self.done = True
            return self._decide(signal_t=bar.t)
        return None

    def window_closed_by(self, bar: Bar, rel: int) -> bool:
        return not self.done and rel == self.e + self.w - 1

    def _decide(self, signal_t: datetime) -> tuple[Trade, int] | None:
        if self.n < 0.8 * self.w or self.first_open is None:
            return None
        move = self.last_close - self.first_open
        if move == 0:
            return None
        side = -1 if move > 0 else 1  # fade
        entry = self.last_close
        stop = ((self.hi + 0.05 * self.R) if side == -1 else (self.lo - 0.05 * self.R)) if self.stop_kind == "extreme" \
            else entry - side * self.stop_kind * self.R
        if (entry - stop) * side <= 0:
            return None
        return Trade(self.name, side, signal_t, entry, stop, None, signal_t), min(FLAT_REL, self.e + self.w + 120)


class AsiaBreakout:
    """ASIA_BO: short-only breakout of the 20:00-02:00 range from 03:00 on 5-minute candles."""

    name = "ASIA_BO"

    def __init__(self):
        self.h = self.l = None
        self.n_range = 0
        self.last_close_before = None   # close of the last bar before 03:00
        self.candles: Candles | None = None
        self.prev_inside: bool | None = None
        self.busy_until: datetime | None = None
        self.open: Trade | None = None
        self.done = False

    def range_bar(self, bar: Bar, rel: int) -> None:
        if 120 <= rel < 480:  # 20:00-02:00
            self.h = bar.high if self.h is None else max(self.h, bar.high)
            self.l = bar.low if self.l is None else min(self.l, bar.low)
            self.n_range += 1

    def on_bar(self, bar: Bar, rel: int) -> list[tuple[str, tuple | None, Bar | None]]:
        """Feed a bar; returns closed candles to evaluate as (kind, candle, next_bar)."""
        if rel < 540:  # before 03:00: remember the last close (the 'previous candle' of the first 03:00 candle)
            self.last_close_before = bar.close
            return []
        if self.done:
            return []
        if self.candles is None:
            if self.h is None or self.n_range < 0.8 * 360:
                self.done = True
                return []
            self.candles = Candles(5, 180)
            H, L = self.h, self.l
            b = self.last_close_before
            self.prev_inside = True if b is None else (L <= b <= H)
        out = []
        closed, ends_now = self.candles.add(bar)
        if closed is not None:
            out.append(("before", closed, bar))
        if ends_now:
            out.append(("now", self.candles.pop(), None))
        return out


@dataclass
class NightEngine:
    """One night (trading day `day`). on_bar() for each 1-minute bar from 18:00 to 09:29 ET, then close()."""

    day: datetime
    levels: NightLevels
    t0: datetime = None
    drift: Timed | None = None
    drift_done: bool = False
    tactical: list = field(default_factory=list)      # Timed shadow trades (all components)
    taken_open: Timed | None = None
    free_at: datetime | None = None
    trades: list[Trade] = field(default_factory=list)
    closed: bool = False
    last_bar: Bar | None = None

    def __post_init__(self):
        self.t0 = self.t0 or night_start(self.day)
        R = self.levels.R
        self.kick = [Kickoff(k, self.day, self.t0, R) for k in ("TOKYO", "EU_PRE", "LONDON")]
        self.abo = AsiaBreakout()
        self.pending: list[tuple[Trade, int]] = []   # tactical entries decided at a bar's close, entering next bar

    def rel(self, bar: Bar) -> int:
        return int((bar.t - self.t0).total_seconds() // 60)

    def on_bar(self, bar: Bar) -> list[Event]:
        rel = self.rel(bar)
        if self.closed or rel < 0 or rel > LAST_REL or (self.last_bar is not None and bar.t <= self.last_bar.t):
            return []
        self.last_bar = bar
        ev: list[Event] = []
        R = self.levels.R
        # drift book: enters at the first bar's open, managed from this bar
        if not self.drift_done and self.drift is None:
            t = Trade("DRIFT", 1, bar.t, bar.open, bar.open - 0.25 * R, None, bar.t)
            t.attach(None)
            t.risk0 = 0.25 * R
            t.taken = True
            self.drift = Timed(t, FLAT_REL)
            self.trades.append(t)
            ev.append(Event("entry", bar.t, t))
        # tactical: entries decided earlier enter on this bar
        entered = []
        for t, end in self.pending:
            t.entry_time = bar.t
            entered.append((t, end))
        self.pending = []
        # kick-offs whose first window ended before this bar (last window bar missing) decide now, entering here
        for k in self.kick:
            d = k.on_bar(bar, rel)
            if d is not None:
                t, end = d
                t.entry_time = bar.t
                entered.append((t, end))
        # ASIA_BO: range, candles closed by this bar's arrival (enter here) or by this bar (enter next bar)
        self.abo.range_bar(bar, rel)
        for kind, cd, nb in self.abo.on_bar(bar, rel):
            d = self._abo_eval(cd)
            if d is not None:
                if kind == "before":
                    d[0].entry_time = bar.t
                    entered.append(d)
                else:
                    self.pending.append(d)
        entered.sort(key=lambda x: (x[0].entry_time, TACTICAL.index(x[0].strategy)))
        for t, end in entered:
            t.attach(None)
            tm = Timed(t, end)
            self.tactical.append(tm)
            self.trades.append(t)
            if t.strategy == "ASIA_BO":
                self.abo.open = t
            if self.taken_open is None and (self.free_at is None or t.entry_time >= self.free_at):
                t.taken = True
                self.taken_open = tm
                ev.append(Event("entry", t.entry_time, t))
            else:
                t.skip_reason = "position open"
                ev.append(Event("skip", t.entry_time, t))
        # exits on this bar
        if self.drift is not None and not self.drift_done and self.drift.on_bar(bar, rel):
            self.drift_done = True
            ev.append(Event("exit", self.drift.t.exit_time, self.drift.t))
        for tm in list(self.tactical):
            if tm.t.exit is None and tm.on_bar(bar, rel):
                if tm.t.strategy == "ASIA_BO":
                    self.abo.busy_until, self.abo.open = tm.t.exit_time, None
                if tm is self.taken_open:
                    self.taken_open, self.free_at = None, tm.t.exit_time
                    ev.append(Event("exit", tm.t.exit_time, tm.t))
        # kick-off windows ending with this bar: decide now, enter next bar
        for k in self.kick:
            if k.window_closed_by(bar, rel):
                k.done = True
                d = k._decide(signal_t=bar.t + ONE_MIN)
                if d is not None:
                    self.pending.append(d)
        if self.pending and self.taken_open is None:
            p = min(self.pending, key=lambda x: TACTICAL.index(x[0].strategy))[0]
            ev.append(Event("signal", p.signal_time, p))
        if rel == LAST_REL:
            ev += self.close()
        return ev

    def _abo_eval(self, cd: tuple) -> tuple[Trade, int] | None:
        o, h, l, c, last_mod = cd
        a = self.abo
        H, L = a.h, a.l
        inside = L <= c <= H
        last_t = datetime(self.day.year, self.day.month, self.day.day, last_mod // 60, last_mod % 60, tzinfo=NY)
        if a.busy_until is not None and last_t < a.busy_until or a.open is not None or any(p[0].strategy == "ASIA_BO" for p in self.pending):
            a.prev_inside = inside
            return None
        fresh = c < L and a.prev_inside
        a.prev_inside = inside
        if not fresh:
            return None
        if last_mod + 1 > 480:
            a.done = True
            return None
        stop = (H + L) / 2
        if (c - stop) * -1 <= 0:
            return None
        return Trade("ASIA_BO", -1, last_t + ONE_MIN, c, stop, None, last_t + ONE_MIN), ABO_END_REL

    def close(self) -> list[Event]:
        """End of the night (after the 09:29 bar, or if bars stop): anything still open exits at the last bar's close."""
        if self.closed:
            return []
        self.closed = True
        ev = []
        lb = self.last_bar
        if self.drift is not None and not self.drift_done and lb is not None:
            self.drift.t._close(lb.t + ONE_MIN, lb.close, "time")
            self.drift_done = True
            ev.append(Event("exit", self.drift.t.exit_time, self.drift.t))
        for tm in self.tactical:
            if tm.t.exit is None and lb is not None:
                tm.t._close(lb.t + ONE_MIN, lb.close, "time")
                if tm is self.taken_open:
                    self.taken_open = None
                    ev.append(Event("exit", tm.t.exit_time, tm.t))
        self.pending = []
        ev.append(Event("night_end", lb.t + ONE_MIN if lb else self.t0, None))
        return ev
