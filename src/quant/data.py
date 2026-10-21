"""Daily OHLCV loader backed by a local parquet cache.

Symbols follow Yahoo Finance conventions: equities/ETFs as-is (SPY, AAPL),
indices with a caret (^GSPC = S&P 500, ^NDX = Nasdaq-100, ^DJI, ^RUT, ^VIX).
Prices are split- and dividend-adjusted, so returns include dividends.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
HISTORY_START = "1990-01-01"


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.upper().replace('^', 'IDX_')}.parquet"


def load_prices(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return adjusted daily bars with columns open, high, low, close, volume.

    The full history is downloaded once and cached; pass refresh=True to
    pull the latest bars.
    """
    path = _cache_path(symbol)
    if path.exists() and not refresh:
        df = pd.read_parquet(path)
    else:
        df = yf.download(symbol, start=HISTORY_START, auto_adjust=True, progress=False)
        if df.empty:
            raise ValueError(f"No data returned for {symbol!r}")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "date"
        df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
    return df.loc[start:end]


def load_many(symbols: list[str], **kwargs) -> pd.DataFrame:
    """Close prices for several symbols, aligned on common dates."""
    closes = {s: load_prices(s, **kwargs)["close"] for s in symbols}
    return pd.DataFrame(closes).dropna()
