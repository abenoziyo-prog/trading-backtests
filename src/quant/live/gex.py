"""Dealer gamma exposure (GEX) from a Schwab option-chain snapshot. Pure calculation: no I/O, no Schwab calls.

Convention (the common "naive GEX", e.g. SqueezeMetrics): dealers are assumed long the calls and short the puts that
customers hold, so call gamma counts +, put gamma counts -. Public data never shows who holds what, so this is an
estimate of the sign, not a measurement.

  GEX of a contract   = sign x gamma x open interest x multiplier x spot^2 x 1%   ($ of underlying dealers must trade
                        per 1% move in the underlying)
  net GEX             sum over contracts; > 0 = "positive gamma" regime (hedging dampens moves), < 0 = "negative"
  gamma flip          the underlying price where net GEX crosses zero, found by re-pricing every contract's gamma
                      (Black-Scholes, its own implied volatility) across spot +/- 10%; the crossing nearest spot
  call wall / put wall  strike with the largest call GEX / the largest (negative) put GEX
Contracts already expired at the snapshot time (today's expiries after 16:00) are left out.
Gamma is computed from each contract's implied volatility (Schwab's 'volatility' field) so the value at spot and the
flip profile are consistent; Schwab's own gamma is kept in the raw rows for reference. Open interest is as of the
previous close (exchanges publish it once a day).

zero_dte() is the same calculation on one expiry only: the nearest one that has not expired (today's until 16:00, the
next session's after that), with the largest open-interest strikes of the calls and of the puts. It needs that expiry's
whole strike range (bots/gex_snapshot.py fetches it without a strike limit), not the +/- 60 strikes of the 30-day chain.
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

YEAR_MIN = 365 * 24 * 60
COLS = ["put_call", "expiry", "dte", "strike", "open_interest", "volume", "iv", "gamma_schwab", "delta", "bid", "ask",
        "mark", "multiplier"]


def contracts(chain: dict) -> pd.DataFrame:
    """Flatten Schwab's callExpDateMap / putExpDateMap into one row per contract."""
    rows = []
    for side, key in (("CALL", "callExpDateMap"), ("PUT", "putExpDateMap")):
        for exp_key, strikes in (chain.get(key) or {}).items():
            for strike, items in strikes.items():
                for o in items:
                    exp = str(o.get("expirationDate") or exp_key.split(":")[0])[:10]
                    rows.append(dict(put_call=side, expiry=exp, dte=int(o.get("daysToExpiration", exp_key.split(":")[-1])),
                                     strike=float(o.get("strikePrice", strike)), open_interest=float(o.get("openInterest") or 0),
                                     volume=float(o.get("totalVolume") or 0), iv=float(o.get("volatility") or np.nan),
                                     gamma_schwab=float(o.get("gamma") or np.nan), delta=float(o.get("delta") or np.nan),
                                     bid=float(o.get("bid") or np.nan), ask=float(o.get("ask") or np.nan),
                                     mark=float(o.get("mark") or np.nan), multiplier=float(o.get("multiplier") or 100)))
    t = pd.DataFrame(rows, columns=COLS)
    num = [k for k in COLS if k not in ("put_call", "expiry")]
    t[num] = t[num].astype(float)                               # an empty response must not turn columns into objects
    t.loc[(t.iv <= 0) | (t.iv > 500), "iv"] = np.nan          # Schwab marks missing values with -999 / NaN
    t.loc[t.gamma_schwab < 0, "gamma_schwab"] = np.nan
    return t


def years_left(expiry: pd.Series, asof: datetime) -> np.ndarray:
    """Time to the 16:00 ET expiry in years; same-day contracts keep at least 15 minutes (gamma stays finite)."""
    exp = pd.to_datetime(expiry).dt.date
    mins = np.array([(datetime.combine(e, time(16), asof.tzinfo) - asof).total_seconds() / 60 for e in exp])
    return np.maximum(mins, 15.0) / YEAR_MIN


