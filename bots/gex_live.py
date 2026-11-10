"""Live ES dealer map: fetch the SPX 0DTE chain every minute and rewrite one self-refreshing HTML page.

    python bots/gex_live.py                 # every 60 seconds until 16:15 New York or Ctrl-C
    python bots/gex_live.py --every 30 --open

Read-only, like bots/gex_snapshot.py: the only Schwab calls are the option chain and the future's price history
(ReadOnlyClient.MARKET_DATA). Three requests per cycle: calls, puts, and the last minutes of /ES.

Each cycle:
  1. the nearest-expiry $SPX chain with every strike (bots/gex_snapshot.fetch_0dte), saved under
     data/schwab/gex/SPX_IDX_0DTE/live/<date>/
  2. the latest /ES price; the index price used for the Greeks is that price minus the basis, so the map follows
     the future also while the cash index is closed
  3. data/schwab/gex/live/es_map.html is rewritten (quant.live.gex_map): levels on /ES, gamma / vanna / charm per
     strike, and what changed against the previous snapshot, five minutes ago and the start of the run
  4. one row is appended to data/schwab/gex/gex_live.csv

The basis (future - index) is fixed when the run starts: outside cash hours it is taken at the last cash close (the
future's 15:59 bar minus the chain's index price, which is that close); inside cash hours from the two live prices at
the start. Start it before the open when possible. Open the page in a browser: it reloads itself.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bots"))

import gex_snapshot as snap  # noqa: E402
from quant.live.gex import FUTURE, futures_levels, strike_table, zero_dte  # noqa: E402
from quant.live.gex_map import Session, write  # noqa: E402
from quant.live.schwab_feed import ReadOnlyClient, history_bars, make_client  # noqa: E402

NY = snap.NY
MIN_EVERY = 20  # seconds: three requests per cycle stay far below the request limit even at this pace


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


def cycle(client: ReadOnlyClient, ses: Session, symbol: str, now: datetime, state: dict, out: Path) -> dict:
    """One fetch and one page. `state` carries the basis and the last known future price between cycles."""
    t0 = time.monotonic()
    c, index_quote = snap.fetch_0dte(client, symbol, now, out=out / (snap.folder(symbol) + "_0DTE") / "live" / f"{now:%Y-%m-%d}", pause=0.1)
    if "basis" not in state:
        state["basis"], state["note"] = start_basis(client, ses.future, index_quote, now)
    px = last_price(client, ses.future, now)
    if px is not None:
        state["fut"], state["fut_at"] = px
    if "fut" not in state:
        raise ValueError(f"no {ses.future} price")
    fetch_s = time.monotonic() - t0
    spot = state["fut"] - state["basis"]
    z = zero_dte(c, spot, now, symbol)
    data = ses.add(now, z, strike_table(c, spot, now), state["fut"], state["basis"], state["note"], index_quote, fetch_s)
    write(out / "live" / "es_map.html", data)
    m = data["M"]
    b = state["basis"]
    row = dict(asof=m["asof"], symbol=symbol, future=ses.future, fut_last=m["futLast"], basis=b, spot_used=m["spot"],
               index_quote=m["indexQuote"], regime=m["regime"], net_gex=m["net"],
               fut_gamma_flip=None if m["flip"] is None else round(m["flip"] + b, 2),
               fut_call_wall=None if m["callWall"] is None else round(m["callWall"] + b, 2),
               fut_put_wall=None if m["putWall"] is None else round(m["putWall"] + b, 2),
               call_volume=m["callVol"], put_volume=m["putVol"], fetch_s=m["fetchS"])
    snap.append_csv(out / "gex_live.csv", pd.DataFrame([row]))
    return data


def line(data: dict) -> str:
    m, top = data["M"], (data["movers"][0] if data["movers"] else None)
    lv = "  ".join(f"{n} {m[k] + m['basis']:,.2f}" for n, k in (("flip", "flip"), ("call wall", "callWall"), ("put wall", "putWall")) if m[k] is not None)
    net = "n/a" if m["net"] is None else f"{m['net'] / 1e9:+.2f}bn"
    act = f"  most active {top['k'] + m['basis']:,.0f} ({top['dc']} calls, {top['dp']} puts)" if top else ""
    return f"{m['clock']}  {m['future']} {m['futLast']:,.2f}  {str(m['regime']).upper()} {net}  {lv}{act}  [{m['fetchS']}s]"


def main() -> None:
    snap.load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="$SPX", choices=sorted(FUTURE))
    ap.add_argument("--every", type=int, default=60, help=f"seconds between snapshots (at least {MIN_EVERY})")
    ap.add_argument("--until", default="16:15", help="New York time to stop at (HH:MM)")
    ap.add_argument("--open", action="store_true", help="open the page in the browser after the first snapshot")
    a = ap.parse_args()
    every = max(a.every, MIN_EVERY)
    missing = [k for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET", "SCHWAB_TOKEN_PATH") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)} (put them in .env: see docs/LOCAL_GEX.md)")
    if not Path(os.environ["SCHWAB_TOKEN_PATH"]).exists():
        sys.exit(f"no Schwab token at {os.environ['SCHWAB_TOKEN_PATH']}: run python bots/schwab_login.py first")
    client = make_client(os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"], os.environ["SCHWAB_TOKEN_PATH"],
                         extra=ReadOnlyClient.MARKET_DATA)
    ses, state, page = Session(FUTURE[a.symbol], every), {}, snap.OUT / "live" / "es_map.html"
    hh, mm = (int(x) for x in a.until.split(":"))
    print(f"Live map: {page}\nOpen it in a browser (it reloads itself). Ctrl-C stops.\n", flush=True)
    opened = False
    try:
        while True:
            now = datetime.now(NY)
            if (now.hour, now.minute) >= (hh, mm):
                print(f"{a.until} New York reached: stopping.")
                break
            started = time.monotonic()
            try:
                print(line(cycle(client, ses, a.symbol, now, state, snap.OUT)), flush=True)
                if a.open and not opened:
                    webbrowser.open(page.as_uri())
                    opened = True
            except Exception as exc:  # one bad cycle (network, an empty answer) must not end the run
                print(f"{now:%H:%M:%S}  snapshot failed: {repr(exc)[:200]}", flush=True)
            time.sleep(max(1.0, every - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
