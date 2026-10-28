"""Intraday option flow from the 5-minute chain captures (bots/option_flow_capture.py). Offline analysis, no Schwab calls.

Schwab serves no option time & sales, so flow is reconstructed from consecutive snapshots of each contract:
  traded            contracts traded in the interval = change in cumulative volume
  side (proxies)    who initiated is unknown; two proxies, both reported, neither trusted until validated:
                      tick: the contract's last trade at the capture against that capture's bid/ask
                            (+1 at/above the ask = bought, -1 at/below the bid = sold, 0 in between or no new trade)
                      iv:   the sign of the implied-volatility change while it traded (buying lifts IV, selling drops it)
  delta_usd         traded x delta x multiplier x spot: the stock-equivalent dollars the contracts carry (calls +, puts -),
                    i.e. what the dealer on the other side must hedge; signed by a proxy it is the customers' net delta
  dealer_hedge_usd  the hedging that dealer gamma alone forces over the interval: -net GEX (per 1%) x the move in %
                    (positive gamma: dealers sell rallies and buy dips; negative gamma: they chase)
Open interest is the previous close's figure and is constant within a day. oi_change() compares the next morning's
open interest with the day's volume per contract: the opening ratio (dOI / volume) says how much of the day's
trading opened new positions (near 1) rather than closing old ones (near -1).
The hypotheses these feed, and how they are tested, are in docs/GEX_FRAMEWORK.md.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant.live.gex import summary

NUM = ["spot", "under_bid", "under_ask", "under_volume", "dte", "strike", "bid", "ask", "last", "mark", "bid_size", "ask_size",
       "last_size", "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega", "quote_ms", "trade_ms", "multiplier"]


def folder(symbol: str) -> str:
    return symbol.replace("$", "") + ("_IDX" if symbol.startswith("$") else "")


def load_day(root: Path, symbol: str, day: str) -> pd.DataFrame:
    """Every capture of one symbol and day, one row per contract and capture, sorted by time."""
    files = sorted((root / folder(symbol) / day).glob("*.csv.gz"))
    if not files:
        return pd.DataFrame()
    t = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    t[NUM] = t[NUM].apply(pd.to_numeric, errors="coerce")
    t["asof"] = pd.to_datetime(t["asof"], utc=True).dt.tz_convert("America/New_York")
    t.loc[(t.iv <= 0) | (t.iv > 500), "iv"] = np.nan   # Schwab's -999 = missing
    return t.sort_values(["asof", "occ"]).reset_index(drop=True)


def contract_flow(t: pd.DataFrame) -> pd.DataFrame:
    """Per contract and interval: contracts traded, the two side proxies, delta dollars and premium."""
    t = t.sort_values(["occ", "asof"]).copy()
    g = t.groupby("occ", sort=False)
    t["traded"] = g.volume.diff().fillna(t.volume)        # the first capture of the day carries everything since the open
    t.loc[t.traded < 0, "traded"] = np.nan                 # a volume that goes down is a data error
    prev_trade = g.trade_ms.shift()
    new_trade = t.trade_ms.notna() & (prev_trade.isna() | (t.trade_ms > prev_trade))
    t["side_tick"] = np.where(new_trade & (t["last"] >= t.ask) & t.ask.gt(0), 1,
                              np.where(new_trade & (t["last"] <= t.bid) & t.bid.gt(0), -1, 0))
    t["side_iv"] = np.sign(g.iv.diff()).fillna(0).where(t.traded > 0, 0)
    t["delta_usd"] = t.traded * t.delta * t.multiplier * t.spot
    t["premium_usd"] = t.traded * t.mark * t.multiplier
    return t.sort_values(["asof", "occ"]).reset_index(drop=True)


def gex_at_capture(t: pd.DataFrame) -> pd.Series:
    """Net GEX (OI-based, $ per 1% move) at each capture: summary() of src/quant/live/gex.py on that capture's rows."""
    out = {}
    for ts, c in t.groupby("asof"):
        c = c.rename(columns={"gamma": "gamma_schwab"})
        s = summary(c, float(c.spot.iloc[0]), ts.to_pydatetime(), str(c.symbol.iloc[0]))
        out[ts] = s.get("net_gex", np.nan)
    return pd.Series(out, name="net_gex")


def intervals(t: pd.DataFrame) -> pd.DataFrame:
    """One row per capture interval: volumes, delta dollars (unsigned and signed by each proxy), the 0DTE share,
    net GEX at the start of the interval and the hedging it forces over the interval's move."""
    f = contract_flow(t)
    f["is_call"] = f.put_call == "CALL"
    f["dte0"] = f.dte == 0
    k = f.groupby("asof")
    out = pd.DataFrame(dict(
        spot=k.spot.first(),
        call_traded=k.apply(lambda x: x.traded[x.is_call].sum(), include_groups=False),
        put_traded=k.apply(lambda x: x.traded[~x.is_call].sum(), include_groups=False),
        dte0_share=k.apply(lambda x: x.traded[x.dte0].sum() / max(x.traded.sum(), 1), include_groups=False),
        delta_usd_abs=k.delta_usd.apply(lambda x: x.abs().sum()),
        delta_usd_tick=k.apply(lambda x: (x.delta_usd * x.side_tick).sum(), include_groups=False),
        delta_usd_iv=k.apply(lambda x: (x.delta_usd * x.side_iv).sum(), include_groups=False),
        premium_usd=k.premium_usd.sum()))
    out["ret_pct"] = 100 * out.spot.pct_change()
    gex = gex_at_capture(t)
    out["net_gex"] = gex
    out["dealer_hedge_usd"] = -gex.shift() * out.ret_pct   # gamma at the start of the interval x the interval's move
    return out


