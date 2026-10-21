"""Five pre-specified PDH/PDL strategies with a VIX regime filter, and a 1-minute trade simulator.

Rules are fixed before testing (no parameter tuning). Signals come from the first
bar of a timeframe that trades through the previous trading day's high (PDH) or
low (PDL); entries happen at that bar's close.

  S1 Panic breakout fade (1h)      VIX >= 30. 1h bar closes beyond PDH/PDL -> fade it.
                                   stop 0.25 ATR, target 0.25 ATR (symmetric).
  S2 Stress breakout fade (30m)    VIX >= 25. 30m bar closes beyond PDH/PDL -> fade it.
                                   stop 0.1 ATR beyond the bar's extreme, target 1.5R.
  S3 Calm momentum long (15m)      VIX < 20. Strong 15m bar closes above PDH -> long.
                                   strong = range >= 1.5x the prior 20 bars' average and
                                   close in the top 25% of the bar. stop bar low, target 2R.
  S4 PDH rejection short (15m)     20 <= VIX < 30. 15m bar trades above PDH, closes back
                                   below it in the bottom half of the bar -> short.
                                   stop 0.05 ATR above bar high, target 1.5R.
  S5 PDL sweep & reclaim (30m)     VIX >= 20. 30m bar trades below PDL, closes back above
                                   it -> long. stop 0.05 ATR below bar low, target 2R.

Simulation (1 contract): after entry, each 1-minute bar is checked for the stop and
the target; if both are inside the same minute the stop is assumed (conservative).
Otherwise the trade closes at the last minute of the trading day. Costs are a flat
amount of points per round trip. One position at a time per strategy.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import reactions as rx
from . import sessions as s


@dataclass(frozen=True)
class Strategy:
    code: str
    name: str
    tf: str
    vix_min: float
    vix_max: float
    levels: tuple[str, ...]  # which levels can trigger: "PDH", "PDL"
    signal: str  # "accepted" (closed beyond), "rejected" (closed back inside)
    mode: str  # "fade" or "follow"
    stop: str  # "atr" (fixed ATR distance) or "bar" (beyond the signal bar's extreme)
    stop_atr: float
    target_r: float | None = None  # target in R multiples (None = fixed ATR target)
    target_atr: float | None = None
    strong_bar: bool = False  # S3 displacement filter
    close_zone: str | None = None  # "bottom_half" for S4


STRATEGIES = [
    Strategy("S1", "Panic breakout fade (1h)", "1h", 30, np.inf, ("PDH", "PDL"), "accepted", "fade", "atr", 0.25, target_atr=0.25),
    Strategy("S2", "Stress breakout fade (30m)", "30m", 25, np.inf, ("PDH", "PDL"), "accepted", "fade", "bar", 0.10, target_r=1.5),
    Strategy("S3", "Calm momentum long (15m)", "15m", 0, 20, ("PDH",), "accepted", "follow", "bar", 0.0, target_r=2.0, strong_bar=True),
    Strategy("S4", "PDH rejection short (15m)", "15m", 20, 30, ("PDH",), "rejected", "fade", "bar", 0.05, target_r=1.5, close_zone="bottom_half"),
    Strategy("S5", "PDL sweep & reclaim long (30m)", "30m", 20, np.inf, ("PDL",), "rejected", "fade", "bar", 0.05, target_r=2.0),
]


def signals(strat: Strategy, events: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """Turn first-touch events into trade instructions (entry, direction, stop, target)."""
    ev = events[(events.tf == strat.tf) & events.level.isin(strat.levels) & (events.reaction == strat.signal)]
    ev = ev[(ev.vix_prev >= strat.vix_min) & (ev.vix_prev < strat.vix_max)].copy()
    if ev.empty:
        return ev
    # trade direction: fading a high = short; following a high = long
    ev["direction"] = np.where(strat.mode == "fade", -ev["side"], ev["side"]).astype(int)
    rng = ev["bar_high"] - ev["bar_low"]
    if strat.strong_bar:
        avg = (bars["high"] - bars["low"]).rolling(20, min_periods=10).mean().shift()  # prior 20 bars only
        avg_at = pd.Series(avg.to_numpy(), index=bars["start"]).reindex(ev["touch_bar"]).to_numpy()
        close_pos = (ev["entry"] - ev["bar_low"]) / rng.replace(0, np.nan)
        ev = ev[(rng.to_numpy() >= 1.5 * avg_at) & (close_pos.to_numpy() >= 0.75)].copy()
        rng = ev["bar_high"] - ev["bar_low"]
    if strat.close_zone == "bottom_half":
        close_pos = (ev["entry"] - ev["bar_low"]) / rng.replace(0, np.nan)
        ev = ev[close_pos <= 0.5].copy()
    d = ev["direction"]
    if strat.stop == "atr":
        ev["stop"] = ev["entry"] - d * strat.stop_atr * ev["atr"]
    else:  # beyond the signal bar's extreme, against the trade
        extreme = np.where(d == 1, ev["bar_low"], ev["bar_high"])
        ev["stop"] = extreme - d * strat.stop_atr * ev["atr"]
    risk = (ev["entry"] - ev["stop"]) * d
    ev = ev[risk > 0].copy()
    risk = risk[risk > 0]
    if strat.target_atr is not None:
        ev["target"] = ev["entry"] + d * strat.target_atr * ev["atr"]
    else:
        ev["target"] = ev["entry"] + d * strat.target_r * risk
    ev["risk_pts"] = risk
    ev["strategy"] = strat.code
    return ev.sort_values("entry_time").reset_index(drop=True)


def simulate(sig: pd.DataFrame, minutes: pd.DataFrame, inst: s.Instrument, cost_pts: float = 1.0) -> pd.DataFrame:
    """Run trades on 1-minute bars. Returns one row per executed trade."""
    if sig.empty:
        return sig
    m = rx.trading_minutes(inst, minutes)
    t = m.index.as_unit("ns").asi8
    hi, lo, cl = m["high"].to_numpy(), m["low"].to_numpy(), m["close"].to_numpy()
    kind = "futures" if inst.day == "futures" else "calendar"
    day_of = s.trading_day(m.index, kind)
    day_end = pd.Series(np.arange(len(t)), index=day_of).groupby(level=0).max()  # index of last minute per day
    rows, busy_until = [], -1
    for r in sig.itertuples(index=False):
        a = np.searchsorted(t, pd.Timestamp(r.entry_time).as_unit("ns").value, side="left")
        tday = s.trading_day(pd.DatetimeIndex([r.touch_bar]), kind)[0]
        if tday not in day_end.index or a >= len(t):
            continue
        b = int(day_end[tday])
        if a > b or a <= busy_until:  # no time left that day, or a position is still open
            continue
        d, stop, target = r.direction, r.stop, r.target
        seg_hi, seg_lo = hi[a: b + 1], lo[a: b + 1]
        stop_hit = (seg_lo <= stop) if d == 1 else (seg_hi >= stop)
        tgt_hit = (seg_hi >= target) if d == 1 else (seg_lo <= target)
        i_s = int(np.argmax(stop_hit)) if stop_hit.any() else None
        i_t = int(np.argmax(tgt_hit)) if tgt_hit.any() else None
        if i_s is not None and (i_t is None or i_s <= i_t):  # same minute -> assume the stop
            j, exit_px, why = a + i_s, stop, "stop"
        elif i_t is not None:
            j, exit_px, why = a + i_t, target, "target"
        else:
            j, exit_px, why = b, cl[b], "end of day"
        pts = d * (exit_px - r.entry) - cost_pts
        rows.append(dict(strategy=r.strategy, level=r.level, direction="long" if d == 1 else "short",
                         entry_time=r.entry_time, entry=r.entry, stop=stop, target=target,
                         exit_time=pd.Timestamp(t[j], tz="UTC").tz_convert(s.TZ) + pd.Timedelta(minutes=1),
                         exit=exit_px, exit_reason=why, pnl_pts=pts, r_multiple=pts / r.risk_pts,
                         risk_pts=r.risk_pts, vix_prev=r.vix_prev, atr=r.atr, test_day=r.test_day))
        busy_until = j
    tr = pd.DataFrame(rows)
    if not tr.empty:
        tr["minutes_held"] = (pd.DatetimeIndex(tr.exit_time) - pd.DatetimeIndex(tr.entry_time)).total_seconds() / 60
    return tr


def stats(tr: pd.DataFrame, dollars_per_point: float = 20.0) -> dict:
    """Headline metrics for a set of trades (1 contract)."""
    if tr is None or tr.empty:
        return dict(trades=0)
    p = tr["pnl_pts"].to_numpy() * dollars_per_point
    eq = np.cumsum(p)
    dd = eq - np.maximum.accumulate(np.r_[0, eq])[1:]
    wins, losses = p[p > 0], p[p <= 0]
    years = max((pd.Timestamp(tr.exit_time.max()) - pd.Timestamp(tr.entry_time.min())).days / 365.25, 1e-9)
    return dict(
        trades=len(p), trades_per_year=len(p) / years, win_rate=len(wins) / len(p),
        profit_factor=wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
        avg_win=wins.mean() if len(wins) else 0.0, avg_loss=losses.mean() if len(losses) else 0.0,
        avg_trade=p.mean(), avg_r=tr["r_multiple"].mean(),
        avg_minutes=tr["minutes_held"].mean(), max_drawdown=dd.min(), net_pnl=p.sum(),
        exits=tr["exit_reason"].value_counts().to_dict(),
    )


# ------------------------------------------------------------------ S6 (pre-registered 2026-10-20)

def rejection_breakout_signals(events: pd.DataFrame, bars: pd.DataFrame, tf: str, kind: str = "futures",
                               vix_min: float = -np.inf, vix_max: float = np.inf,
                               stop_atr: float = 0.10, target_r: float = 2.0) -> pd.DataFrame:
    """S6: PDH rejection, then a later same-day bar closes above the rejection bar's high -> long.

    events: first-touch events (detect_events) with columns level, tf, reaction, price (= PDH),
            bar_high, bar_low, entry (= rejection bar close), atr, vix_prev, touch_bar, test_day.
    The rejection bar must close back below PDH in the bottom half of its range (as S4).
    Entry at the trigger bar's close, stop PDH - stop_atr x ATR, target target_r x risk.
    """
    ev = events[(events.tf == tf) & (events.level == "PDH") & (events.reaction == "rejected")]
    ev = ev[(ev.vix_prev >= vix_min) & (ev.vix_prev < vix_max)]
    rng = ev.bar_high - ev.bar_low
    ev = ev[((ev.entry - ev.bar_low) / rng.replace(0, np.nan)) <= 0.5]
    if ev.empty:
        return ev
    bars = bars.reset_index(drop=True)
    pos = pd.Index(pd.DatetimeIndex(bars["start"]))
    closes, close_t = bars["close"].to_numpy(), bars["close_time"]
    bday = s.trading_day(pd.DatetimeIndex(bars["start"]), kind).to_numpy()
    rows = []
    for r in ev.itertuples(index=False):
        j = pos.get_loc(pd.Timestamp(r.touch_bar))  # the rejection bar
        k = j + 1
        while k < len(bars) and bday[k] == bday[j]:
            if closes[k] > r.bar_high:
                break
            k += 1
        if k >= len(bars) or bday[k] != bday[j]:
            continue  # no break-up later that day
        entry = closes[k]
        stop = r.price - stop_atr * r.atr
        risk = entry - stop
        if risk <= 0:
            continue
        rows.append(dict(strategy="S6", level="PDH", direction=1, entry_time=close_t.iat[k], entry=entry, stop=stop,
                         target=entry + target_r * risk, risk_pts=risk, vix_prev=r.vix_prev, atr=r.atr,
                         test_day=r.test_day, touch_bar=bars.at[k, "start"], rejection_bar=r.touch_bar,
                         rejection_high=r.bar_high, pdh=r.price))
    out = pd.DataFrame(rows)
    return out.sort_values("entry_time").reset_index(drop=True) if not out.empty else out
