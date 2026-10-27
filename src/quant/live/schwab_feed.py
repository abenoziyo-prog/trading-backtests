"""Read-only Schwab market data for the live bot: 1-minute /NQ bars from the CHART_FUTURES stream.

DATA ONLY. The Schwab client is wrapped in ReadOnlyClient, which exposes just what the streamer needs
(user preferences for the streamer address, and the access token). Any other attribute (place_order,
replace_order, cancel_order, account endpoints, ...) raises PermissionError, so this bot cannot send an
order even by mistake. tests/test_live_bot.py also checks that no order code is referenced.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .engine import Bar

log = logging.getLogger("nqbot.feed")
NY = ZoneInfo("America/New_York")
REFRESH_TOKEN_LIFE_S = 7 * 24 * 3600  # Schwab refresh tokens last 7 days; then a browser login is needed


class ReadOnlyClient:
    """Allow-list proxy around a schwab-py client: market-data streaming only."""

    ALLOWED = frozenset({"get_user_preferences", "token_metadata", "token_age"})
    PRICE_HISTORY = frozenset({"get_price_history_every_minute"})  # extra market-data call for the archiver
    OPTION_CHAIN = frozenset({"get_option_chain"})  # extra market-data call for the GEX snapshot (bots/gex_snapshot.py)
    MARKET_DATA = PRICE_HISTORY | OPTION_CHAIN  # the only extras a caller can ask for: quotes and history, never trading

    def __init__(self, client, extra: frozenset = frozenset()):
        object.__setattr__(self, "_c", client)
        object.__setattr__(self, "_allowed", ReadOnlyClient.ALLOWED | (extra & ReadOnlyClient.MARKET_DATA))

    def __getattr__(self, name):
        if name not in object.__getattribute__(self, "_allowed"):
            raise PermissionError(f"nq paper bot is data-only: '{name}' is blocked")
        return getattr(object.__getattribute__(self, "_c"), name)

    def __setattr__(self, name, value):
        raise PermissionError("read-only client")


def make_client(api_key: str, app_secret: str, token_path: str, extra: frozenset = frozenset()) -> ReadOnlyClient:
    from schwab.auth import client_from_token_file  # imported lazily: replay/tests don't need schwab-py

    return ReadOnlyClient(client_from_token_file(token_path, api_key, app_secret), extra)


def refresh_token_hours_left(client: ReadOnlyClient) -> float:
    return (REFRESH_TOKEN_LIFE_S - client.token_age()) / 3600


def max_diff(a: Bar, b: Bar) -> float:
    return max(abs(a.open - b.open), abs(a.high - b.high), abs(a.low - b.low), abs(a.close - b.close))


class MinuteAggregator:
    """Turns stream chart messages into final 1-minute bars, in time order.

    Schwab's stream can send a minute more than once, and in busy minutes its bar can differ from the
    bar its price-history endpoint serves later (2026-10-21: 3% of minutes, closes up to 9 pts). So a
    minute is finalized `settle_s` seconds after it ends, not when the next minute arrives:
      * with a `resolver` (history lookup), the history bar is used as soon as it exists, retried every
        `retry_s` seconds; if history still lacks it `max_wait_s` after the minute ended, the latest stream
        bar is used instead;
      * without a resolver, the latest stream bar `settle_s` after the minute ended is used.
    Every finalized minute and every update arriving after finalization is reported to `audit`.
    """

    def __init__(self, on_bar: Callable[[Bar], None], settle_s: float = 5.0, max_wait_s: float = 30.0,
                 resolver: Callable[[datetime], Bar | None] | None = None, audit: Callable[[str, dict], None] | None = None,
                 retry_s: float = 3.0, clock: Callable[[], float] = time.time):
        self.on_bar, self.settle, self.max_wait, self.retry = on_bar, settle_s, max_wait_s, retry_s
        self.resolver, self.audit, self.clock = resolver, audit, clock
        self.pending: dict[datetime, dict] = {}  # minute -> {bar, first_rx, last_rx, n, next_try}
        self.final: dict[datetime, Bar] = {}  # recent finalized bars (for late-update diffs)
        self.last_final: datetime | None = None
        self.logged = 0

    def update(self, ms: int, o: float, h: float, l: float, c: float) -> None:
        rx = self.clock()
        t = datetime.fromtimestamp(ms / 1000, NY)
        end = ms / 1000 + 60
        if self.logged < 5:  # timestamps must be the minute's START: a bar should arrive ~60 s after it
            self.logged += 1
            log.info("bar %s received %.0f s after its timestamp (expect ~60; ~0 would mean end-of-minute labels)",
                     t.strftime("%H:%M"), rx - ms / 1000)
        b = Bar(t, float(o), float(h), float(l), float(c))
        if self.last_final is not None and t <= self.last_final:
            used = self.final.get(t)
            if self.audit:
                self.audit("late", dict(minute=t.isoformat(), rx_after_end_s=round(rx - end, 2),
                                        diff_vs_used=None if used is None else round(max_diff(b, used), 4),
                                        open=b.open, high=b.high, low=b.low, close=b.close))
            return
        p = self.pending.get(t)
        if p is None:
            self.pending[t] = dict(bar=b, first=b, first_rx=rx, last_rx=rx, n=1, next_try=0.0)
        else:
            p.update(bar=b, last_rx=rx, n=p["n"] + 1)

    def tick(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        while self.pending:
            t = min(self.pending)
            p, end = self.pending[t], t.timestamp() + 60
            if now < end + self.settle:
                return
            if self.resolver is not None and now < end + self.max_wait:
                if now < p["next_try"]:
                    return
                hb = self._resolve(t)
                if hb is None:
                    p["next_try"] = now + self.retry
                    return
                self._finalize(t, hb, "history", now)
            else:
                self._finalize(t, p["bar"], "stream" if self.resolver is None else "stream (history late)", now)

    def _resolve(self, t: datetime) -> Bar | None:
        try:
            return self.resolver(t)
        except Exception as exc:
            log.warning("history lookup for %s failed: %r", t.strftime("%H:%M"), exc)
            return None

    def _finalize(self, t: datetime, bar: Bar, source: str, now: float) -> None:
        p = self.pending.pop(t)
        end = t.timestamp() + 60
        self.last_final = t
        self.final[t] = bar
        for k in [k for k in self.final if k < t - timedelta(minutes=120)]:
            del self.final[k]
        if self.audit:
            self.audit("minute", dict(minute=t.isoformat(), source=source, finalized_after_end_s=round(now - end, 2),
                                      first_rx_after_end_s=round(p["first_rx"] - end, 2), last_rx_after_end_s=round(p["last_rx"] - end, 2),
                                      stream_msgs=p["n"], stream_vs_used=round(max_diff(p["bar"], bar), 4),
                                      first_stream_vs_used=round(max_diff(p["first"], bar), 4),
                                      open=bar.open, high=bar.high, low=bar.low, close=bar.close))
        self.on_bar(bar)


def history_bars(client: ReadOnlyClient, symbol: str, start: datetime, end: datetime) -> list[Bar]:
    """Completed 1-minute bars from Schwab price history (read-only), oldest first."""
    r = client.get_price_history_every_minute(symbol, start_datetime=start, end_datetime=end, need_extended_hours_data=True)
    r.raise_for_status()
    cut = end.timestamp() - 60  # the minute still forming is excluded
    return [Bar(datetime.fromtimestamp(c["datetime"] / 1000, NY), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]))
            for c in r.json().get("candles", []) if c["datetime"] / 1000 <= cut]


class HistoryResolver:
    """Look up one finished minute in Schwab's price history (read-only). Returns None until it is there."""

    def __init__(self, get_client: Callable[[], ReadOnlyClient], symbol: str):
        self.get_client, self.symbol = get_client, symbol

    def __call__(self, t: datetime) -> Bar | None:
        r = self.get_client().get_price_history_every_minute(self.symbol, start_datetime=t - timedelta(minutes=2),
                                                             end_datetime=t + timedelta(minutes=2), need_extended_hours_data=True)
        r.raise_for_status()
        ms = int(t.timestamp() * 1000)
        for c in r.json().get("candles", []):
            if int(c["datetime"]) == ms:
                return Bar(t, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]))
        return None


