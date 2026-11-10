"""Live ES dealer map: fetch the SPX 0DTE chain as fast as Schwab's request limit allows and update the page.

    python bots/gex_live.py --open                    # ES from SPX, about every 1.1 seconds until 16:15 New York
    python bots/gex_live.py --symbols '$SPX' SPY      # a second map from SPY: about every 1.6 seconds
    python bots/gex_live.py --open --stream           # /ES price from the quote stream: a snapshot about every 0.55 seconds
    python bots/gex_live.py --every 10                # slower

Read-only, like bots/gex_snapshot.py: the only Schwab calls are the option chain and the future's price history
(ReadOnlyClient.MARKET_DATA) and, with --stream, a quote subscription for the future (LEVELONE_FUTURES).

Pace. A cycle is one request for the /ES price and one per symbol (calls and puts together): two for SPX. Schwab's
limit is reported as 120 requests a minute (REQUEST_LIMIT); the loop spends --rate of them (default 110), which makes
a cycle every 2 x 60 / 110 = 1.1 seconds. A cycle cannot be shorter than Schwab takes to answer, about a second. If
Schwab does not answer the combined request with both sides, the loop asks for calls and puts separately from then on
(three requests, 1.6 seconds). If Schwab answers 429 (too many requests) the loop slows down by half and says so.

Requests do not wait for one another: a new one goes out on schedule while earlier answers are still on their way (at
most MAX_FLIGHT at a time), so a slow answer does not delay the next snapshot. An answer that arrives after a newer one
is dropped. While the access token is within two minutes of its renewal, requests go one at a time.

--stream takes the /ES price from Schwab's quote stream instead of asking for it: the price is then the last trade at
the moment a chain arrives, and a snapshot costs one request, so the same budget gives one every 0.55 seconds. While
the stream is not connected the loop asks for the price as before. Schwab may allow one stream per login: do not use
--stream while another program streams with the same Schwab login (the cloud /NQ bot does).

Each cycle:
  1. the latest /ES price (the newest 1-minute candle, including the minute still forming)
  2. the nearest-expiry chains with every strike; one snapshot a minute is kept under
     data/schwab/gex/<SYMBOL>_0DTE/live/<date>/
  3. the Greeks at the price implied by /ES (index: /ES minus the basis; SPY: /ES divided by the price ratio), then
     data/schwab/gex/live/es_map.html (es_spy_map.html) plus its data file (quant.live.gex_map): levels on /ES,
     gamma / vanna / charm per strike, and what changed against the previous snapshot, five minutes ago and the start
  4. one row per symbol is appended to data/schwab/gex/gex_live.csv

The basis (future - index) is fixed when the run starts: outside cash hours it is taken at the last cash close (the
future's 15:59 bar minus the chain's index price, which is that close); inside cash hours from the two live prices at
the start. Start it before the open when possible. SPY has no basis: its strikes are moved onto /ES by the price ratio
/ES / SPY, taken once at the start from SPY's newest 1-minute bar and the future's bar of the same minute.

data/schwab/gex/live/live_map.html shows the map (with SPY, the two side by side). Open it once: it updates in place.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
import time
import webbrowser
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bots"))

import gex_snapshot as snap  # noqa: E402
from quant.live.gex import futures_levels, strike_table, zero_dte  # noqa: E402
from quant.live.gex_map import Session, page_name, write, write_frame  # noqa: E402
from quant.live.schwab_feed import LivePrice, ReadOnlyClient, history_bars, make_client, run_price_stream  # noqa: E402

NY = snap.NY
REQUEST_LIMIT = 120  # requests a minute, as reported for Schwab's market-data API (schwabdev documentation, not Schwab's own)
DEFAULT_RATE = 110   # what the loop spends of it
SOURCES = {"$SPX": "/ES", "SPY": "/ES"}  # option chain -> the future its levels are read on
ETFS = ("SPY",)
MAX_FLIGHT = 4       # snapshots whose answers may be awaited at the same time
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
    T = Client.Options.ContractType
    got = []
    if not state.get("split"):   # calls and puts in one request; if Schwab does not answer that with both sides, two
        try:
            got = [snap.fetch_side(client, symbol, state["day"], T.ALL)]
            if got[0][0].put_call.nunique() < 2:
                raise ValueError("one side missing")
        except Exception as exc:
            if "429" in repr(exc):
                raise
            state["split"], got = True, []
            print(f"{now:%H:%M:%S}  {symbol}: the combined calls-and-puts request failed ({repr(exc)[:120]}); using two requests from now on", flush=True)
    if not got:
        got = list((pool.map if pool else map)(lambda ct: snap.fetch_side(client, symbol, state["day"], ct), (T.CALL, T.PUT)))
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


def token_fresh(client: ReadOnlyClient, margin_s: float = 120.0) -> bool:
    """True when the access token is known to stay valid for `margin_s` more seconds. Only then are the requests of a
    cycle sent at the same time: a renewal must happen in one request alone, or two would write the token file at once.
    Anything unknown counts as not fresh."""
    try:
        return float(client.token_metadata.token["expires_at"]) - time.time() > margin_s
    except Exception:
        return False


def interval(requests: int, rate: float, every: float = 0.0) -> float:
    """Seconds between cycles: the fastest the request budget allows for `requests` per cycle, or `every` when that
    is slower."""
    return max(float(every), requests * 60.0 / min(max(rate, 1.0), REQUEST_LIMIT))


def requests_per_cycle(states: list[dict]) -> int:
    """One for the future's price, one per chain (two for a chain that has to be asked for as calls and puts)."""
    return 1 + sum(2 if st.get("split") else 1 for st in states)


