"""Lab backtest stats of the shadow-book ideas, for the bot's status report: src/quant/live/shadow_baseline.json.

    python scripts/make_shadow_baseline.py LAB_RESULTS_DIR [MORE_DIRS ...]

Reads <idea>.json files written by the lab harness (later directories win) and keeps, per idea: session, lab
verdict, trades, win %, profit factor, net and average per trade. Re-run after a lab run that adds or re-tests ideas.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant.live.shadow import load_ideas  # noqa: E402

OUT = ROOT / "src/quant/live/shadow_baseline.json"


def main() -> None:
    found = {}
    for d in map(Path, sys.argv[1:]):
        for p in d.glob("*.json"):
            found[p.stem] = json.loads(p.read_text())
    out = {}
    for m in load_ideas():
        r = found.get(m.NAME)
        if r is None:
            print(f"{m.NAME}: no lab result found")
            continue
        s = r["stats"]
        out[m.NAME] = dict(session=m.SESSION, verdict=r["verdict"], trades=s["trades"], win_pct=round(100 * s["win"], 1),
                           pf=round(s["pf"], 2), net=round(s["net"], 2), avg=round(s["net"] / s["trades"], 2) if s["trades"] else 0.0,
                           evaluated=r["evaluated_at"])
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{len(out)} ideas -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
