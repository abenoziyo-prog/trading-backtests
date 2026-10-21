import zipfile

import pandas as pd
import pytest

from quant import free_data as fd


def _zip(tmp_path, rows, extra=None):
    path = tmp_path / "HISTDATA_ASCII_NSXUSD_M1_2024.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("DAT_ASCII_NSXUSD_M1_2024.csv", "\n".join(rows) + "\n")
        zf.writestr("DAT_ASCII_NSXUSD_M1_2024.txt", "status report")
        if extra:
            zf.writestr(*extra)
    return path


def test_histdata_stamps_are_new_york_local_time(tmp_path):
    path = _zip(tmp_path, [
        "20240115 093000;100;101;99;100.5;0",  # winter: EST
        "20240715 093000;200;201;199;200.5;0",  # summer: EDT, same wall-clock time
    ])
    df = fd.read_histdata_zip(path)
    assert list(df.index.hour) == [9, 9] and list(df.index.minute) == [30, 30]
    assert [ts.utcoffset().total_seconds() / 3600 for ts in df.index] == [-5, -4]
    assert list(df.columns) == ["open", "high", "low", "close"]
    assert df["close"].tolist() == [100.5, 200.5]


def _week(start: str, break_hour: int) -> pd.DatetimeIndex:
    """Synthetic Sun 18:00 -> Fri 17:00 week of 1-min stamps with the daily break at break_hour."""
    mins = pd.date_range(pd.Timestamp(start) + pd.Timedelta(hours=18 - (17 - break_hour)),
                         periods=5 * 24 * 60, freq="1min")
    return mins[mins.hour != break_hour]


def test_align_to_cme_break_shifts_only_lagging_weeks():
    normal = _week("2025-03-02", 17)
    lagging = _week("2025-03-09", 16)  # US on DST, EU not yet: stamped an hour behind
    out = fd.align_to_cme_break(normal.append(lagging))
    assert (out[: len(normal)] == normal).all()
    shifted = out[len(normal):]
    assert (shifted == lagging + pd.Timedelta(hours=1)).all()
    midweek = shifted[shifted.dayofweek < 4]
    assert (midweek.hour == 17).sum() == 0  # break now at 17:00


def test_histdata_rejects_unexpected_archives(tmp_path):
    path = _zip(tmp_path, ["20240115 093000;1;1;1;1;0"], extra=("evil/other.csv", "x"))
    with zipfile.ZipFile(path) as zf:
        assert len([m for m in zf.namelist() if m.endswith(".csv")]) == 2
    # nested member is ignored, so exactly one top-level CSV is still read
    assert len(fd.read_histdata_zip(path)) == 1

    path2 = tmp_path / "two.zip"
    with zipfile.ZipFile(path2, "w") as zf:
        zf.writestr("a.csv", "20240115 093000;1;1;1;1;0\n")
        zf.writestr("b.csv", "20240115 093000;1;1;1;1;0\n")
    with pytest.raises(RuntimeError):
        fd.read_histdata_zip(path2)


def test_histdata_periods():
    periods = fd.histdata_periods("nsxusd", now=pd.Timestamp("2026-10-18"))
    assert periods[0] == "2010" and periods[15] == "2025"
    assert periods[16:] == [f"2026/{m}" for m in range(1, 11)]


def test_yahoo_days_validated():
    with pytest.raises(ValueError):
        fd.load_yahoo_minutes("QQQ", days=45)


