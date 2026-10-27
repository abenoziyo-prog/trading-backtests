"""Daily dealer-gamma (GEX) snapshot from Schwab option chains: QQQ, NDX, SPY and SPX
(Schwab's chains endpoint does not serve /NQ or /ES futures options).

    python bots/gex_snapshot.py                      # all default symbols
    python bots/gex_snapshot.py --symbols QQQ

Read-only: the only Schwab call is the option chain (ReadOnlyClient allows nothing else). Run by bots/cloud_runner.py
at 09:20 and 16:20 ET on trading days. For each symbol it saves the raw chain (expirations up to --days ahead,
--strikes strikes each side of the money, fetched a few expiry days and one contract type per request) and one summary row (net GEX, gamma flip, call/put walls: see
src/quant/live/gex.py). A symbol Schwab does not serve is logged and skipped.
Files: data/schwab/gex/<SYMBOL>/<YYYY-MM-DD_HHMM>.parquet and data/schwab/gex/gex_daily.csv.
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

from quant.live.gex import contracts, spot_of, summary  # noqa: E402
from quant.live.schwab_feed import ReadOnlyClient, make_client  # noqa: E402

NY = ZoneInfo("America/New_York")
OUT = ROOT / "data" / "schwab" / "gex"
SYMBOLS = ["QQQ", "$NDX", "SPY", "$SPX"]  # /NQ futures options: the chains endpoint answers 400 (checked 2026-10-27)
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=SYMBOLS)
    ap.add_argument("--days", type=int, default=30, help="expirations up to this many days ahead")
    ap.add_argument("--strikes", type=int, default=60, help="strikes on each side of the money")
    a = ap.parse_args()
    missing = [k for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET", "SCHWAB_TOKEN_PATH") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)} (see deploy/nq_paper_bot.env.example)")
    client = make_client(os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"], os.environ["SCHWAB_TOKEN_PATH"],
                         extra=ReadOnlyClient.OPTION_CHAIN)
    now = datetime.now(NY)
    rows = []
    for sym in a.symbols:
        try:
            rows.append(snapshot(client, sym, now, a.days, a.strikes))
        except Exception as exc:  # one symbol failing (e.g. not served by the chains endpoint) must not stop the others
            rows.append(dict(asof=now.isoformat(timespec="minutes"), symbol=sym, error=repr(exc)[:300]))
        time.sleep(0.5)
    log = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "gex_daily.csv"
    old = pd.read_csv(p) if p.exists() else None
    pd.concat([old, log], ignore_index=True).to_csv(p, index=False) if old is not None else log.to_csv(p, index=False)
    print(log.to_string(index=False))
    if "error" in log and log.error.notna().all():
        sys.exit(1)


if __name__ == "__main__":
    main()
