"""Keyless 1-minute data sources.

  * histdata.com - Nasdaq-100 (NSXUSD) 1-minute BID bars from Nov 2010, on the
                   CME futures session (Sun-Fri 18:00-17:00 New York). It tracks
                   NQ futures tick for tick (1-min return corr 0.985) but trades
                   ~1% below the front contract, i.e. at cash-index level.
                   Prices only: the volume column is always 0. Raw stamps are
                   New York local time (see read_histdata_zip). Free yearly zips
                   (monthly for the current year).
  * Yahoo        - QQQ / NQ=F 1-minute bars with real volume, last ~30 days only,
                   fetched in 7-day windows (Yahoo's per-request maximum for 1m).

Downloaded zips are untrusted input: they are stored in data/raw/histdata/, and
only the single expected CSV member is read (never extracted to disk).
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "histdata"
FREE_DIR = ROOT / "data" / "free"
TZ = "America/New_York"

HISTDATA_BASE = "https://www.histdata.com"
HISTDATA_PAGE = HISTDATA_BASE + "/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{pair}/{period}"
HISTDATA_FIRST_YEAR = {"nsxusd": 2010, "spxusd": 2010, "udxusd": 2010}
# Daily trading break (start, end hour, New York) used to detect hour-shifted weeks
SESSION_BREAKS = {"nsxusd": (17, 18), "spxusd": (17, 18), "udxusd": (17, 20)}
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"
MAX_ZIP_BYTES = 200 * 1024 * 1024
_FIELD_RE = re.compile(r'<input[^>]*name="(\w+)"[^>]*value="([^"]*)"', re.S)


# --------------------------------------------------------------------------- histdata

def histdata_periods(pair: str = "nsxusd", now: pd.Timestamp | None = None) -> list[str]:
    """Periods histdata offers: full years up to last year, then months of this year."""
    now = now or pd.Timestamp.now()
    first = HISTDATA_FIRST_YEAR.get(pair.lower(), 2010)
    years = [str(y) for y in range(first, now.year)]
    months = [f"{now.year}/{m}" for m in range(1, now.month + 1)]
    return years + months


def _histdata_form(session: requests.Session, pair: str, period: str) -> dict:
    url = HISTDATA_PAGE.format(pair=pair.lower(), period=period)
    r = session.get(url, timeout=60)
    r.raise_for_status()
    form = re.search(r'<form[^>]*id="file_down".*?</form>', r.text, re.S)
    if not form:
        raise RuntimeError(f"histdata: no download form for {pair} {period} (not published yet?)")
    fields = dict(_FIELD_RE.findall(form.group(0)))
    expected = {"tk", "date", "datemonth", "platform", "timeframe", "fxpair"}
    if not expected <= set(fields) or fields["fxpair"].lower() != pair.lower() or fields["timeframe"] != "M1":
        raise RuntimeError(f"histdata: unexpected form for {pair} {period}: {sorted(fields)}")
    return {"url": url, "fields": fields}


def download_histdata_zip(
    session: requests.Session, pair: str, period: str, refresh: bool = False, log: Callable[[str], None] = print,
) -> Path:
    """Fetch one yearly ('2015') or monthly ('2026/9') zip into data/raw/histdata/."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"HISTDATA_ASCII_{pair.upper()}_M1_{period.replace('/', '_')}.zip"
    current_year = str(pd.Timestamp.now().year)
    # Current-year months keep growing until the month ends, so re-fetch the latest one.
    if path.exists() and not refresh and not period.startswith(current_year + "/"):
        return path
    form = _histdata_form(session, pair, period)
    r = session.post(HISTDATA_BASE + "/get.php", data=form["fields"], headers={"Referer": form["url"]}, timeout=300)
    r.raise_for_status()
    body = r.content
    if len(body) > MAX_ZIP_BYTES or not body.startswith(b"PK"):
        raise RuntimeError(f"histdata: {pair} {period} did not return a zip ({len(body):,} bytes)")
    path.write_bytes(body)
    log(f"  histdata {pair.upper()} {period}: {len(body) / 1e6:.1f} MB")
    return path