def bs_gamma(spot: np.ndarray, strike: np.ndarray, iv: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Black-Scholes gamma (zero rate and dividend: gamma barely depends on them over weeks)."""
    sig = iv / 100.0
    sd = sig * np.sqrt(t)
    d1 = (np.log(spot / strike) + 0.5 * sig ** 2 * t) / sd
    return np.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi) / (spot * sd)


def gex_at(c: pd.DataFrame, spot: float, t: np.ndarray) -> np.ndarray:
    sign = np.where(c.put_call.to_numpy() == "CALL", 1.0, -1.0)
    g = bs_gamma(np.full(len(c), spot), c.strike.to_numpy(), c.iv.to_numpy(), t)
    return sign * g * c.open_interest.to_numpy() * c.multiplier.to_numpy() * spot ** 2 * 0.01


def summary(c: pd.DataFrame, spot: float, asof: datetime, symbol: str) -> dict:
    num = [k for k in COLS if k not in ("put_call", "expiry")]
    c = c.astype({k: float for k in num if k in c})
    c = c[(c.open_interest > 0) & c.iv.notna() & (c.strike > 0)]
    # contracts that have expired by `asof` carry no gamma: earlier days, and today's once the 16:00 close has passed
    # (the chain still lists today's expiries after the close; counted at the 15-minute floor they swamped the totals)
    exp = pd.to_datetime(c.expiry).dt.date
    c = c[(exp > asof.date()) | ((exp == asof.date()) & (asof.time() < time(16)))].reset_index(drop=True)
    out = dict(asof=asof.isoformat(timespec="minutes"), symbol=symbol, spot=round(spot, 2), contracts=len(c))
    if c.empty:
        return out
    t = years_left(c.expiry, asof)
    g = gex_at(c, spot, t)
    by = pd.DataFrame(dict(strike=c.strike, side=c.put_call, gex=g)).groupby(["strike", "side"]).gex.sum().unstack(fill_value=0.0)
    calls, puts = by.get("CALL", pd.Series(dtype=float)), by.get("PUT", pd.Series(dtype=float))
    oi = c.groupby("strike").open_interest.sum()
    today = asof.date().isoformat()
    grid = spot * np.linspace(0.90, 1.10, 201)
    prof = np.array([gex_at(c, s, t).sum() for s in grid])
    cross = np.flatnonzero(np.diff(np.sign(prof)) != 0)
    flip = np.nan
    if len(cross):
        i = cross[np.argmin(np.abs(grid[cross] - spot))]
        flip = grid[i] - prof[i] * (grid[i + 1] - grid[i]) / (prof[i + 1] - prof[i])   # linear interpolation
    net = float(g.sum())
    out.update(net_gex=round(net), call_gex=round(float(g[g > 0].sum())), put_gex=round(float(g[g < 0].sum())),
               net_gex_0dte=round(float(g[(c.expiry == today).to_numpy()].sum())),
               regime="positive" if net > 0 else "negative",
               gamma_flip=round(float(flip), 2) if np.isfinite(flip) else None,
               spot_vs_flip_pct=round(100 * (spot / flip - 1), 2) if np.isfinite(flip) else None,
               call_wall=float(calls.idxmax()) if len(calls) and calls.max() > 0 else None,
               put_wall=float(puts.idxmin()) if len(puts) and puts.min() < 0 else None,
               max_oi_strike=float(oi.idxmax()), expiries=int(c.expiry.nunique()),
               call_volume=int(c[c.put_call == "CALL"].volume.sum()), put_volume=int(c[c.put_call == "PUT"].volume.sum()))
    return out


def drop_am_settled(chain: dict) -> dict:
    """Remove AM-settled contracts (Schwab's settlementType "A": the monthly SPX / NDX series, which stop trading the
    evening before and settle on the opening prices) so that an expiry day's 0DTE figures hold only the contracts that
    are alive during that session. Contracts without the field are kept."""
    for key in ("callExpDateMap", "putExpDateMap"):
        for strikes in (chain.get(key) or {}).values():
            for strike in list(strikes):
                strikes[strike] = [o for o in strikes[strike] if str(o.get("settlementType") or "").upper() != "A"]
                if not strikes[strike]:
                    del strikes[strike]
    return chain


def nearest_expiry(c: pd.DataFrame, asof: datetime) -> str | None:
    """The earliest expiry in `c` that is still alive at `asof` (today's counts until 16:00 ET)."""
    if c.empty:
        return None
    first = asof.date() if asof.time() < time(16) else asof.date() + timedelta(days=1)
    alive = sorted(e for e in c.expiry.unique() if pd.Timestamp(e).date() >= first)
    return alive[0] if alive else None


def zero_dte(c: pd.DataFrame, spot: float, asof: datetime, symbol: str, top: int = 5) -> dict:
    """Dealer gamma and open interest of the nearest live expiry only (same-day before the close).
      net_gex / regime      as in summary(), on this expiry alone: "positive" or "negative"
      gamma_flip            where this expiry's net GEX crosses zero (nearest crossing to spot), if any
      call_wall / put_wall  strike with the largest call GEX / the largest (negative) put GEX
      max_call_oi_strike, max_put_oi_strike, max_call_oi, max_put_oi   the largest open-interest strike per side
      top_call_oi / top_put_oi   the `top` largest strikes per side as "strike:open interest" joined by "|"
    Open interest is the previous close's figure: positions opened today are not in it."""
    out = dict(asof=asof.isoformat(timespec="minutes"), symbol=symbol, spot=round(spot, 2))
    exp = nearest_expiry(c, asof)
    if exp is None:
        return {**out, "contracts": 0}
    num = [k for k in COLS if k not in ("put_call", "expiry")]
    c = c.astype({k: float for k in num if k in c})
    c = c[(c.expiry == exp) & (c.strike > 0)]
    listed = len(c)        # contracts Schwab lists for the expiry; early in the morning their open interest is still 0
    c = c[c.open_interest > 0].reset_index(drop=True)
    out.update(expiry=exp, dte=(pd.Timestamp(exp).date() - asof.date()).days, listed=listed, contracts=len(c))
    if c.empty:
        return out
    for side, name in (("CALL", "call"), ("PUT", "put")):
        oi = c[c.put_call == side].groupby("strike").open_interest.sum().sort_values(ascending=False, kind="stable")
        out[f"{name}_oi"] = int(oi.sum())
        out[f"max_{name}_oi_strike"] = float(oi.index[0]) if len(oi) else None
        out[f"max_{name}_oi"] = int(oi.iloc[0]) if len(oi) else None
        out[f"top_{name}_oi"] = "|".join(f"{k:g}:{int(v)}" for k, v in oi.head(top).items())
        out[f"{name}_volume"] = int(c[c.put_call == side].volume.sum())
    g_c = c[c.iv.notna()].reset_index(drop=True)          # gamma needs an implied volatility
    if g_c.empty:
        return out
    t = years_left(g_c.expiry, asof)
    g = gex_at(g_c, spot, t)
    by = pd.DataFrame(dict(strike=g_c.strike, side=g_c.put_call, gex=g)).groupby(["strike", "side"]).gex.sum().unstack(fill_value=0.0)
    calls, puts = by.get("CALL", pd.Series(dtype=float)), by.get("PUT", pd.Series(dtype=float))
    grid = spot * np.linspace(0.95, 1.05, 201)            # one expiry: its gamma lives close to spot
    prof = np.array([gex_at(g_c, s, t).sum() for s in grid])
    cross = np.flatnonzero(np.diff(np.sign(prof)) != 0)
    flip = np.nan
    if len(cross):
        i = cross[np.argmin(np.abs(grid[cross] - spot))]
        flip = grid[i] - prof[i] * (grid[i + 1] - grid[i]) / (prof[i + 1] - prof[i])
    net = float(g.sum())
    out.update(net_gex=round(net), call_gex=round(float(g[g > 0].sum())), put_gex=round(float(g[g < 0].sum())),
               regime="positive" if net > 0 else "negative",
               gamma_flip=round(float(flip), 2) if np.isfinite(flip) else None,
               spot_vs_flip_pct=round(100 * (spot / flip - 1), 2) if np.isfinite(flip) else None,
               call_wall=float(calls.idxmax()) if len(calls) and calls.max() > 0 else None,
               put_wall=float(puts.idxmin()) if len(puts) and puts.min() < 0 else None)
    return out


def spot_of(chain: dict) -> float | None:
    u = chain.get("underlying") or {}
    for v in (chain.get("underlyingPrice"), u.get("last"), u.get("mark"), u.get("close")):
        if v is not None and float(v) > 0:
            return float(v)
    return None


FUTURE = {"$SPX": "/ES", "$NDX": "/NQ"}  # the future each index's levels are read on


def cash_open(now: datetime) -> bool:
    """Regular index hours, 09:30-16:00 New York on a weekday (holidays are not known here)."""
    return now.weekday() < 5 and time(9, 30) <= now.time() < time(16)


def futures_levels(z: dict, bars: list, now: datetime, future: str) -> dict:
    """Index levels of a zero_dte() row moved onto the future: level + basis, basis = future - index at one moment.
    `bars` are the future's 1-minute bars, oldest first (anything with .t = bar start and .close).
      cash open    basis = latest future price - the chain's index price (both live); a bar older than 5 minutes is
                   refused
      cash closed  the chain's index price is the last cash close, so the basis is taken at that close: the future's
                   15:59 bar (it ends at 16:00) of the latest weekday that has one, minus the index price
    Returns {} when the bars do not allow it. The basis moves a point or two during the day and steps at each
    quarterly roll, so the moved levels are zones, not exact prices."""
    spot = z.get("spot")
    if not bars or not spot:
        return {}
    last = bars[-1]
    if cash_open(now):
        if (now - last.t).total_seconds() > 360:
            return {}
        ref, kind = last, "live"
    else:
        day = now.date() if now.weekday() < 5 and now.time() >= time(16) else now.date() - timedelta(days=1)
        at = {b.t.replace(tzinfo=None): b for b in bars if b.t.time() == time(15, 59)}
        ref = None
        for _ in range(6):
            ref = at.get(datetime.combine(day, time(15, 59))) if day.weekday() < 5 else None
            if ref:
                break
            day -= timedelta(days=1)
        if ref is None:
            return {}
        kind = "close"
    basis = ref.close - spot
    out = dict(fut_symbol=future, fut_last=round(last.close, 2), fut_last_at=last.t.isoformat(timespec="minutes"),
               fut_basis=round(basis, 2), fut_basis_kind=kind, fut_basis_at=ref.t.isoformat(timespec="minutes"))
    for k in ("gamma_flip", "call_wall", "put_wall"):
        if z.get(k) is not None and not pd.isna(z.get(k)):
            out["fut_" + k] = round(float(z[k]) + basis, 2)
    return out
