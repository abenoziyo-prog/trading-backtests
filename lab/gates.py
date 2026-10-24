"""The gates every idea must pass (fixed in advance; see lab/README.md for the reasoning).

  G1 sample       >= 30 trades over 18 months and >= 10 in the last 6 months
  G2 edge         profit factor >= 1.15 over 18 months and t-stat (mean R / standard error) >= the required t,
                  which rises with the number of ideas tested so far: t_req = 1.5 + 0.25 * log2(N) (luck correction)
  G3 consistency  profit factor > 1 in BOTH the first 12 and the last 6 months, and net positive in >= 6 of the
                  9 two-month blocks
  G4 add-on       added to the live baseline at the lowest priority (one position at a time), it raises net P&L in
                  BOTH the first 12 and the last 6 months, without deepening the max drawdown by more than 20%
  G5 robustness   (only if the idea lists NEIGHBORS) >= 60% of its neighbouring parameter sets pass G3
PASS = all applicable gates. A PASS makes an idea a candidate for shadow trading, not for live trading.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("lab_op", ROOT / "scripts" / "optimize_picker.py")
op = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(op)

START, SPLIT, END = pd.Timestamp("2025-04-10"), pd.Timestamp("2026-04-10"), pd.Timestamp("2026-10-09")
BLOCKS = pd.date_range(START, periods=9, freq=pd.DateOffset(months=2))


def pf(p: np.ndarray) -> float:
    lo = -p[p <= 0].sum()
    return float(p[p > 0].sum() / lo) if lo > 0 else (math.inf if len(p) else float("nan"))


def stats(t: pd.DataFrame) -> dict:
    p = t.pnl_usd.to_numpy()
    if not len(p):
        return dict(trades=0, net=0.0, pf=float("nan"), win=float("nan"), t=float("nan"), maxdd=0.0)
    r = t.r_multiple.to_numpy()
    eq = np.cumsum(p[np.argsort(t.exit_time.to_numpy())])
    return dict(trades=len(p), net=float(p.sum()), pf=pf(p), win=float((p > 0).mean()),
                t=float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))) if len(r) > 2 and r.std(ddof=1) > 0 else float("nan"),
                maxdd=float((eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min()))


def halves(t: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return t[t.day < SPLIT], t[t.day >= SPLIT]


def blocks(t: pd.DataFrame) -> list[float]:
    out = []
    for b in BLOCKS:
        e = b + pd.DateOffset(months=2)
        out.append(float(t[(t.day >= b) & (t.day < e)].pnl_usd.sum()))
    return out


def picker(trades: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    order = [k for k in order if k in set(trades.cand)]
    if not order:
        return trades.iloc[:0]
    arrs = op.Arrays(trades)
    taken = op.run_picker(arrs, order)
    parts = [trades[trades.cand == k].sort_values("entry_time", kind="stable") for k in order]
    return pd.concat(parts)[taken]


def t_required(n_tested: int) -> float:
    return 1.5 + 0.25 * math.log2(max(1, n_tested))


def consistency(t: pd.DataFrame) -> tuple[bool, dict]:
    a, b = halves(t)
    bl = blocks(t)
    pos = sum(x > 0 for x in bl)
    ok = pf(a.pnl_usd.to_numpy()) > 1 and pf(b.pnl_usd.to_numpy()) > 1 and pos >= 6
    return ok, dict(pf_first12=pf(a.pnl_usd.to_numpy()), pf_last6=pf(b.pnl_usd.to_numpy()), positive_blocks=pos, blocks=bl)


def evaluate(idea: pd.DataFrame, base: pd.DataFrame, order: list[str], n_tested: int, neighbors: list[pd.DataFrame] | None = None) -> dict:
    s = stats(idea)
    _, last6 = halves(idea)
    g1 = s["trades"] >= 30 and len(last6) >= 10
    t_req = t_required(n_tested)
    g2 = s["trades"] > 2 and s["pf"] >= 1.15 and s["t"] >= t_req
    g3, cons = consistency(idea)
    base_taken = picker(base, order)
    both = picker(pd.concat([base, idea.assign(cand="IDEA")], ignore_index=True), order + ["IDEA"])
    ba, bb = halves(base_taken)
    wa, wb = halves(both)
    gain_a, gain_b = wa.pnl_usd.sum() - ba.pnl_usd.sum(), wb.pnl_usd.sum() - bb.pnl_usd.sum()
    dd_base, dd_with = stats(base_taken)["maxdd"], stats(both)["maxdd"]
    g4 = gain_a > 0 and gain_b > 0 and dd_with >= 1.2 * dd_base
    gates = dict(G1_sample=g1, G2_edge=g2, G3_consistency=g3, G4_addon=g4)
    nb = None
    if neighbors:
        passed = [consistency(x)[0] for x in neighbors]
        nb = dict(n=len(passed), passed=sum(passed))
        gates["G5_robustness"] = sum(passed) >= 0.6 * len(passed)
    return dict(stats=s, t_required=t_req, consistency=cons, gates=gates, verdict="PASS" if all(gates.values()) else "FAIL",
                addon=dict(gain_first12=float(gain_a), gain_last6=float(gain_b), base_net=float(base_taken.pnl_usd.sum()),
                           with_net=float(both.pnl_usd.sum()), base_maxdd=dd_base, with_maxdd=dd_with,
                           idea_trades_taken=int((both.cand == "IDEA").sum())),
                neighbors=nb)
