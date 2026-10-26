"""Lanto Trades framework, codified for backtesting (NSX 1-minute, last 18 months).

    python scripts/lanto.py             # parameter grid -> reports/lanto/grid.csv, trades.parquet
    (the canonical, pre-registered configuration is ideas/lanto_canonical.py)

Source: the research brief (X posts "MY STRATEGY SIMPLIFIED", ORM / IFVG + FVG videos). Four layers, all times ET:

1. HTF bias at 09:30 from COMPLETED candles only: Daily (futures day 18:00-17:00), 4H (18/22/02/06/10/14), 1H.
   On each timeframe, track fair value gaps (3-candle): bullish if low[i] > high[i-2] (zone high[i-2]..low[i]),
   bearish if high[i] < low[i-2]; minimum size 0.3 x ATR(14) of that timeframe. A zone is INVERTED (IFVG) when a
   candle closes through its far side; an inverted zone closed through again is dropped. Timeframe bias = direction of
   the most recent active zone event (creation or inversion). Score = Daily x1 + 4H x2 + 1H x1 (4H weighted most,
   as Lanto colour-codes and cites 4H gaps). Bullish if score >= +2, bearish if <= -2, else neutral (no trade).
2. Overnight range: high/low of 18:00 (previous day) -> 08:00 (Asia + London), or -> 09:29 (variant, includes the
   US pre-market so the ORM is the first move after the open). True Day Open (TDO) = 00:00 open.
3. ORM (Opening Range Move) within the first W minutes after 09:30: the first of
     bullish: a 1m bar trades below the overnight low and a 1m close back above it (sweep + reclaim), or two
              consecutive 1m closes above the overnight high (break + hold);  bearish: mirror.
   The ORM must agree with the HTF bias ("hard"), or, in the "orm_only" variant, the ORM alone sets the direction.
4. Entries (bias direction only) on 1m or 5m candles from the ORM confirmation until 11:00 (or 11:30):
     Type A FVG retest: a gap formed after the ORM; a later candle trades into it and closes beyond its midpoint ->
            enter at that close; stop beyond the far side of the gap - 1 pt.
     Type B IFVG: an opposite-direction gap (formed since 09:30) is closed through -> enter at that close; stop beyond
            the far side of the inverted gap - 1 pt.
   Minimum gap size 0 or 0.03 x R0 (R0 = previous US range). Optional True Day Open filter: longs only below TDO
   (discount), shorts only above (premium).
Exits: target PDH (longs) / PDL (shorts) if at least 1R away, else 2R ("pdhl"); or fixed 2R / 3R. Optional breakeven
at +1R (stop to entry from the next bar). Flat at 16:00. Max 1 or 2 trades a day, one at a time.
Approved rules: same-minute stop + target = stop; 1 NQ contract, $20/pt, 1 pt round trip; no slippage.
"""
from __future__ import annotations

import importlib.util
import itertools
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from quant import sessions as s

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "lanto"
_spec = importlib.util.spec_from_file_location("lanto_orb", ROOT / "scripts" / "open_orb.py")
o = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o)
NY = s.TZ
WEIGHTS = {"D": 1, "4H": 2, "1H": 1}


@dataclass(frozen=True)
class Config:
    bias: str = "hard"          # hard (HTF score and ORM agree) | orm_only
    orm_window: int = 30        # minutes after 09:30
    entry_tf: int = 1           # 1 or 5 minutes
    entry_type: str = "AB"      # A (FVG retest) | B (IFVG) | AB (either)
    min_gap_r: float = 0.03     # minimum entry gap size, x R0
    target: str = "pdhl"        # pdhl | 2R | 3R
    breakeven: bool = True      # stop to entry once +1R
    max_trades: int = 2
    entry_end: int = 660        # 11:00 (minutes after midnight)
    tdo_filter: bool = False    # longs only below the True Day Open, shorts only above
    on_end: str = "08:00"       # overnight range ends 08:00 (Asia + London) or "09:29" (incl. US pre-market)

    @property
    def key(self) -> str:
        return (f"bias={self.bias}|orm{self.orm_window}|{self.entry_tf}m|type{self.entry_type}|gap{self.min_gap_r}|"
                f"tgt{self.target}|be{int(self.breakeven)}|max{self.max_trades}|until{self.entry_end // 60}:{self.entry_end % 60:02d}|"
                f"tdo{int(self.tdo_filter)}|on{self.on_end}")


