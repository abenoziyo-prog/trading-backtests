"""NQ and VIX: how they move together, and the live strategies' 18-month backtest P&L split by the VIX state at entry.

    python scripts/vix_strategy_study.py

Exploratory: the splits use the definitions pre-registered for the live tags (src/quant/live/vix_tags.py), but this is
the first look at them, on the same 18 months the strategies were built on. Nothing here becomes a filter unless it
later passes the lab gates and holds on live data (docs/GEX_FRAMEWORK.md, section V).
Data: Cboe VIX daily closes; VIX 1-minute bars 2024-01 -> 2026-01-02 only (so the intraday features, VIX move and
coupling, cover about the first 9 of the 18 months); NSX 1-minute RTH bars; no VIX3M history (term structure: live only).
Writes reports/vix/VIX_STUDY.md and reports/vix/backtest_trades_vix.csv.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant.live import vix_tags as V  # noqa: E402

OUT = ROOT / "reports" / "vix"
NY = "America/New_York"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def money(x: float) -> str:
    return f"{'−' if x < 0 else '+'}${abs(x):,.0f}"


def table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def split_rows(t: pd.DataFrame, col: str) -> list[list]:
    rows = []
    key = t[col].replace("", "no data")
    for k in sorted(key.unique(), key=lambda x: (x == "no data", x)):
        p = t.pnl_usd[key == k]
        w, l = p[p > 0].sum(), -p[p <= 0].sum()
        rows.append([k, len(p), f"{100 * (p > 0).mean():.0f}%", money(p.sum()), money(p.mean()), f"{w / l:.2f}" if l else "∞"])
    return rows


def market_section(nq_rth: pd.DataFrame, vix_daily: pd.Series, vix_min: pd.Series) -> list[str]:
    day = nq_rth.groupby(nq_rth.index.tz_localize(None).normalize())
    d = pd.DataFrame(dict(close=day.close.last(), high=day.high.max(), low=day.low.min(), open=day.open.first()))
    d["ret"] = 100 * d.close.pct_change()
    d["range_pct"] = 100 * (d.high - d.low) / d.open
    v = vix_daily.reindex(d.index)
    d["vix_chg"] = v.diff()
    d["vix_chg_pct"] = 100 * v.pct_change()
    d["vix_prev"] = vix_daily.shift().reindex(d.index)
    d = d.dropna()
    roll = d.ret.rolling(20).corr(d.vix_chg_pct).dropna()
    beta = np.polyfit(d.ret, d.vix_chg, 1)[0]
    up, dn = d[d.ret > 0], d[d.ret < 0]
    out = ["## 1. How NQ and VIX move together (18 months, US session)", "",
           f"{len(d)} sessions, NQ = NSX 09:30–16:00 close to close, VIX = Cboe official close.", ""]
    out += table(["Measure", "Value"], [
        ["Correlation, daily NQ % vs VIX % change", f"{d.ret.corr(d.vix_chg_pct):.2f}"],
        ["VIX points per 1% NQ move (beta)", f"{beta:.2f}"],
        ["VIX % change per 1% NQ, down days / up days", f"{np.polyfit(dn.ret, dn.vix_chg_pct, 1)[0]:.1f} / {np.polyfit(up.ret, up.vix_chg_pct, 1)[0]:.1f}"],
        ["Days NQ and VIX rose together", f"{((d.ret > 0) & (d.vix_chg > 0)).mean() * 100:.0f}%"],
        ["Rolling 20-day correlation: median / 10th pct / max", f"{roll.median():.2f} / {roll.quantile(0.1):.2f} / {roll.max():.2f}"],
        ["Correlation, prior VIX close vs NQ session range %", f"{d.vix_prev.corr(d.range_pct):.2f}"]])
    # range by VIX level
    d["lvl"] = d.vix_prev.map(V.level_bucket)
    rows = [[k, len(g), f"{g.range_pct.mean():.2f}%", f"{g.ret.abs().mean():.2f}%", f"{(g.ret > 0).mean() * 100:.0f}%"]
            for k, g in sorted(d.groupby("lvl"), key=lambda x: ["< 15", "15-20", "20-25", ">= 25"].index(x[0]))]
    out += ["", "**NQ session by VIX level (prior close)**", ""]
    out += table(["VIX level", "Sessions", "Avg range", "Avg |close-to-close|", "Up days"], rows)
    # intraday 5-minute
    m = pd.concat([nq_rth.close.rename("nq"), vix_min.rename("vix")], axis=1, join="inner")
    m5 = m.resample("5min", origin="start_day", offset="30min").last().dropna()
    m5 = m5[m5.index.time < pd.Timestamp("16:00").time()]
    r = pd.DataFrame(dict(nq=m5.nq.pct_change() * 100, vix=m5.vix.diff())).dropna()
    r = r[r.index.normalize() == r.index.shift(-5, freq="min").normalize()]
    by_hour = r.groupby(r.index.hour).apply(lambda g: g.nq.corr(g.vix))
    out += ["", f"**Intraday, 5-minute changes** ({r.index.normalize().nunique()} sessions with VIX minutes, through 2026-01-02)", ""]
    out += table(["Measure", "Value"], [
        ["Correlation, 5-min NQ % vs VIX pts", f"{r.nq.corr(r.vix):.2f}"],
        ["Same, by hour: " + ", ".join(f"{h}h" for h in by_hour.index), ", ".join(f"{x:.2f}" for x in by_hour)],
        ["VIX 5-min change leads NQ next 5 min (corr)", f"{r.vix.corr(r.nq.shift(-1)):.3f}"],
        ["NQ 5-min change leads VIX next 5 min (corr)", f"{r.nq.corr(r.vix.shift(-1)):.3f}"]])
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    lr = load("lr", "us_level_reactions_tf.py")
    nq_rth = lr.rth_minutes("NSX")
    vd = pd.read_parquet(ROOT / "data/free/VIX_daily_cboe.parquet").close
    vd.index = pd.to_datetime(vd.index).normalize()
    vm = pd.read_parquet(ROOT / "data/free/VIX_1min_2024_2025.parquet").close
    vm.index = vm.index.tz_convert(NY)
    t = load("mlb", "make_live_baseline.py").backtest_trades().reset_index(drop=True)
    f = V.features(t.entry_time, vd, vm, nq_rth.close.tz_convert(NY) if nq_rth.index.tz else nq_rth.close)
    t = pd.concat([t, f], axis=1)
    t.to_csv(OUT / "backtest_trades_vix.csv", index=False)

    md = ["# NQ and VIX", "", "Exploratory, built by scripts/vix_strategy_study.py. Window: the strategies' 18 months "
          "(2025-04-10 → 2026-10-09). Splits use the definitions pre-registered for the live tags (src/quant/live/vix_tags.py). "
          "This is the first look, on the data the strategies were built on, so nothing here is a rule.", ""]
    md += market_section(nq_rth, vd, vm)
    md += ["", "## 2. Live strategies' backtest P&L by VIX state at entry", "",
           "Taken trades of the live engines (the same replay as baseline.json). VIX move and coupling need VIX minutes, "
           "which end 2026-01-02, so later trades show \"no data\" there. Overnight entries (18:00) have no VIX print "
           "within 30 minutes.", ""]
    for col, title in (("vix_level", "VIX level (prior close)"), ("vix_move", "VIX move since prior close"),
                       ("coupling", "NQ / VIX coupling (60 min before entry)")):
        md += [f"### {title}", "", "**All live strategies**", ""]
        md += table(["State", "Trades", "Win %", "Net", "Avg", "PF"], split_rows(t, col))
        for book, g in t.groupby("book"):
            md += ["", f"**{book}**", ""] + table(["State", "Trades", "Win %", "Net", "Avg", "PF"], split_rows(g, col))
        md += [""]
    md += ["### By strategy × VIX level", ""]
    piv = t.pivot_table(index="strategy", columns="vix_level", values="pnl_usd", aggfunc=["count", "sum"], fill_value=0)
    lv = [c for c in ["< 15", "15-20", "20-25", ">= 25"] if ("count", c) in piv.columns]
    md += table(["Strategy"] + [f"{c}: trades / net" for c in lv],
                [[s] + [f"{int(piv.loc[s, ('count', c)])} / {money(piv.loc[s, ('sum', c)])}" for c in lv] for s in piv.index])
    (OUT / "VIX_STUDY.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