def handle_chart_message(msg: dict, symbol: str, agg: MinuteAggregator) -> None:
    """One labeled CHART_FUTURES message from schwab-py: {"content": [{"key": "/NQZ26", "CHART_TIME_MILLIS": ...}]}.
    "/NQ" also matches the dated contract symbol the stream may report (e.g. /NQZ26)."""
    for item in msg.get("content", []):
        sym = (item.get("key") or item.get("SYMBOL") or "").upper()
        if sym and sym != symbol.upper() and not sym.startswith(symbol.upper()):
            continue
        agg.update(int(item["CHART_TIME_MILLIS"]), item["OPEN_PRICE"], item["HIGH_PRICE"], item["LOW_PRICE"], item["CLOSE_PRICE"])


async def stream_bars(client: ReadOnlyClient, symbol: str, agg: MinuteAggregator, stop: asyncio.Event) -> None:
    """Stream CHART_FUTURES for `symbol` into `agg` until `stop` is set or the connection drops (raises)."""
    from schwab.streaming import StreamClient

    sc = StreamClient(client)
    await sc.login()
    log.info("streamer logged in; subscribing CHART_FUTURES %s", symbol)

    sc.add_chart_futures_handler(lambda msg: handle_chart_message(msg, symbol, agg))
    await sc.chart_futures_subs([symbol])
    try:
        while not stop.is_set():
            await asyncio.wait_for(sc.handle_message(), timeout=90)
    finally:
        try:
            await sc.logout()
        except Exception:
            pass