# ---------------------------------------------------------------- HTF bias
def htf_bars(m: pd.DataFrame) -> dict[str, pd.DataFrame]:
    agg = dict(open="first", high="max", low="min", close="last")
    out = {"1H": m.resample("1h").agg(agg).dropna(), "4H": m.resample("4h", offset="2h").agg(agg).dropna(),
           "D": m.resample("1D", offset="18h").agg(agg).dropna()}
    for k, f in {"1H": "1h", "4H": "4h", "D": "1D"}.items():
        b = out[k]
        b["end"] = b.index + pd.Timedelta(f)
    return out


def bias_events(b: pd.DataFrame, min_atr: float = 0.3, keep: int = 60) -> pd.Series:
    """Direction (+1/-1) of the most recent active FVG/IFVG event, known at each candle's end time."""
    h, l, c = b.high.to_numpy(), b.low.to_numpy(), b.close.to_numpy()
    tr = np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])
    atr = pd.Series(np.r_[h[0] - l[0], tr]).rolling(14, min_periods=5).mean().to_numpy()
    zones: list[list] = []  # [dir, bot, top, event_i, inverted, created_i]
    out = np.zeros(len(b))
    last_dir = 0
    for i in range(len(b)):
        for z in zones:
            if z[0] == 1 and c[i] < z[1] or z[0] == -1 and c[i] > z[2]:
                if z[4]:
                    z[0] = 0  # inverted zone closed through again: drop
                else:
                    z[0], z[3], z[4] = -z[0], i, True
        zones = [z for z in zones if z[0] != 0 and i - z[5] <= keep]
        if i >= 2 and np.isfinite(atr[i]):
            if l[i] > h[i - 2] and l[i] - h[i - 2] >= min_atr * atr[i]:
                zones.append([1, h[i - 2], l[i], i, False, i])
            elif h[i] < l[i - 2] and l[i - 2] - h[i] >= min_atr * atr[i]:
                zones.append([-1, h[i], l[i - 2], i, False, i])
        if zones:
            last_dir = max(zones, key=lambda z: z[3])[0]
        out[i] = last_dir
    return pd.Series(out, index=b.end.to_numpy())


def bias_at(series: pd.Series, t: pd.Timestamp) -> int:
    i = series.index.searchsorted(t, side="right") - 1
    return int(series.iloc[i]) if i >= 0 else 0


# ---------------------------------------------------------------- day context
@lru_cache(maxsize=1)
def day_context() -> list[dict]:
    m = s.load(s.INSTRUMENTS["NSX"])
    m = m[(m.index >= pd.Timestamp("2025-01-15", tz=NY)) & (m.index < pd.Timestamp("2026-10-10", tz=NY))]
    bars = htf_bars(m)
    ev = {k: bias_events(v) for k, v in bars.items()}
    lv = pd.read_csv(o.SRC / "daily_levels_NSX.csv", index_col=0, parse_dates=[0])
    idx = m.index
    out = []
    for d in o.load_days():
        day = d["day"]
        if day not in lv.index:
            continue
        t930 = pd.Timestamp(day, tz=NY) + pd.Timedelta(hours=9, minutes=30)
        score = sum(WEIGHTS[k] * bias_at(ev[k], t930) for k in WEIGHTS)
        a = idx.searchsorted(pd.Timestamp(day - pd.Timedelta(days=1), tz=NY) + pd.Timedelta(hours=18))
        b8 = idx.searchsorted(pd.Timestamp(day, tz=NY) + pd.Timedelta(hours=8))
        b930 = idx.searchsorted(pd.Timestamp(day, tz=NY) + pd.Timedelta(hours=9, minutes=30))
        b0 = idx.searchsorted(pd.Timestamp(day, tz=NY))
        if b8 - a < 300 or b0 >= len(idx):
            continue
        on, pre = m.iloc[a:b8], m.iloc[a:b930]
        r = lv.loc[day]
        out.append(dict(d=d, score=score, htf=int(np.sign(score)) if abs(score) >= 2 else 0, on_h=on.high.max(), on_l=on.low.min(),
                        on_h_0929=pre.high.max(), on_l_0929=pre.low.min(),
                        tdo=m.open.iloc[b0], pdh=r.PDH, pdl=r.PDL, R0=r.PDH - r.PDL))
    return out


