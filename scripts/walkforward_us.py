"""Walk-forward optimization of the US session system over every strategy variant x day-condition gate (NSX).

    python scripts/walkforward_us.py        # writes reports/walkforward_us/

Candidates: every variant in reports/optimizer/library.parquet (level, gap-day, gap-fade, ORB, first-candle ORB) and
reports/optimizer/library_am.parquet (opening drive, failed breakout), long and short, on 1/2/3/5/10-minute candles,
each gated by a condition known at 09:30 (scripts/us_conditions.py: all days, gap down/up/flat, overnight down/up,
open below PDL / above PDH / inside, prior day down/up, VIX high/low).
Picker: one position at a time, 1 NQ contract, same-minute ties to the earlier-chosen strategy (as the live bot).

Folds: train on 6 months, test on the next 2 months, step 2 months -> 6 folds, 12 stitched unseen months
(2025-10-10 -> 2026-10-09). In each fold, only the training months are used to choose:
  pool    candidates with >= 10 training trades, PF >= 1.20 overall and PF > 1 in each 3-month half of training
  greedy  add the candidate that raises the picker's training net P&L most; stop when the gain is < 3% or after 8
Systems compared on the same unseen months:
  A  current system R (GF-1, ORB-C, S3, L2), unchanged
  B  system R kept as the core + gated add-ons chosen per fold (the greedy only adds after R's four)
  C  full re-optimization per fold (greedy from scratch)
Costs as approved: $20/pt, 1 pt round trip, no slippage; same-minute stop+target = stop.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "walkforward_us"
_spec = importlib.util.spec_from_file_location("op", ROOT / "scripts" / "optimize_picker.py")
op = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(op)
GATES = ["all", "gap_down", "gap_up", "flat_open", "overnight_down", "overnight_up", "below_PDL", "above_PDH",
         "inside_range", "prior_down", "prior_up", "vix_high", "vix_low"]
R_SYSTEM = ["gapfade|confirm|full|gap|up|1m@all", "orb|OR15|opposite|1R|long|all|3m@all", "level|S3|1m@all", "level|L2|10m@all"]
TEST_STARTS = pd.date_range("2025-10-10", periods=6, freq=pd.DateOffset(months=2))
MAX_ADD, MIN_GAIN = 8, 0.03


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    lib = pd.read_parquet(ROOT / "reports/optimizer/library.parquet")
    am = pd.read_parquet(ROOT / "reports/optimizer/library_am.parquet")
    cols = ["cand", "day", "side", "entry_time", "exit_time", "pnl_usd"]
    lib = pd.concat([lib[cols], am[cols]], ignore_index=True)
    f = pd.read_csv(ROOT / "reports/us_conditions/day_features.csv", index_col=0, parse_dates=[0])
    return lib, f


def gated(lib: pd.DataFrame, f: pd.DataFrame, a, b) -> pd.DataFrame:
    """All (variant @ gate) trades with a <= day <= b."""
    x = lib[(lib.day >= a) & (lib.day <= b)]
    parts = []
    for g in GATES:
        days = f.index[f[g] == True]
        y = x[x.day.isin(days)]
        parts.append(y.assign(cand=y.cand + "@" + g))
    return pd.concat(parts, ignore_index=True)


def pf(p: np.ndarray) -> float:
    lo = -p[p <= 0].sum()
    return p[p > 0].sum() / lo if lo > 0 else np.inf


def pool_of(train: pd.DataFrame, a, b) -> list[str]:
    mid = a + (b - a) / 2
    s = train.groupby("cand").pnl_usd.agg(n="size", net="sum", pf=lambda p: pf(p.to_numpy()))
    h1 = train[train.day < mid].groupby("cand").pnl_usd.agg(lambda p: pf(p.to_numpy()))
    h2 = train[train.day >= mid].groupby("cand").pnl_usd.agg(lambda p: pf(p.to_numpy()))
    s = s.join(h1.rename("pf1")).join(h2.rename("pf2"))
    ok = s[(s.n >= 10) & (s.pf >= 1.2) & (s.pf1 > 1) & (s.pf2 > 1) & (s.net > 0)].sort_values("net", ascending=False)
    arrs = op.Arrays(train[train.cand.isin(ok.index)])
    seen, uniq = set(), []
    for k in ok.index:  # identical trade lists (e.g. 'both' vs 'short' when only shorts trade) count once
        sig = (arrs.c[k][0].tobytes(), arrs.c[k][2].round(2).tobytes())
        if sig not in seen:
            seen.add(sig)
            uniq.append(k)
    return uniq


def greedy(train: pd.DataFrame, pool: list[str], core: list[str]) -> list[str]:
    arrs = op.Arrays(train[train.cand.isin(pool + core)])
    chosen = [c for c in core if c in arrs.c]
    cur = op.score(arrs, chosen)[0] if chosen else 0.0
    for _ in range(MAX_ADD):
        best = None
        for k in pool:
            if k in chosen:
                continue
            net = op.score(arrs, chosen + [k])[0]
            if best is None or net > best[1]:
                best = (k, net)
        if best is None or best[1] - cur < MIN_GAIN * max(abs(cur), 1.0):
            break
        chosen.append(best[0])
        cur = best[1]
    return chosen


def picker_trades(trades: pd.DataFrame, chosen: list[str]) -> pd.DataFrame:
    sub = trades[trades.cand.isin(chosen)]
    chosen = [k for k in chosen if k in set(sub.cand)]
    if not chosen:
        return sub.iloc[:0]
    arrs = op.Arrays(sub)
    taken = op.run_picker(arrs, chosen)
    parts = [sub[sub.cand == k].sort_values("entry_time", kind="stable") for k in chosen]
    return pd.concat(parts)[taken]


def metrics(t: pd.DataFrame, n_days: int) -> dict:
    p = t.pnl_usd.to_numpy()
    if not len(p):
        return dict(trades=0, net=0.0)
    eq = np.cumsum(p[np.argsort(t.exit_time.to_numpy())])
    return dict(trades=len(p), trades_per_day=len(p) / n_days, win=(p > 0).mean(), pf=pf(p), net=p.sum(),
                maxdd=(eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min(), shorts=int((t.side == -1).sum()))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    lib, f = load()
    sel_rows, oos = [], {"A": [], "B": [], "C": []}
    for ts in TEST_STARTS:
        te = ts + pd.DateOffset(months=2) - pd.Timedelta(days=1)
        tr_a, tr_b = ts - pd.DateOffset(months=6), ts - pd.Timedelta(days=1)
        train, test = gated(lib, f, tr_a, tr_b), gated(lib, f, ts, te)
        pool = pool_of(train, tr_a, tr_b)
        sys = {"A": R_SYSTEM, "B": greedy(train, [k for k in pool if k not in R_SYSTEM], R_SYSTEM), "C": greedy(train, pool, [])}
        for k, chosen in sys.items():
            got = picker_trades(test, chosen).assign(fold=ts.date().isoformat())
            oos[k].append(got)
            sel_rows.append(dict(fold_test=f"{ts.date()} -> {te.date()}", system=k, pool=len(pool), n=len(chosen),
                                 chosen=" ; ".join(chosen), test_net=got.pnl_usd.sum(), test_trades=len(got)))
        print(f"fold {ts.date()}: pool {len(pool)} | A ${sel_rows[-3]['test_net']:,.0f} | B ${sel_rows[-2]['test_net']:,.0f} "
              f"({sys['B'][4:]}) | C ${sel_rows[-1]['test_net']:,.0f} ({sys['C']})", flush=True)
    sel = pd.DataFrame(sel_rows)
    sel.to_csv(OUT / "folds.csv", index=False)
    n_days = f[(f.index >= TEST_STARTS[0])].shape[0]
    res = []
    for k, parts in oos.items():
        t = pd.concat(parts)
        t.to_csv(OUT / f"oos_trades_{k}.csv", index=False)
        res.append(dict(system=k, **metrics(t, n_days)))
    r = pd.DataFrame(res)
    r.to_csv(OUT / "oos_summary.csv", index=False)
    print(r.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
