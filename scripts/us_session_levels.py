"""US-session (09:30-16:00 ET) analysis of PDH/PDL and Asia/London highs and lows.

    python scripts/us_session_levels.py

Windows: last 18, 12 and 6 months ending at the last NSX bar (2026-10-09). Nothing older.
Look-back is one day: PDH/PDL = previous US session's high/low; distances are expressed in
multiples of the previous US session's range (PDH - PDL).
Asia = 20:00-00:00 ET the evening before, London = 02:00-05:00 ET, both before the US open.
QQQ (data to 2025-12-31, no Asia trading) is included for the PDH/PDL part where data exists.
Writes reports/us_session_levels/.
"""
from __future__ import annotations

from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from quant import reactions as rx
from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "us_session_levels"
END = pd.Timestamp("2026-10-09")
WINDOWS = {"18m": pd.Timestamp("2025-04-10"), "12m": pd.Timestamp("2025-10-10"), "6m": pd.Timestamp("2026-04-10")}


def day_table(name: str) -> pd.DataFrame:
    inst = s.INSTRUMENTS[name]
    m = s.load(inst)
    # one extra prior day is needed only for the first day's PDH/PDL (1-day look-back)
    m = m[(m.index >= pd.Timestamp("2025-04-01", tz=s.TZ)) & (m.index < pd.Timestamp("2026-10-10", tz=s.TZ))]
    mod = s._minutes(m.index.tz_convert(s.TZ))
    rth = m[(mod >= 570) & (mod < 960) & (m.index.dayofweek < 5)]
    d = s.aggregate(rth, [rth.index.tz_localize(None).normalize()], ["day"])
    d = d[d.bars >= 200]  # skip stray partial sessions (half days have 210 bars)
    # keep only official US trading days (futures also trade short sessions on market holidays)
    cal = pd.read_parquet(ROOT / "data" / "free" / "NDX_daily_clean.parquet").index
    d = d[d.index.isin(cal)]
    out = pd.DataFrame(index=d.index)
    out["us_open"], out["us_high"], out["us_low"], out["us_close"] = d.open, d.high, d.low, d.close
    out["us_high_time"], out["us_low_time"] = d.high_time, d.low_time
    out["PDH"], out["PDL"], out["PDC"] = d.high.shift(), d.low.shift(), d.close.shift()
    out["prev_day"] = pd.Series(d.index, index=d.index).shift()
    if inst.day == "futures":  # NSX trades overnight: Asia and London exist
        sess = s.session_levels(inst, m)
        for tag, name_ in (("asia", "asia"), ("london", "london")):
            x = sess[(sess.session == name_) & (sess.coverage >= 0.8)].set_index("trading_day")
            out[f"{tag}_high"], out[f"{tag}_low"] = x.high.reindex(out.index), x.low.reindex(out.index)
            out[f"{tag}_open"], out[f"{tag}_close"] = x.open.reindex(out.index), x.close.reindex(out.index)
            out[f"{tag}_high_time"], out[f"{tag}_low_time"] = x.high_time.reindex(out.index), x.low_time.reindex(out.index)
    # 1-day look-back only: PDH/PDL must come from the immediately preceding official trading day
    official_prev = pd.Series([cal[cal < x].max() for x in out.index], index=out.index)
    out = out[out.prev_day == official_prev]
    out = out.dropna(subset=["PDH"])
    out = out[(out.index >= WINDOWS["18m"]) & (out.index <= END)]
    # first minute today's US session trades through each level
    by_day = {day: g for day, g in rth.groupby(rth.index.tz_localize(None).normalize())}
    levels = [("pdh_break_time", "PDH", 1), ("pdl_break_time", "PDL", -1)]
    if "london_high" in out:
        levels += [("london_high_break_time", "london_high", 1), ("london_low_break_time", "london_low", -1),
                   ("asia_high_break_time", "asia_high", 1), ("asia_low_break_time", "asia_low", -1)]
    for col, lvl, side in levels:
        vals = []
        for day, r in out.iterrows():
            g, level = by_day.get(day), r[lvl]
            if g is None or pd.isna(level):
                vals.append(pd.NaT)
                continue
            hit = g.index[(g.high > level).to_numpy()] if side == 1 else g.index[(g.low < level).to_numpy()]
            vals.append(hit[0] if len(hit) else pd.NaT)
        out[col] = vals
    out.insert(0, "instrument", name)
    return out


