"""Daily dealer-gamma (GEX) snapshot from Schwab option chains: QQQ, NDX, SPY and SPX
(Schwab's chains endpoint does not serve /NQ or /ES futures options).

    python bots/gex_snapshot.py                      # all default symbols
    python bots/gex_snapshot.py --symbols QQQ

Read-only: the only Schwab call is the option chain (ReadOnlyClient allows nothing else). Run by bots/cloud_runner.py
at 09:20 and 16:20 ET on trading days. For each symbol it saves the raw chain (expirations up to --days ahead,
--strikes strikes around the money (Schwab counts both sides together), fetched a few expiry days and one contract type per request) and one summary row (net GEX, gamma flip, call/put walls: see
src/quant/live/gex.py). A symbol Schwab does not serve is logged and skipped.
Files: data/schwab/gex/<SYMBOL>/<YYYY-MM-DD_HHMM>.parquet and data/schwab/gex/gex_daily.csv.

0DTE (--zero-dte, default $SPX and $NDX): one more request pair per symbol for the nearest live expiry alone, with NO
strike limit, because the largest open-interest strikes of a same-day expiry often sit outside the +/- 60 strikes of the
30-day chain. One row per symbol goes to data/schwab/gex/gex_0dte.csv (net GEX, positive / negative, flip, walls, the
largest open-interest strikes of the calls and of the puts: quant.live.gex.zero_dte) and the raw rows to
data/schwab/gex/<SYMBOL>_0DTE/<YYYY-MM-DD_HHMM>.parquet. The 09:20 row is the one for the session: the 16:20 row
already describes the next session's expiry, on open interest that is one day old by then.

Local use (docs/LOCAL_GEX.md): the Schwab keys are read from the repository's .env, the token path defaults to
state/nqbot/schwab_token.json, and --zero-dte-only skips the 30-day chains for a run of a few seconds:

    python bots/gex_snapshot.py --zero-dte-only

Futures levels: each 0DTE row also carries the flip and the walls moved onto the future the index is traded through
($SPX -> /ES, $NDX -> /NQ): level + basis, from the future's 1-minute price history (quant.live.gex.futures_levels).
Price history is the only call added; if it fails the row is saved without the futures columns.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.live.gex import FUTURE, contracts, drop_am_settled, futures_levels, spot_of, summary, zero_dte  # noqa: E402
from quant.live.schwab_feed import ReadOnlyClient, history_bars, make_client  # noqa: E402

NY = ZoneInfo("America/New_York")
OUT = ROOT / "data" / "schwab" / "gex"
SYMBOLS = ["QQQ", "$NDX", "SPY", "$SPX"]  # /NQ futures options: the chains endpoint answers 400 (checked 2026-10-27)
ZERO_DTE = ["$SPX", "$NDX"]  # the nearest-expiry summary (gex_0dte.csv); both have an expiry every trading day
RETRY_S = 300  # --wait: seconds between tries while the API reports no open interest
ZERO_DTE_LOOKAHEAD = 6  # days searched for the nearest expiry (covers a long weekend)
CHUNK_DAYS = 3  # expiry days per request: keeps each response small (the container has 256 MB)


def folder(symbol: str) -> str:
    return symbol.replace("/", "").replace("$", "") + ("_FUT" if symbol.startswith("/") else "_IDX" if symbol.startswith("$") else "")


def snapshot(client: ReadOnlyClient, symbol: str, now: datetime, days: int, strikes: int, chunk_days: int = CHUNK_DAYS) -> dict:
    """Fetch the chain in small requests (calls and puts separately, a few expiry days at a time) and keep only the
    compact rows: a whole NDX chain in one response does not fit the container's memory."""
    from schwab.client import Client  # the enum only: the request still goes through the read-only client
    CALL, PUT = Client.Options.ContractType.CALL, Client.Options.ContractType.PUT
    parts, spot, a, end = [], None, now.date(), (now + timedelta(days=days)).date()
    while a <= end:
        b = min(a + timedelta(days=chunk_days - 1), end)
        for ct in (CALL, PUT):
            r = client.get_option_chain(symbol, contract_type=ct, include_underlying_quote=True, strike_count=strikes,
                                        from_date=a, to_date=b)
            r.raise_for_status()
            chain = r.json()
            del r
            if chain.get("status") == "FAILED":
                raise ValueError(f"Schwab returned no chain for {symbol}")
            spot = spot or spot_of(chain)
            part = contracts(chain)
            if len(part):
                parts.append(part)
            del chain
            time.sleep(0.3)  # stay well inside Schwab's request rate limit
        a = b + timedelta(days=1)
    c = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if c.empty or spot is None:
        raise ValueError(f"empty chain or no underlying price for {symbol} ({len(c)} contracts)")
    d = OUT / folder(symbol)
    d.mkdir(parents=True, exist_ok=True)
    c.assign(spot=spot, asof=now.isoformat(timespec="minutes")).to_parquet(d / f"{now:%Y-%m-%d_%H%M}.parquet")
    return summary(c, spot, now, symbol)


