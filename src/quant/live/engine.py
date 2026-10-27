"""Bar-by-bar engine for system R (reports/picker, reports/optimizer): GF-1, ORB-C, S3, L2 + the picker.

Feed it completed 1-minute US-session bars (09:30-16:00 ET) in time order; it returns events. The rules
reproduce the backtests exactly (scripts/open_gaps.py, scripts/open_orb.py, scripts/us_level_strategies_v2.py):

  * candles are grouped by clock time; a candle ends with its last clock minute, or when a bar of a later
    candle arrives (missing minutes never stretch a candle). Entry = the candle's close, at the next bar.
  * no entries from candles whose last minute starts at or after 15:30; everything is flat at the close.
  * exits are checked on 1-minute bars from the entry bar; stop and target in the same bar = stop.
  * each strategy runs on its own ("shadow" trades, one at a time, multiple per day); the picker takes a
    shadow trade only when no taken trade is open, same-minute ties going to the higher priority.
  * P&L: 1 NQ contract, $20/pt, 1 pt round trip, no slippage.
Pure logic: no I/O, no broker calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

US_OPEN = 9 * 60 + 30
LAST_ENTRY = 15 * 60 + 30
COST_PTS, DPP = 1.0, 20.0
PRIORITY = ["GF-1", "ORB-C", "S3", "L2"]
ONE_MIN = timedelta(minutes=1)


@dataclass(frozen=True)
class Bar:
    t: datetime  # bar start, America/New_York (tz-aware)
    open: float
    high: float
    low: float
    close: float

    @property
    def mod(self) -> int:
        return self.t.hour * 60 + self.t.minute


@dataclass(frozen=True)
class DayLevels:
    pdh: float
    pdl: float
    pdc: float
    london_high: float | None = None  # 02:00-05:00 ET high (None when coverage < 80%)

    @property
    def R(self) -> float:
        return self.pdh - self.pdl


@dataclass(frozen=True)
class ExitPolicy:
    """Dynamic exit rules on top of a strategy's own stop/target (all optional; None = rule off).
      stop_pts       set the initial stop this many points from entry, replacing the strategy's own (applied at entry)
      target_pts     set the target this many points from entry, replacing the strategy's own (applied at entry)
      max_loss_pts   cap the initial stop distance at this many points (applied at entry)
      be_trigger_pts once the trade has been this far in profit, move the stop to entry +/- be_offset_pts
      trail_pts      trail the stop this far behind the best price (from be_trigger_pts if set, else from entry)
      time_stop_min  after this many minutes, exit at the bar's close if the trade is not in profit
    Stop moves are decided at a bar's close and apply from the next bar (no intrabar look-ahead)."""

    stop_pts: float | None = None
    target_pts: float | None = None
    max_loss_pts: float | None = None
    be_trigger_pts: float | None = None
    be_offset_pts: float = 1.0
    trail_pts: float | None = None
    time_stop_min: int | None = None


@dataclass
class Trade:
    strategy: str
    side: int
    entry_time: datetime
    entry: float
    stop: float
    target: float | None
    signal_time: datetime  # when the signal was known (end of the signal candle)
    exit_time: datetime | None = None
    exit: float | None = None
    exit_reason: str | None = None
    taken: bool = False
    skip_reason: str = ""
    policy: ExitPolicy | None = None
    risk0: float | None = None  # initial risk (entry to the strategy's own stop), for R-multiples
    best: float | None = None  # best price since entry (for breakeven / trailing)

    @property
    def pnl_pts(self) -> float | None:
        return None if self.exit is None else self.side * (self.exit - self.entry) - COST_PTS

    @property
    def pnl_usd(self) -> float | None:
        return None if self.exit is None else self.pnl_pts * DPP

    @property
    def r_multiple(self) -> float | None:
        return None if self.exit is None else self.pnl_pts / (self.risk0 or abs(self.entry - self.stop))

    def attach(self, policy: ExitPolicy | None) -> None:
        """Apply exit rules at entry: set or cap the stop, set the target, remember the initial risk."""
        if policy is not None and policy.stop_pts is not None:
            self.stop = self.entry - self.side * policy.stop_pts
        if policy is not None and policy.target_pts is not None:
            self.target = self.entry + self.side * policy.target_pts
        self.risk0 = abs(self.entry - self.stop)
        self.policy, self.best = policy, self.entry
        if policy is not None and policy.max_loss_pts is not None and self.risk0 > policy.max_loss_pts:
            self.stop = self.entry - self.side * policy.max_loss_pts

    def check(self, bar: Bar) -> bool:
        """Apply one bar; True when the trade closed on it."""
        stop_hit = bar.low <= self.stop if self.side == 1 else bar.high >= self.stop
        tgt_hit = self.target is not None and (bar.high >= self.target if self.side == 1 else bar.low <= self.target)
        if stop_hit:
            moved = self.policy is not None and self.stop != self._initial_stop()  # breakeven / trailing stop
            self._close(bar.t + ONE_MIN, self.stop, "dynamic stop" if moved else "stop")
        elif tgt_hit:
            self._close(bar.t + ONE_MIN, self.target, "target")
        elif self.policy is not None:
            self._manage(bar)
        return self.exit is not None

    def _initial_stop(self) -> float:
        p = self.policy
        risk = self.risk0 if p is None or p.max_loss_pts is None else min(self.risk0, p.max_loss_pts)
        return self.entry - self.side * risk

    def _manage(self, bar: Bar) -> None:
        p, s = self.policy, self.side
        self.best = max(self.best, bar.high) if s == 1 else min(self.best, bar.low)
        gain = s * (self.best - self.entry)
        tighter = (lambda new: max(self.stop, new)) if s == 1 else (lambda new: min(self.stop, new))
        armed = p.be_trigger_pts is None or gain >= p.be_trigger_pts
        if p.be_trigger_pts is not None and armed:
            self.stop = tighter(self.entry + s * p.be_offset_pts)
        if p.trail_pts is not None and armed:
            self.stop = tighter(self.best - s * p.trail_pts)
        if p.time_stop_min is not None and (bar.t + ONE_MIN - self.entry_time).total_seconds() >= 60 * p.time_stop_min \
                and s * (bar.close - self.entry) <= 0:
            self._close(bar.t + ONE_MIN, bar.close, "time stop")

    def _close(self, t: datetime, px: float, why: str) -> None:
        self.exit_time, self.exit, self.exit_reason = t, px, why


