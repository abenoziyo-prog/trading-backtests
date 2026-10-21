"""Alerts for mirroring paper trades by hand in thinkorswim paperMoney. All channels are free; each is used
only when its environment variables are set. Failures are logged, never raised.

  NTFY_TOPIC (+ optional NTFY_SERVER, default https://ntfy.sh)   push to the ntfy phone app
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID                           Telegram bot message
  DISCORD_WEBHOOK_URL                                             Discord channel webhook
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

from .engine import DPP, Event

log = logging.getLogger("nqbot.alerts")


def fmt_px(x: float | None) -> str:
    return "—" if x is None else f"{x:,.2f}"


def describe(ev: Event, symbol: str) -> tuple[str, str, str]:
    """(title, body, priority) for an event."""
    t = ev.trade
    side = "SHORT" if t is not None and t.side == -1 else "LONG"
    if ev.kind == "signal":
        tgt = "hold to close" if t.target is None else fmt_px(t.target)
        return (f"{t.strategy}: {side} 1 {symbol} now",
                f"Enter {side} at market ~{fmt_px(t.entry)} (signal close {t.signal_time:%H:%M} ET)\n"
                f"Stop {fmt_px(t.stop)}  |  Target {tgt}", "high")
    if ev.kind == "entry":
        tgt = "hold to close" if t.target is None else fmt_px(t.target)
        return (f"{t.strategy}: paper {side} filled {fmt_px(t.entry)}",
                f"Entry {t.entry_time:%H:%M} ET. Stop {fmt_px(t.stop)} | Target {tgt}", "default")
    if ev.kind == "exit":
        pnl = t.pnl_usd
        return (f"{t.strategy}: EXIT {side} {fmt_px(t.exit)} ({t.exit_reason})",
                f"Close the {side.lower()} now. Paper P&L {'+' if pnl >= 0 else '−'}${abs(pnl):,.0f} "
                f"({t.pnl_pts:+.2f} pts incl. 1 pt cost, ${DPP:.0f}/pt)", "high")
    if ev.kind == "skip":
        return (f"{t.strategy}: skipped (position open)", f"Signal at {t.entry_time:%H:%M} ET not taken", "low")
    return (ev.text or ev.kind, ev.text, "default")


class Alerter:
    def __init__(self, env: dict | None = None, send_skips: bool = False):
        e = env if env is not None else os.environ
        self.ntfy = (e.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/"), e["NTFY_TOPIC"]) if e.get("NTFY_TOPIC") else None
        self.tg = (e["TELEGRAM_BOT_TOKEN"], e["TELEGRAM_CHAT_ID"]) if e.get("TELEGRAM_BOT_TOKEN") and e.get("TELEGRAM_CHAT_ID") else None
        self.discord = e.get("DISCORD_WEBHOOK_URL") or None
        self.send_skips = send_skips

    @property
    def channels(self) -> list[str]:
        return [n for n, v in (("ntfy", self.ntfy), ("telegram", self.tg), ("discord", self.discord)) if v]

    def event(self, ev: Event, symbol: str) -> None:
        if ev.kind == "skip" and not self.send_skips:
            title, body, _ = describe(ev, symbol)
            log.info("%s | %s", title, body)
            return
        if ev.kind == "day_end":
            return
        self.send(*describe(ev, symbol))

    def send(self, title: str, body: str, priority: str = "default") -> None:
        log.info("ALERT %s | %s", title, body.replace("\n", " / "))
        if self.ntfy:
            self._post(f"{self.ntfy[0]}/{self.ntfy[1]}", body.encode(), {"Title": title.encode("ascii", "replace").decode(), "Priority": priority})
        if self.tg:
            self._post(f"https://api.telegram.org/bot{self.tg[0]}/sendMessage",
                       json.dumps({"chat_id": self.tg[1], "text": f"{title}\n{body}"}).encode(), {"Content-Type": "application/json"})
        if self.discord:
            self._post(self.discord, json.dumps({"content": f"**{title}**\n{body}"}).encode(), {"Content-Type": "application/json"})

    @staticmethod
    def _post(url: str, data: bytes, headers: dict) -> None:
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        except Exception as exc:  # never let an alert failure stop the bot
            log.warning("alert to %s failed: %s", url.split("/")[2], exc)
