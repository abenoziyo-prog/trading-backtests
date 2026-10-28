"""0DTE dealer gamma vs intraday /NQ price action, from our own 5-minute Schwab captures. PRE-REGISTERED 2026-10-28
(docs/GEX_FRAMEWORK.md, section Z), before any capture was analysed.

    python scripts/pull_cloud_files.py data/schwab/flow/ data/schwab/NQ_1min.parquet   # the captures + /NQ bars
    python scripts/zero_dte_study.py                    # QQQ (primary) and $SPX, $NDX, SPY (reported)

Only same-day expiries. Dealer gamma per capture (quant.live.flow.dealer_gamma_0dte, $ of underlying per 1% move):
g_total = opening open interest with the naive sign + -(customers' net contracts bought since the open, tick proxy)
x gamma; g_oi, g_tick and g_iv are reported alongside. State: dealer LONG gamma if g_total > 0, SHORT if < 0.
Size: |g_total| percentile among all captures of the same half hour (0DTE gamma grows into the close by construction).
Price action on /NQ 1-minute bars (Schwab archive), measured after each capture t between 10:00 and 15:30:
  range30   /NQ high - low over (t, t+30 min], % of /NQ at t
  cont15    sign of the /NQ move over the 15 minutes before t x the /NQ return over the next 15 minutes, %
            (> 0 = the move continued, < 0 = it reversed)
  hedge     -g_total x the underlying's % move over the 15 minutes before t (the hedge flow gamma forces), and its
            sign x the next 15-minute /NQ return
Hypotheses (directions fixed):
  Z1  range30 is larger when dealers are short 0DTE gamma than when they are long
  Z2  cont15 is positive when short, negative when long (short-minus-long > 0)
  Z3  size: within each state, the top third of |g_total| shows the state's effect on range30 more strongly than the
      bottom third (long: smaller range; short: larger range)
  Z4  pin: on days dealers are long 0DTE gamma at 14:30, /NQ (mapped) closes nearer the 14:30 pin strike than it was
      at 14:30, by more than it does for placebo strikes two strikes either side
  Z5  the forced hedge flow points the next 15 minutes' way: mean of sign(hedge) x next 15-minute return > 0
Statistics: each outcome is first demeaned by half hour (all sessions); per session the short-minus-long (or top-minus
bottom) difference; t across sessions that have both groups. Verdicts only from 40 sessions: sessions 1-20 are the
design sample (data checks only), sessions 21+ are judged; PASS = predicted direction and |t| >= 3.0.
Writes reports/zero_dte/ZERO_DTE_STUDY.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant.live import flow  # noqa: E402

OUT = ROOT / "reports" / "zero_dte"
FLOW = ROOT / "data" / "schwab" / "flow"
NQ = ROOT / "data" / "schwab" / "NQ_1min.parquet"
NY = "America/New_York"
SYMBOLS = ["QQQ", "$SPX", "$NDX", "SPY"]
DESIGN, MIN_VERDICT, T_PASS = 20, 40, 3.0


def table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def nq_minutes() -> pd.DataFrame:
    m = pd.read_parquet(NQ)
    m.index = m.index.tz_convert(NY)
    return m


def window(nq: pd.DataFrame, a: pd.Timestamp, b: pd.Timestamp) -> pd.DataFrame:
    return nq[(nq.index > a) & (nq.index <= b)]


def close_at(nq: pd.DataFrame, t: pd.Timestamp) -> float:
    i = nq.index.searchsorted(t, side="right") - 1
    return float(nq.close.iloc[i]) if i >= 0 and t - nq.index[i] <= pd.Timedelta(minutes=3) else np.nan


def day_rows(sym: str, day: str, nq: pd.DataFrame) -> tuple[pd.DataFrame, dict | None]:
    t = flow.load_day(FLOW, sym, day)
    if t.empty:
        return pd.DataFrame(), None
    g = flow.dealer_gamma_0dte(t)
    if g.empty:
        return pd.DataFrame(), None
    m15 = pd.Timedelta(minutes=15)
    rows = []
    for ts, r in g.iterrows():
        mod = ts.hour * 60 + ts.minute
        if not 600 <= mod <= 930:
            continue
        n0, nb, nf = close_at(nq, ts), close_at(nq, ts - m15), close_at(nq, ts + m15)
        w = window(nq, ts, ts + pd.Timedelta(minutes=30))
        prev = g.spot[(g.index >= ts - m15) & (g.index <= ts)]
        u_move = 100 * (prev.iloc[-1] / prev.iloc[0] - 1) if len(prev) > 1 else np.nan
        rows.append(dict(day=day, t=ts, half=(mod - 570) // 30, g_total=r.g_total, g_oi=r.g_oi, g_tick=r.g_tick, g_iv=r.g_iv,
                         range30=100 * (w.high.max() - w.low.min()) / n0 if len(w) >= 20 else np.nan,
                         cont15=np.sign(n0 - nb) * 100 * (nf / n0 - 1),
                         u_move=u_move, next15=100 * (nf / n0 - 1)))
    # pin (Z4): the 14:30 capture's pin strike, mapped onto /NQ by the ratio at 14:30
    pin = None
    a = g[(g.index.hour == 14) & (g.index.minute == 30)]
    if len(a):
        ta, ra = a.index[0], a.iloc[0]
        strikes = np.sort(t[(t.dte == 0)].strike.unique())
        i = np.searchsorted(strikes, ra.pin)
        n_a, n_c = close_at(nq, ta), close_at(nq, ta.normalize() + pd.Timedelta(hours=15, minutes=59))
        if 2 <= i < len(strikes) - 2 and np.isfinite(n_a) and np.isfinite(n_c):
            ratio = n_a / ra.spot

            def shrink(k):
                lvl = k * ratio
                return 100 * (abs(n_a - lvl) - abs(n_c - lvl)) / n_a     # > 0 = closed nearer the level than at 14:30
            pin = dict(day=day, long=ra.g_total > 0, pin=shrink(ra.pin),
                       placebo=np.mean([shrink(strikes[i + j]) for j in (-2, 2)]))
    return pd.DataFrame(rows), pin


def paired(x: pd.DataFrame, col: str, mask_a, mask_b) -> tuple[float, float, int]:
    """Mean over sessions of (mean col where a) - (mean col where b), its t across sessions, and the session count."""
    d = x.assign(_a=mask_a, _b=mask_b)
    per = d.groupby("day").apply(lambda g: g.loc[g._a, col].mean() - g.loc[g._b, col].mean(), include_groups=False).dropna()
    if len(per) < 3 or per.std() == 0:
        return (per.mean() if len(per) else np.nan), np.nan, len(per)
    return per.mean(), per.mean() / per.std() * np.sqrt(len(per)), len(per)


def tests(x: pd.DataFrame, pins: pd.DataFrame, g: str) -> list[tuple[str, float, float, int, int]]:
    """(hypothesis, effect, t, sessions, predicted sign) for one gamma estimate g."""
    for c in ("range30", "cont15", "next15"):
        x[c + "_dm"] = x[c] - x.groupby("half")[c].transform("mean")
    short, long_ = x[g] < 0, x[g] > 0
    pct = x.groupby("half")[g].transform(lambda s: s.abs().rank(pct=True))
    out = [("Z1 range30: short − long", *paired(x, "range30_dm", short, long_), 1),
           ("Z2 cont15: short − long", *paired(x, "cont15_dm", short, long_), 1),
           ("Z3 long: range30 top − bottom third of |gamma|", *paired(x, "range30_dm", long_ & (pct > 2 / 3), long_ & (pct < 1 / 3)), -1),
           ("Z3 short: range30 top − bottom third of |gamma|", *paired(x, "range30_dm", short & (pct > 2 / 3), short & (pct < 1 / 3)), 1)]
    s = x.assign(z5=np.sign(-x[g] * x.u_move) * x.next15)          # the hedge this estimate implies, and what followed
    per = s.groupby("day").z5.mean().dropna()
    out.append(("Z5 sign(hedge flow) × next 15 min", per.mean(), per.mean() / per.std() * np.sqrt(len(per)) if len(per) > 2 and per.std() > 0 else np.nan,
                len(per), 1))
    if len(pins):
        p = pins[pins.long]
        dd = (p.pin - p.placebo)
        out.append(("Z4 pin vs placebo (dealer-long days, % of price)", dd.mean(),
                    dd.mean() / dd.std() * np.sqrt(len(dd)) if len(dd) > 2 and dd.std() > 0 else np.nan, len(dd), 1))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if not NQ.exists() or not FLOW.exists():
        sys.exit("pull the captures and /NQ bars first (see the header)")
    nq = nq_minutes()
    md = ["# 0DTE dealer gamma vs intraday /NQ price action", "",
          "Pre-registered in scripts/zero_dte_study.py and docs/GEX_FRAMEWORK.md (section Z). Our own 5-minute Schwab captures; "
          "same-day expiries only. Verdicts from 40 sessions on: sessions 1-20 = design (data checks), 21+ = judged, PASS = "
          "predicted direction and |t| >= 3.0.", ""]
    for sym in SYMBOLS:
        days = sorted(p.name for p in (FLOW / flow.folder(sym)).glob("20*")) if (FLOW / flow.folder(sym)).exists() else []
        rows, pins = [], []
        for d in days:
            r, p = day_rows(sym, d, nq)
            if len(r):
                rows.append(r)
            if p:
                pins.append(p)
        x = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        pins = pd.DataFrame(pins)
        n = x.day.nunique() if len(x) else 0
        md += [f"## {sym}: {n} sessions", ""]
        if n == 0:
            md += ["No captures with same-day expiries.", ""]
            continue
        long_share = (x.g_total > 0).mean() * 100
        md += table(["Measure", "Value"], [
            ["Captures analysed (10:00-15:30)", len(x)],
            ["Dealer LONG 0DTE gamma (g_total > 0)", f"{long_share:.0f}% of captures"],
            ["Median |g_total|, $ per 1% move", f"${x.g_total.abs().median():,.0f}"],
            ["g_tick agrees in sign with g_iv", f"{(np.sign(x.g_tick) == np.sign(x.g_iv)).mean() * 100:.0f}%"],
            ["Days whose state changes during the session", f"{(x.groupby('day').g_total.apply(lambda s: (s > 0).nunique() > 1)).mean() * 100:.0f}%"]]) + [""]
        sessions = sorted(x.day.unique())
        judged = sessions[DESIGN:]
        for label, keep in (("All sessions (descriptive)", sessions), (f"Judged sessions (21+): {len(judged)}", judged)):
            if not keep:
                continue
            xs, ps = x[x.day.isin(keep)].copy(), pins[pins.day.isin(keep)] if len(pins) else pins
            md += [f"**{label}**", ""]
            rows = []
            for g in ("g_total", "g_oi", "g_tick", "g_iv"):
                for name, eff, t, ns, sgn in tests(xs.copy(), ps, g):
                    verdict = ""
                    if label.startswith("Judged") and g == "g_total":
                        verdict = ("—" if len(sessions) < MIN_VERDICT else
                                   "✅ PASS" if np.isfinite(t) and np.sign(eff) == sgn and abs(t) >= T_PASS else "❌ FAIL")
                    rows.append([g, name, f"{eff:+.4f}" if np.isfinite(eff) else "–", f"{t:+.1f}" if np.isfinite(t) else "–", ns, verdict])
            md += table(["Gamma estimate", "Test", "Effect", "t", "Sessions", "Verdict (g_total)"], rows) + [""]
        if len(sessions) < MIN_VERDICT:
            md += [f"Verdicts wait for {MIN_VERDICT} sessions ({MIN_VERDICT - len(sessions)} to go).", ""]
    (OUT / "ZERO_DTE_STUDY.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
