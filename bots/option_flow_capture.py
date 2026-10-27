"""Intraday option-chain capture: near-expiry chains for QQQ, SPY, NDX and SPX every 5 minutes through the US session.

    python bots/option_flow_capture.py --loop        # 09:30-16:15 ET, one capture per 5-minute mark, then exit
    python bots/option_flow_capture.py               # one capture now

Read-only: the only Schwab call is the option chain (ReadOnlyClient allows nothing else). Started by
bots/cloud_runner.py on trading days. Schwab has no option time & sales and no chain history, so these snapshots are
the raw material for intraday flow: per contract, cumulative volume (the change between captures = contracts traded),
the last trade (price, size, time) against the bid/ask, implied volatility, greeks and open interest. Open interest is
the exchanges' previous-close figure and does not change during the day; the next morning's change against today's
volume tells whether today's trades opened or closed positions. Analysis runs offline (src/quant/live/flow.py).

Kept light for the 256 MB container: no pandas, one small request per symbol and contract type, plain gzip CSV.
Files: data/schwab/flow/<SYMBOL>/<YYYY-MM-DD>/<HHMM>.csv.gz and data/schwab/flow/capture_log.csv.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import os
import sys
import time
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.live.schwab_feed import ReadOnlyClient, make_client  # noqa: E402

NY = ZoneInfo("America/New_York")
OUT = ROOT / "data" / "schwab" / "flow"
SYMBOLS = ["QQQ", "SPY", "$NDX", "$SPX"]
DAYS = 3        # expirations up to 3 calendar days ahead: 0DTE and the next ones carry the intraday flow
STRIKES = 30    # strikes each side of the money
EVERY_MIN = 5
START, END = dtime(9, 30), dtime(16, 15)  # SPX/NDX options trade until 16:15
FIELDS = ["asof", "symbol", "spot", "under_bid", "under_ask", "under_volume", "put_call", "occ", "expiry", "dte", "strike",
          "bid", "ask", "last", "mark", "bid_size", "ask_size", "last_size", "volume", "open_interest", "iv", "delta",
          "gamma", "theta", "vega", "quote_ms", "trade_ms", "multiplier"]
LOG_FIELDS = ["asof", "symbol", "contracts", "seconds", "error"]


def folder(symbol: str) -> str:
    return symbol.replace("$", "") + ("_IDX" if symbol.startswith("$") else "")


def rows_of(chain: dict, asof: str, symbol: str) -> list[list]:
    """One row per contract from a Schwab chain response (callExpDateMap / putExpDateMap)."""
    u = chain.get("underlying") or {}
    spot = chain.get("underlyingPrice") or u.get("last") or u.get("mark")
    head = [asof, symbol, spot, u.get("bid"), u.get("ask"), u.get("totalVolume")]
    out = []
    for side, key in (("CALL", "callExpDateMap"), ("PUT", "putExpDateMap")):
        for exp_key, strikes in (chain.get(key) or {}).items():
            for strike, items in strikes.items():
                for o in items:
                    out.append(head + [side, (o.get("symbol") or "").replace(" ", ""),
                                       str(o.get("expirationDate") or exp_key.split(":")[0])[:10],
                                       o.get("daysToExpiration", exp_key.split(":")[-1]), o.get("strikePrice", strike),
                                       o.get("bid"), o.get("ask"), o.get("last"), o.get("mark"), o.get("bidSize"),
                                       o.get("askSize"), o.get("lastSize"), o.get("totalVolume"), o.get("openInterest"),
                                       o.get("volatility"), o.get("delta"), o.get("gamma"), o.get("theta"), o.get("vega"),
                                       o.get("quoteTimeInLong"), o.get("tradeTimeInLong"), o.get("multiplier")])
    return out


def capture(client: ReadOnlyClient, symbol: str, now: datetime, days: int = DAYS, strikes: int = STRIKES) -> int:
    from schwab.client import Client  # the enum only: the request still goes through the read-only client
    asof, rows = now.isoformat(timespec="seconds"), []
    for ct in (Client.Options.ContractType.CALL, Client.Options.ContractType.PUT):
        r = client.get_option_chain(symbol, contract_type=ct, include_underlying_quote=True, strike_count=strikes,
                                    from_date=now.date(), to_date=(now + timedelta(days=days)).date())
        r.raise_for_status()
        chain = r.json()
        if chain.get("status") == "FAILED":
            raise ValueError(f"Schwab returned no chain for {symbol}")
        rows += rows_of(chain, asof, symbol)
        del chain, r
        time.sleep(0.3)
    if not rows:
        raise ValueError(f"empty chain for {symbol}")
    d = OUT / folder(symbol) / now.date().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(FIELDS)
    w.writerows(rows)
    tmp = d / f"{now:%H%M}.tmp"
    tmp.write_bytes(gzip.compress(buf.getvalue().encode(), compresslevel=9))
    tmp.replace(d / f"{now:%H%M}.csv.gz")
    return len(rows)


def capture_all(client: ReadOnlyClient, symbols: list[str], now: datetime) -> list[dict]:
    log = []
    for sym in symbols:
        t0 = time.time()
        try:
            n, err = capture(client, sym, now), ""
        except Exception as exc:  # one symbol failing must not stop the others
            n, err = 0, repr(exc)[:300]
        log.append(dict(asof=now.isoformat(timespec="seconds"), symbol=sym, contracts=n, seconds=round(time.time() - t0, 1), error=err))
    p = OUT / "capture_log.csv"
    OUT.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerows(log)
    return log


def next_mark(now: datetime, every: int = EVERY_MIN) -> datetime:
    """The next clock mark (xx:00, xx:05, ...) at or after `now`, never before the session start."""
    t = now.replace(second=0, microsecond=0)
    if t < now:
        t += timedelta(minutes=1)
    t += timedelta(minutes=(-t.minute) % every)
    return max(t, now.replace(hour=START.hour, minute=START.minute, second=0, microsecond=0))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=SYMBOLS)
    ap.add_argument("--loop", action="store_true", help="capture every 5 minutes until 16:15 ET, then exit")
    a = ap.parse_args()
    missing = [k for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET", "SCHWAB_TOKEN_PATH") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)} (see deploy/nq_paper_bot.env.example)")
    client = make_client(os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"], os.environ["SCHWAB_TOKEN_PATH"],
                         extra=ReadOnlyClient.OPTION_CHAIN)
    if not a.loop:
        for r in capture_all(client, a.symbols, datetime.now(NY)):
            print(r)
        return
    while True:
        t = next_mark(datetime.now(NY))
        if t.time() > END:
            return
        time.sleep(max(0.0, (t - datetime.now(NY)).total_seconds()))
        log = capture_all(client, a.symbols, t)
        print(" ".join(f"{r['symbol']}:{r['contracts'] or 'ERR'}" for r in log), t.strftime("%H:%M"), flush=True)


if __name__ == "__main__":
    main()
