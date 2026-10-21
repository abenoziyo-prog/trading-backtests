"""1-minute market data downloaders with request validation and a per-month cache.

Sources:
  * Alpaca    - US stocks/ETFs (QQQ, SPY, ...). Free with paper-account keys.
                Bars: open, high, low, close, volume, trade_count, vwap.
                Free plan: history from 2016; the consolidated SIP feed is
                allowed for anything older than 15 minutes.
  * Databento - CME futures (NQ, ES, ...) from GLBX.MDP3, billed per request
                against the sign-up credit. Bars (ohlcv-1m): open, high, low,
                close, volume - no trade count. Optional extras:
                  stats  -> daily settlement, open interest, cleared volume,
                            session open/high/low (cheap)
                  trades -> per-minute trade_count, buy/sell aggressor volume
                            (large; check the estimate)

Every download is validated first by a plan_*() call that clamps the window to
what the source accepts and estimates request counts (and cost for Databento).

Bars are indexed by bar-open time in America/New_York.
Cache layout: data/minute/<source>/<SYMBOL>/<YYYY-MM>.parquet; complete months
are downloaded once, a partial month is cached under its end time.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MINUTE_DIR = ROOT / "data" / "minute"
TZ = "America/New_York"
COLUMNS = ["open", "high", "low", "close", "volume"]

# Alpaca limits (docs.alpaca.markets: /v2/stocks/bars and market-data plans)
ALPACA_EARLIEST = pd.Timestamp("2016-01-01", tz="UTC")
ALPACA_SIP_DELAY = pd.Timedelta(minutes=15)  # free plan: no SIP inside the last 15 min
ALPACA_PAGE_LIMIT = 10_000  # max bars per request; alpaca-py pages through the rest
ALPACA_CALLS_PER_MIN = 200  # free plan historical rate limit
EXTENDED_MINUTES_PER_DAY = 16 * 60  # 04:00-20:00 New York
ALPACA_FIELDS = COLUMNS + ["trade_count", "vwap"]
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Databento
DATABENTO_DATASET = "GLBX.MDP3"  # CME Globex
SCHEMA_BARS, SCHEMA_STATS, SCHEMA_TRADES = "ohlcv-1m", "statistics", "trades"
_FUT_ROOT_RE = re.compile(r"^[A-Z0-9]{1,6}$")
# databento_dbn.StatType codes -> column name, and whether the value is a price or a quantity
STAT_COLUMNS = {
    1: ("opening_price", "price"),
    3: ("settlement_price", "price"),
    4: ("session_low", "price"),
    5: ("session_high", "price"),
    6: ("cleared_volume", "quantity"),
    9: ("open_interest", "quantity"),
}
TRADE_CHUNK = 2_000_000  # rows per chunk when aggregating trades


@dataclass
class RequestPlan:
    source: str
    symbol: str
    start: pd.Timestamp  # first bar requested (month-aligned for the cache)
    end: pd.Timestamp
    requests: int  # estimated HTTP requests
    bars: int  # estimated (Alpaca) or exact (Databento) bar count
    fields: list[str]
    notes: list[str] = field(default_factory=list)
    costs: dict[str, float] = field(default_factory=dict)  # Databento only, USD per schema

    @property
    def total_cost(self) -> float:
        return sum(self.costs.values())

    def describe(self) -> str:
        lines = [
            f"{self.source} {self.symbol}: {self.start:%Y-%m-%d %H:%M} -> {self.end:%Y-%m-%d %H:%M} UTC",
            f"  fields: {', '.join(self.fields)}",
            f"  ~{self.bars:,} bars in ~{self.requests:,} requests",
        ]
        for schema, cost in self.costs.items():
            lines.append(f"  cost {schema}: ${cost:,.2f}")
        if self.costs:
            lines.append(f"  total: ${self.total_cost:,.2f} (upper bound; cached months are not re-billed)")
        lines += [f"  - {n}" for n in self.notes]
        return "\n".join(lines)


# --------------------------------------------------------------------------- shared

def month_ranges(start: pd.Timestamp, end: pd.Timestamp) -> Iterator[tuple[pd.Timestamp, pd.Timestamp]]:
    """Split [start, end) into calendar-month chunks (UTC timestamps)."""
    start, end = _ts(start), _ts(end)
    cursor = start
    while cursor < end:
        next_month = cursor.normalize().replace(day=1) + pd.offsets.MonthBegin(1)
        chunk_end = min(next_month, end)
        yield cursor, chunk_end
        cursor = chunk_end


def rth_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep regular US equity session bars (09:30-15:59 New York, weekdays)."""
    idx = df.index.tz_convert(TZ) if df.index.tz is not None else df.index
    minutes = idx.hour * 60 + idx.minute
    mask = (minutes >= 9 * 60 + 30) & (minutes < 16 * 60) & (idx.dayofweek < 5)
    return df[mask]


