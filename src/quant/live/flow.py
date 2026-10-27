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
