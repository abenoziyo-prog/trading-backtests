"""Daily status report: <ledger dir>/status.md, rebuilt from trades.csv each time a session's night or US day closes.

Sections: totals, P&L by book and by session, per-strategy live vs backtest (baseline.json, made by
scripts/make_live_baseline.py), kill-switch distance and the full trade log. The kill switch is a report only:
the bot never pauses a strategy on its own.

Kill-switch rule (pre-set, per strategy): pause when the live drawdown exceeds DD_MULT x the backtest max drawdown,
or the live losing streak exceeds the backtest worst streak + STREAK_ADD. WATCH from WATCH_AT of either limit.
Review checkpoint: REVIEW_TRADES live trades per strategy.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
BASELINE = Path(__file__).with_name("baseline.json")
DD_MULT, STREAK_ADD, WATCH_AT, REVIEW_TRADES = 1.5, 3, 0.5, 30
ORDER = ["GF-1", "ORB-C", "S3", "L2", "DRIFT", "TOKYO", "EU_PRE", "LONDON", "ASIA_BO"]
SESSION_OF = {"DRIFT": "Overnight", "TOKYO": "Asia", "ASIA_BO": "Asia", "EU_PRE": "London", "LONDON": "London"}
BOOKS = ["US", "overnight-drift", "overnight-tactical"]


def money(x: float) -> str:
    return "$0.00" if round(x, 2) == 0 else f"{'−' if x < 0 else '+'}${abs(x):,.2f}"


def table(head: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def drawdown(pnl: list[float]) -> float:
    eq = peak = dd = 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd


def losing_streak(pnl: list[float]) -> int:
    n = 0
    for p in reversed(pnl):
        if p > 0:
            break
        n += 1
    return n


def kill_switch(pnl: list[float], base: dict) -> tuple[str, str, str]:
    dd, streak = drawdown(pnl), losing_streak(pnl)
    dd_lim, st_lim = DD_MULT * base["max_dd"], base["worst_streak"] + STREAK_ADD
    used = max(dd / dd_lim if dd_lim else 0.0, streak / st_lim)
    state = "⛔ PAUSE" if dd < dd_lim or streak > st_lim else "⚠️ WATCH" if used >= WATCH_AT else "✅ OK"
    return (f"{money(dd)} of {money(dd_lim)} ({abs(dd / dd_lim):.0%})" if dd_lim else money(dd),
            f"{streak} of {st_lim}", state)


def duration(a: str, b: str) -> str:
    m = int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() // 60)
    return f"{m // 60}h {m % 60:02d}m"


def build(trades_csv: Path, baseline: dict | None = None, now: datetime | None = None) -> str:
    base = baseline if baseline is not None else (json.loads(BASELINE.read_text()) if BASELINE.exists() else {})
    with trades_csv.open() as f:
        rows = [r for r in csv.DictReader(f) if r["taken"] == "True" and r["pnl_usd"] != ""]
    rows.sort(key=lambda r: r["entry_time"])
    pnl = [float(r["pnl_usd"]) for r in rows]
    total, wins = sum(pnl), sum(p > 0 for p in pnl)
    days = sorted({r["day"] for r in rows})
    out = [f"# NQ paper bot: status ({days[-1] if days else 'no trades yet'})", "",
           f"Updated {now or datetime.now(NY):%Y-%m-%d %H:%M} ET. Paper only: no orders are ever sent. "
           "1 NQ contract per trade, $20/pt, 1 pt round trip.", "",
           f"**Final P&L {money(total)}** over {len(rows)} trades ({wins} W / {len(rows) - wins} L) "
           f"in {len(days)} sessions.", ""]

    out += ["## P&L by book", ""]
    out += table(["Book", "Trades", "W / L", "Net", "Backtest net (18m)"],
                 [[b, len(p), f"{sum(x > 0 for x in p)} / {sum(x <= 0 for x in p)}", money(sum(p)),
                   money(base["books"][b]["net"]) if b in base.get("books", {}) else "–"]
                  for b in BOOKS for p in [[float(r["pnl_usd"]) for r in rows if r.get("book", "US") == b]]])

    out += ["", "## P&L by session", ""]
    cum, srows = 0.0, []
    for d in days:
        by = {b: sum(float(r["pnl_usd"]) for r in rows if r["day"] == d and r.get("book", "US") == b) for b in BOOKS}
        cum += sum(by.values())
        srows.append([f"{datetime.fromisoformat(d):%a %m-%d}", *(money(by[b]) for b in BOOKS), f"**{money(sum(by.values()))}**", money(cum)])
    out += table(["Session", "US", "Drift", "Tactical", "Total", "Cumulative"], srows)

    strats = [s for s in ORDER if s in base.get("strategies", {}) or any(r["strategy"] == s for r in rows)]
    strats += sorted({r["strategy"] for r in rows} - set(strats))
    live = {s: [float(r["pnl_usd"]) for r in rows if r["strategy"] == s] for s in strats}

    out += ["", "## Live vs backtest, by strategy", "",
            f"Backtest: {base.get('window', 'n/a')}, the trades the bot itself would have taken. "
            "Expected = backtest average per trade × live trades.", ""]
    lrows = []
    for s in strats:
        p, b = live[s], base.get("strategies", {}).get(s)
        n = len(p)
        lrows.append([s, n, f"{sum(x > 0 for x in p)} / {sum(x <= 0 for x in p)}" if n else "–", money(sum(p)) if n else "–",
                      f"{100 * sum(x > 0 for x in p) / n:.0f}%" if n else "–",
                      f"{b['win_pct']:.0f}%" if b else "–", f"{b['pf']:.2f}" if b else "–",
                      money(b["avg"] * n) if b and n else "–", f"{n} / {REVIEW_TRADES}"])
    out += table(["Strategy", "Live trades", "W / L", "Live net", "Live win %", "Backtest win %", "Backtest PF",
                  "Expected net", "Review at"], lrows)

    out += ["", "## Kill-switch distance", "",
            f"Pause a strategy if its live drawdown exceeds {DD_MULT} × its backtest max drawdown, or its losing streak "
            f"exceeds its backtest worst streak + {STREAK_ADD}. WATCH from {WATCH_AT:.0%} of either limit. "
            "Report only: the bot does not pause anything by itself.", ""]
    krows = []
    for s in strats:
        b = base.get("strategies", {}).get(s)
        if b and live[s]:
            krows.append([s, *kill_switch(live[s], b)])
        elif b:
            krows.append([s, "no trades yet", f"0 of {b['worst_streak'] + STREAK_ADD}", "✅ OK"])
    out += table(["Strategy", "Live drawdown vs limit", "Losing streak vs limit", "Status"], krows)

    out += ["", "## Trade log", "", "Date = session date; overnight trades open at 18:00 ET the evening before.", ""]
    out += table(["Date", "Strategy", "Side", "Duration", "Result", "Session", "Net"],
                 [[f"{datetime.fromisoformat(r['day']):%a %m-%d}", r["strategy"], r["side"].capitalize(),
                   duration(r["entry_time"], r["exit_time"]), "✅ Winner" if float(r["pnl_usd"]) > 0 else "❌ Loser",
                   SESSION_OF.get(r["strategy"], "US"), money(float(r["pnl_usd"]))] for r in rows]
                 + [["", "", "", "", f"**{wins} W / {len(rows) - wins} L**", "", f"**FINAL {money(total)}**"]])
    return "\n".join(out) + "\n"


def write_status(ledger_dir: Path, now: datetime | None = None) -> Path:
    path = ledger_dir / "status.md"
    path.write_text(build(ledger_dir / "trades.csv", now=now))
    return path
