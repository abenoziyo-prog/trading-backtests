"""NQ paper bot (system R): Schwab real-time /NQ data in, simulated fills + alerts out. Never sends orders.

    python bots/nq_paper_bot.py                       # live: stream /NQ, trade on paper, alert
    python bots/nq_paper_bot.py --test-alert          # send one test alert to the configured channels
    python bots/nq_paper_bot.py --replay 2026-09-01 2026-10-09 --state-dir /tmp/nqbot-replay
                                                      # replay NSX history through the same code path

Strategies, picker and fills are quant.live.engine (verified to reproduce the backtest exactly:
scripts/check_live_engine.py). You mirror the alerts by hand in thinkorswim paperMoney.

Environment (see deploy/nq_paper_bot.env.example): SCHWAB_API_KEY, SCHWAB_APP_SECRET, SCHWAB_TOKEN_PATH,
NQBOT_SYMBOL (default /NQ), NQBOT_STATE_DIR, and the alert channels in quant/live/alerts.py.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.live.alerts import Alerter  # noqa: E402
from quant.live.calendar import Calendar  # noqa: E402
from quant.live.engine import Bar, DayEngine  # noqa: E402
from quant.live.ledger import Ledger  # noqa: E402
from quant.live.store import BarStore  # noqa: E402

NY = ZoneInfo("America/New_York")
log = logging.getLogger("nqbot")


class Bot:
    """Routes finalized 1-minute bars to the store, the day's engine, the ledger and the alerts."""

    def __init__(self, state: Path, symbol: str, alerter: Alerter):
        self.state, self.symbol, self.alerter = state, symbol, alerter
        self.cal = Calendar(state / "calendar_overrides.json")
        self.store, self.ledger = BarStore(state, NY), Ledger(state)
        self.engine: DayEngine | None = None
        self.engine_day: date | None = None
        self.levels_note = ""
        self.no_trade_day: date | None = None
        self.last_bar_wall = time.time()
        self.quiet = False  # True while back-filling missed minutes: events are logged, not alerted

    def on_bar(self, bar: Bar) -> None:
        self.last_bar_wall = time.time()
        if not self.store.append(bar):
            return
        d = bar.t.date()
        if not self.cal.is_trading_day(d) or not (9 * 60 + 30 <= bar.mod < 16 * 60):
            return
        close_mod = self.cal.close_time(d).hour * 60
        if bar.mod >= close_mod:
            return
        if self.engine_day != d:
            self.start_day(d, bar.t)
        if self.engine is None:
            return
        for ev in self.engine.on_bar(bar):
            if self.quiet:
                log.info("back-fill %s: %s %s", bar.t.strftime("%H:%M"), ev.kind, ev.trade.strategy if ev.trade else "")
            else:
                self.alerter.event(ev, self.symbol)
        if self.engine.closed:
            self.finish_day()

    def start_day(self, d: date, t: datetime) -> None:
        self.engine_day = d
        lv, note = self.store.levels(d, self.cal)
        self.levels_note = note
        if lv is None:
            self.engine = None
            if self.no_trade_day != d:
                self.no_trade_day = d
                self.alerter.send(f"NQ bot: NO TRADING {d}", f"Levels unavailable: {note}. "
                                  "Write state/seed_levels.json (date, pdh, pdl, pdc) to trade today.", "high")
            return
        self.engine = DayEngine(t.replace(hour=0, minute=0, second=0, microsecond=0), lv)
        lh = "n/a" if lv.london_high is None else f"{lv.london_high:,.2f}"
        self.alerter.send(f"NQ bot ready {d}", f"PDH {lv.pdh:,.2f}  PDL {lv.pdl:,.2f}  PDC {lv.pdc:,.2f}  London high {lh}\n{note}", "low")
        self.catch_up(d, t)

    def catch_up(self, d: date, t: datetime) -> None:
        """After a restart mid-session, rebuild today's engine from the saved bars before `t` (no alerts),
        then report any position that is still open, so the paper ledger matches an uninterrupted run."""
        earlier = [b for b in self.store.bars(d) if 9 * 60 + 30 <= b.mod < 16 * 60 and b.t < t]
        if not earlier:
            return
        for b in earlier:
            self.engine.on_bar(b)
        done = [x for x in self.engine.trades if x.taken]
        log.info("catch-up: replayed %d saved bars (%s-%s), %d trades taken so far", len(earlier),
                 earlier[0].t.strftime("%H:%M"), earlier[-1].t.strftime("%H:%M"), len(done))
        o = self.engine.taken_open
        if o is not None and not self.quiet:  # during a back-fill, backfill() reports the position once at the end
            tgt = "hold to close" if o.target is None else f"{o.target:,.2f}"
            self.alerter.send(f"NQ bot restarted: {o.strategy} {'LONG' if o.side == 1 else 'SHORT'} still open",
                              f"Entered {o.entry_time:%H:%M} ET at {o.entry:,.2f}. Stop {o.stop:,.2f} | Target {tgt}", "high")

    def backfill(self, bars: list[Bar]) -> None:
        """Feed minutes the bot missed (from Schwab history) through the normal path without alerts, then
        report the paper position that is open now, if any."""
        new = [b for b in bars if b.t.isoformat() not in self.store._seen]
        if not new:
            return
        today = new[-1].t.date()
        self.quiet = True
        try:
            for b in new:
                if b.t.date() < today:
                    self.store.append(b)  # earlier days: record only (they feed future levels, not today's trades)
                else:
                    self.on_bar(b)
        finally:
            self.quiet = False
        log.info("back-filled %d missed minutes (%s-%s) from Schwab history", len(new), new[0].t.strftime("%m-%d %H:%M"),
                 new[-1].t.strftime("%H:%M"))
        o = self.engine.taken_open if self.engine is not None else None
        if o is not None:
            tgt = "hold to close" if o.target is None else f"{o.target:,.2f}"
            self.alerter.send(f"NQ bot resumed: {o.strategy} {'LONG' if o.side == 1 else 'SHORT'} open",
                              f"Entered {o.entry_time:%H:%M} ET at {o.entry:,.2f}. Stop {o.stop:,.2f} | Target {tgt}", "high")

    def finish_day(self) -> None:
        if self.engine is None:
            return
        for ev in self.engine.close_day():
            self.alerter.event(ev, self.symbol)
        row = self.ledger.close_day(self.engine, self.levels_note)
        self.alerter.send(f"NQ bot day summary {row['day']}",
                          f"{row['trades_taken']} trades ({row['wins']}W/{row['losses']}L), net ${float(row['net_pnl_usd']):,.0f}, "
                          f"cumulative ${float(row['cum_pnl_usd']):,.0f}", "low")
        self.engine = None

    def on_clock(self, now: datetime) -> None:
        """Called every second: flatten at the close (13:00 on early-close days) if bars stopped early."""
        if self.engine is not None and self.engine_day == now.date():
            close = datetime.combine(now.date(), self.cal.close_time(now.date()), NY)
            if now >= close + timedelta(seconds=15):
                self.finish_day()