def read_histdata_zip(path: Path, break_hours: tuple[int, int] | None = (17, 18)) -> pd.DataFrame:
    """Parse one histdata M1 zip without extracting it. Returns NY-time OHLC bars.

    break_hours=None skips the hour-shift correction (instruments without a daily break, e.g. spot FX).
    """
    with zipfile.ZipFile(path) as zf:
        members = [m for m in zf.infolist() if m.filename.lower().endswith(".csv") and "/" not in m.filename]
        if len(members) != 1 or members[0].file_size > 2 * MAX_ZIP_BYTES:
            raise RuntimeError(f"{path.name}: expected one CSV, found {[m.filename for m in zf.infolist()]}")
        raw = zf.read(members[0])
    df = pd.read_csv(
        io.BytesIO(raw), sep=";", header=None,
        names=["ts", "open", "high", "low", "close", "volume"],
        dtype={"ts": str}, engine="c",
    )
    # histdata's spec says "EST without daylight saving", but for NSXUSD the raw
    # stamps are New York local time WITH daylight saving: the CME 17:00-18:00
    # maintenance break sits at raw 17:00 in both winter and summer, and a fixed
    # UTC-5 reading is 60 minutes off versus NQ=F/QQQ in summer (corr 0.03 vs
    # 0.985 when read as local time). DST switches happen while the market is
    # closed, so ambiguous/nonexistent stamps should not occur; any that do are dropped.
    ts = pd.DatetimeIndex(pd.to_datetime(df["ts"], format="%Y%m%d %H%M%S"))
    if break_hours is not None:
        ts = align_to_session_break(ts, *break_hours)
    idx = ts.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
    df.index = idx
    df = df[~idx.isna()]
    df.index.name = "timestamp"
    return df[["open", "high", "low", "close"]].astype(float)


def _session_week(ts: pd.DatetimeIndex) -> pd.PeriodIndex:
    # Trading week runs Sunday evening -> Friday 17:00; shift +7h so it falls in one calendar week.
    return (ts + pd.Timedelta(hours=7)).to_period("W-SAT")


def detect_shifted_weeks(ts: pd.DatetimeIndex, break_start: int = 17, break_end: int = 18) -> pd.PeriodIndex:
    """Weeks whose daily break shows up an hour early, i.e. stamped an hour behind New York."""
    week = _session_week(ts)
    midweek = ts.dayofweek < 4
    before = pd.Series((ts.hour == break_start - 1) & midweek).groupby(week).sum()
    last = pd.Series((ts.hour == break_end - 1) & midweek).groupby(week).sum()
    return before[(before < 0.2 * last) & (last > 60)].index


def shift_weeks(ts: pd.DatetimeIndex, weeks, minutes: int = 60) -> pd.DatetimeIndex:
    """Move every stamp that falls in `weeks` forward by `minutes`."""
    if len(weeks) == 0:
        return ts
    hit = pd.Index(_session_week(ts)).isin(weeks)
    return pd.DatetimeIndex(ts + pd.to_timedelta(hit.astype(int) * minutes, unit="min"))


def align_to_session_break(ts: pd.DatetimeIndex, break_start: int = 17, break_end: int = 18) -> pd.DatetimeIndex:
    """Shift whole weeks whose clock is an hour behind New York.

    From 2019 some weeks between the US and European daylight-saving switches
    (mid/late March, late October/early November) are stamped one hour behind
    New York. The instrument's daily break (CME equity futures 17:00-18:00,
    ICE dollar index 17:00-20:00 New York, Mon-Thu) is a fixed clock: in those
    weeks the hour before the break is empty and the break's last hour is not.
    Such weeks are moved forward 60 minutes. Weeks without a break (e.g. the
    24h dollar index before 2016) are never shifted.
    """
    return shift_weeks(ts, detect_shifted_weeks(ts, break_start, break_end))