def orm(d: dict, ctx: dict, window: int, on_end: str = "08:00") -> tuple[int, int] | None:
    """(direction, index of the confirming 1m bar) of the first ORM within `window` minutes after 09:30, or None.
    bullish: the overnight low is traded below and a later (or the same) 1m close is back above it, or two
             consecutive 1m closes above the overnight high;  bearish: mirror."""
    H, L = (ctx["on_h"], ctx["on_l"]) if on_end == "08:00" else (ctx["on_h_0929"], ctx["on_l_0929"])
    swept_lo = swept_hi = False
    prev_c = np.nan
    for i in range(len(d["c"])):
        if d["mod"][i] >= 570 + window:
            return None
        swept_lo |= d["l"][i] < L
        swept_hi |= d["h"][i] > H
        c = d["c"][i]
        bull = (swept_lo and c > L) or (prev_c > H and c > H)
        bear = (swept_hi and c < H) or (prev_c < L and c < L)
        if bull != bear:  # exactly one side confirmed (a bar confirming both is ambiguous: wait)
            return (1 if bull else -1), i
        prev_c = c
    return None


# ---------------------------------------------------------------- entries and exits
def simulate(d: dict, pos: int, side: int, entry: float, stop: float, target: float | None, breakeven: bool) -> tuple[int, float, str]:
    """Walk 1m bars from pos: stop first, then target; breakeven (stop to entry) applies from the bar after +1R."""
    risk, st = abs(entry - stop), stop
    for j in range(pos, len(d["c"])):
        h, l = d["h"][j], d["l"][j]
        if (l <= st) if side == 1 else (h >= st):
            return j, st, "stop" if st == stop else "breakeven"
        if target is not None and ((h >= target) if side == 1 else (l <= target)):
            return j, target, "target"
        if breakeven and ((h - entry) if side == 1 else (entry - l)) >= risk:
            st = entry
    j = len(d["c"]) - 1
    return j, d["c"][j], "16:00 close"


def run_day(ctx: dict, cfg: Config) -> list[dict]:
    d = ctx["d"]
    om = orm(d, ctx, cfg.orm_window, cfg.on_end)
    if om is None:
        return []
    side, k = om
    if cfg.bias == "hard" and ctx["htf"] != side:
        return []
    cd = o.candles_after(d, 0, cfg.entry_tf)
    ch, cl, cc, last = cd["h"], cd["l"], cd["c"], cd["last"]
    min_gap = max(cfg.min_gap_r * ctx["R0"], 1e-9)
    gaps: list[dict] = []
    trades, busy = [], -1
    for i in range(len(cc)):
        li = last[i]
        tradable = li > k and li > busy and d["mod"][li] + 1 <= cfg.entry_end and len(trades) < cfg.max_trades
        sig = None
        if tradable:
            for g in reversed(gaps):  # gaps formed on earlier candles only; newest first
                if g["used"]:
                    continue
                if cfg.entry_type in ("A", "AB") and g["dir"] == side and g["after_orm"]:
                    mid = (g["bot"] + g["top"]) / 2
                    if side == 1 and cl[i] <= g["top"] and cc[i] > mid:
                        sig = (cc[i], g["bot"] - 1.0)
                    elif side == -1 and ch[i] >= g["bot"] and cc[i] < mid:
                        sig = (cc[i], g["top"] + 1.0)
                if sig is None and cfg.entry_type in ("B", "AB") and g["dir"] == -side:
                    if side == 1 and cc[i] > g["top"]:
                        sig = (cc[i], g["bot"] - 1.0)
                    elif side == -1 and cc[i] < g["bot"]:
                        sig = (cc[i], g["top"] + 1.0)
                if sig is not None:
                    g["used"] = True
                    break
        if i >= 2:  # a gap completed by this candle can be used from the next candle on
            if cl[i] > ch[i - 2] and cl[i] - ch[i - 2] >= min_gap:
                gaps.append(dict(dir=1, bot=ch[i - 2], top=cl[i], after_orm=last[i - 2] > k, used=False))
            elif ch[i] < cl[i - 2] and cl[i - 2] - ch[i] >= min_gap:
                gaps.append(dict(dir=-1, bot=ch[i], top=cl[i - 2], after_orm=last[i - 2] > k, used=False))
        if sig is None:
            continue
        entry, stop = sig
        if (entry - stop) * side <= 0:
            continue
        if cfg.tdo_filter and ((side == 1 and entry >= ctx["tdo"]) or (side == -1 and entry <= ctx["tdo"])):
            continue
        risk = abs(entry - stop)
        if cfg.target == "pdhl":
            lvl = ctx["pdh"] if side == 1 else ctx["pdl"]
            target = lvl if (lvl - entry) * side >= risk else entry + side * 2 * risk
        else:
            target = entry + side * float(cfg.target[:-1]) * risk
        pos = li + 1
        if pos >= len(d["c"]):
            break
        j, px, why = simulate(d, pos, side, entry, stop, target, cfg.breakeven)
        pts = side * (px - entry) - o.COST
        trades.append(dict(day=d["day"], side=side, entry_time=d["t"][pos], exit_time=d["t"][j] + pd.Timedelta(minutes=1),
                           entry=entry, stop=stop, target=target, exit=px, exit_reason=why, pnl_usd=pts * o.DPP,
                           r_multiple=pts / risk, htf_score=ctx["score"]))
        busy = j
    return trades