def features(t: pd.DataFrame) -> pd.DataFrame:
    f = t.copy()
    R = f.PDH - f.PDL
    f["prev_range_pct"] = R / f.PDC * 100
    f["open_pos"] = (f.us_open - f.PDL) / R  # 0 = at PDL, 1 = at PDH, >1 above PDH, <0 below PDL
    f["open_zone"] = np.select([f.us_open > f.PDH, f.us_open < f.PDL], ["above PDH", "below PDL"], "inside")
    f["took_pdh"], f["took_pdl"] = f.us_high > f.PDH, f.us_low < f.PDL
    f["pdh_excess"] = (f.us_high - f.PDH) / R  # how far beyond PDH (negative = fell short)
    f["pdl_excess"] = (f.PDL - f.us_low) / R
    f["us_return"] = (f.us_close - f.us_open) / R
    f["us_range"] = (f.us_high - f.us_low) / R
    f["us_range_pct"] = (f.us_high - f.us_low) / f.us_open * 100
    f["close_vs_pdh"] = f.us_close > f.PDH
    f["close_vs_pdl"] = f.us_close < f.PDL
    if "asia_high" in f:
        for tag in ("asia", "london"):
            f[f"{tag}_range"] = (f[f"{tag}_high"] - f[f"{tag}_low"]) / R
            f[f"{tag}_range_pct"] = (f[f"{tag}_high"] - f[f"{tag}_low"]) / f.PDC * 100
            f[f"{tag}_dir"] = (f[f"{tag}_close"] - f[f"{tag}_open"]) / R
            f[f"{tag}_mid_pos"] = ((f[f"{tag}_high"] + f[f"{tag}_low"]) / 2 - f.PDL) / R
            f[f"{tag}_took_pdh"], f[f"{tag}_took_pdl"] = f[f"{tag}_high"] > f.PDH, f[f"{tag}_low"] < f.PDL
            f[f"us_took_{tag}_high"], f[f"us_took_{tag}_low"] = f.us_high > f[f"{tag}_high"], f.us_low < f[f"{tag}_low"]
        f["premkt_dir"] = (f.us_open - f.PDC) / R  # overnight move into the open
    return f


# ------------------------------------------------------------------ statistics

def wilson(k, n, z=1.96):
    if n == 0:
        return np.nan, np.nan
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def spearman(x, y):
    ok = ~(pd.isna(x) | pd.isna(y))
    x, y = pd.Series(x[ok]).rank(), pd.Series(y[ok]).rank()
    n = len(x)
    if n < 10:
        return np.nan, np.nan, n
    r = np.corrcoef(x, y)[0, 1]
    t = r * sqrt((n - 2) / max(1e-12, 1 - r * r))
    p = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))  # normal approximation, fine for n >= 30
    return r, p, n


def prop(mask) -> str:
    mask = pd.Series(mask).dropna().astype(bool)
    k, n = int(mask.sum()), len(mask)
    if n == 0:
        return "–"
    lo, hi = wilson(k, n)
    return f"{k / n * 100:.0f}% ({lo * 100:.0f}–{hi * 100:.0f}), n={n}"


def window(f: pd.DataFrame, key: str) -> pd.DataFrame:
    return f[(f.index >= WINDOWS[key]) & (f.index <= END)]


CORR_PAIRS_PD = [
    ("open_pos", "pdh_excess", "Open position in yesterday's range → how far today's high gets above PDH"),
    ("open_pos", "pdl_excess", "Open position in yesterday's range → how far today's low gets below PDL"),
    ("open_pos", "us_return", "Open position in yesterday's range → US session return (close − open)"),
    ("prev_range_pct", "us_range_pct", "Yesterday's US range (%) → today's US range (%)"),
]
CORR_PAIRS_SESSIONS = [
    ("asia_range_pct", "us_range_pct", "Asia range (%) → US range (%)"),
    ("london_range_pct", "us_range_pct", "London range (%) → US range (%)"),
    ("asia_dir", "us_return", "Asia direction (close − open) → US return"),
    ("london_dir", "us_return", "London direction (close − open) → US return"),
    ("premkt_dir", "us_return", "Overnight move (US open − PDC) → US return"),
    ("london_mid_pos", "us_return", "London midpoint vs yesterday's range → US return"),
    ("london_dir", "pdh_excess", "London direction → today's high vs PDH"),
    ("london_dir", "pdl_excess", "London direction → today's low vs PDL"),
]


def corr_table(f: pd.DataFrame, pairs) -> pd.DataFrame:
    rows = []
    for x, y, label in pairs:
        if x not in f or y not in f:
            continue
        r, p, n = spearman(f[x].to_numpy(dtype=float), f[y].to_numpy(dtype=float))
        rows.append(dict(relationship=label, rho=r, p_value=p, days=n,
                         meaningful=bool(abs(r) >= 0.2 and p < 0.01) if not np.isnan(r) else False))
    return pd.DataFrame(rows)


