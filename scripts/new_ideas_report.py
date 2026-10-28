"""Evaluate the ideas pre-registered in reports/new_ideas_2026-10-28/PREREGISTRATION.md on the 12 / 8 / 6-month windows.

    python scripts/new_ideas_report.py      # writes reports/new_ideas_2026-10-28/REPORT_DATA.md and trades.csv

Uses the lab's data, helpers, baselines and one-position picker (lab/), so results match what the lab would replay.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from lab import baselines  # noqa: E402
from lab.data import Context  # noqa: E402
from lab.gates import pf, picker, stats  # noqa: E402
from lab.harness import load_idea, normalize  # noqa: E402

OUT = ROOT / "reports" / "new_ideas_2026-10-28"
IDEAS = ["data830_fade", "weekend_gap_fade", "tom_long", "three_down_long", "lowgex_orb30", "highgex_em_fade",
         "late_trend_lowgex"]
END = pd.Timestamp("2026-10-09")
WINDOWS = {"12m": pd.Timestamp("2025-10-10"), "8m": pd.Timestamp("2026-02-10"), "6m": pd.Timestamp("2026-04-10")}
EARLY = (pd.Timestamp("2025-10-10"), pd.Timestamp("2026-04-09"))
T_STRONG = 2.58  # lab luck correction for 20 ideas: 1.5 + 0.25 * log2(20)


def window(t: pd.DataFrame, a: pd.Timestamp, b: pd.Timestamp = END) -> pd.DataFrame:
    return t[(t.day >= a) & (t.day <= b)]


def addon_gain(base: pd.DataFrame, idea: pd.DataFrame, order: list[str], a, b) -> tuple[float, float, float]:
    base_w, idea_w = window(base, a, b), window(idea, a, b)
    base_taken = picker(base_w, order)
    both = picker(pd.concat([base_w, idea_w.assign(cand="IDEA")], ignore_index=True), order + ["IDEA"])
    return float(both.pnl_usd.sum() - base_taken.pnl_usd.sum()), stats(base_taken)["maxdd"], stats(both)["maxdd"]


def main() -> None:
    ctx = Context()
    rows, all_trades, verdicts = [], [], []
    for name in IDEAS:
        path = ROOT / "ideas" / f"{name}.py"  # rejected ideas live in the report folder, out of the shadow book
        m = load_idea(path if path.exists() else OUT / "ideas" / f"{name}.py")
        bname = getattr(m, "BASELINE", m.SESSION)
        base, order = getattr(baselines, bname)(ctx), list(baselines.ORDER[bname])
        t = normalize(m.trades(ctx, **m.PARAMS))
        all_trades.append(t.assign(idea=name))
        per = {}
        for w, a in {**WINDOWS, "earlier 6m": EARLY[0]}.items():
            b = EARLY[1] if w == "earlier 6m" else END
            tw = window(t, a, b)
            s = stats(tw)
            gain, dd0, dd1 = addon_gain(base, t, order, a, b)
            per[w] = dict(s, gain=gain, dd_base=dd0, dd_with=dd1)
            rows.append(dict(idea=name, window=w, trades=s["trades"], win=s["win"], pf=s["pf"], net=s["net"],
                             avg=s["net"] / s["trades"] if s["trades"] else float("nan"), t=s["t"], maxdd=s["maxdd"],
                             addon_gain=gain, dd_base=dd0, dd_with=dd1))
        nb_ok = []
        for nb in m.NEIGHBORS:
            x = normalize(m.trades(ctx, **{**m.PARAMS, **nb}))
            nb_ok.append(pf(window(x, *EARLY).pnl_usd.to_numpy()) > 1 and pf(window(x, WINDOWS["6m"]).pnl_usd.to_numpy()) > 1)
        checks = {
            "PF >= 1.15 in 12m, 8m, 6m and earlier 6m": all(per[w]["pf"] >= 1.15 for w in per),
            ">= 20 trades in 12m, >= 8 in 6m": per["12m"]["trades"] >= 20 and per["6m"]["trades"] >= 8,
            "add-on raises net in earlier 6m and 6m, DD no more than 20% deeper":
                all(per[w]["gain"] > 0 and per[w]["dd_with"] >= 1.2 * per[w]["dd_base"] for w in ("earlier 6m", "6m")),
            ">= 60% of neighbours PF > 1 in both halves": sum(nb_ok) >= 0.6 * len(nb_ok),
        }
        cand = all(checks.values())
        v = "STRONG CANDIDATE" if cand and per["12m"]["t"] >= T_STRONG else "CANDIDATE" if cand else "REJECT"
        verdicts.append(dict(idea=name, session=m.SESSION, verdict=v, neighbours=f"{sum(nb_ok)}/{len(nb_ok)}",
                             t12=per["12m"]["t"], **{k: ("✅" if ok else "❌") for k, ok in checks.items()}))
    res, ver = pd.DataFrame(rows), pd.DataFrame(verdicts)
    pd.concat(all_trades).to_csv(OUT / "trades.csv", index=False)
    res.to_csv(OUT / "results.csv", index=False)
    ver.to_csv(OUT / "verdicts.csv", index=False)

    def money(x):
        return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else (f"−${abs(x):,.0f}" if x < 0 else f"${x:,.0f}")

    def num(x, d=2):
        return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else ("∞" if math.isinf(x) else f"{x:.{d}f}")

    lines = ["| Idea | Window | Trades | Win % | PF | Net | Avg / trade | t | Max DD | Add-on gain to live book |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in res.itertuples():
        lines.append(f"| {r.idea} | {r.window} | {r.trades} | {num(r.win * 100, 0) if r.trades else '–'} | {num(r.pf)} | "
                     f"{money(r.net)} | {money(r.avg)} | {num(r.t)} | {money(r.maxdd)} | {money(r.addon_gain)} |")
    lines += ["", "| Idea | Verdict | 12m t | Neighbours OK | " + " | ".join(list(checks)) + " |",
              "|---|---|---|---|" + "---|" * len(checks)]
    for r in ver.to_dict("records"):
        lines.append(f"| {r['idea']} | **{r['verdict']}** | {num(r['t12'])} | {r['neighbours']} | "
                     + " | ".join(r[k] for k in checks) + " |")
    (OUT / "REPORT_DATA.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