@dataclass
class Event:
    kind: str  # "signal", "entry", "skip", "exit", "day_end"
    time: datetime
    trade: Trade | None = None
    text: str = ""


class Candles:
    """Clock-time candles of `n` minutes anchored at `anchor` (minutes after midnight)."""

    def __init__(self, n: int, anchor: int):
        self.n, self.anchor = n, anchor
        self.key = None
        self.o = self.h = self.l = self.c = None
        self.last_mod = None

    def bucket(self, mod: int) -> int:
        return (mod - self.anchor) // self.n

    def add(self, bar: Bar) -> tuple[tuple | None, bool]:
        """Add a bar. Returns (candle closed by this bar's arrival before it, this bar closes its own candle).
        A closed candle is (open, high, low, close, last_mod)."""
        k = self.bucket(bar.mod)
        closed_before = None
        if self.key is not None and k != self.key:
            closed_before = (self.o, self.h, self.l, self.c, self.last_mod)
            self.key = None
        if self.key is None:
            self.key, self.o, self.h, self.l = k, bar.open, bar.high, bar.low
        else:
            self.h, self.l = max(self.h, bar.high), min(self.l, bar.low)
        self.c, self.last_mod = bar.close, bar.mod
        ends_now = (bar.mod - self.anchor) % self.n == self.n - 1
        return closed_before, ends_now

    def pop(self) -> tuple:
        out = (self.o, self.h, self.l, self.c, self.last_mod)
        self.key = None
        return out


