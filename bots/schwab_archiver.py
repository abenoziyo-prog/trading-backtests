"""Archive Schwab 1-minute OHLCV for /NQ, QQQ, /ES, SPY and the VIX family ($VIX, $VIX3M, $VIX9D, $VVIX) so the history keeps growing past Schwab's ~6-week window.

    python bots/schwab_archiver.py                    # update data/schwab/*_1min.parquet (first run backfills)
    python bots/schwab_archiver.py --symbols /NQ QQQ ES=... --days 60

Read-only: the only Schwab call is price history (ReadOnlyClient allows nothing else). Schwab keeps about
6 weeks of 1-minute bars (checked 2026-10-21: earliest 2026-09-10), so running this at least every few weeks
loses nothing; bots/cloud_runner.py runs it daily at 18:30 ET. Each run fetches from the last saved
bar (or --days back on the first run) in chunks under Schwab's 40,000-bar per-request cap, keeps only
completed minutes, merges, de-duplicates and writes atomically.
Files: data/schwab/<NQ|QQQ|ES|SPY|VIX|VIX3M|VIX9D|VVIX>_1min.parquet (index: bar start, America/New_York;
columns open, high, low, close, volume); a run log in data/schwab/archive_log.csv.
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

from quant.live.schwab_feed import ReadOnlyClient, make_client  # noqa: E402

NY = ZoneInfo("America/New_York")
OUT = ROOT / "data" / "schwab"
CHUNK_DAYS = 20  # /NQ trades ~1,380 minutes a day: 20 days stays under the 40,000-bar cap


def file_for(symbol: str) -> Path:
    return OUT / f"{symbol.strip('/').replace('/', '_').replace('$', '')}_1min.parquet"


def fetch(client: ReadOnlyClient, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    frames, a = [], start
    while a < end:
        b = min(a + timedelta(days=CHUNK_DAYS), end)
        r = client.get_price_history_every_minute(symbol, start_datetime=a, end_datetime=b, need_extended_hours_data=True)
        r.raise_for_status()
        c = r.json().get("candles", [])
        if c:
            frames.append(pd.DataFrame(c))
        a = b
        time.sleep(0.5)  # stay well inside Schwab's request rate limit
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.concat(frames, ignore_index=True)
    df.index = pd.to_datetime(df.pop("datetime"), unit="ms", utc=True).dt.tz_convert(NY)
    df.index.name = "time"
    return df[["open", "high", "low", "close", "volume"]].astype({"volume": "int64"})


def update(client: ReadOnlyClient, symbol: str, days: int, now: datetime) -> dict:
    path = file_for(symbol)
    old = pd.read_parquet(path) if path.exists() else None
    start = (old.index.max() - timedelta(hours=1)) if old is not None and len(old) else now - timedelta(days=days)
    new = fetch(client, symbol, start.to_pydatetime() if hasattr(start, "to_pydatetime") else start, now)
    new = new[new.index < pd.Timestamp(now).floor("min") - pd.Timedelta(minutes=1)]  # completed minutes only
    df = new if old is None else pd.concat([old, new])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp)
    tmp.replace(path)
    added = len(df) - (0 if old is None else len(old))
    gap = (new.index.min() - old.index.max()) if old is not None and len(old) and len(new) else pd.Timedelta(0)
    return dict(run=now.isoformat(timespec="seconds"), symbol=symbol, file=path.name, bars=len(df), added=added,
                first=df.index.min().isoformat() if len(df) else "", last=df.index.max().isoformat() if len(df) else "",
                days=df.index.normalize().nunique(), gap_warning=bool(gap > pd.Timedelta(days=4)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=["/NQ", "QQQ", "/ES", "SPY", "$VIX", "$VIX3M", "$VIX9D", "$VVIX"])
    ap.add_argument("--days", type=int, default=60, help="look-back on the first run (Schwab keeps ~6 weeks)")
    a = ap.parse_args()
    missing = [k for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET", "SCHWAB_TOKEN_PATH") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)} (see deploy/nq_paper_bot.env.example)")
    client = make_client(os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"], os.environ["SCHWAB_TOKEN_PATH"],
                         extra=ReadOnlyClient.PRICE_HISTORY)
    now = datetime.now(NY)
    rows = []
    for sym in a.symbols:
        try:
            rows.append(update(client, sym, a.days, now))
        except Exception as exc:  # one symbol failing must not stop the other
            rows.append(dict(run=now.isoformat(timespec="seconds"), symbol=sym, error=repr(exc)))
    log = pd.DataFrame(rows)
    p = OUT / "archive_log.csv"
    log.to_csv(p, mode="a", header=not p.exists(), index=False)
    print(log.to_string(index=False))
    if "error" in log and log.error.notna().any():
        sys.exit(1)


if __name__ == "__main__":
    main()