def _month_floor(ts: pd.Timestamp) -> pd.Timestamp:
    return _ts(ts).normalize().replace(day=1)


def _cached_months(
    source: str,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    fetch_month: Callable[[pd.Timestamp, pd.Timestamp], pd.DataFrame],
    refresh: bool,
    log: Callable[[str], None],
    dedupe: bool = True,
) -> pd.DataFrame:
    """Load month files from cache, downloading missing ones.

    dedupe=False keeps rows that share a timestamp (event data such as statistics).
    """
    folder = MINUTE_DIR / source / symbol.upper().replace(".", "_")
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    # Whole months are downloaded so each past month is cached exactly once.
    for chunk_start, chunk_end in month_ranges(_month_floor(start), end):
        label = chunk_start.strftime("%Y-%m")
        complete = chunk_end == chunk_start + pd.offsets.MonthBegin(1)
        name = label if complete else f"{label}_to_{chunk_end:%Y%m%dT%H%M}"
        path = folder / f"{name}.parquet"
        if path.exists() and not refresh:
            frames.append(pd.read_parquet(path))
            continue
        log(f"  {source}/{label}: downloading...")
        df = fetch_month(chunk_start, chunk_end)
        for stale in folder.glob(f"{label}_to_*.parquet"):
            stale.unlink()
        df.to_parquet(path)
        frames.append(df)
        log(f"  {source}/{label}: {len(df):,} rows")
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).sort_index(kind="stable")
    if dedupe:
        out = out[~out.index.duplicated(keep="last")]
    return out[out.index >= _ts(start).tz_convert(TZ)]


# --------------------------------------------------------------------------- Alpaca

def plan_alpaca(
    symbol: str = "QQQ",
    start=None,
    end=None,
    months: int = 24,
    feed: str = "sip",
    now: pd.Timestamp | None = None,
) -> RequestPlan:
    """Validate an Alpaca 1-minute request and estimate its size. No network."""
    symbol = symbol.strip().upper()
    if symbol.startswith("^") or not _TICKER_RE.match(symbol):
        raise ValueError(f"{symbol!r} is not a US stock/ETF ticker (indices like ^NDX aren't served; use QQQ).")
    feed = feed.lower()
    if feed not in {"sip", "iex"}:
        raise ValueError("feed must be 'sip' or 'iex'")
    if months < 1:
        raise ValueError("months must be >= 1")

    notes = []
    now = _ts(now) if now is not None else pd.Timestamp.now(tz="UTC")
    # One extra minute so a bar that is still forming is never requested.
    latest = now - ALPACA_SIP_DELAY - pd.Timedelta(minutes=1) if feed == "sip" else now
    end_ts = _ts(end) if end is not None else latest
    if end_ts > latest:
        notes.append(f"end clamped to {latest:%Y-%m-%d %H:%M} UTC (free plan: no SIP data from the last 15 minutes)")
        end_ts = latest
    start_ts = _ts(start) if start is not None else end_ts - pd.DateOffset(months=months)
    if start_ts < ALPACA_EARLIEST:
        raise ValueError(f"Alpaca history starts {ALPACA_EARLIEST.date()}; got start {start_ts.date()}")
    if start_ts >= end_ts:
        raise ValueError(f"start {start_ts} is not before end {end_ts}")

    requests, bars = 0, 0
    for a, b in month_ranges(_month_floor(start_ts), end_ts):
        month_bars = len(pd.bdate_range(a.date(), (b - pd.Timedelta(seconds=1)).date())) * EXTENDED_MINUTES_PER_DAY
        bars += month_bars
        requests += max(1, math.ceil(month_bars / ALPACA_PAGE_LIMIT))
    minutes_needed = requests / ALPACA_CALLS_PER_MIN
    notes += [
        f"page size {ALPACA_PAGE_LIMIT:,} bars = API maximum; pagination via next_page_token",
        f"rate limit {ALPACA_CALLS_PER_MIN}/min -> ~{minutes_needed:.1f} min of quota; 429s are retried by the SDK",
        "adjustment=all (splits + dividends), timeframe=1Min, sort=asc, includes 04:00-20:00 extended hours",
        "bar count is an upper bound (assumes a bar every extended-hours minute, ignores holidays)",
    ]
    if feed == "iex":
        notes.append("IEX feed = one exchange (~2-3% of volume); volume and trade_count will be far below SIP")
    return RequestPlan(
        source="alpaca", symbol=symbol, start=_month_floor(start_ts), end=end_ts,
        requests=requests, bars=bars, fields=ALPACA_FIELDS, notes=notes,
    )