def grab(client: ReadOnlyClient, runs: dict, future: str, now: datetime, out: Path, pool, live: LivePrice | None) -> dict:
    """The network part of one snapshot, run in a worker thread: the future's price (from the stream when it is
    connected, else one request) and every symbol's chain. Returns dict(now, px, chains, errors, fetch_s)."""
    t0, errors, px = time.monotonic(), [], None
    p = live.get() if live else None
    px_job = None
    if p is not None:
        px = (p, now)
    elif token_fresh(client):
        px_job = pool.submit(last_price, client, future, now)
    else:   # a token close to its renewal: alone and first, so the renewal happens in this one request
        try:
            px = last_price(client, future, now)
        except Exception as exc:
            errors.append(f"{future} price: {repr(exc)[:160]}")
    jobs = {sym: pool.submit(fetch, client, sym, now, state, out) for sym, (_, state) in runs.items()}
    if px_job is not None:
        try:
            px = px_job.result()
        except Exception as exc:
            errors.append(f"{future} price: {repr(exc)[:160]}")
    chains = {}
    for sym, job in jobs.items():
        try:
            chains[sym] = job.result()
        except Exception as exc:  # one bad answer must not end the run or the other symbol
            errors.append(f"{sym} chain: {repr(exc)[:160]}")
    return dict(now=now, px=px, chains=chains, errors=errors, fetch_s=time.monotonic() - t0, polled=p is None)


def stream_status(on: bool, live: LivePrice) -> str:
    """What the terminal says about the price stream: connected, still connecting, or why the last try failed."""
    if on:
        return "from the stream"
    if live.error:
        return f"one request per snapshot. The stream failed and will be tried again: {live.error}"
    return "one request per snapshot while the stream connects"