def _time_of(day: datetime, mod: int) -> datetime:
    return day.replace(hour=mod // 60, minute=mod % 60, second=0, microsecond=0)


class Strategy:
    """Base: one shadow position at a time; signals come from closed candles; entry at the next bar."""

    name = ""

    def __init__(self, lv: DayLevels, day: datetime):
        self.lv, self.day = lv, day
        self.open: Trade | None = None
        self.pending: Trade | None = None  # signal known, entry at the next bar
        self.done = False  # no more entries today
        self.trades: list[Trade] = []

    def on_bar(self, bar: Bar) -> list[Trade]:
        """Returns trades that entered on this bar (their exits are checked from this bar on)."""
        raise NotImplementedError

    def _enter_pending(self, bar: Bar) -> list[Trade]:
        if self.pending is None:
            return []
        t, self.pending = self.pending, None
        t.entry_time = bar.t
        self.open = t
        self.trades.append(t)
        return [t]

    def manage(self, bar: Bar) -> list[Trade]:
        """Check the open shadow trade against this bar; returns trades closed."""
        if self.open is not None and self.open.check(bar):
            t, self.open = self.open, None
            return [t]
        return []


class GF1(Strategy):
    """Gap-up fade: first 1-minute candle closes red -> short at its close, target previous close, stop open + gap.
    Skipped if the first candle already traded at the previous close. One setup per day."""

    name = "GF-1"

    def __init__(self, lv: DayLevels, day: datetime):
        super().__init__(lv, day)
        self.first: Bar | None = None
        self.low_first = None

    def on_bar(self, bar: Bar) -> list[Trade]:
        entered = self._enter_pending(bar)
        if self.done:
            return entered
        if self.first is None:
            if bar.mod >= US_OPEN + 1:  # no 09:30 bar
                self.done = True
                return entered
            self.first = bar
            self._signal(bar)
            return entered
        return entered

    def _signal(self, b: Bar) -> None:
        self.done = True
        op, pdc = b.open, self.lv.pdc
        gap = op - pdc
        if gap <= 0 or not (b.close < b.open) or b.low <= pdc:
            return
        entry, stop, target = b.close, op + gap, pdc
        if (target - entry) * -1 <= 0 or (entry - stop) * -1 <= 0:
            return
        self.pending = Trade(self.name, -1, b.t + ONE_MIN, entry, stop, target, signal_time=b.t + ONE_MIN)


class ORBC(Strategy):
    """15-minute opening range; long when a 3-minute candle closes above the OR high after the previous candle
    closed inside the range; stop = OR low, target = 1R. Re-entries on fresh breakouts after an exit."""

    name = "ORB-C"
    OR_MIN, TF = 15, 3

    def __init__(self, lv: DayLevels, day: datetime):
        super().__init__(lv, day)
        self.orh = self.orl = None
        self.or_done = False
        self.candles: Candles | None = None
        self.prev_inside = True
        self.busy_until: datetime | None = None  # exit_time of the shadow trade (candles ending before it are skipped)

    def on_bar(self, bar: Bar) -> list[Trade]:
        entered = []
        if not self.or_done:
            if bar.mod < US_OPEN + self.OR_MIN:
                self.orh = bar.high if self.orh is None else max(self.orh, bar.high)
                self.orl = bar.low if self.orl is None else min(self.orl, bar.low)
                return entered
            self.or_done = True
            if self.orh is None or self.orh - self.orl <= 0:
                self.done = True
            self.candles = Candles(self.TF, bar.mod)  # anchored at the first bar after the range
        entered += self._enter_pending(bar)
        if self.done:
            return entered
        closed, ends_now = self.candles.add(bar)
        if closed is not None:
            entered += self._evaluate(closed, next_bar=bar)
        if ends_now and not self.done:
            self._evaluate(self.candles.pop(), next_bar=None)
        return entered

    def _evaluate(self, cd: tuple, next_bar: Bar | None) -> list[Trade]:
        o, h, l, c, last_mod = cd
        inside = self.orl <= c <= self.orh
        last_t = _time_of(self.day, last_mod)
        if self.busy_until is not None and last_t < self.busy_until or self.open is not None or self.pending is not None:
            self.prev_inside = inside
            return []
        side = 1 if c > self.orh else (-1 if c < self.orl else 0)
        fresh = side != 0 and self.prev_inside
        self.prev_inside = inside
        if not fresh or side == -1:
            return []
        if last_mod + 1 > LAST_ENTRY:
            self.done = True
            return []
        risk = c - self.orl
        if risk <= 0:
            return []
        self.pending = Trade(self.name, 1, last_t + ONE_MIN, c, self.orl, c + risk, signal_time=last_t + ONE_MIN)
        return self._enter_pending(next_bar) if next_bar is not None else []

    def manage(self, bar: Bar) -> list[Trade]:
        closed = super().manage(bar)
        for t in closed:
            self.busy_until = t.exit_time
        return closed


class LevelCross(Strategy):
    """Fresh close across a level on n-minute candles (anchored 09:30); the first candle compares with the
    previous US close. Stop and target in multiples of R (previous US range); target None = hold to close."""

    def __init__(self, lv: DayLevels, day: datetime, name: str, level: float | None, side: int, n: int,
                 stop_r: float, target_r: float | None):
        super().__init__(lv, day)
        self.name, self.level, self.side, self.n = name, level, side, n
        self.stop_r, self.target_r = stop_r, target_r
        self.candles = Candles(n, US_OPEN)
        self.prev_close = lv.pdc
        self.free_from: datetime | None = None
        if level is None or not lv.R > 0:
            self.done = True

    def on_bar(self, bar: Bar) -> list[Trade]:
        entered = self._enter_pending(bar)
        if self.done:
            return entered
        closed, ends_now = self.candles.add(bar)
        if closed is not None:
            entered += self._evaluate(closed, next_bar=bar)
        if ends_now and not self.done:
            self._evaluate(self.candles.pop(), next_bar=None)
        return entered

    def _evaluate(self, cd: tuple, next_bar: Bar | None) -> list[Trade]:
        o, h, l, c, last_mod = cd
        prev, self.prev_close = self.prev_close, c
        last_t = _time_of(self.day, last_mod)
        if self.free_from is not None and last_t < self.free_from or self.open is not None or self.pending is not None:
            return []
        if not (prev <= self.level and c > self.level):  # both S3 and L2 are "cross above"
            return []
        if last_mod + 1 > LAST_ENTRY:
            self.done = True
            return []
        R = self.lv.R
        stop = c - self.side * self.stop_r * R
        target = None if self.target_r is None else c + self.side * self.target_r * R
        self.pending = Trade(self.name, self.side, last_t + ONE_MIN, c, stop, target, signal_time=last_t + ONE_MIN)
        return self._enter_pending(next_bar) if next_bar is not None else []

    def manage(self, bar: Bar) -> list[Trade]:
        closed = super().manage(bar)
        for t in closed:
            self.free_from = t.exit_time
        return closed


# Exit rules the live bot trades with (reports/exit_rules/REPORT.md, enabled 2026-10-22): L2 gets a managed exit
# (stop capped at 60 pts; at +75 pts the stop moves to entry + 1, then trails 50 pts behind the best price).
# The other strategies keep their own stops/targets. replay()/research default to no extra rules.
LIVE_EXITS: dict[str, ExitPolicy] = {"L2": ExitPolicy(max_loss_pts=60, be_trigger_pts=75, trail_pts=50)}


def make_strategies(lv: DayLevels, day: datetime) -> list[Strategy]:
    return [GF1(lv, day), ORBC(lv, day),
            LevelCross(lv, day, "S3", lv.london_high, -1, 1, 0.25, 0.25),
            LevelCross(lv, day, "L2", lv.pdh, 1, 10, 0.25, None)]


@dataclass
class DayEngine:
    """One US session. Call on_bar() for each completed 1-minute bar, then close_day()."""

    day: datetime
    levels: DayLevels
    exits: dict[str, ExitPolicy] = field(default_factory=dict)  # per strategy name, or "*" for all; empty = off
    strategies: list[Strategy] = field(default_factory=list)
    taken_open: Trade | None = None
    free_at: datetime | None = None
    last_bar: Bar | None = None
    closed: bool = False

    def __post_init__(self):
        if not self.strategies:
            self.strategies = make_strategies(self.levels, self.day)
        self._announced: set[int] = set()

    def on_bar(self, bar: Bar) -> list[Event]:
        if self.closed or not (US_OPEN <= bar.mod < 16 * 60):
            return []
        if self.last_bar is not None and bar.t <= self.last_bar.t:
            return []  # duplicate / out of order
        self.last_bar = bar
        ev: list[Event] = []
        # 1) entries on this bar (signals from candles that closed before it), in priority order
        entered = []
        for s in self.strategies:
            entered += s.on_bar(bar)
        entered.sort(key=lambda t: (t.entry_time, PRIORITY.index(t.strategy)))
        for t in entered:
            t.attach(self.exits.get(t.strategy, self.exits.get("*")))
            if self.taken_open is None and (self.free_at is None or t.entry_time >= self.free_at):
                t.taken = True
                self.taken_open = t
                ev.append(Event("entry", t.entry_time, t))
            else:
                t.skip_reason = "position open"
                ev.append(Event("skip", t.entry_time, t))
        # 2) exits checked on this bar (including trades that entered on it); a moved stop on the taken trade is
        #    reported so it can be mirrored by hand
        o = self.taken_open
        stop_before = None if o is None else o.stop
        for s in self.strategies:
            for t in s.manage(bar):
                if t.taken:
                    self.taken_open, self.free_at = None, t.exit_time
                    ev.append(Event("exit", t.exit_time, t))
        if o is not None and o.exit is None and o.stop != stop_before:
            ev.append(Event("stop", bar.t + ONE_MIN, o, text=f"{stop_before:.2f}"))
        # 3) signals that became known at this bar's close (entry at the next bar) -> early alert, only for the
        #    one the picker will take (no position open, highest priority); the others get a "skip" next bar
        new = [s.pending for s in self.strategies if s.pending is not None and id(s.pending) not in self._announced]
        for p in new:
            self._announced.add(id(p))
        if new and self.taken_open is None:
            p = min(new, key=lambda x: PRIORITY.index(x.strategy))
            ev.append(Event("signal", p.signal_time, p))
        if bar.mod == 16 * 60 - 1:
            ev += self.close_day()
        return ev

    def close_day(self) -> list[Event]:
        """Flat at the close: open shadow trades exit at the last bar's close."""
        if self.closed:
            return []
        self.closed = True
        ev = []
        for s in self.strategies:
            s.pending = None
            if s.open is not None and self.last_bar is not None:
                t = s.open
                t._close(self.last_bar.t + ONE_MIN, self.last_bar.close, "16:00 close")
                s.open = None
                if t.taken:
                    self.taken_open = None
                    ev.append(Event("exit", t.exit_time, t))
        ev.append(Event("day_end", self.last_bar.t + ONE_MIN if self.last_bar else self.day, None))
        return ev

    @property
    def trades(self) -> list[Trade]:
        return [t for s in self.strategies for t in s.trades]