def load_alpaca_minutes(
    symbol: str = "QQQ",
    start=None,
    end=None,
    months: int = 24,
    feed: str = "sip",
    refresh: bool = False,
    log: Callable[[str], None] = print,
    log_plan: bool = True,
) -> pd.DataFrame:
    """1-minute bars with volume, trade_count and vwap. Needs ALPACA_API_KEY / ALPACA_SECRET_KEY."""
    from alpaca.common.exceptions import APIError
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    plan = plan_alpaca(symbol, start, end, months, feed)
    requested_start = _ts(start) if start is not None else plan.end - pd.DateOffset(months=months)
    if log_plan:
        log(plan.describe())
    client = StockHistoricalDataClient(_env("ALPACA_API_KEY"), _env("ALPACA_SECRET_KEY"))
    state = {"feed": DataFeed(feed.lower())}

    def request(a: pd.Timestamp, b: pd.Timestamp):
        req = StockBarsRequest(
            symbol_or_symbols=plan.symbol,
            timeframe=TimeFrame.Minute,
            start=a.to_pydatetime(),
            # Alpaca's end is inclusive; stop one second before the next chunk.
            end=(b - pd.Timedelta(seconds=1)).to_pydatetime(),
            feed=state["feed"],
            adjustment=Adjustment.ALL,
        )
        return client.get_stock_bars(req).df

    def fetch(a: pd.Timestamp, b: pd.Timestamp) -> pd.DataFrame:
        try:
            raw = request(a, b)
        except APIError as exc:
            if state["feed"] != DataFeed.SIP:
                raise
            log(f"  SIP refused ({exc}); falling back to IEX (one exchange, thinner volume).")
            state["feed"] = DataFeed.IEX
            raw = request(a, b)
        if raw.empty:
            return pd.DataFrame(columns=ALPACA_FIELDS)
        df = raw.xs(plan.symbol, level="symbol") if isinstance(raw.index, pd.MultiIndex) else raw
        df.index = pd.DatetimeIndex(df.index).tz_convert(TZ)
        df.index.name = "timestamp"
        return df[[c for c in ALPACA_FIELDS if c in df.columns]].astype(float)

    return _cached_months("alpaca", plan.symbol, requested_start, plan.end, fetch, refresh, log)


# --------------------------------------------------------------------------- Databento

def databento_symbol(root: str, roll: str = "volume") -> str:
    """Continuous front-month symbol: NQ.v.0 rolls on volume, NQ.c.0 on calendar, NQ.n.0 on open interest."""
    root = root.strip().upper()
    if not _FUT_ROOT_RE.match(root):
        raise ValueError(f"{root!r} is not a futures root like NQ, ES, MNQ")
    codes = {"volume": "v", "calendar": "c", "open_interest": "n"}
    if roll not in codes:
        raise ValueError(f"roll must be one of {sorted(codes)}")
    return f"{root}.{codes[roll]}.0"


def _databento_client():
    import databento as db

    return db.Historical(_env("DATABENTO_API_KEY"))