def pd_stats(f: pd.DataFrame) -> list[tuple[str, str]]:
    rows = [
        ("US session trades above PDH", prop(f.took_pdh)),
        ("US session trades below PDL", prop(f.took_pdl)),
        ("Both PDH and PDL taken (outside day)", prop(f.took_pdh & f.took_pdl)),
        ("Neither taken (inside day)", prop(~f.took_pdh & ~f.took_pdl)),
        ("Closes above PDH, when PDH was taken", prop(f.close_vs_pdh[f.took_pdh])),
        ("Closes back below PDH, when PDH was taken", prop(~f.close_vs_pdh[f.took_pdh])),
        ("Closes below PDL, when PDL was taken", prop(f.close_vs_pdl[f.took_pdl])),
        ("Closes back above PDL, when PDL was taken", prop(~f.close_vs_pdl[f.took_pdl])),
    ]
    for zone in ("above PDH", "inside", "below PDL"):
        z = f[f.open_zone == zone]
        rows.append((f"Opens {zone}: share of days", prop(f.open_zone == zone)))
        if len(z):
            rows.append((f"  … then takes PDH", prop(z.took_pdh)))
            rows.append((f"  … then takes PDL", prop(z.took_pdl)))
            rows.append((f"  … then closes up (close > open)", prop(z.us_close > z.us_open)))
    for col, lab in (("pdh_break_time", "PDH"), ("pdl_break_time", "PDL")):
        tt = pd.DatetimeIndex(f[col].dropna())
        if len(tt):
            mins = tt.hour * 60 + tt.minute
            med = int(np.median(mins))
            share = (mins < 600).mean()
            rows.append((f"First {lab} break: median time / share before 10:00", f"{med // 60:02d}:{med % 60:02d} / {share * 100:.0f}% (n={len(tt)})"))
    return rows


def session_stats(f: pd.DataFrame) -> list[tuple[str, str]]:
    rows = []
    for tag, lab in (("asia", "Asia"), ("london", "London")):
        rows += [
            (f"{lab} high above PDH (taken before US open)", prop(f[f"{tag}_took_pdh"])),
            (f"{lab} low below PDL (taken before US open)", prop(f[f"{tag}_took_pdl"])),
            (f"US session takes the {lab} high", prop(f[f"us_took_{tag}_high"])),
            (f"US session takes the {lab} low", prop(f[f"us_took_{tag}_low"])),
            (f"US session takes both {lab} high and low", prop(f[f"us_took_{tag}_high"] & f[f"us_took_{tag}_low"])),
        ]
    l = f
    rows += [
        ("If London took PDH: US high goes above the London high", prop(l.us_took_london_high[l.london_took_pdh])),
        ("If London took PDH: US closes back below PDH", prop(~l.close_vs_pdh[l.london_took_pdh])),
        ("If London took PDL: US low goes below the London low", prop(l.us_took_london_low[l.london_took_pdl])),
        ("If London took PDL: US closes back above PDL", prop(~l.close_vs_pdl[l.london_took_pdl])),
        ("If London stayed inside yesterday's range: US takes PDH", prop(l.took_pdh[~l.london_took_pdh & ~l.london_took_pdl])),
        ("If London stayed inside yesterday's range: US takes PDL", prop(l.took_pdl[~l.london_took_pdh & ~l.london_took_pdl])),
        ("London closed up: US closes up", prop((l.us_close > l.us_open)[l.london_dir > 0])),
        ("London closed down: US closes up", prop((l.us_close > l.us_open)[l.london_dir < 0])),
        ("Asia closed up: US closes up", prop((l.us_close > l.us_open)[l.asia_dir > 0])),
        ("Asia closed down: US closes up", prop((l.us_close > l.us_open)[l.asia_dir < 0])),
    ]
    # which London extreme the US session takes first, and how the day ends
    hi_t, lo_t = pd.to_datetime(l.london_high_break_time), pd.to_datetime(l.london_low_break_time)
    high_first = hi_t.notna() & (lo_t.isna() | (hi_t < lo_t))
    low_first = lo_t.notna() & (hi_t.isna() | (lo_t < hi_t))
    up = l.us_close > l.us_open
    rows += [
        ("US takes the London HIGH first: share of days", prop(high_first)),
        ("  … then US closes up", prop(up[high_first])),
        ("  … then US also takes the London low later", prop(lo_t[high_first].notna())),
        ("US takes the London LOW first: share of days", prop(low_first)),
        ("  … then US closes up", prop(up[low_first])),
        ("  … then US also takes the London high later", prop(hi_t[low_first].notna())),
        ("US takes neither London extreme", prop(hi_t.isna() & lo_t.isna())),
    ]
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tables = {}
    for name in ("NSX", "QQQ"):
        t = day_table(name)
        t.to_csv(OUT / f"daily_levels_{name}.csv")
        tables[name] = features(t)
        print(f"{name}: {len(t)} US sessions marked ({t.index.min().date()} -> {t.index.max().date()})")
    results = []
    for key in WINDOWS:
        for name, f in tables.items():
            w = window(f, key)
            if len(w) < 20:
                continue
            for item, val in pd_stats(w):
                results.append(dict(window=key, instrument=name, section="PDH/PDL vs US session", item=item, value=val, days=len(w)))
            c = corr_table(w, CORR_PAIRS_PD + (CORR_PAIRS_SESSIONS if name == "NSX" else []))
            c.insert(0, "window", key); c.insert(1, "instrument", name)
            c.to_csv(OUT / f"correlations_{name}_{key}.csv", index=False)
            if name == "NSX":
                for item, val in session_stats(w):
                    results.append(dict(window=key, instrument=name, section="Asia/London vs US session", item=item, value=val, days=len(w)))
    pd.DataFrame(results).to_csv(OUT / "stats_all_windows.csv", index=False)
    print("written to", OUT)


if __name__ == "__main__":
    main()
