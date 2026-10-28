"""Deep research grid (protocol: reports/deep_research_2026-10-28/PROTOCOL.md).

    python scripts/deep_research.py            # writes reports/deep_research_2026-10-28/{trades.parquet, configs.parquet}

Families x candle sizes (1/2/3/5/10 min) x directions x stops x targets, first signal of the day, US session, approved
rules (1 NQ, $20/pt, 1 pt round trip, same-minute stop+target = stop). Regime filters are applied afterwards as day
masks. Settings are chosen on 2024-10-10 -> 2025-10-09 only; scripts/deep_research_report.py judges them on the
unseen 12 / 8 / 6 months.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant.live.calendar import Calendar  # noqa: E402
from quant.live.research_us import COST, DPP, LAST_ENTRY, candles_after, exit_trade, or_end  # noqa: E402

OUT = ROOT / "reports" / "deep_research_2026-10-28"
TZ = "America/New_York"
FIRST, LAST = pd.Timestamp("2024-10-10"), pd.Timestamp("2026-10-09")
TFS = [1, 2, 3, 5, 10]
DIRS = ["long", "short", "both"]
STOPS = ["sig", 0.10, 0.20, 0.30]
TARGETS = [1.0, 2.0, 3.0, None]
FAMILIES = {"pd_break": [None], "pd_reject": [None], "on_break": [None], "on_reject": [None], "orb": [5, 15, 30],
            "noise": [1.0, 1.5], "donchian": [10, 20], "stretch": [2.0, 3.0], "pdc_cross": [None]}
NOISE_DAYS = 14


def load_days(start: str = "2024-08-01", end: pd.Timestamp = LAST) -> list[dict]:
    m = pd.read_parquet(ROOT / "data/free/NSXUSD_1min_histdata.parquet")
    m = m[(m.index >= pd.Timestamp(start, tz=TZ)) & (m.index < end.tz_localize(TZ) + pd.Timedelta(days=1))]
    mod = m.index.hour * 60 + m.index.minute
    rth = m[(mod >= 570) & (mod < 960) & (m.index.day_of_week < 5)]
    days, prev = [], None
    cal = Calendar()
    for day, g in rth.groupby(rth.index.normalize().tz_localize(None)):
        if not cal.is_trading_day(day.date()):  # holidays have a few futures hours; they are not sessions
            continue
        on = m[(m.index >= (day - pd.Timedelta(days=3 if day.day_of_week == 0 else 1)).tz_localize(TZ) + pd.Timedelta(hours=18))
               & (m.index < day.tz_localize(TZ) + pd.Timedelta(hours=9, minutes=30))]
        d = dict(day=day, t=g.index, mod=(g.index.hour * 60 + g.index.minute).to_numpy(), o=g.open.to_numpy(),
                 h=g.high.to_numpy(), l=g.low.to_numpy(), c=g.close.to_numpy(),
                 ONH=on.high.max() if len(on) else np.nan, ONL=on.low.min() if len(on) else np.nan)
        if prev is not None and len(g) >= 300:  # half days count as the previous session but aren't traded
            d.update(PDH=prev["h"].max(), PDL=prev["l"].min(), PDC=prev["c"][-1])
            d["R0"] = d["PDH"] - d["PDL"]
            days.append(d)
        prev = d
    add_noise(days)
    return days


def add_noise(days: list[dict]) -> None:
    """sigma[m] = average |close / open - 1| at minute-of-day m over the previous NOISE_DAYS sessions."""
    grid = np.full((len(days), 390), np.nan)
    for i, d in enumerate(days):
        grid[i, d["mod"] - 570] = np.abs(d["c"] / d["o"][0] - 1)
    for i, d in enumerate(days):
        d["sigma"] = np.nanmean(grid[i - NOISE_DAYS:i], axis=0)[d["mod"] - 570] if i >= NOISE_DAYS else None


def day_context(days: list[dict]) -> pd.DataFrame:
    """Regime and sentiment tags known before each open."""
    vix = pd.read_parquet(ROOT / "data/free/VIX_daily_cboe.parquet").close
    ndx = pd.read_parquet(ROOT / "data/free/NDX_daily_clean.parquet").close
    dix = pd.read_csv(ROOT / "data/free/SPX_GEX_squeezemetrics.csv", parse_dates=["date"]).set_index("date").dix
    dixp = dix.rolling(252, min_periods=126).apply(lambda x: (x[:-1] < x[-1]).mean() * 100, raw=True)
    sma50, sma200 = ndx.rolling(50).mean(), ndx.rolling(200).mean()

    def prior(s: pd.Series, day):
        p = s[s.index < day]
        return float(p.iloc[-1]) if len(p) else np.nan

    rows = []
    for d in days:
        day, op = d["day"], d["o"][0]
        v, n, a50, a200, dx = (prior(x, day) for x in (vix, ndx, sma50, sma200, dixp))
        gap = (op - d["PDC"]) / d["R0"]
        rows.append(dict(day=day, all=True, vix_lt16=v < 16, vix_16_22=16 <= v < 22, vix_ge22=v >= 22,
                         above_sma50=n > a50, below_sma50=n <= a50, above_sma200=n > a200, below_sma200=n <= a200,
                         dix_high=dx >= 60, dix_low=dx <= 40, gap_up=gap > 0.10, gap_down=gap < -0.10,
                         flat_open=abs(gap) <= 0.10, open_above_pdh=op > d["PDH"], open_below_pdl=op < d["PDL"],
                         open_inside=d["PDL"] <= op <= d["PDH"], prior_up=d["PDC"] > d.get("PPC", np.nan),
                         prior_down=d["PDC"] < d.get("PPC", np.nan)))
    return pd.DataFrame(rows).set_index("day")


def signals(d: dict, fam: str, p, tf: int) -> tuple[dict, np.ndarray, np.ndarray] | None:
    """Candles and long / short signal flags for one day."""
    start = or_end(d, p) if fam == "orb" else 0
    if start >= len(d["c"]) - 1:
        return None
    cd = candles_after(d, start, tf)
    c, h, l, n = cd["c"], cd["h"], cd["l"], len(cd["c"])
    prev_c = np.r_[d["c"][start - 1] if start > 0 else d["o"][0], c[:-1]]
    z = np.zeros(n, bool)
    if fam in ("pd_break", "on_break", "orb", "pdc_cross"):
        if fam == "orb":
            hi, lo = d["h"][:start].max(), d["l"][:start].min()
        elif fam == "pdc_cross":
            hi = lo = d["PDC"]
        else:
            hi, lo = (d["PDH"], d["PDL"]) if fam == "pd_break" else (d["ONH"], d["ONL"])
        return cd, (c > hi) & (prev_c <= hi), (c < lo) & (prev_c >= lo)
    if fam in ("pd_reject", "on_reject"):
        hi, lo = (d["PDH"], d["PDL"]) if fam == "pd_reject" else (d["ONH"], d["ONL"])
        return cd, (l < lo) & (c >= lo) & (prev_c >= lo), (h > hi) & (c <= hi) & (prev_c <= hi)
    if fam == "noise":
        if d["sigma"] is None:
            return None
        sig = d["sigma"][cd["last"]] * p
        up = max(d["o"][0], d["PDC"]) * (1 + sig)
        dn = min(d["o"][0], d["PDC"]) * (1 - sig)
        return cd, c > up, c < dn
    if fam == "donchian":
        if n <= p:
            return None
        hh = pd.Series(h).rolling(p).max().shift(1).to_numpy()
        ll = pd.Series(l).rolling(p).min().shift(1).to_numpy()
        return cd, np.nan_to_num(c > hh), np.nan_to_num(c < ll)
    if fam == "stretch":
        twap = np.cumsum(d["c"])[cd["last"]] / (cd["last"] + 1)
        atr = pd.Series(h - l).rolling(14).mean().to_numpy()
        ok = ~np.isnan(atr) & (d["mod"][cd["last"]] >= 600)
        return cd, ok & (c < twap - p * np.nan_to_num(atr)), ok & (c > twap + p * np.nan_to_num(atr))
    return cd, z, z


def first(cd: dict, d: dict, flags: np.ndarray) -> int | None:
    for i in np.flatnonzero(flags):
        if d["mod"][cd["last"][i]] + 1 > LAST_ENTRY:
            return None
        if cd["last"][i] + 1 < len(d["c"]):
            return int(i)
    return None


def run(days: list[dict]) -> pd.DataFrame:
    rows = []
    for d in days:
        if not d["R0"] > 0:
            continue
        for fam, ps in FAMILIES.items():
            for p, tf in itertools.product(ps, TFS):
                s = signals(d, fam, p, tf)
                if s is None:
                    continue
                cd, sl, ss = s
                il, ish = first(cd, d, sl), first(cd, d, ss)
                picks = {"long": (il, 1), "short": (ish, -1)}
                cands = [x for x in ((il, 1), (ish, -1)) if x[0] is not None]
                picks["both"] = min(cands) if cands else (None, 0)
                for dr in DIRS:
                    i, side = picks[dr]
                    if i is None:
                        continue
                    entry, pos = cd["c"][i], cd["last"][i] + 1
                    for st in STOPS:
                        stop = (cd["l"][i] if side == 1 else cd["h"][i]) if st == "sig" else entry - side * st * d["R0"]
                        risk = side * (entry - stop)
                        if risk <= 0:
                            continue
                        for tg in TARGETS:
                            j, px, why = exit_trade(d, pos, side, stop, None if tg is None else entry + side * tg * risk)
                            pts = side * (px - entry) - COST
                            rows.append((d["day"], fam, -1 if p is None else p, tf, dr, str(st), -1 if tg is None else tg,
                                         side, d["t"][pos], d["t"][j], pts * DPP, pts / risk))
    return pd.DataFrame(rows, columns=["day", "family", "param", "tf", "dir", "stop", "target", "side", "entry_time",
                                       "exit_time", "pnl_usd", "r_multiple"])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    days = load_days()
    for a, b in zip(days[:-1], days[1:]):
        b["PPC"] = a["PDC"]
    days = [d for d in days if FIRST <= d["day"] <= LAST]
    print(f"{len(days)} sessions {days[0]['day']:%Y-%m-%d} -> {days[-1]['day']:%Y-%m-%d}")
    day_context(days).to_parquet(OUT / "context.parquet")
    t = run(days)
    t.to_parquet(OUT / "trades.parquet")
    print(f"{len(t):,} trades")


if __name__ == "__main__":
    main()