def plan_databento(
    root: str = "NQ",
    start=None,
    end=None,
    months: int = 24,
    roll: str = "volume",
    with_stats: bool = True,
    with_trades: bool = False,
    client=None,
) -> RequestPlan:
    """Validate a Databento request against the dataset's available range and price it.

    Uses only free metadata calls (get_dataset_range, get_cost, get_record_count).
    """
    if months < 1:
        raise ValueError("months must be >= 1")
    symbol = databento_symbol(root, roll)
    client = client or _databento_client()

    available = client.metadata.get_dataset_range(dataset=DATABENTO_DATASET)
    schema_range = available.get("schema", {}).get(SCHEMA_BARS, available)
    avail_start, avail_end = _ts(schema_range["start"]), _ts(schema_range["end"])

    notes = []
    end_ts = _ts(end) if end is not None else avail_end
    if end_ts > avail_end:
        notes.append(f"end clamped to dataset's available end {avail_end:%Y-%m-%d %H:%M} UTC")
        end_ts = avail_end
    start_ts = _ts(start) if start is not None else end_ts - pd.DateOffset(months=months)
    fetch_start = max(_month_floor(start_ts), avail_start)
    if start_ts < avail_start:
        raise ValueError(f"{DATABENTO_DATASET} starts {avail_start.date()}; got start {start_ts.date()}")
    if fetch_start >= end_ts:
        raise ValueError(f"start {fetch_start} is not before end {end_ts}")

    params = dict(
        dataset=DATABENTO_DATASET, symbols=[symbol], stype_in="continuous",
        start=fetch_start.isoformat(), end=end_ts.isoformat(),
    )
    schemas = [SCHEMA_BARS] + ([SCHEMA_STATS] if with_stats else []) + ([SCHEMA_TRADES] if with_trades else [])
    costs = {s: float(client.metadata.get_cost(schema=s, **params)) for s in schemas}
    bars = int(client.metadata.get_record_count(schema=SCHEMA_BARS, **params))
    n_months = sum(1 for _ in month_ranges(fetch_start, end_ts))

    fields = list(COLUMNS) + ["instrument_id"]
    if with_trades:
        fields += ["trade_count", "buy_volume", "sell_volume"]
        trades = int(client.metadata.get_record_count(schema=SCHEMA_TRADES, **params))
        notes.append(f"trades schema: {trades:,} raw trades aggregated to per-minute counts in {TRADE_CHUNK:,}-row chunks")
    if with_stats:
        notes.append("statistics: daily " + ", ".join(name for name, _ in STAT_COLUMNS.values()))
    notes += [
        f"dataset {DATABENTO_DATASET}, stype_in=continuous, symbol {symbol} (front month, not back-adjusted)",
        "ohlcv-1m has no trade count, and a bar is only published for minutes that had trades",
        f"one timeseries.get_range per schema per month ({n_months} months)",
    ]
    return RequestPlan(
        source="databento", symbol=symbol, start=fetch_start, end=end_ts,
        requests=n_months * len(schemas), bars=bars, fields=fields, notes=notes, costs=costs,
    )


def _bars_frame(store) -> pd.DataFrame:
    raw = store.to_df()
    if raw.empty:
        return pd.DataFrame(columns=COLUMNS + ["instrument_id"])
    df = raw[COLUMNS + ["instrument_id"]].copy()
    df[COLUMNS] = df[COLUMNS].astype(float)
    df.index = pd.DatetimeIndex(df.index).tz_convert(TZ)
    df.index.name = "timestamp"
    return df


def aggregate_trades(chunks) -> pd.DataFrame:
    """Per-minute trade_count and aggressor volume from Databento trades frames.

    side 'B' = buy aggressor (lifted the offer), 'A' = sell aggressor, 'N' = unknown.
    """
    parts = []
    for chunk in chunks:
        if chunk.empty:
            continue
        minute = pd.DatetimeIndex(chunk.index).floor("1min")
        size = chunk["size"].astype(float)
        part = pd.DataFrame({
            "trade_count": 1,
            "buy_volume": size.where(chunk["side"] == "B", 0.0).to_numpy(),
            "sell_volume": size.where(chunk["side"] == "A", 0.0).to_numpy(),
        }, index=minute)
        parts.append(part.groupby(level=0).sum())
    if not parts:
        return pd.DataFrame(columns=["trade_count", "buy_volume", "sell_volume"])
    out = pd.concat(parts).groupby(level=0).sum()  # a minute can straddle chunks
    out.index = out.index.tz_convert(TZ) if out.index.tz is not None else out.index.tz_localize("UTC").tz_convert(TZ)
    out.index.name = "timestamp"
    return out


