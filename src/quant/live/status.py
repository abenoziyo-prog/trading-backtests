"""Daily status report: <ledger dir>/status.md, rebuilt from trades.csv each time a session's night or US day closes.

Sections: totals, P&L by book and by session, per-strategy live vs backtest (baseline.json, made by
scripts/make_live_baseline.py), kill-switch distance, the full trade log, P&L by GEX regime at entry (gex_tags.py)
and the shadow book. The kill switch is a report only:
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
SHADOW_BASELINE = Path(__file__).with_name("shadow_baseline.json")
SHADOW_LOG_ROWS = 40
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


def shadow_section(shadow_csv: Path, baseline: dict | None = None) -> str:
    """Shadow book: lab ideas paper-traded apart from the live books (not in the P&L above)."""
    base = baseline if baseline is not None else (json.loads(SHADOW_BASELINE.read_text()) if SHADOW_BASELINE.exists() else {})
    rows = []
    if shadow_csv.exists():
        with shadow_csv.open() as f:
            rows = sorted(csv.DictReader(f), key=lambda r: r["entry_time"])
    pnl = [float(r["pnl_usd"]) for r in rows]
    wins = sum(p > 0 for p in pnl)
    out = ["", "## Shadow book (lab ideas, paper, NOT in the P&L above)", "",
           "Every lab idea, run with its own lab code on the bot's /NQ bars at each session close. Each idea trades "
           "on its own (no shared position slot). Lab verdicts are from the 18-month backtest gates.", "",
           f"**Shadow total {money(sum(pnl))}** over {len(rows)} trades ({wins} W / {len(rows) - wins} L).", ""]
    names = sorted(set(base) | {r["idea"] for r in rows})
    trows = []
    for name in names:
        p, b = [float(r["pnl_usd"]) for r in rows if r["idea"] == name], base.get(name)
        n = len(p)
        trows.append([name, b["session"] if b else "–", b["verdict"] if b else "–", n,
                      f"{sum(x > 0 for x in p)} / {sum(x <= 0 for x in p)}" if n else "–", money(sum(p)) if n else "–",
                      f"{100 * sum(x > 0 for x in p) / n:.0f}%" if n else "–", f"{b['win_pct']:.0f}%" if b else "–",
                      f"{b['pf']:.2f}" if b else "–", money(b["avg"] * n) if b and n else "–"])
    out += table(["Idea", "Session", "Lab verdict", "Trades", "W / L", "Net", "Live win %", "Backtest win %", "Backtest PF",
                  "Expected net"], trows)
    if rows:
        out += ["", f"Last {min(SHADOW_LOG_ROWS, len(rows))} shadow trades:", ""]
        out += table(["Date", "Idea", "Side", "Duration", "Result", "Net"],
                     [[f"{datetime.fromisoformat(r['day']):%a %m-%d}", r["idea"], r["side"].capitalize(),
                       duration(r["entry_time"], r["exit_time"]), "✅ Winner" if float(r["pnl_usd"]) > 0 else "❌ Loser",
                       money(float(r["pnl_usd"]))] for r in rows[-SHADOW_LOG_ROWS:]])
    return "\n".join(out) + "\n"


def gex_section(ledger_dir: Path, **dirs) -> str:
    """P&L by the GEX state at entry (quant.live.gex_tags), live and shadow. Also writes the tagged trades to
    ledger/trades_gex.csv and ledger/shadow_trades_gex.csv. Never stops the report: a failure becomes a note."""
    try:
        import pandas as pd

        from quant.live.gex_tags import regime_tables, tag
        out = ["", "## P&L by GEX regime (state at entry)", "",
               "Recording only: no strategy uses GEX yet. The tests that decide whether it ever will are pre-registered "
               "in docs/GEX_FRAMEWORK.md. Regimes use the naive sign (calls +, puts −); \"no data\" = before the first "
               "snapshot or capture.", ""]
        for title, src, dst in (("Live books", "trades.csv", "trades_gex.csv"), ("Shadow book", "shadow_trades.csv", "shadow_trades_gex.csv")):
            f = ledger_dir / src
            t = pd.read_csv(f) if f.exists() else pd.DataFrame()
            if "taken" in t:
                t = t[(t.taken.astype(str) == "True") & t.pnl_usd.notna()]
            if t.empty:
                out += [f"### {title}", "", "No trades yet.", ""]
                continue
            g = tag(t.reset_index(drop=True), ledger_dir.parent, **dirs)
            g.to_csv(ledger_dir / dst, index=False)
            out += [f"### {title}", ""] + regime_tables(g)
        return "\n".join(out) + "\n"
    except Exception as exc:  # the GEX files are optional; the report must still be written
        return f"\n## P&L by GEX regime\n\nUnavailable: {exc!r}\n"


def write_status(ledger_dir: Path, now: datetime | None = None) -> Path:
    path = ledger_dir / "status.md"
    path.write_text(build(ledger_dir / "trades.csv", now=now) + gex_section(ledger_dir)
                    + shadow_section(ledger_dir / "shadow_trades.csv"))
    return path