# ------------------------------------------------------------------ live
class AuditLog:
    """Per-day CSVs of bar timing: audit/minutes_YYYY-MM-DD.csv (every finalized minute: source, delays,
    stream-vs-used difference) and audit/late_YYYY-MM-DD.csv (stream updates after a minute was finalized)."""

    def __init__(self, state: Path):
        self.dir = state / "audit"
        self.dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, kind: str, row: dict) -> None:
        import csv

        p = self.dir / f"{'minutes' if kind == 'minute' else 'late'}_{row['minute'][:10]}.csv"
        new = not p.exists()
        with p.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
        if kind == "late" and row.get("diff_vs_used"):
            log.info("late stream update for %s, %.1f s after the minute ended, differs by %.2f pts",
                     row["minute"][11:16], row["rx_after_end_s"], row["diff_vs_used"])


async def run_live(bot: Bot, env: dict) -> None:
    from quant.live.schwab_feed import (HistoryResolver, MinuteAggregator, ReadOnlyClient, history_bars, make_client,
                                        refresh_token_hours_left, stream_bars)

    holder: dict = {}
    use_history = env.get("NQBOT_BAR_SOURCE", "history") == "history"
    agg = MinuteAggregator(bot.on_bar, settle_s=float(env.get("NQBOT_SETTLE_S", 5)), max_wait_s=float(env.get("NQBOT_MAX_WAIT_S", 30)),
                           resolver=HistoryResolver(lambda: holder["client"], bot.symbol) if use_history else None,
                           audit=AuditLog(bot.state))
    log.info("bars: %s, settle %.0f s, history wait up to %.0f s", "Schwab history (stream as fallback)" if use_history else "stream",
             agg.settle, agg.max_wait)
    stop = asyncio.Event()
    last_token_warn = 0.0
    backoff = 5

    async def clock() -> None:
        while not stop.is_set():
            agg.tick()
            bot.on_clock(datetime.now(NY))
            await asyncio.sleep(1)

    asyncio.create_task(clock())
    while True:
        try:
            client = make_client(env["SCHWAB_API_KEY"], env["SCHWAB_APP_SECRET"], env["SCHWAB_TOKEN_PATH"],
                                 extra=ReadOnlyClient.PRICE_HISTORY)
            holder["client"] = client
            now = datetime.now(NY)
            prev = bot.cal.previous_trading_day(now.date())  # always cover yesterday's whole US session (for levels)
            since = min(now - timedelta(hours=20), datetime(prev.year, prev.month, prev.day, 9, 0, tzinfo=NY))
            missed = history_bars(client, bot.symbol, since, now)
            bot.backfill(missed)
            if missed:
                agg.last_final = max(agg.last_final or missed[-1].t, missed[-1].t)
            hours = refresh_token_hours_left(client)
            if hours < 48 and time.time() - last_token_warn > 12 * 3600:
                last_token_warn = time.time()
                bot.alerter.send("NQ bot: Schwab login needed soon",
                                 f"Refresh token expires in {max(hours, 0):.0f} h. Run bots/schwab_login.py before then.", "high")
            await stream_bars(client, bot.symbol, agg, stop)
            backoff = 5
        except Exception as exc:  # network drop, expired token, Schwab outage: alert, wait, retry
            log.warning("stream stopped: %r; retrying in %ss", exc, backoff)
            if backoff >= 60:
                bot.alerter.send("NQ bot: data stream down", f"{exc!r}. Retrying every {backoff}s. "
                                 "If the token expired, run bots/schwab_login.py.", "high")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