def pivot_statistics(raw: pd.DataFrame) -> pd.DataFrame:
    """Daily table of settlement, open interest, cleared volume and session prices."""
    if raw.empty:
        return pd.DataFrame(columns=[name for name, _ in STAT_COLUMNS.values()])
    df = raw[raw["stat_type"].astype(int).isin(STAT_COLUMNS)].copy()
    if "update_action" in df:
        df = df[df["update_action"].astype(int) == 1]  # 1 = new, 2 = delete
    df["stat"] = df["stat_type"].astype(int).map(lambda t: STAT_COLUMNS[t][0])
    df["value"] = [
        row.price if STAT_COLUMNS[int(row.stat_type)][1] == "price" else row.quantity
        for row in df.itertuples()
    ]
    ref = pd.to_datetime(df["ts_ref"], utc=True) if "ts_ref" in df else pd.DatetimeIndex(df.index)
    df["date"] = pd.DatetimeIndex(ref).tz_convert(TZ).normalize()
    table = df.pivot_table(index="date", columns="stat", values="value", aggfunc="last")
    table.index = table.index.tz_localize(None)
    return table.sort_index()


def load_databento_minutes(
    root: str = "NQ",
    start=None,
    end=None,
    months: int = 24,
    roll: str = "volume",
    with_stats: bool = True,
    with_trades: bool = False,
    refresh: bool = False,
    log: Callable[[str], None] = print,
    log_plan: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Returns (minute_bars, daily_stats). SPENDS Databento credit for uncached months.

    minute_bars: open, high, low, close, volume, instrument_id
                 (+ trade_count, buy_volume, sell_volume with with_trades=True).
    instrument_id changes on the roll bar; prices are not back-adjusted.
    """
    client = _databento_client()
    plan = plan_databento(root, start, end, months, roll, with_stats, with_trades, client=client)
    requested_start = _ts(start) if start is not None else plan.end - pd.DateOffset(months=months)
    requested_start = max(requested_start, plan.start)
    if log_plan:
        log(plan.describe())
    base = dict(dataset=DATABENTO_DATASET, symbols=[plan.symbol], stype_in="continuous")

    def get(schema: str, a: pd.Timestamp, b: pd.Timestamp):
        return client.timeseries.get_range(schema=schema, start=a.isoformat(), end=b.isoformat(), **base)

    bars = _cached_months(
        "databento", plan.symbol, requested_start, plan.end,
        lambda a, b: _bars_frame(get(SCHEMA_BARS, a, b)), refresh, log,
    )

    if with_trades:
        counts = _cached_months(
            "databento_trades", plan.symbol, requested_start, plan.end,
            lambda a, b: aggregate_trades(get(SCHEMA_TRADES, a, b).to_df(count=TRADE_CHUNK)), refresh, log,
        )
        bars = bars.join(counts, how="left")
        bars[["trade_count", "buy_volume", "sell_volume"]] = bars[["trade_count", "buy_volume", "sell_volume"]].fillna(0)

    stats = None
    if with_stats:
        def fetch_stats(a, b):
            raw = get(SCHEMA_STATS, a, b).to_df()
            keep = [c for c in ("stat_type", "price", "quantity", "ts_ref", "update_action") if c in raw]
            out = raw[keep].copy()
            out.index = pd.DatetimeIndex(out.index).tz_convert(TZ)
            return out

        raw_stats = _cached_months(
            "databento_stats", plan.symbol, requested_start, plan.end, fetch_stats, refresh, log, dedupe=False,
        )
        stats = pivot_statistics(raw_stats)
    return bars, stats


def estimate_databento_cost(*args, **kwargs) -> RequestPlan:
    """Backwards-compatible alias for plan_databento()."""
    return plan_databento(*args, **kwargs)


# --------------------------------------------------------------------------- helpers

def _ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def _env(name: str) -> str:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is not set. Add it to {ROOT / '.env'} (see .env.example).")
    return value
