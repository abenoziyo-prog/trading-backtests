"""Live ES dealer maps: fetch the SPX and SPY 0DTE chains as fast as Schwab's request limit allows and update the pages.

    python bots/gex_live.py --open          # ES from SPX and from SPY, about every 2.7 seconds until 16:15 New York
    python bots/gex_live.py --symbols '$SPX'   # SPX alone: about every 1.6 seconds
    python bots/gex_live.py --every 10      # slower

Read-only, like bots/gex_snapshot.py: the only Schwab calls are the option chain and the future's price history
(ReadOnlyClient.MARKET_DATA).

Pace. A cycle is one request for the /ES price and two per symbol (calls, puts): five for SPX and SPY. Schwab's limit is
reported as 120 requests a minute (REQUEST_LIMIT); the loop spends --rate of them (default 110), which makes a cycle
every 5 x 60 / 110 = 2.7 seconds. The chain requests of a cycle run at the same time, so a cycle lasts about as long as
its slowest request. If Schwab answers 429 (too many requests) the loop slows down by half and says so.

Each cycle:
  1. the latest /ES price (the newest 1-minute candle, including the minute still forming)
  2. the nearest-expiry chains with every strike; one snapshot a minute is kept under
     data/schwab/gex/<SYMBOL>_0DTE/live/<date>/
  3. the Greeks at the price implied by /ES (index: /ES minus the basis; SPY: /ES divided by the price ratio), then
     data/schwab/gex/live/es_map.html and es_spy_map.html plus their data files (quant.live.gex_map): levels on /ES,
     gamma / vanna / charm per strike, and what changed against the previous snapshot, five minutes ago and the start
  4. one row per symbol is appended to data/schwab/gex/gex_live.csv

The basis (future - index) is fixed when the run starts: outside cash hours it is taken at the last cash close (the
future's 15:59 bar minus the chain's index price, which is that close); inside cash hours from the two live prices at
the start. Start it before the open when possible. SPY has no basis: its strikes are moved onto /ES by the price ratio
/ES / SPY, taken once at the start from SPY's newest 1-minute bar and the future's bar of the same minute.

data/schwab/gex/live/live_map.html shows the two maps side by side. Open it once: the pages update in place.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bots"))

import gex_snapshot as snap  # noqa: E402
from quant.live.gex import futures_levels, strike_table, zero_dte  # noqa: E402
from quant.live.gex_map import Session, page_name, write, write_frame  # noqa: E402
from quant.live.schwab_feed import ReadOnlyClient, history_bars, make_client  # noqa: E402

NY = snap.NY
REQUEST_LIMIT = 120  # requests a minute, as reported for Schwab's market-data API (schwabdev documentation, not Schwab's own)
DEFAULT_RATE = 110   # what the loop spends of it
SOURCES = {"$SPX": "/ES", "SPY": "/ES"}  # option chain -> the future its levels are read on
ETFS = ("SPY",)
PRINT_EVERY = 15     # seconds between status lines in the terminal (--verbose prints every cycle)


def last_price(client: ReadOnlyClient, future: str, now: datetime) -> tuple[float, datetime] | None:
    """The newest 1-minute candle of the future, including the minute still forming (its close is the last trade)."""
    r = client.get_price_history_every_minute(future, start_datetime=now - timedelta(minutes=20), end_datetime=now,
                                              need_extended_hours_data=True)
    r.raise_for_status()
    candles = r.json().get("candles", [])
    if not candles:
        return None
    c = max(candles, key=lambda x: x["datetime"])
    return float(c["close"]), datetime.fromtimestamp(c["datetime"] / 1000, NY)


def start_basis(client: ReadOnlyClient, future: str, index_price: float, now: datetime) -> tuple[float, str]:
    """The basis for the run and a short description of where it comes from (see the module text)."""
    lv = futures_levels(dict(spot=index_price), history_bars(client, future, now - timedelta(days=5), now), now, future)
    if not lv:
        raise ValueError(f"no usable {future} bar for the basis")
    note = (f"live at the start, {now:%H:%M} New York" if lv["fut_basis_kind"] == "live"
            else f"at the cash close, {lv['fut_basis_at'][:10]} 16:00")
    return float(lv["fut_basis"]), note


def start_ratio(client: ReadOnlyClient, future: str, etf: str, now: datetime) -> tuple[float, str]:
    """future / ETF price ratio for the run, from the ETF's newest 1-minute bar and the future's bar of that minute."""
    e = last_price(client, etf, now)
    if e is None:
        e_bars = history_bars(client, etf, now - timedelta(days=5), now)
        if not e_bars:
            raise ValueError(f"no {etf} price for the ratio")
        e = (e_bars[-1].close, e_bars[-1].t)
    price, at = e
    f_bars = [b for b in history_bars(client, future, at - timedelta(minutes=5), at + timedelta(minutes=2)) if b.t <= at]
    if not f_bars or price <= 0:
        raise ValueError(f"no {future} bar at {at:%Y-%m-%d %H:%M} for the ratio")
    return f_bars[-1].close / price, f"at {at:%H:%M} New York, {at:%Y-%m-%d}"


def fetch(client: ReadOnlyClient, symbol: str, now: datetime, state: dict, out: Path, pool=None) -> tuple[pd.DataFrame, float]:
    """The nearest-expiry chain of `symbol` and its underlying price. The first call finds the expiry day
    (bots/gex_snapshot.fetch_0dte); later ones ask for that day directly, calls and puts at the same time when a
    thread pool is given. One snapshot a minute is saved."""
    d = out / (snap.folder(symbol) + "_0DTE") / "live" / f"{now:%Y-%m-%d}"
    live_day = now.date() if (now.hour, now.minute) < (16, 0) else now.date() + timedelta(days=1)
    if state.get("day") is None or state["day"] < live_day:   # the start of the run, or today's expiry is over
        c, spot = snap.fetch_0dte(client, symbol, now, out=d, pause=0.0)
        state["day"], state["saved"] = date.fromisoformat(str(c.expiry.iloc[0])[:10]), f"{now:%H%M}"
        return c, spot
    from schwab.client import Client
    sides = (Client.Options.ContractType.CALL, Client.Options.ContractType.PUT)
    got = list((pool.map if pool else map)(lambda ct: snap.fetch_side(client, symbol, state["day"], ct), sides))
    parts, spot = [p for p, _ in got if len(p)], next((x for _, x in got if x is not None), None)
    if not parts or spot is None:
        raise ValueError(f"empty {symbol} chain for {state['day']}")
    c = pd.concat(parts, ignore_index=True)
    if state.get("saved") != f"{now:%H%M}":
        d.mkdir(parents=True, exist_ok=True)
        c.assign(spot=spot, asof=now.isoformat(timespec="seconds")).to_parquet(d / f"{now:%Y-%m-%d_%H%M}.parquet")
        state["saved"] = f"{now:%H%M}"
    return c, spot


def cycle(client: ReadOnlyClient, ses: Session, symbol: str, now: datetime, state: dict, out: Path, chain=None, px=None,
          fetch_s: float | None = None) -> dict:
    """One snapshot and one page. `state` carries the expiry day, the basis and the last known future price between
    cycles. `chain` (fetch()'s result) and `px` (last_price()'s) are fetched here when the caller did not."""
    t0 = time.monotonic()
    c, index_quote = chain or fetch(client, symbol, now, state, out)
    if "basis" not in state:
        if symbol in ETFS:
            state["basis"] = 0.0
            state["ratio"], state["note"] = start_ratio(client, ses.future, symbol, now)
        else:
            state["ratio"] = 1.0
            state["basis"], state["note"] = start_basis(client, ses.future, index_quote, now)
    px = px or last_price(client, ses.future, now)
    if px is not None:
        state["fut"], state["fut_at"] = px
    if "fut" not in state:
        raise ValueError(f"no {ses.future} price")
    if fetch_s is None:
        fetch_s = time.monotonic() - t0
    spot = (state["fut"] - state["basis"]) / state["ratio"]
    z = zero_dte(c, spot, now, symbol)
    data = ses.add(now, z, strike_table(c, spot, now), state["fut"], state["basis"], state["note"], index_quote, fetch_s,
                   state["ratio"])
    write(out / "live" / page_name(ses.future, ses.index), data)
    m = data["M"]
    a, b = state["ratio"], state["basis"]
    row = dict(asof=m["asof"], symbol=symbol, future=ses.future, fut_last=m["futLast"], basis=b, ratio=round(a, 5), spot_used=m["spot"],
               index_quote=m["indexQuote"], regime=m["regime"], net_gex=m["net"],
               fut_gamma_flip=None if m["flip"] is None else round(m["flip"] * a + b, 2),
               fut_call_wall=None if m["callWall"] is None else round(m["callWall"] * a + b, 2),
               fut_put_wall=None if m["putWall"] is None else round(m["putWall"] * a + b, 2),
               call_volume=m["callVol"], put_volume=m["putVol"], fetch_s=m["fetchS"])
    append_row(out / "gex_live.csv", row)
    return data


def append_row(path: Path, row: dict) -> None:
    """Add one line to the CSV without reading it back (the file grows by thousands of rows a session). A file whose
    columns differ, from an older version, is rewritten once with the union of the columns."""
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f), None)
        if header != list(row):
            snap.append_csv(path, pd.DataFrame([row]))
            return
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if f.tell() == 0:
            w.writerow(list(row))
        w.writerow(["" if v is None else v for v in row.values()])


def line(data: dict) -> str:
    m, top = data["M"], (data["movers"][0] if data["movers"] else None)
    on = lambda v: v * (m.get("ratio") or 1.0) + m["basis"]  # noqa: E731
    lv = "  ".join(f"{n} {on(m[k]):,.2f}" for n, k in (("flip", "flip"), ("call wall", "callWall"), ("put wall", "putWall")) if m[k] is not None)
    net = "n/a" if m["net"] is None else f"{m['net'] / 1e9:+.2f}bn"
    act = f"  most active {on(top['k']):,.0f} ({top['dc']} calls, {top['dp']} puts)" if top else ""
    return f"{m['clock']}  {m['index']:<4}{m['future']} {m['futLast']:,.2f}  {str(m['regime']).upper()} {net}  {lv}{act}  [{m['fetchS']}s]"


def interval(n_symbols: int, rate: float, every: float = 0.0) -> float:
    """Seconds between cycles: the fastest the request budget allows (2 requests per symbol and 1 for the future),
    or `every` when that is slower."""
    return max(float(every), (2 * n_symbols + 1) * 60.0 / min(max(rate, 1.0), REQUEST_LIMIT))


def main() -> None:
    snap.load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=["$SPX", "SPY"], choices=sorted(SOURCES), help="shown left to right")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE, help=f"requests a minute to spend (at most {REQUEST_LIMIT})")
    ap.add_argument("--every", type=float, default=0.0, help="seconds between snapshots; 0 (default) = as fast as --rate allows")
    ap.add_argument("--until", default="16:15", help="New York time to stop at (HH:MM)")
    ap.add_argument("--open", action="store_true", help="open the page in the browser after the first snapshot")
    ap.add_argument("--verbose", action="store_true", help="print every cycle (default: a status line every 15 seconds)")
    a = ap.parse_args()
    for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET"):
        if not os.environ.get(k):
            sys.exit(f"{k} is not set. Add it to {ROOT / '.env'}")
    symbols = list(dict.fromkeys(a.symbols))
    every = interval(len(symbols), a.rate, a.every)
    client = make_client(os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"], os.environ["SCHWAB_TOKEN_PATH"],
                         extra=ReadOnlyClient.MARKET_DATA)
    runs = {sym: (Session(SOURCES[sym], every, index=sym.strip("$")), {}) for sym in symbols}
    future = SOURCES[symbols[0]]
    page = snap.OUT / "live" / "live_map.html"
    write_frame(page, [(SOURCES[sym], sym.strip("$")) for sym in symbols])
    hh, mm = (int(x) for x in a.until.split(":"))
    print(f"Live maps: {page}\nA snapshot every {every:.1f} seconds ({(2 * len(symbols) + 1) * 60 / every:.0f} requests a minute). "
          f"Open the page once, it updates in place. Ctrl-C stops.\n", flush=True)
    opened, printed, slow = False, 0.0, 0
    try:
        with ThreadPoolExecutor(len(symbols)) as outer, ThreadPoolExecutor(2 * len(symbols)) as inner:
            while True:
                now = datetime.now(NY)
                if (now.hour, now.minute) >= (hh, mm):
                    print(f"{a.until} New York reached: stopping.")
                    break
                started, errors = time.monotonic(), []
                try:   # alone and first: a token renewal happens here, not in several requests at once
                    px = last_price(client, future, now)
                except Exception as exc:
                    px = None
                    errors.append(f"{future} price: {repr(exc)[:160]}")
                jobs = {sym: outer.submit(fetch, client, sym, now, state, snap.OUT, inner) for sym, (_, state) in runs.items()}
                chains = {}
                for sym, job in jobs.items():
                    try:
                        chains[sym] = job.result()
                    except Exception as exc:  # one bad answer must not end the run or the other symbol
                        errors.append(f"{sym} chain: {repr(exc)[:160]}")
                fetch_s = time.monotonic() - started
                lines = []
                for sym, chain in chains.items():
                    ses, state = runs[sym]
                    try:
                        lines.append(line(cycle(client, ses, sym, now, state, snap.OUT, chain, px, fetch_s)))
                    except Exception as exc:
                        errors.append(f"{sym}: {repr(exc)[:160]}")
                took = time.monotonic() - started
                if a.verbose or started - printed >= PRINT_EVERY:
                    print("\n".join(lines) + (f"   cycle {took:.2f}s" if lines else ""), flush=True)
                    printed = started
                for e in errors:
                    print(f"{now:%H:%M:%S}  failed: {e}", flush=True)
                if any("429" in e for e in errors):   # too many requests: slow down by half and wait before the next try
                    every = min(every * 1.5, 60.0)
                    for ses, _ in runs.values():
                        ses.every_s = every
                    print(f"{now:%H:%M:%S}  Schwab answered 429 (too many requests): now one snapshot every {every:.1f} seconds", flush=True)
                    time.sleep(10)
                if took > every:
                    slow += 1
                    if slow in (5, 50):
                        print(f"{now:%H:%M:%S}  cycles take {took:.1f}s, longer than the {every:.1f}s pace: Schwab's answers set the pace", flush=True)
                if a.open and not opened and lines:
                    webbrowser.open(page.as_uri())
                    opened = True
                time.sleep(max(0.0, every - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