def main() -> None:
    snap.load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=["$SPX"], choices=sorted(SOURCES), help="shown left to right")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE, help=f"requests a minute to spend (at most {REQUEST_LIMIT})")
    ap.add_argument("--every", type=float, default=0.0, help="seconds between snapshots; 0 (default) = as fast as --rate allows")
    ap.add_argument("--stream", action="store_true",
                    help="take the future's price from Schwab's quote stream instead of one request per snapshot")
    ap.add_argument("--until", default="16:15", help="New York time to stop at (HH:MM)")
    ap.add_argument("--open", action="store_true", help="open the page in the browser after the first snapshot")
    ap.add_argument("--verbose", action="store_true", help="print every cycle (default: a status line every 15 seconds)")
    a = ap.parse_args()
    for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET"):
        if not os.environ.get(k):
            sys.exit(f"{k} is not set. Add it to {ROOT / '.env'}")
    symbols = list(dict.fromkeys(a.symbols))
    client = make_client(os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"], os.environ["SCHWAB_TOKEN_PATH"],
                         extra=ReadOnlyClient.MARKET_DATA)
    future = SOURCES[symbols[0]]
    live, stop = (LivePrice() if a.stream else None), threading.Event()
    if live is not None:
        threading.Thread(target=run_price_stream, args=(client, future, live, stop), daemon=True).start()
    runs = {sym: (Session(SOURCES[sym], 1.0, index=sym.strip("$")), {}) for sym in symbols}

    def states() -> list[dict]:
        return [st for _, st in runs.values()]

    def pace(brake: float, streamed: bool) -> float:   # the stream saves the price request of every snapshot
        return min(60.0, brake * interval(requests_per_cycle(states()) - (1 if streamed else 0), a.rate, a.every))

    every = pace(1.0, False)
    page = snap.OUT / "live" / "live_map.html"
    write_frame(page, [(SOURCES[sym], sym.strip("$")) for sym in symbols])
    hh, mm = (int(x) for x in a.until.split(":"))
    print(f"Live maps: {page}\nA snapshot every {every:.1f} seconds"
          + (f", {interval(len(symbols), a.rate, a.every):.2f} once the {future} price stream is connected" if live else "")
          + ". Open the page once, it updates in place. Ctrl-C stops.\n", flush=True)
    opened, printed, brake, done_at, streaming, stream_err, n_done = False, 0.0, 1.0, None, None, None, 0
    pending: list = []
    next_at = time.monotonic()
    try:
        with ThreadPoolExecutor(MAX_FLIGHT) as outer, ThreadPoolExecutor(MAX_FLIGHT * (len(symbols) + 1)) as inner:
            while True:
                now = datetime.now(NY)
                if (now.hour, now.minute) >= (hh, mm):
                    print(f"{a.until} New York reached: stopping.")
                    break
                # a new request goes out on schedule without waiting for the previous answer, but only once the run is
                # set up and while the token is far from its renewal; otherwise one at a time
                ready = all("basis" in st for st in states())
                room = len(pending) < (MAX_FLIGHT if ready and token_fresh(client) else 1)
                if room and time.monotonic() >= next_at:
                    pending.append(outer.submit(grab, client, runs, future, now, snap.OUT, inner, live))
                    next_at = max(next_at + every, time.monotonic())   # late once does not mean a burst afterwards
                pause = max(0.0, min(next_at - time.monotonic(), 0.25))
                if pending:   # until the next request is due; with no room left, until an answer arrives
                    done = wait(pending, timeout=pause if room else 0.25, return_when=FIRST_COMPLETED)[0]
                else:
                    done = set()
                    time.sleep(pause)
                results = []
                for job in done:
                    pending.remove(job)
                    try:
                        results.append(job.result())
                    except Exception as exc:
                        print(f"{now:%H:%M:%S}  failed: {repr(exc)[:200]}", flush=True)
                for r in sorted(results, key=lambda r: r["now"]):
                    errors, lines = r["errors"], []
                    if done_at is not None and r["now"] <= done_at:   # an older answer that arrived after a newer one
                        continue
                    done_at = r["now"]
                    p = live.get() if live else None
                    px = (p, datetime.now(NY)) if p is not None else r["px"]
                    for sym, chain in r["chains"].items():
                        ses, state = runs[sym]
                        ses.every_s = every
                        try:
                            lines.append(line(cycle(client, ses, sym, r["now"], state, snap.OUT, chain, px, r["fetch_s"])))
                        except Exception as exc:
                            errors.append(f"{sym}: {repr(exc)[:160]}")
                    n_done += 1 if lines else 0
                    mono = time.monotonic()
                    if lines and (a.verbose or mono - printed >= PRINT_EVERY):
                        print("\n".join(lines) + f"   answer {r['fetch_s']:.2f}s, {n_done} snapshots", flush=True)
                        printed = mono
                    for e in errors:
                        print(f"{r['now']:%H:%M:%S}  failed: {e}", flush=True)
                    if any("429" in e for e in errors):   # too many requests: slow down by half and pause
                        brake = min(brake * 1.5, 20.0)
                        print(f"{r['now']:%H:%M:%S}  Schwab answered 429 (too many requests): slowing down to one snapshot "
                              f"every {pace(brake, bool(p)):.1f} seconds", flush=True)
                        next_at = time.monotonic() + 10
                    if a.open and not opened and lines:
                        webbrowser.open(page.as_uri())
                        opened = True
                on = bool(live and live.get() is not None)
                if live is not None and (on != streaming or (not on and live.error != stream_err)):
                    stream_err = live.error
                    print(f"{datetime.now(NY):%H:%M:%S}  {future} price: " + stream_status(on, live), flush=True)
                    streaming = on
                every = pace(brake, on)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