def test_synthetic_dxy_formula():
    idx = pd.date_range("2024-03-04 10:00", periods=3, freq="1min", tz=fd.TZ)
    ones = pd.DataFrame({c: 1.0 for c in ("open", "high", "low", "close")}, index=idx)
    fx = {p: ones.copy() for p in fd.DXY_WEIGHTS}
    out = fd.synthetic_dxy(fx)
    assert out["close"].tolist() == pytest.approx([fd.DXY_CONST] * 3)
    fx["eurusd"] = ones * 1.1  # stronger euro -> weaker dollar index
    assert (fd.synthetic_dxy(fx)["close"] < fd.DXY_CONST).all()
    # a realistic snapshot (2024-03-04 closes) lands near the published ~103.8
    snap = {"eurusd": 1.0855, "usdjpy": 150.50, "gbpusd": 1.2690, "usdcad": 1.3580, "usdsek": 10.33, "usdchf": 0.8845}
    fx = {p: ones * v for p, v in snap.items()}
    assert fd.synthetic_dxy(fx)["close"].iloc[0] == pytest.approx(103.8, abs=0.3)


def test_synthetic_dxy_requires_all_pairs_and_limits_ffill():
    idx = pd.date_range("2024-03-04 10:00", periods=10, freq="1min", tz=fd.TZ)
    ones = pd.DataFrame({c: 1.0 for c in ("open", "high", "low", "close")}, index=idx)
    fx = {p: ones.copy() for p in fd.DXY_WEIGHTS}
    fx["usdsek"] = ones.iloc[:2]  # SEK stops trading after 2 minutes
    out = fd.synthetic_dxy(fx, ffill_minutes=3)
    assert len(out) == 5  # 2 real + 3 carried forward, then dropped
    del fx["usdchf"]
    with pytest.raises(ValueError):
        fd.synthetic_dxy(fx)


def test_clean_vix_master_keeps_real_bars_in_new_york_time(tmp_path):
    path = tmp_path / "vix.csv"
    path.write_text(
        "Datetime,Open,Close,High,Low,Timestamp,ATR,RSI,ADX,BB_Upper,BB_Lower\n"
        "2024-07-01 13:30:00,12.5,12.6,12.7,12.4,1719840600000,,,,,\n"   # real: 09:30 New York (EDT)
        "2024-07-01 13:31:00,12.5,12.6,12.7,12.4,1719840600000,,,,,\n"   # forward-filled copy
        "2024-12-02 14:31:00,13.200000000000001,13.1,13.3,13.0,1733149860000,1,2,3,4,5\n"  # real: 09:31 EST
    )
    df = fd.clean_vix_master(path)
    assert len(df) == 2
    assert list(df.columns) == ["open", "high", "low", "close"]
    assert [t.strftime("%H:%M") for t in df.index] == ["09:30", "09:31"]
    assert df.iloc[0].tolist() == [12.5, 12.7, 12.4, 12.6]  # source column order was Open, Close, High, Low
    assert df.iloc[1]["open"] == 13.2


def _export(tmp_path, rows):
    path = tmp_path / "export.csv"
    path.write_text("Volume,VWAP,Open,Close,High,Low,Timestamp,Transactions,Datetime\n" + "\n".join(rows) + "\n")
    return path


def test_clean_minute_export_converts_utc_to_new_york(tmp_path):
    path = _export(tmp_path, [
        "100,401.6,401.5,401.6,401.7,401.4,1704292200000,5,2024-01-03 14:30:00",  # 09:30 EST
        "200,550.1,550.0,550.2,550.3,549.9,1720013400000,9,2024-07-03 13:30:00",  # 09:30 EDT
    ])
    df = fd.clean_minute_export(path)
    assert [t.strftime("%H:%M") for t in df.index] == ["09:30", "09:30"]
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "trade_count", "vwap"]
    assert df.iloc[0][["open", "high", "low", "close"]].tolist() == [401.5, 401.7, 401.4, 401.6]


def test_clean_minute_export_rejects_filled_rows(tmp_path):
    path = _export(tmp_path, [
        "100,1,1,1,1,1,1704292200000,5,2024-01-03 14:30:00",
        "100,1,1,1,1,1,1704292200000,5,2024-01-03 14:31:00",  # copy of the previous bar
    ])
    with pytest.raises(ValueError):
        fd.clean_minute_export(path)
