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
"""
from __future__ import annotations

import math
from datetime import datetime, time

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


def spot_of(chain: dict) -> float | None:
    u = chain.get("underlying") or {}
    for v in (chain.get("underlyingPrice"), u.get("last"), u.get("mark"), u.get("close")):
        if v is not None and float(v) > 0:
            return float(v)
    return None
