# Review: the three VIX/QQQ files in ~/

Reviewed 2026-10-18 against official Cboe VIX daily data and Yahoo QQQ daily data.

| File | Rows | Span | What it is | Verdict |
|---|---|---|---|---|
| `VIX_2Year_Perfect_Master (1).csv` (166 MB; identical to the copy in Downloads) | 1,053,405 | 2024-01-02 → 2026-01-02 | VIX 1-minute bars on a 24/7 grid, plus ATR, RSI, ADX and Bollinger bands | ✅ **Usable after cleaning:** real prices are accurate, but 68% of rows are filled in |
| `QQQ_VIX_Research_Master.csv` (72 MB) | 285,039 | 2024-01-03 → 2025-12-31 | QQQ 1-minute bars (volume, VWAP, trade count) plus VIX columns and indicators | ❌ **Not usable as-is:** time labels are UTC, so afternoons are missing and pre-market is mislabelled |
| `QQQ_VIX_Research_Master - QQQ_VIX_Research_Master.csv` (53 MB) | 285,039 | same | Google Sheets re-export of the file above (numbers rounded to ~10 digits) | Duplicate, safe to delete |

## 1. VIX_2Year_Perfect_Master

**Columns:** Datetime, Open, **Close, High, Low** (note the non-standard order), Timestamp (epoch ms), ATR, RSI, ADX, BB_Upper, BB_Lower.

**What's good**
- Every one of the 518 trading days has real bars.
- **The daily close matches Cboe's official close within 0.05 points on 96.9% of days** (max difference 0.47).
- Extremes match exactly: the low of 10.62 (2024-07-19) and the high of 65.73 (2024-08-05 08:37 New York, the yen carry-trade crash).
- No impossible bars (e.g. a high below the low).
- It covers both sessions: overnight (03:15–09:15 New York) and the regular session (09:30–16:15).

**What to fix before use**
1. **`Datetime` is UTC, not New York time.** The first bar, labelled 08:30, is 03:30 New York (the overnight open). Convert with `tz_localize('UTC').tz_convert('America/New_York')`.
2. **Only 31.6% of rows are real bars (332,352).** The other 721,053 rows, including every weekend minute (299,520 rows), are copies of the last real bar, carried forward up to 83 hours. A real bar is a row where `Datetime == Timestamp`; keep only those.
3. **The indicators were computed on the filled rows**, so RSI, ATR, ADX and the Bollinger bands are distorted around nights and weekends. Recompute them on real bars.
4. **Coverage gaps:** the regular session has a median of 390 of 405 minutes per day, but only 57% of days are at least 95% complete. 2024 Q1–Q2 is weakest (87–92%), and the overnight session averages about 70%. Treat missing minutes as gaps, not as unchanged prices.
5. **25% of real bars have high = low** (VIX is computed every 15 s, and quiet minutes are flat). Floating-point noise (13.200000000000001) is harmless.

## 2. QQQ_VIX_Research_Master

**Columns:** Datetime, QQQ_Vol, vw (VWAP), QQQ_Open/Close/High/Low, Timestamp, n (trade count), QQQ_ATR, QQQ_ADX, QQQ_ADL, QQQ_BBU, QQQ_BBL, VIX_Close, VIX_RSI, VIX_ADX, VIX_ATR, VIX_BBU (VIX_BBL is missing). The layout (`vw`, `n`) looks like a Polygon/Massive export.

**The critical problem: the "09:30–16:00" labels are UTC clock times.**
- The rows labelled 09:30–16:00 actually hold **04:30–11:00 New York time in summer (05:30–12:00 in winter)**. The first row, "2024-01-03 09:30", is really 04:29 New York, with 564 shares traded.
- The volume confirms it: the median per minute is about 1,100 shares at labelled 09:00–12:00 and jumps to about 48,000 at labelled 14:00, which is the real 09:30 open.
- **So every day is missing roughly 11:00/12:00–16:00 New York**, and the file's daily "close" is a late-morning price (only 8.6% of days within 5 bp of QQQ's official close). The real session's open, highs, lows and closing moves are mostly absent.
- **This can't be fixed by relabelling alone**, because the afternoon bars were never captured.

**Other issues**
- **The grid includes all calendar days:** 729 days, of which 208 are weekends (81,328 rows) and about 20 are market holidays, all filled in.
- **37% of rows are filled, and the fill copies volume forward:** 105,392 filled rows carry non-zero `QQQ_Vol` and `n`. Any volume total or VWAP calculation double-counts.
- The VIX columns match `VIX_2Year_Perfect_Master` at the same label (100%), so QQQ and VIX are aligned to each other within the file. Both inherit the UTC labels and filled rows.
- The indicators (ATR, ADX, ADL, Bollinger bands, VIX RSI/ADX/ATR) were computed over the filled rows, so they're distorted.

## 3. The Sheets copy
It has the same rows and timestamps as file 2. Values are rounded to about 10 significant digits (max relative difference 0.2%) and times print as `9:30:00`. It adds nothing, so delete it to avoid confusion.

## Recommendation
- **VIX:** keep it. After cleaning (convert to New York time, keep only real bars, recompute indicators) it is **the only 1-minute VIX history you have** (2024–2025), and it agrees with Cboe's official data.
- **QQQ:** don't use this file for backtests. Re-export it from the original source with New York time labels and the full 04:00–20:00 window, with no weekend rows and no forward-filled volume. Alternatively, use the Alpaca downloader in this project once you have keys; it produces correct New York-time bars with volume, trade count and VWAP.

**Snippet to clean the VIX file:**
```python
import pandas as pd
v = pd.read_csv("~/VIX_2Year_Perfect_Master (1).csv")
real = v[pd.to_datetime(v.Datetime) == pd.to_datetime(v.Timestamp, unit="ms")]
vix = real[["Open", "High", "Low", "Close"]].set_axis(
    pd.to_datetime(real.Datetime).dt.tz_localize("UTC").dt.tz_convert("America/New_York"))
```