MAX_GAP = 10       # minutes: volume over a longer gap between captures is not signed (see dealer_gamma_0dte)
AM_ROOTS = ("SPX2", "NDX2")  # AM-settled monthlies (root SPX / NDX, no W / P): expired at the open, no gamma intraday


def dealer_gamma_0dte(t: pd.DataFrame) -> pd.DataFrame:
    """Dealers' same-day-expiry (0DTE) gamma at each capture, in $ of the underlying per 1% move, from one symbol-day
    of captures (load_day). Three estimates, reported side by side because none is known to be right yet:
      g_oi    open interest at the open with the naive sign (dealers long calls, short puts: calls +, puts -)
      g_tick  -(customers' net contracts bought since the open) x gamma, customers' side from the tick proxy:
              a customer buying any option (call or put) leaves the dealer short its gamma
      g_iv    the same with the implied-volatility proxy
      g_total g_oi + g_tick (the primary estimate of docs/GEX_FRAMEWORK.md section Z)
    Gamma is Black-Scholes at each capture's IV and time to 16:00 (quant.live.gex). Also per capture: the 0DTE strike
    with the most open-interest-plus-traded gamma (pin), 0DTE contracts traded so far, the share of that volume the
    proxies could sign (flow_signed) and the customers' net buying as a share of it (net_buy_tick), and the spot.
    Volume is signed only over intervals of at most MAX_GAP minutes: a first capture after a gap (a late start, a
    restart) carries the whole backlog since the open, which one last trade cannot sign; it adds no flow. Captures from
    16:00 on are dropped (same-day expiries are dead)."""
    from quant.live.gex import bs_gamma
    f = contract_flow(t[t.dte == 0])
    mod = f["asof"].dt.hour * 60 + f["asof"].dt.minute
    f = f[~f.occ.str.startswith(AM_ROOTS) & f.iv.notna() & (f.strike > 0) & (mod < 960)].copy()
    if f.empty:
        return pd.DataFrame()
    f = f.sort_values(["occ", "asof"])
    g = f.groupby("occ", sort=False)
    gap = (f["asof"] - g["asof"].shift()).dt.total_seconds() / 60
    first_ok = (f["asof"].dt.hour * 60 + f["asof"].dt.minute) <= 570 + MAX_GAP     # a first capture right after the open
    signable = np.where(gap.isna(), first_ok, gap <= MAX_GAP)
    vol = f.traded.fillna(0).where(signable, 0.0)
    f["signed_vol"] = vol
    f["cum_tick"] = (vol * f.side_tick).groupby(f.occ).cumsum()
    f["cum_iv"] = (vol * f.side_iv).groupby(f.occ).cumsum()
    f["cum_vol"] = vol.groupby(f.occ).cumsum()
    f["cum_hit"] = (vol * (f.side_tick != 0)).groupby(f.occ).cumsum()
    f["oi0"] = g.open_interest.transform("first")
    f["gam"] = bs_gamma(f.spot.to_numpy(), f.strike.to_numpy(), f.iv.to_numpy(), _years(f))
    scale = f.gam * f.multiplier * f.spot ** 2 * 0.01
    sign = np.where(f.put_call == "CALL", 1.0, -1.0)
    f["g_oi"], f["g_tick"], f["g_iv"] = sign * f.oi0 * scale, -f.cum_tick * scale, -f.cum_iv * scale
    f["g_abs"] = (f.oi0 + f.volume.fillna(0)) * scale
    k = f.groupby("asof")
    out = pd.DataFrame(dict(spot=k.spot.first(), g_oi=k.g_oi.sum(), g_tick=k.g_tick.sum(), g_iv=k.g_iv.sum(),
                            traded=k.volume.sum(), cum_vol=k.cum_vol.sum(),
                            flow_signed=k.cum_hit.sum() / k.cum_vol.sum().replace(0, np.nan),
                            net_buy_tick=k.cum_tick.sum() / k.cum_vol.sum().replace(0, np.nan),
                            pin=k.apply(lambda x: x.groupby("strike").g_abs.sum().idxmax(), include_groups=False)))
    out["g_total"] = out.g_oi + out.g_tick
    return out


def _years(f: pd.DataFrame) -> np.ndarray:
    """Years from each row's capture time to 16:00 ET that day (at least 15 minutes), for same-day expiries."""
    a = f["asof"]
    mins = (a.dt.normalize() + pd.Timedelta(hours=16) - a).dt.total_seconds().to_numpy() / 60
    return np.maximum(mins, 15.0) / (365 * 24 * 60)


def oi_change(today: pd.DataFrame, next_day: pd.DataFrame) -> pd.DataFrame:
    """Per contract: today's volume and the change in open interest published the next morning (opening ratio)."""
    a = today.sort_values("asof").groupby("occ").agg(put_call=("put_call", "first"), expiry=("expiry", "first"),
                                                   strike=("strike", "first"), oi=("open_interest", "first"),
                                                   volume=("volume", "last"))
    b = next_day.sort_values("asof").groupby("occ").open_interest.first().rename("oi_next")
    m = a.join(b, how="inner")
    m["d_oi"] = m.oi_next - m.oi
    m["opening_ratio"] = (m.d_oi / m.volume).where(m.volume > 0)
    return m
