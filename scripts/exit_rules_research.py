"""Dynamic exit rules for system R (GF-1, ORB-C, S3, L2 + picker): can losers be cut earlier without killing winners?

    python scripts/exit_rules_research.py            # writes reports/exit_rules/

Every configuration re-runs the live engine (quant.live.engine, the code the bot trades with) over 18 months of
NSX bars, so re-entries and the one-position picker react to the new exits. Rules (quant.live.engine.ExitPolicy):
  max_loss_pts   cap on the initial stop distance            None / 40 / 50 / 60 / 75 / 100
  be_trigger_pts move stop to entry + 1 pt after this profit  None / 25 / 50 / 75
  trail_pts      trail the stop behind the best price         None / 30 / 50 / 75 (armed with the breakeven trigger)
  time_stop_min  exit if not in profit after N minutes        None / 15 / 30 / 60
Stage A: one rule set for all four strategies (384 sets). Stage B: rules per strategy (each strategy's own grid
with the others at baseline), best per strategy combined. Selection is on the FIRST 12 MONTHS only
(2025-04-10 -> 2026-04-09); the last 6 months (2026-04-10 -> 2026-10-09) are the unseen check.
Costs as approved: 1 NQ contract, $20/pt, 1 pt round trip, no slippage; same-minute stop+target = stop.
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant.live.engine import ExitPolicy, PRIORITY  # noqa: E402
from quant.live.replay import replay  # noqa: E402

OUT = ROOT / "reports" / "exit_rules"
TRAIN_END, TEST_START = pd.Timestamp("2026-04-09"), pd.Timestamp("2026-04-10")
PERIODS = {"first 12m (selection)": (pd.Timestamp("2025-04-10"), TRAIN_END), "last 6m (unseen)": (TEST_START, pd.Timestamp("2026-10-09")),
           "18m": (pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09")), "12m": (pd.Timestamp("2025-10-10"), pd.Timestamp("2026-10-09")),
           "6m": (TEST_START, pd.Timestamp("2026-10-09"))}
GRID = dict(max_loss_pts=[None, 40, 50, 60, 75, 100], be_trigger_pts=[None, 25, 50, 75], trail_pts=[None, 30, 50, 75],
            time_stop_min=[None, 15, 30, 60])
_DATA = None


def _data():
    global _DATA
    if _DATA is None:
        spec = importlib.util.spec_from_file_location("lr", ROOT / "scripts" / "us_level_reactions_tf.py")
        lr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lr)
        lv = pd.read_csv(ROOT / "reports/us_session_levels/daily_levels_NSX.csv", index_col=0)
        lv.index = pd.to_datetime(lv.index)
        _DATA = (lr.rth_minutes("NSX"), lv)
    return _DATA


def run(exits: dict) -> pd.DataFrame:
    rth, lv = _data()
    rows = []
    for eng, _ in replay(rth, lv, exits=exits):
        for t in eng.trades:
            if t.taken:
                rows.append((pd.Timestamp(eng.day.date()), t.strategy, t.pnl_usd, t.exit_reason))
    return pd.DataFrame(rows, columns=["day", "strategy", "pnl", "why"])


def metrics(t: pd.DataFrame) -> dict:
    p = t.pnl.to_numpy()
    if not len(p):
        return dict(trades=0, net=0.0)
    w, lo = p[p > 0], p[p <= 0]
    eq = np.cumsum(p)
    return dict(trades=len(p), win=len(w) / len(p), pf=w.sum() / -lo.sum() if lo.sum() < 0 else np.inf, net=p.sum(),
                avg_win=w.mean() if len(w) else np.nan, avg_loss=lo.mean() if len(lo) else np.nan,
                maxdd=(eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min())


def evaluate(key: tuple) -> list[dict]:
    label, exits = key
    t = run(exits)
    out = []
    for per, (a, b) in PERIODS.items():
        x = t[(t.day >= a) & (t.day <= b)]
        out.append(dict(config=label, period=per, **metrics(x)))
        for s in PRIORITY:
            out.append(dict(config=label, period=per, strategy=s, **metrics(x[x.strategy == s])))
    return out


def policies() -> list[tuple[str, ExitPolicy | None]]:
    out = []
    for ml, be, tr, ts in itertools.product(*GRID.values()):
        if tr is not None and be is None and tr >= 75:
            continue  # a 75-pt trail from entry is looser than most initial stops: skip
        p = ExitPolicy(max_loss_pts=ml, be_trigger_pts=be, trail_pts=tr, time_stop_min=ts)
        out.append((f"loss{ml}|be{be}|trail{tr}|time{ts}", None if p == ExitPolicy() else p))
    return out


def table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pols = policies()
    jobs = [(f"ALL|{name}", {} if p is None else {"*": p}) for name, p in pols]
    with ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 1)) as ex:
        res = [r for rs in ex.map(evaluate, jobs) for r in rs]
        a = table(res)
        a.to_csv(OUT / "stage_a_all_strategies.csv", index=False)
        sys_a = a[a.strategy.isna()]
        tr = sys_a[sys_a.period == "first 12m (selection)"].set_index("config")
        base = "ALL|lossNone|beNone|trailNone|timeNone"
        print("baseline first12:", round(tr.loc[base, "net"]), "| best stage A on first12:", tr.net.idxmax(), round(tr.net.max()))
        # Stage B: per strategy
        jobs_b = [(f"{s}|{name}", {s: p} if p is not None else {}) for s in PRIORITY for name, p in pols]
        res_b = [r for rs in ex.map(evaluate, jobs_b) for r in rs]
    b = table(res_b)
    b.to_csv(OUT / "stage_b_per_strategy.csv", index=False)
    best = {}
    for s in PRIORITY:
        x = b[(b.strategy.isna()) & (b.period == "first 12m (selection)") & (b.config.str.startswith(s + "|"))].set_index("config")
        best[s] = x.net.idxmax()
    print("best per strategy on first12:", best)
    combo = {s: dict(pols)[best[s].split("|", 1)[1]] for s in PRIORITY}
    combo = {s: p for s, p in combo.items() if p is not None}
    c = table(evaluate(("COMBINED per-strategy", combo)))
    c.to_csv(OUT / "stage_b_combined.csv", index=False)
    pd.Series({s: best[s] for s in PRIORITY}).to_csv(OUT / "stage_b_choice.csv")
    print(c[c.strategy.isna()].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