def align_to_cme_break(ts: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """CME equity futures break (17:00-18:00 New York)."""
    return align_to_session_break(ts, 17, 18)


def load_histdata(
    pair: str = "nsxusd", refresh: bool = False, pause: float = 1.0, log: Callable[[str], None] = print,
) -> pd.DataFrame:
    """All available 1-minute bars for a histdata index pair (default Nasdaq-100)."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    frames = []
    for period in histdata_periods(pair):
        try:
            path = download_histdata_zip(session, pair, period, refresh=refresh, log=log)
        except RuntimeError as exc:
            log(f"  skipped {period}: {exc}")
            continue
        frames.append(read_histdata_zip(path, SESSION_BREAKS.get(pair.lower(), (17, 18))))
        time.sleep(pause)  # be polite to a free service
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


# --------------------------------------------------------------------------- Yahoo

def load_yahoo_minutes(
    symbol: str, days: int = 29, prepost: bool = True, log: Callable[[str], None] = print,
) -> pd.DataFrame:
    """Last ~30 days of 1-minute bars with volume (Yahoo allows 7 days per 1m request)."""
    import yfinance as yf

    if not 1 <= days <= 29:
        raise ValueError("Yahoo serves 1-minute bars for the last 30 days only; use days in 1..29")
    end = pd.Timestamp.now(tz="UTC").floor("min")
    cursor = end - pd.Timedelta(days=days)
    frames = []
    while cursor < end:
        stop = min(cursor + pd.Timedelta(days=7), end)
        df = yf.download(
            symbol, start=cursor.to_pydatetime(), end=stop.to_pydatetime(), interval="1m",
            prepost=prepost, auto_adjust=False, progress=False, threads=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not df.empty:
            frames.append(df)
        log(f"  yahoo {symbol} {cursor:%Y-%m-%d} -> {stop:%Y-%m-%d}: {len(df):,} bars")
        cursor = stop
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.concat(frames)
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    df.index = pd.DatetimeIndex(df.index).tz_convert(TZ)
    df.index.name = "timestamp"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(float)


CBOE_VIX_DAILY = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def load_cboe_vix_daily() -> pd.DataFrame:
    """Official Cboe VIX daily open/high/low/close since 1990 (no key)."""
    r = requests.get(CBOE_VIX_DAILY, timeout=60, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"date", "open", "high", "low", "close"} <= set(df.columns):
        raise RuntimeError(f"Unexpected Cboe VIX columns: {list(df.columns)}")
    df.index = pd.to_datetime(df.pop("date"), format="%m/%d/%Y")
    df.index.name = "date"
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def file_stem(symbol: str) -> str:
    """'^VIX' -> 'VIX', 'NQ=F' -> 'NQF', 'DX-Y.NYB' -> 'DXY_NYB'."""
    return symbol.upper().replace("^", "").replace("=", "").replace("-", "").replace(".", "_")


def save(df: pd.DataFrame, name: str, csv: bool = True) -> Path:
    FREE_DIR.mkdir(parents=True, exist_ok=True)
    path = FREE_DIR / f"{name}.parquet"
    df.to_parquet(path)
    if csv:
        df.to_csv(path.with_suffix(".csv"))
    return path


# --------------------------------------------------------------------------- US Dollar Index

# ICE US Dollar Index formula (fixed since 1999): 50.14348112 x product of FX rates ^ weight
DXY_CONST = 50.14348112
DXY_WEIGHTS = {"eurusd": -0.576, "usdjpy": 0.136, "gbpusd": -0.119, "usdcad": 0.091, "usdsek": 0.042, "usdchf": 0.036}
# histdata's "UDXUSD" holds Dow Jones prices until this bar, the real dollar index after it
UDX_REAL_FROM = pd.Timestamp("2018-12-16 18:00", tz=TZ)


def load_cached_histdata(pair: str, break_hours: tuple[int, int] | None, shifted_weeks=None) -> pd.DataFrame:
    """All cached zips for a pair (no network).

    shifted_weeks: weeks to move forward an hour (for instruments with no daily
    break of their own, e.g. spot FX, using weeks detected on another instrument).
    """
    paths = sorted(RAW_DIR.glob(f"HISTDATA_ASCII_{pair.upper()}_M1_*.zip"))
    if not paths:
        raise FileNotFoundError(f"No cached histdata files for {pair}; download them first")
    df = pd.concat(read_histdata_zip(p, break_hours) for p in paths).sort_index()
    if shifted_weeks is not None and len(shifted_weeks):
        local = shift_weeks(df.index.tz_localize(None), shifted_weeks)
        df.index = local.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
        df = df[~df.index.isna()].sort_index()
    return df[~df.index.duplicated(keep="last")]


def histdata_shifted_weeks(pair: str = "udxusd") -> pd.PeriodIndex:
    """Hour-behind weeks detected on a cached instrument with a daily break."""
    paths = sorted(RAW_DIR.glob(f"HISTDATA_ASCII_{pair.upper()}_M1_*.zip"))
    raw = pd.concat(read_histdata_zip(p, None) for p in paths)
    return detect_shifted_weeks(raw.index.tz_localize(None), *SESSION_BREAKS[pair])


def synthetic_dxy(fx: dict[str, pd.DataFrame], ffill_minutes: int = 5) -> pd.DataFrame:
    """Rebuild the dollar index minute by minute from its six component FX pairs.

    Bars exist where EUR/USD (58% of the index) traded; other pairs are carried
    forward at most `ffill_minutes`. open/close are exact index values from the
    component opens/closes; high/low are max/min of open and close (the true
    intrabar extremes cannot be recovered), so bar ranges are understated.
    """
    missing = set(DXY_WEIGHTS) - set(fx)
    if missing:
        raise ValueError(f"missing FX pairs: {sorted(missing)}")
    anchor = fx["eurusd"].index
    out = {}
    for field in ("open", "close"):
        frame = pd.concat({p: fx[p][field] for p in DXY_WEIGHTS}, axis=1).sort_index()
        frame = frame.ffill(limit=ffill_minutes).reindex(anchor)
        logv = sum(np.log(frame[p]) * w for p, w in DXY_WEIGHTS.items())
        out[field] = DXY_CONST * np.exp(logv)
    df = pd.DataFrame(out).dropna()
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    return df[["open", "high", "low", "close"]]


def build_dxy(log: Callable[[str], None] = print) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Full-history 1-minute DXY: FX-rebuilt before Dec 16 2018, histdata UDXUSD after.

    Returns (dxy, dow, rebuilt):
      dxy     - combined series with a `source` column (fx_rebuilt / histdata_dxy)
      dow     - the Dow Jones data histdata ships under the UDXUSD name, Nov 2010 - Dec 14 2018
      rebuilt - the FX-rebuilt index for the whole span (for validation against the real one)
    """
    udx = load_cached_histdata("udxusd", SESSION_BREAKS["udxusd"])
    real, dow = udx[udx.index >= UDX_REAL_FROM], udx[udx.index < UDX_REAL_FROM]
    log(f"  UDXUSD: {len(dow):,} Dow-level bars before {UDX_REAL_FROM.date()}, {len(real):,} real DXY bars after")
    # Spot FX has no daily break; reuse the hour-behind weeks found on the dollar-index futures.
    weeks = histdata_shifted_weeks("udxusd")
    log(f"  correcting {len(weeks)} hour-behind weeks in the FX pairs")
    fx = {p: load_cached_histdata(p, None, shifted_weeks=weeks) for p in DXY_WEIGHTS}
    syn = synthetic_dxy(fx)
    early = syn[syn.index < UDX_REAL_FROM].assign(source="fx_rebuilt")
    late = real.assign(source="histdata_dxy")
    dxy = pd.concat([early, late]).sort_index()
    dxy["source"] = dxy["source"].astype("category")
    return dxy, dow, syn


# --------------------------------------------------------------------------- user-supplied VIX file

def clean_vix_master(path: str | Path) -> pd.DataFrame:
    """Clean 'VIX_2Year_Perfect_Master.csv' (24/7 grid, UTC labels, forward-filled).

    Keeps only real bars (Datetime == epoch Timestamp; the rest are copies of an
    earlier bar), converts the UTC labels to New York time, drops the indicator
    columns (computed over the filled rows) and rounds float noise to 2 dp.
    """
    raw = pd.read_csv(path, usecols=["Datetime", "Open", "High", "Low", "Close", "Timestamp"])
    label = pd.to_datetime(raw["Datetime"])
    stamp = pd.to_datetime(raw["Timestamp"], unit="ms")
    real = raw[label == stamp]
    idx = pd.DatetimeIndex(label[label == stamp]).tz_localize("UTC").tz_convert(TZ)
    df = pd.DataFrame(
        real[["Open", "High", "Low", "Close"]].to_numpy().round(2),
        index=idx, columns=["open", "high", "low", "close"],
    )
    df.index.name = "timestamp"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    bad = (df.high < df[["open", "close"]].max(axis=1)) | (df.low > df[["open", "close"]].min(axis=1))
    if bad.any():
        raise ValueError(f"{int(bad.sum())} bars with inconsistent OHLC after cleaning")
    return df


# --------------------------------------------------------------------------- user-supplied QQQ export

def clean_minute_export(path: str | Path) -> pd.DataFrame:
    """Convert a Polygon-style 1-minute export (Volume, VWAP, Open, Close, High, Low,
    Timestamp [epoch ms, UTC], Transactions, Datetime [UTC label]) to New York time.

    Returns open, high, low, close, volume, trade_count, vwap indexed by bar-open
    time in America/New_York. Rejects files with duplicate stamps or forward-filled
    rows (Datetime label disagreeing with the epoch Timestamp).
    """
    raw = pd.read_csv(path)
    stamp = pd.to_datetime(raw["Timestamp"], unit="ms")
    if "Datetime" in raw and (pd.to_datetime(raw["Datetime"]) != stamp).any():
        raise ValueError("Datetime and Timestamp disagree: file contains filled or relabelled rows")
    if stamp.duplicated().any():
        raise ValueError("duplicate timestamps")
    df = pd.DataFrame({
        "open": raw["Open"], "high": raw["High"], "low": raw["Low"], "close": raw["Close"],
        "volume": raw["Volume"], "trade_count": raw["Transactions"], "vwap": raw["VWAP"],
    }).astype(float)
    df.index = pd.DatetimeIndex(stamp).tz_localize("UTC").tz_convert(TZ)
    df.index.name = "timestamp"
    return df.sort_index()