# ------------------------------------------------------------------ replay
def run_replay(bot: Bot, start: str, end: str) -> None:
    """Feed historical NSX 1-minute bars (all hours, so levels come from the store) through the bot."""
    from quant import sessions as s

    m = s.load(s.INSTRUMENTS["NSX"])
    lo = pd_ts(start) - timedelta(days=5)
    m = m[(m.index >= lo) & (m.index < pd_ts(end) + timedelta(days=1))]
    for t, o, h, l, c in zip(m.index, m.open.to_numpy(), m.high.to_numpy(), m.low.to_numpy(), m.close.to_numpy()):
        tt = t.to_pydatetime()
        if tt.date() < date.fromisoformat(start) and 9 * 60 + 30 <= tt.hour * 60 + tt.minute < 16 * 60:
            bot.store.append(Bar(tt, float(o), float(h), float(l), float(c)))  # warm-up: record only
            continue
        if bot.engine is not None and bot.engine_day != tt.date():
            bot.finish_day()
        bot.on_bar(Bar(tt, float(o), float(h), float(l), float(c)))
    bot.finish_day()


def pd_ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=NY)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", default=os.environ.get("NQBOT_STATE_DIR", str(ROOT / "state" / "nqbot")))
    ap.add_argument("--symbol", default=os.environ.get("NQBOT_SYMBOL", "/NQ"))
    ap.add_argument("--replay", nargs=2, metavar=("START", "END"))
    ap.add_argument("--test-alert", action="store_true")
    ap.add_argument("--quiet-alerts", action="store_true", help="log alerts only (no ntfy/Telegram/Discord)")
    a = ap.parse_args()
    state = Path(a.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(state / "bot.log")])
    alerter = Alerter({} if a.quiet_alerts or a.replay else os.environ)
    bot = Bot(state, a.symbol, alerter)
    if a.test_alert:
        alerter.send("NQ bot test alert", f"Channels: {', '.join(alerter.channels) or 'none configured (log only)'}")
        return
    if a.replay:
        run_replay(bot, *a.replay)
        return
    missing = [k for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET", "SCHWAB_TOKEN_PATH") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)} (see deploy/nq_paper_bot.env.example)")
    log.info("NQ paper bot starting: %s, state %s, alerts: %s", a.symbol, state, ", ".join(alerter.channels) or "log only")
    asyncio.run(run_live(bot, dict(os.environ)))


if __name__ == "__main__":
    main()
