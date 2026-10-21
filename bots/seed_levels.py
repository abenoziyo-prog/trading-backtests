"""Write state/nqbot/seed_levels.json for a day the bot did not record the previous session of.

    python bots/seed_levels.py                 # today (New York date)
    python bots/seed_levels.py --date 2026-10-22

Levels (same definitions as the bot and the backtests): PDH/PDL/PDC = previous trading day's 09:30-16:00 ET
high/low/last close; london_high = today's 02:00-05:00 ET high (if >= 80% of minutes exist).
Source: Schwab price history for the bot's symbol (read-only market data); if Schwab returns no futures
history, Yahoo Finance NQ=F 1-minute bars (same front-month contract). Data only: no order code.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.live.calendar import Calendar  # noqa: E402

NY = ZoneInfo("America/New_York")


def schwab_bars(symbol: str, start: datetime, end: datetime) -> list[tuple[datetime, float, float, float, float]]:
    from schwab.auth import client_from_token_file

    c = client_from_token_file(os.environ["SCHWAB_TOKEN_PATH"], os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"])
    r = c.get_price_history_every_minute(symbol, start_datetime=start, end_datetime=end, need_extended_hours_data=True)
    r.raise_for_status()
    return [(datetime.fromtimestamp(x["datetime"] / 1000, NY), x["open"], x["high"], x["low"], x["close"])
            for x in r.json().get("candles", [])]


def yahoo_bars(start: datetime, end: datetime) -> list[tuple[datetime, float, float, float, float]]:
    import yfinance as yf

    df = yf.download("NQ=F", start=start.date(), end=(end + timedelta(days=1)).date(), interval="1m", progress=False,
                     auto_adjust=False, prepost=True)
    if df.empty:
        return []
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    df.index = df.index.tz_convert(NY)
    return [(t.to_pydatetime(), float(r.Open), float(r.High), float(r.Low), float(r.Close)) for t, r in df.iterrows()]


def levels(bars, prev: date, today: date) -> dict:
    mod = lambda t: t.hour * 60 + t.minute
    rth = sorted(b for b in bars if b[0].date() == prev and 570 <= mod(b[0]) < 960)
    lon = [b for b in bars if b[0].date() == today and 120 <= mod(b[0]) < 300]
    if len(rth) < 200:
        raise SystemExit(f"only {len(rth)} bars for the {prev} US session")
    return dict(pdh=max(b[2] for b in rth), pdl=min(b[3] for b in rth), pdc=rth[-1][4], rth_bars=len(rth),
                london_high=max(b[2] for b in lon) if len(lon) >= 144 else None, london_bars=len(lon),
                first=rth[0][0].strftime("%H:%M"), last=rth[-1][0].strftime("%H:%M"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(NY).date().isoformat())
    ap.add_argument("--source", choices=["auto", "schwab", "yahoo"], default="auto")
    a = ap.parse_args()
    today = date.fromisoformat(a.date)
    prev = Calendar().previous_trading_day(today)
    start, end = datetime.combine(prev, datetime.min.time(), NY), datetime.combine(today, datetime.min.time(), NY) + timedelta(hours=6)
    symbol = os.environ.get("NQBOT_SYMBOL", "/NQ")
    bars, src = [], ""
    if a.source in ("auto", "schwab"):
        try:
            bars, src = schwab_bars(symbol, start, end), f"Schwab {symbol}"
            print(f"Schwab returned {len(bars)} bars")
        except Exception as exc:
            print(f"Schwab price history failed: {exc!r}")
    if len(bars) < 200 and a.source in ("auto", "yahoo"):
        bars, src = yahoo_bars(start, end), "Yahoo NQ=F"
        print(f"Yahoo returned {len(bars)} bars")
    lv = levels(bars, prev, today)
    state = Path(os.environ.get("NQBOT_STATE_DIR", ROOT / "state" / "nqbot"))
    seed = dict(date=today.isoformat(), pdh=lv["pdh"], pdl=lv["pdl"], pdc=lv["pdc"], london_high=lv["london_high"],
                source=f"{src}: {prev} 09:30-16:00 ET ({lv['rth_bars']} bars, {lv['first']}-{lv['last']}), "
                       f"London {lv['london_bars']}/180 bars")
    (state / "seed_levels.json").write_text(json.dumps(seed, indent=2))
    print(json.dumps(seed, indent=2))


if __name__ == "__main__":
    main()