def snapshot_0dte(client: ReadOnlyClient, symbol: str, now: datetime, lookahead: int = ZERO_DTE_LOOKAHEAD) -> dict:
    """One summary row (zero_dte) from fetch_0dte."""
    c, spot = fetch_0dte(client, symbol, now, lookahead)
    return zero_dte(c, spot, now, symbol)


def fetch_0dte(client: ReadOnlyClient, symbol: str, now: datetime, lookahead: int = ZERO_DTE_LOOKAHEAD,
               out: Path | None = None, pause: float = 0.3) -> tuple[pd.DataFrame, float]:
    """The nearest live expiry of `symbol` with every strike Schwab lists (no strike_count), calls and puts in separate
    requests, one expiry day per request. Before 16:00 ET the search starts today (the 0DTE), afterwards tomorrow.
    Returns the contract rows and the underlying price, and saves the rows under `out` (default <SYMBOL>_0DTE/)."""
    from schwab.client import Client
    CALL, PUT = Client.Options.ContractType.CALL, Client.Options.ContractType.PUT
    day = now.date() if (now.hour, now.minute) < (16, 0) else now.date() + timedelta(days=1)
    last = None
    for _ in range(lookahead + 1):
        parts, spot = [], None
        try:
            for ct in (CALL, PUT):
                r = client.get_option_chain(symbol, contract_type=ct, include_underlying_quote=True, from_date=day,
                                            to_date=day)
                r.raise_for_status()
                chain = r.json()
                del r
                spot = spot or spot_of(chain)
                part = contracts(drop_am_settled(chain))   # a day without an expiry answers with empty maps (or FAILED)
                if len(part):
                    parts.append(part)
                del chain
                time.sleep(pause)
        except Exception as exc:  # a day Schwab refuses is skipped; the error is reported if no day works
            last = exc
        if parts and spot is not None:
            c = pd.concat(parts, ignore_index=True)
            d = out or OUT / (folder(symbol) + "_0DTE")
            d.mkdir(parents=True, exist_ok=True)
            c.assign(spot=spot, asof=now.isoformat(timespec="minutes")).to_parquet(d / f"{now:%Y-%m-%d_%H%M}.parquet")
            return c, spot
        day += timedelta(days=1)
    raise ValueError(f"no expiry found for {symbol} within {lookahead} days (last error: {last!r})")


def add_futures(client: ReadOnlyClient, z: dict, now: datetime) -> dict:
    """The row with its levels on the future (see futures_levels). Never raises: the reason goes to `fut_error`."""
    fut = FUTURE.get(z.get("symbol"))
    if not fut or z.get("error") or not z.get("contracts"):
        return z
    try:
        lv = futures_levels(z, history_bars(client, fut, now - timedelta(days=5), now), now, fut)
        return {**z, **(lv or dict(fut_symbol=fut, fut_error="no usable bar for the basis"))}
    except Exception as exc:
        return {**z, "fut_symbol": fut, "fut_error": repr(exc)[:200]}


def append_csv(p: Path, log: pd.DataFrame) -> None:
    old = pd.read_csv(p) if p.exists() else None
    pd.concat([old, log], ignore_index=True).to_csv(p, index=False) if old is not None else log.to_csv(p, index=False)


def load_env(root: Path = ROOT, env=os.environ) -> None:
    """Local runs: read the repository's .env (values already set in the environment win) and default the token path
    to the one bots/schwab_login.py writes. In the cloud container there is no .env and the runner sets the path."""
    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except ImportError:  # the container image installs only what the bots need
        pass
    env.setdefault("SCHWAB_TOKEN_PATH", str(root / "state" / "nqbot" / "schwab_token.json"))


def oi_missing(zrows: list[dict]) -> bool:
    """True when Schwab listed contracts for a symbol but reported open interest 0 on every one of them."""
    return any(not z.get("error") and z.get("listed") and not z.get("contracts") for z in zrows)