def run(cfg: Config) -> pd.DataFrame:
    rows = [t for ctx in day_context() for t in run_day(ctx, cfg)]
    return pd.DataFrame(rows, columns=["day", "side", "entry_time", "exit_time", "entry", "stop", "target", "exit", "exit_reason",
                                       "pnl_usd", "r_multiple", "htf_score"])


GRID = dict(bias=["hard", "orm_only"], orm_window=[15, 30, 45], entry_tf=[1, 5], entry_type=["A", "B", "AB"],
            min_gap_r=[0.0, 0.03], target=["pdhl", "2R", "3R"], breakeven=[True, False], max_trades=[1, 2],
            tdo_filter=[False, True], on_end=["08:00", "09:29"])


def _grid_chunk(cfgs: list[tuple]) -> tuple[list[dict], list[pd.DataFrame]]:
    keys, SPLIT = list(GRID), pd.Timestamp("2026-04-10")
    rows, all_t = [], []
    for vals in cfgs:
        cfg = Config(**dict(zip(keys, vals)))
        t = run(cfg)
        if t.empty:
            continue
        all_t.append(t.assign(config=cfg.key))
        for per, x in (("first 12m", t[t.day < SPLIT]), ("last 6m", t[t.day >= SPLIT]), ("18m", t)):
            p = x.pnl_usd.to_numpy()
            lo = -p[p <= 0].sum()
            rows.append(dict(config=cfg.key, **dict(zip(keys, vals)), period=per, trades=len(p), win=(p > 0).mean() if len(p) else np.nan,
                             pf=p[p > 0].sum() / lo if lo > 0 else np.nan, net=p.sum(), avg_r=x.r_multiple.mean() if len(p) else np.nan))
    return rows, all_t


def main() -> None:
    import os
    from concurrent.futures import ProcessPoolExecutor
    OUT.mkdir(parents=True, exist_ok=True)
    combos = list(itertools.product(*GRID.values()))
    workers = max(2, (os.cpu_count() or 4) - 1)
    chunks = [combos[i::workers * 8] for i in range(workers * 8)]
    rows, all_t = [], []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for n, (r, t) in enumerate(ex.map(_grid_chunk, chunks)):
            rows += r
            all_t += t
            print(f"chunk {n + 1}/{len(chunks)}", flush=True)
    pd.DataFrame(rows).to_csv(OUT / "grid.csv", index=False)
    pd.concat(all_t).to_parquet(OUT / "trades.parquet")
    print(f"done: {len(combos)} configs")


if __name__ == "__main__":
    main()
