"""Run ideas through the gates and record the results.

    python -m lab.harness ideas/gapdown_orb60_short.py [more ideas ...]
    python -m lab.harness --all                   # every idea in ideas/
    python -m lab.harness --all --new-only        # ideas not yet in the registry (nightly CI)

An idea is a Python file in ideas/ defining:
    NAME        short id (default: the file name)
    SESSION     "us" (09:30-16:00 ET) or "overnight" (18:00-09:29 ET)
    HYPOTHESIS  one or two sentences: what and why
    trades(ctx, **params) -> DataFrame with columns day, side, entry_time, exit_time, pnl_usd, r_multiple
    PARAMS      optional dict of default parameters passed to trades()
    NEIGHBORS   optional list of parameter dicts (nearby settings) for the robustness gate
Outputs (in --out, default lab_results/): <name>.json and <name>.md per idea, registry.csv (append-only record of
every evaluation; its idea count sets the luck correction in gate G2), leaderboard.md (latest result per idea).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import baselines
from .data import Context
from .gates import BLOCKS, evaluate

ROOT = Path(__file__).resolve().parents[1]
REG_FIELDS = ["evaluated_at", "idea", "session", "verdict", "trades", "pf", "t", "t_required", "pf_first12", "pf_last6",
              "positive_blocks", "addon_gain_first12", "addon_gain_last6", "n_tested"]


def load_idea(path: Path):
    spec = importlib.util.spec_from_file_location(f"idea_{path.stem}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.NAME = getattr(m, "NAME", path.stem)
    assert getattr(m, "SESSION", None) in ("us", "overnight"), f"{path.name}: SESSION must be 'us' or 'overnight'"
    return m


def normalize(t: pd.DataFrame) -> pd.DataFrame:
    t = t.copy()
    t["day"] = pd.to_datetime(t["day"]).dt.tz_localize(None).dt.normalize() if hasattr(t["day"].dtype, "tz") \
        else pd.to_datetime(t["day"]).dt.normalize()
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    return t[["day", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple"]]


def fmt(x, money=False, d=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    if money:
        return f"−${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"
    return f"{x:.{d}f}" if isinstance(x, float) else str(x)


def report(m, r: dict) -> str:
    s, c, a = r["stats"], r["consistency"], r["addon"]
    gates = "\n".join(f"| {k} | {'✅ pass' if v else '❌ fail'} |" for k, v in r["gates"].items())
    blk = " | ".join(fmt(x, money=True) for x in c["blocks"])
    heads = " | ".join(f"{b:%b %y}" for b in BLOCKS)
    nb = "" if not r["neighbors"] else f"\n**Neighbours:** {r['neighbors']['passed']} of {r['neighbors']['n']} pass G3.\n"
    return f"""# {m.NAME}: **{r['verdict']}**

{getattr(m, 'HYPOTHESIS', '').strip()}

**Session:** {m.SESSION}. **Evaluated:** {r['evaluated_at']} (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
{gates}
{nb}
| Metric | Value |
|---|---|
| Trades (18 months) | {s['trades']} |
| Win rate | {fmt(s['win'] * 100 if s['trades'] else float('nan'), d=0)}% |
| Profit factor | {fmt(s['pf'])} (first 12m {fmt(c['pf_first12'])}, last 6m {fmt(c['pf_last6'])}) |
| Net P&L | {fmt(s['net'], money=True)} |
| t-stat (required) | {fmt(s['t'])} ({fmt(r['t_required'])}) |
| Max drawdown | {fmt(s['maxdd'], money=True)} |
| Two-month blocks positive | {c['positive_blocks']} of 9 |

**Two-month blocks:**

| {heads} |
|{'---|' * 9}
| {blk} |

**Added to the live {m.SESSION} baseline (lowest priority, one position at a time):**
- **Change in net P&L:** {fmt(a['gain_first12'], money=True)} in the first 12 months, {fmt(a['gain_last6'], money=True)} in the last 6.
- **Idea trades actually taken:** {a['idea_trades_taken']}.
- **Max drawdown:** {fmt(a['base_maxdd'], money=True)} → {fmt(a['with_maxdd'], money=True)}.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ideas", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "lab_results"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    reg_path = out / "registry.csv"
    reg = pd.read_csv(reg_path) if reg_path.exists() else pd.DataFrame(columns=REG_FIELDS)
    paths = sorted((ROOT / "ideas").glob("*.py")) if a.all else [Path(p) for p in a.ideas]
    paths = [p for p in paths if not p.name.startswith("_")]
    ideas = [load_idea(p) for p in paths]
    if a.new_only:
        ideas = [m for m in ideas if m.NAME not in set(reg.idea)]
    if not ideas:
        print("no ideas to evaluate")
        return
    ctx = Context()
    base = {}
    for m in ideas:
        if m.SESSION not in base:
            base[m.SESSION] = getattr(baselines, m.SESSION)(ctx)
        params = dict(getattr(m, "PARAMS", {}))
        trades = normalize(m.trades(ctx, **params))
        neighbors = [normalize(m.trades(ctx, **{**params, **nb})) for nb in getattr(m, "NEIGHBORS", [])]
        n_tested = len(set(reg.idea) | {m.NAME})
        r = evaluate(trades, base[m.SESSION], list(baselines.ORDER[m.SESSION]), n_tested, neighbors)
        r["evaluated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        (out / f"{m.NAME}.json").write_text(json.dumps(r, indent=2, default=str))
        (out / f"{m.NAME}.md").write_text(report(m, r))
        s, c = r["stats"], r["consistency"]
        reg = pd.concat([reg, pd.DataFrame([dict(evaluated_at=r["evaluated_at"], idea=m.NAME, session=m.SESSION, verdict=r["verdict"],
                                                 trades=s["trades"], pf=s["pf"], t=s["t"], t_required=r["t_required"],
                                                 pf_first12=c["pf_first12"], pf_last6=c["pf_last6"], positive_blocks=c["positive_blocks"],
                                                 addon_gain_first12=r["addon"]["gain_first12"], addon_gain_last6=r["addon"]["gain_last6"],
                                                 n_tested=n_tested)])], ignore_index=True)
        print(f"{m.NAME:32s} {r['verdict']}  " + "  ".join(f"{k.split('_')[0]}:{'ok' if v else 'x'}" for k, v in r["gates"].items()))
    reg.to_csv(reg_path, index=False)
    latest = reg.drop_duplicates("idea", keep="last").sort_values(["verdict", "addon_gain_last6"], ascending=[False, False])
    lines = ["# Strategy lab leaderboard", "", f"{len(latest)} ideas tested. PASS = candidate for shadow trading.", "",
             "| Idea | Session | Verdict | Trades | PF | PF first 12m / last 6m | Blocks + | Add-on gain first 12m / last 6m | Evaluated |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in latest.itertuples():
        lines.append(f"| [{r.idea}]({r.idea}.md) | {r.session} | **{r.verdict}** | {r.trades} | {fmt(float(r.pf))} | "
                     f"{fmt(float(r.pf_first12))} / {fmt(float(r.pf_last6))} | {r.positive_blocks}/9 | "
                     f"{fmt(float(r.addon_gain_first12), money=True)} / {fmt(float(r.addon_gain_last6), money=True)} | {r.evaluated_at} |")
    (out / "leaderboard.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