def describe(z: dict) -> str:
    """One 0DTE row as a few readable lines for the terminal."""
    if not z.get("error") and not z.get("contracts") and z.get("listed"):
        return (f"{z['symbol']}: Schwab lists {z['listed']} contracts for {z['expiry']} but reports open interest 0 on all of "
                "them. On 2026-11-10 that was the case until about 06:30 New York time and the same request worked at "
                "06:34. Run again in a few minutes, or use --wait to keep trying.")
    if z.get("error") or not z.get("contracts"):
        return f"{z['symbol']}: no 0DTE data ({z.get('error') or 'no live expiry with open interest'})"

    def top(side: str) -> str:
        return ", ".join(f"{k} ({int(v):,})" for k, v in (x.split(":") for x in str(z.get(f"top_{side}_oi") or "").split("|") if x))
    net = z.get("net_gex")
    lines = [f"{z['symbol']}  expiry {z['expiry']} (in {z['dte']} days)  spot {z['spot']:,.2f}  at {z['asof']}",
             f"  regime            {str(z.get('regime', 'n/a')).upper()}" + (f"   net GEX {net / 1e9:+.2f}bn per 1% move" if net is not None else ""),
             f"  gamma flip        {z.get('gamma_flip')}   call wall {z.get('call_wall')}   put wall {z.get('put_wall')}",
             f"  largest call OI   {top('call')}",
             f"  largest put OI    {top('put')}",
             f"  total OI          calls {z.get('call_oi', 0):,}   puts {z.get('put_oi', 0):,}"]
    if z.get("fut_basis") is not None:
        when = "live, index and future now" if z["fut_basis_kind"] == "live" else f"at the cash close, {z['fut_basis_at'][:10]} 16:00"
        lv = "   ".join(f"{n} {z[k]:,.2f}" for n, k in (("flip", "fut_gamma_flip"), ("call wall", "fut_call_wall"),
                                                         ("put wall", "fut_put_wall")) if z.get(k) is not None)
        lines += [f"  on {z['fut_symbol']:<14} last {z['fut_last']:,.2f}   basis {z['fut_basis']:+,.2f} ({when})", f"    levels          {lv}"]
        if z.get("fut_gamma_flip") is not None:
            d = z["fut_last"] - z["fut_gamma_flip"]
            lines.append(f"    last vs flip    {d:+,.2f} ({'above' if d >= 0 else 'below'})")
    elif z.get("fut_error"):
        lines.append(f"  on {z['fut_symbol']:<14} not available ({z['fut_error']})")
    return "\n".join(lines)


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=SYMBOLS)
    ap.add_argument("--days", type=int, default=30, help="expirations up to this many days ahead")
    ap.add_argument("--strikes", type=int, default=120, help="strikes around the money, both sides together (~60 each side)")
    ap.add_argument("--zero-dte", nargs="*", default=ZERO_DTE, help="symbols for the nearest-expiry summary (none: skip)")
    ap.add_argument("--wait", type=float, default=0, metavar="MINUTES",
                    help="0DTE rows: while Schwab's API reports open interest 0, try again every 5 minutes for this long")
    ap.add_argument("--zero-dte-only", action="store_true", help="skip the 30-day chains; only the 0DTE rows")
    a = ap.parse_args()
    missing = [k for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET", "SCHWAB_TOKEN_PATH") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)} (put them in .env: see docs/LOCAL_GEX.md)")
    if not Path(os.environ["SCHWAB_TOKEN_PATH"]).exists():
        sys.exit(f"no Schwab token at {os.environ['SCHWAB_TOKEN_PATH']}: run python bots/schwab_login.py first")
    client = make_client(os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"], os.environ["SCHWAB_TOKEN_PATH"],
                         extra=ReadOnlyClient.MARKET_DATA)
    now = datetime.now(NY)
    rows = []
    for sym in ([] if a.zero_dte_only else a.symbols):
        try:
            rows.append(snapshot(client, sym, now, a.days, a.strikes))
        except Exception as exc:  # one symbol failing (e.g. not served by the chains endpoint) must not stop the others
            rows.append(dict(asof=now.isoformat(timespec="minutes"), symbol=sym, error=repr(exc)[:300]))
        time.sleep(0.5)
    log = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    if rows:
        append_csv(OUT / "gex_daily.csv", log)
        print(log.to_string(index=False))
    deadline = time.monotonic() + 60 * a.wait
    while True:
        zrows = []
        for sym in a.zero_dte:
            try:
                zrows.append(add_futures(client, snapshot_0dte(client, sym, now), now))
            except Exception as exc:  # the 0DTE rows are an extra: a failure here never fails the snapshot job
                zrows.append(dict(asof=now.isoformat(timespec="minutes"), symbol=sym, error=repr(exc)[:300]))
            time.sleep(0.5)
        if not oi_missing(zrows) or time.monotonic() >= deadline:
            break
        print(f"{datetime.now(NY):%H:%M} New York: Schwab's API still reports open interest 0; trying again in "
              f"{RETRY_S // 60} minutes (Ctrl-C to stop)", flush=True)
        time.sleep(RETRY_S)
        now = datetime.now(NY)
    if zrows:
        z = pd.DataFrame(zrows)
        append_csv(OUT / "gex_0dte.csv", z)
        print("\n" + "\n\n".join(describe(r) for r in zrows))
        print(f"\nSaved to {OUT / 'gex_0dte.csv'}")
    judged = rows or zrows   # the 30-day chains decide the exit code; the 0DTE rows only when they are all that ran
    if judged and all(r.get("error") for r in judged):
        sys.exit(1)


if __name__ == "__main__":
    main()
