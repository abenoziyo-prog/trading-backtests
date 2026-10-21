"""Optimize the strategy picker for maximum net P&L with at least 1 trade per day (NSX, last 18 months).

    python scripts/optimize_picker.py          # needs reports/optimizer/library.parquet (strategy_library.py)

Picker (as scripts/strategy_picker.py, no gate): one position at a time, 1 NQ contract; a signal is taken only
if nothing is open at its entry minute; same-minute signals go to the strategy added earlier.

Search: greedy forward selection over every candidate in the library (each strategy x parameters x candle
size 1/2/3/5/10m). Each step adds the candidate that gives the highest picker net P&L; MAX_STEPS steps.
The chosen system is the step with the highest net P&L among those with >= MIN_TRADES_PER_DAY trades per
trading day on average. Run twice:
  A  selected on the first 12 months (2025-04-10 -> 2026-04-09), then checked on the unseen last 6 months
  B  selected on all 18 months (in-sample maximum)
Candidates must have >= 20 trades and profit factor >= 1.05 in the selection period to enter the search.
Writes reports/optimizer/.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "optimizer"
START, END = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-10-09")
TRAIN_END, TEST_START = pd.Timestamp("2026-04-09"), pd.Timestamp("2026-04-10")
MAX_STEPS = 15
MIN_TRADES_PER_DAY = 1.0


class Arrays:
    """Per-candidate numpy arrays: entry ns, exit ns, P&L, day."""

    def __init__(self, lib: pd.DataFrame):
        self.c = {}
        for cand, g in lib.groupby("cand", sort=False):
            g = g.sort_values("entry_time", kind="stable")
            self.c[cand] = (g.entry_time.to_numpy().astype("datetime64[ns]").astype(np.int64),
                            g.exit_time.to_numpy().astype("datetime64[ns]").astype(np.int64),
                            g.pnl_usd.to_numpy(), g.day.to_numpy())


def run_picker(arrs: Arrays, chosen: list[str]) -> np.ndarray:
    """Taken mask over the concatenated trades of `chosen` (one position at a time; priority = list order)."""
    ent = np.concatenate([arrs.c[k][0] for k in chosen])
    ext = np.concatenate([arrs.c[k][1] for k in chosen])
    prio = np.concatenate([np.full(len(arrs.c[k][0]), i) for i, k in enumerate(chosen)])
    order = np.lexsort((prio, ent))
    taken = np.zeros(len(ent), bool)
    free = -(1 << 62)
    for n, (a, b) in enumerate(zip(ent[order].tolist(), ext[order].tolist())):
        if a >= free:
            taken[order[n]] = True
            free = b
    return taken


def score(arrs: Arrays, chosen: list[str]) -> tuple[float, int, int]:
    taken = run_picker(arrs, chosen)
    pnl = np.concatenate([arrs.c[k][2] for k in chosen])[taken]
    days = np.concatenate([arrs.c[k][3] for k in chosen])[taken]
    return pnl.sum(), int(taken.sum()), len(np.unique(days))


def greedy(lib: pd.DataFrame, n_days: int, label: str) -> pd.DataFrame:
    p = lib.pnl_usd
    stats = pd.DataFrame({"trades": lib.groupby("cand").size(),
                          "gross_win": p.where(p > 0, 0).groupby(lib.cand).sum(),
                          "gross_loss": -p.where(p <= 0, 0).groupby(lib.cand).sum(),
                          "net": p.groupby(lib.cand).sum()})
    stats["pf"] = stats.gross_win / stats.gross_loss.replace(0, np.nan)
    pool = stats[(stats.trades >= 20) & (stats.pf >= 1.05)].index.tolist()
    arrs = Arrays(lib[lib.cand.isin(pool)])
    seen, uniq = set(), []  # identical trade lists add nothing: keep one candidate per signature
    for k in sorted(pool, key=lambda k: -stats.net[k]):
        sig = (arrs.c[k][0].tobytes(), arrs.c[k][2].round(2).tobytes())
        if sig not in seen:
            seen.add(sig)
            uniq.append(k)
    print(f"[{label}] pool: {len(pool)} candidates pass the filter, {len(uniq)} distinct", flush=True)
    chosen, rows = [], []
    for step in range(MAX_STEPS):
        best = None
        for k in uniq:
            if k in chosen:
                continue
            net, n, d = score(arrs, chosen + [k])
            if best is None or net > best[1]:
                best = (k, net, n, d)
        chosen.append(best[0])
        rows.append(dict(selection=label, step=step + 1, added=best[0], net_pnl=best[1], trades=best[2],
                         trades_per_day=best[2] / n_days, days_traded_pct=best[3] / n_days))
        print(f"  step {step + 1:2d}: +{best[0]:<45s} net ${best[1]:>10,.0f}  {best[2] / n_days:.2f} trades/day", flush=True)
    return pd.DataFrame(rows)


def system_trades(lib: pd.DataFrame, chosen: list[str]) -> pd.DataFrame:
    """Every candidate trade of the chosen strategies over all 18 months, with the picker's `taken` flag."""
    sub = lib[lib.cand.isin(chosen)]
    arrs = Arrays(sub)
    taken = run_picker(arrs, chosen)
    parts = [sub[sub.cand == k].sort_values("entry_time", kind="stable") for k in chosen]
    t = pd.concat(parts, ignore_index=True)
    t["priority"] = np.concatenate([np.full(len(p), i + 1) for i, p in enumerate(parts)])
    t["taken"] = taken
    return t


def main() -> None:
    lib = pd.read_parquet(OUT / "library.parquet")
    all_days = np.sort(lib.day.unique())
    n_train, n_all = int((all_days <= np.datetime64(TRAIN_END)).sum()), len(all_days)
    steps = []
    for label, data, nd in (("A: selected on first 12m", lib[lib.day <= TRAIN_END], n_train), ("B: selected on all 18m", lib, n_all)):
        s = greedy(data, nd, label)
        ok = s[s.trades_per_day >= MIN_TRADES_PER_DAY]
        pick = (ok if len(ok) else s).sort_values("net_pnl").iloc[-1].step
        s["chosen"] = s.step <= pick
        steps.append(s)
        system_trades(lib, s[s.chosen].added.tolist()).to_parquet(OUT / f"system_{label[0]}.parquet")
        print(f"[{label}] chosen: first {pick} steps", flush=True)
    pd.concat(steps).to_csv(OUT / "greedy_steps.csv", index=False)


if __name__ == "__main__":
    main()
