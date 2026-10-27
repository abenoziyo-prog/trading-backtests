"""Backtest baseline for the bot's daily status report: per-strategy stats of the trades the live bot would have taken.

    python scripts/make_live_baseline.py

Replays 18 months of NSX bars through the live engines (US system R with LIVE_EXITS, both overnight books) and
writes src/quant/live/baseline.json (taken trades only). The bot ships this file in its image: reports/ and data/
are not in the container. Re-run it whenever a strategy, exit rule or the data window changes.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant.live.engine import LIVE_EXITS  # noqa: E402
from quant.live.ledger import book_of  # noqa: E402
from quant.live.replay import replay, trades_frame  # noqa: E402

OUT = ROOT / "src/quant/live/baseline.json"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def stats(p: pd.Series) -> dict:
    wins, losses = p[p > 0], p[p <= 0]
    streak = worst = 0
    for x in p:
        streak = streak + 1 if x <= 0 else 0
        worst = max(worst, streak)
    eq = p.cumsum()
    return dict(trades=int(len(p)), win_pct=round(float((p > 0).mean() * 100), 1), pf=round(float(wins.sum() / -losses.sum()), 2),
                avg=round(float(p.mean()), 2), avg_win=round(float(wins.mean()), 2), avg_loss=round(float(losses.mean()), 2),
                worst_streak=int(worst), max_dd=round(float((eq - eq.cummax().clip(lower=0)).min()), 2), net=round(float(p.sum()), 2))


def main() -> None:
    lr = load("lr", "us_level_reactions_tf.py")
    lv = pd.read_csv(ROOT / "reports/us_session_levels/daily_levels_NSX.csv", index_col=0)
    lv.index = pd.to_datetime(lv.index)
    us = trades_frame([e for e, _ in replay(lr.rth_minutes("NSX"), lv, exits=LIVE_EXITS)])
    us = us[us.taken][["day", "strategy", "entry_time", "pnl_usd"]]
    night = load("check_overnight_engine", "check_overnight_engine.py").replay_nights()
    night = night[night.taken][["day", "strategy", "entry_time", "pnl_usd"]]
    t = pd.concat([us, night])
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
    t = t.sort_values("entry_time")
    t["book"] = t.strategy.map(book_of)
    days = pd.to_datetime(t.day)
    out = dict(window=f"{days.min():%Y-%m-%d} -> {days.max():%Y-%m-%d}", sessions=int(days.nunique()),
               strategies={s: {"book": book_of(s), **stats(g.pnl_usd)} for s, g in t.groupby("strategy")},
               books={b: stats(g.pnl_usd) for b, g in t.groupby("book")}, total=stats(t.pnl_usd))
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    for s, v in out["strategies"].items():
        print(f"{s:8s} {v['trades']:4d} trades  win {v['win_pct']:5.1f}%  PF {v['pf']:.2f}  net ${v['net']:>10,.0f}  "
              f"worst streak {v['worst_streak']:2d}  max DD ${v['max_dd']:>9,.0f}")
    print(f"TOTAL ${out['total']['net']:,.0f} over {out['sessions']} sessions -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
