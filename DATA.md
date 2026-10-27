# Dataset summary

Snapshot taken 2026-10-18. All files are in `data/free/`, indexed by bar-open time in `America/New_York`, with one row per minute that had data. Rebuild with `scripts/download_free.py`.

## Master table

Last regenerated 2026-10-19 from the files in `data/free/` (17 files). Intraday timestamps are the **bar-open time in New York time** (`America/New_York`, daylight saving applied); daily files are indexed by date.

### Main 1-minute series

| Ticker | Instrument | Start | End | Years | Rows | Open/High/Low/Close | Volume | Trade count / VWAP | Session (NY time) | Source | Main caveats | File |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **NSX** | Nasdaq-100 (index level from 2019, NQ futures prices before) | 2010-11-14 | 2026-10-09 | 15.9 | 5,298,913 | ✅ (bid) | ❌ | ❌ | Sun–Fri 18:00–17:00; from 2019 18:00–16:14 | histdata.com | Feb–Jul 2023 hole; futures gap before 2019; 4 missing days | `NSXUSD_1min_histdata.parquet` |
| **QQQ** | Invesco QQQ ETF | 2024-01-03 | 2025-12-31 | 2.0 | 430,500 | ✅ | ✅ all sessions | ✅ `trade_count`, `vwap` | 04:00–20:00 | your export, converted from UTC | Opens match official prices on 501/501 days; official close = 16:00 auction trade; thin pre-market spikes | `QQQ_1min_2024_2025.parquet` (+ .csv) |
| **VIX** | Cboe Volatility Index | 2024-01-02 | 2026-01-02 | 2.0 | 332,352 | ✅ | ❌ | ❌ | 03:15–09:15, 09:30–16:15 | your file, cleaned | ~4% of regular-session and ~30% of overnight minutes missing | `VIX_1min_2024_2025.parquet` (+ .csv) |
| **DXY** | US Dollar Index, spot (rebuilt from 6 FX pairs) | 2010-01-03 | 2026-10-09 | 16.8 | 6,067,619 | ✅ (high/low understated) | ❌ | ❌ | 24h, Sun 17:00 → Fri 17:00 | histdata FX, rebuilt | Mar–Jul 2023 hole | `DXY_1min_rebuilt.parquet` |
| DX | US Dollar Index futures | 2018-12-16 | 2026-10-09 | 7.8 | 2,382,778 | ✅ | ❌ | ❌ | Sun 20:00 → Fri 17:00 | histdata (`UDXUSD`) | 2–57 bp below spot | `DXY_futures_1min_histdata.parquet` |
| DOW | Dow Jones futures | 2010-11-14 | 2018-12-14 | 8.1 | 2,612,055 | ✅ | ❌ | ❌ | futures session | histdata (shipped as `UDXUSD`) | bonus file, not validated | `DOW_1min_histdata_2010_2018.parquet` |

### Last ~30 days, 1-minute (Yahoo)

| Ticker | Instrument | Start | End | Rows | Volume | Session (NY time) | File |
|---|---|---|---|---|---|---|---|
| QQQ | Invesco QQQ ETF | 2026-09-21 | 2026-10-16 | 17,718 | ✅ regular session only | 04:00–20:00 | `QQQ_1min_yahoo.parquet` (+ .csv) |
| NQ=F | Nasdaq-100 E-mini futures | 2026-09-20 | 2026-10-16 | 27,386 | ✅ | Sun–Fri 18:00–17:00 | `NQF_1min_yahoo.parquet` (+ .csv) |
| ^VIX | Cboe Volatility Index | 2026-09-21 | 2026-10-16 | 15,500 | ❌ (column is 0) | 03:15–16:15 | `VIX_1min_yahoo.parquet` (+ .csv) |
| DX-Y.NYB | US Dollar Index, spot | 2026-09-20 | 2026-10-16 | 26,665 | ❌ (column is 0) | Sun–Fri 18:00–17:00 | `DXY_NYB_1min_yahoo.parquet` (+ .csv) |

### Daily

| Ticker | Instrument | Start | End | Years | Rows | Fields | Source | Notes | File |
|---|---|---|---|---|---|---|---|---|---|
| **^NDX** | Nasdaq-100, **corrected** | 2010-11-01 | 2026-10-16 | 16.0 | 4,014 | O/H/L/C | Yahoo, 2 values fixed | **Use this one.** Closes match FRED within 1 bp on 4,012/4,013 days; exact calendar. Fixed 2019-07-08 high and 2019-08-19 low; Jun 2012 spikes unconfirmed | `NDX_daily_clean.parquet` (+ .csv) |
| VIX | Cboe Volatility Index | 1990-01-02 | 2026-10-16 | 36.8 | 9,296 | O/H/L/C (close only 1990–91) | Cboe (official) | clean since 2007 | `VIX_daily_cboe.parquet` (+ .csv) |
| QQQ | Invesco QQQ ETF | 2024-01-02 | 2025-12-31 | 2.0 | 502 | O/H/L/C, Adj Close, Volume | Yahoo | ⚠️ bad high on 2025-07-16 (560.21; actual ~557.94) and low on 2025-03-24 (484.40; actual ~487.12) | `QQQ_daily_yahoo.parquet` |
| DX-Y.NYB | US Dollar Index | 2010-11-01 | 2026-10-16 | 16.0 | 4,015 | close only | Yahoo | validation reference | `DXY_daily_yahoo.parquet` |

### Reference copies (used for validation)

| File | What | Span | Rows |
|---|---|---|---|
| `NDX_daily_yahoo.parquet` | ^NDX as downloaded (includes the 2 bad 2019 values) | 2010-11-01 → 2026-10-16 | 4,014 |
| `NDX_daily_fred.parquet` | Nasdaq-100 official closes from FRED | 1986-01-02 → 2026-10-15 | 10,278 |
| `NDX_daily_nasdaqcom.parquet` | Nasdaq-100 OHLC from Nasdaq.com (opens unreliable in mid-2026; 18 opens are 0) | 2014-01-01 → 2026-10-16 | 3,235 |

**Shared windows**
- **2024-01-03 → 2025-12-31:** NSX, QQQ (with volume), VIX and DXY all at 1 minute. This is the best window for analytics.
- **2010-11 → 2026-10:** NSX and DXY at 1 minute; ^NDX and VIX daily.
- **Missing:** long NQ futures history with volume (only 30 days from Yahoo); Databento is the planned source.

## At a glance

| | NSXUSD (histdata) | QQQ (Yahoo) | NQ=F (Yahoo) |
|---|---|---|---|
| What | Nasdaq-100, at index level, on the CME futures session | Invesco QQQ ETF | NQ E-mini front-month futures |
| Period | 2010-11-14 → 2026-10-09 (~16 years) | 2026-09-21 → 2026-10-16 (20 days) | 2026-09-20 → 2026-10-16 (24 days) |
| Bars | 5,298,913 | 17,718 | 27,386 |
| Session | Sun–Fri 18:00–17:00 (from 2019: 18:00–16:14) | 04:00–20:00 | Sun–Fri 18:00–17:00 |
| Columns | open, high, low, close | open, high, low, close, volume | open, high, low, close, volume |
| Volume | **none** | regular session only | yes, all sessions |
| Trade count / VWAP | no | no | no |
| Price basis | bid quotes | trades, not adjusted | trades |
| File | `NSXUSD_1min_histdata.parquet` (100 MB) | `QQQ_1min_yahoo.parquet` + `.csv` | `NQF_1min_yahoo.parquet` + `.csv` |

### Dollar index and VIX

| | DXY rebuilt (main) | DXY futures (histdata) | VIX daily (Cboe) | VIX / DXY 1-min (Yahoo) |
|---|---|---|---|---|
| What | **Spot** US Dollar Index, recomputed each minute from its 6 FX pairs with ICE's formula | ICE dollar-index **futures** prices | Official VIX | `^VIX`, `DX-Y.NYB` |
| Period | 2010-01-03 → 2026-10-09 | 2018-12-16 → 2026-10-09 | 1990 → 2026-10-16 | 2026-09-20 → 2026-10-16 |
| Bars / rows | 6,067,619 | 2,382,778 | 9,296 days | 15,500 / 26,665 |
| Session | 24h, Sun 17:00 → Fri 17:00 | Sun 20:00 → Fri 17:00 (break 17:00–20:00) | daily | VIX 03:15–16:15, DXY 18:00–17:00 |
| Columns | open, high, low, close (see note) | open, high, low, close | open, high, low, close | open, high, low, close |
| Volume | none | none | — | none |
| Official-close check | **median 0 bp** every year (90% of days within 6–18 bp) | −2 to −57 bp (the futures-vs-spot gap) | is the official series | VIX last bar matches Cboe close to ±0.02 |
| File | `DXY_1min_rebuilt.parquet` | `DXY_futures_1min_histdata.parquet` | `VIX_daily_cboe.parquet/.csv` | `VIX_1min_yahoo`, `DXY_NYB_1min_yahoo` |

**No free source has 1-minute VIX history** (searched Reddit, GitHub, GitLab, Hugging Face and vendors). Only paid vendors (FirstRate, Portara) or broker APIs have it. The official daily OHLC plus the last ~30 days at 1-minute is the best available without a key, **plus the 2024–2025 file you supplied, below**.

### VIX 1-minute, 2024–2025 (cleaned from your file)

| | |
|---|---|
| File | `VIX_1min_2024_2025.parquet` + `.csv` |
| Source | `~/VIX_2Year_Perfect_Master (1).csv`, cleaned with `quant.free_data.clean_vix_master()` (review: [VIX_FILES_REVIEW.md](VIX_FILES_REVIEW.md)) |
| Period | 2024-01-02 03:30 → 2026-01-02 16:14 New York (517 trading days) |
| Bars | 332,352 real bars (the 721,053 filled-in rows were dropped) |
| Session | 03:15–09:15 overnight and 09:30–16:15 regular, New York time (the original labels were UTC) |
| Columns | open, high, low, close (rounded to 0.01; the original's indicator columns were dropped) |
| Official-close check | the 16:15 close matches Cboe's on **96.9% of days** within 0.05 points (max 0.47); the 10.62 low and 65.73 high match exactly |

**Gaps:** these are real missing minutes, not flat prices. The regular session has a median of 390 of 405 minutes per day, and 57% of days are at least 95% complete (2024 Q1–Q2 is weakest at 87–92%). The overnight session averages about 70%. 25% of bars are flat (high = low), because VIX is computed every 15 seconds. Recompute any indicators on this file rather than reusing the original's.

**DXY notes**
- **The rebuilt index is the one to use.** Its formula is 50.14348112 × EURUSD^−0.576 × USDJPY^0.136 × GBPUSD^−0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036, applied to histdata 1-minute bid quotes. Bars exist where EUR/USD traded; other pairs are carried forward at most 5 minutes.
- **`high` and `low` are max/min of open and close**, because the true intrabar extremes of a product of six rates can't be recovered. Bar ranges are understated; use the close for signals.
- Against histdata's futures series over 2019–2026, minute returns correlate at 0.86 (0.92 median weekly) with no timing offsets.
- **histdata's "UDXUSD" file is mislabelled.** Until Dec 14, 2018 it contains **Dow Jones futures** (22–43 bp from the official Dow close), and only after that the dollar-index futures. The Dow part is saved separately as `DOW_1min_histdata_2010_2018.parquet`, and the downloader refuses `histdata --pair udxusd`.
- **Clock corrections:** from 2019, 28 weeks in March and late Oct/early Nov are stamped an hour behind New York in the dollar-index and currency files. They are detected from the futures' 17:00–20:00 break and corrected in all of them. The same weeks before 2019 test as correctly timed.
- **Mar–Jul 2023 has a source gap** (about 620 of 1,430 bars per weekday), the same hole as NSX.
- The large 1-minute moves are real events (Fed QE2 2010-11-03 14:15, ECB QE 2015-01-22, payroll days at 08:30). The largest is +1.03% on 2015-11-06 at 08:30.

**VIX daily notes:** no missing Nasdaq trading days since 2010, and no high/low inconsistencies after 2006 (47 before that, mostly 1992–94). 1990–91 rows carry the close only.

Rebuild with `scripts/download_free.py dxy`, `vix-daily`, and `yahoo ^VIX DX-Y.NYB`.

## NSXUSD: Nasdaq-100, 2010–2026

- **What it tracks:** NQ futures almost exactly. Over 18,664 overlapping minutes, the 1-minute return correlation with NQ=F is **0.985** (0.75 with QQQ during the regular session).
- **Price level:** about 270 points (~1%) below the December 2026 futures contract, i.e. close to the cash index. **It has no contract-roll jumps**, so it can be backtested as one continuous series.
- **Price range:** 1,973 (2011 low) → 31,362 (2026 high). The last close is 30,887.
- **Regular session (09:30–16:00):** 4,092 days, with a median of 390 of 390 minutes. 93.5% of days are at least 95% complete.

| Year | Bars | Days | Year-end close | Return | Complete regular sessions |
|---|---|---|---|---|---|
| 2010 | 40,855 | 40 | 2,215 | (starts Nov 14) | 71% |
| 2011 | 331,649 | 309 | 2,275 | +2.7% | 97% |
| 2012 | 325,049 | 311 | 2,659 | +16.9% | 96% |
| 2013 | 314,774 | 310 | 3,584 | +34.8% | 96% |
| 2014 | 318,873 | 311 | 4,234 | +18.1% | 96% |
| 2015 | 335,257 | 312 | 4,592 | +8.5% | 97% |
| 2016 | 341,629 | 310 | 4,863 | +5.9% | 96% |
| 2017 | 330,380 | 303 | 6,387 | +31.3% | 97% |
| 2018 | 348,607 | 312 | 6,341 | −0.7% | 96% |
| 2019 | 341,335 | 311 | 8,745 | +37.9% | 97% |
| 2020 | 339,962 | 312 | 12,883 | +47.3% | 97% |
| 2021 | 342,946 | 311 | 16,341 | +26.8% | 98% |
| 2022 | 342,711 | 310 | 10,958 | −32.9% | 97% |
| 2023 | 299,289 | 309 | 16,824 | +53.5% | **54%** ⚠️ |
| 2024 | 340,926 | 313 | 21,036 | +25.0% | 95% |
| 2025 | 340,493 | 312 | 25,249 | +20.0% | 96% |
| 2026 | 264,178 | 240 | 30,887 (Oct 9) | +22.3% YTD | 97% |

(Returns are price only, with no dividends, measured close to close on this series.)

> **See [NSX_VALIDATION.md](NSX_VALIDATION.md) for the full validation.** In short, the data is NQ-futures-priced before 2019 and index-level from 2019, and from 2019 each day's data stops at 16:14.

### Known issues
1. **Mar–Jul 2023 is missing whole hours.** For example, 2023-05-11 has no 10:00 or 12:00 bars, which leaves a median of about 210 of 390 regular-session minutes in those months. This gap is in the source data. Exclude those months, or treat them as low quality, when testing intraday strategies.
2. **No volume at all.** histdata always publishes 0, so the column was dropped.
3. **30 weekdays have no data.** Most are exchange holidays (Good Friday, Christmas Eve, New Year's Day). **2026-09-25** is a genuine missing day.
4. **Timestamps were corrected.** histdata documents its times as fixed EST, but they're actually New York local time. From 2019, some weeks between the US and EU clock changes are also stamped an hour behind. Both are fixed in the loader (`align_to_cme_break`), and the futures' daily 17:00 break now lines up all year.
5. **The trading schedule changed over time.** Before 2016 the futures session paused at 16:15–16:30 and traded until about 17:15. From 2016 to 2018 there's a single 17:00 bar per day. These are real, not data errors.
6. **Bid-only prices.** Each bar is built from bid quotes, so it sits slightly below trade prices (spread about one tick, 0.25 points).
7. **Not published yet:** Oct 10–16, 2026. Re-run the download later to add it.

## QQQ: Yahoo, last ~30 days
- About 956 bars a day across 04:00–20:00. The regular session is 100% complete on all 20 days.
- Median daily volume is 29.6M shares. **Volume is reported only for the regular session.** Pre- and post-market bars have prices but 0 volume, which is why 56% of rows show zero volume.
- Prices are not adjusted for dividends (no corporate actions fell in this window).

## NQ=F: Yahoo, last ~30 days
- About 1,370 bars a day on the full futures session. The regular session is 100% complete.
- Median daily volume is 513K contracts, 74% of it in the regular session. Only 0.1% of bars have zero volume.
- It's a continuous front-month series. Yahoo rolls it without adjusting, but no roll fell in this window.

## What's missing, and where to get it
| Need | Source | Cost |
|---|---|---|
| QQQ volume, trade count and VWAP back to 2016 | Alpaca: `scripts/download_minute.py alpaca` | Free account |
| NQ futures volume, trade counts, buy/sell volume, settlement and open interest | Databento: `scripts/download_minute.py databento` | Sign-up credit |

## Schwab 1-minute archive (growing daily)

| Instrument | Source | Fields | Starts | Updated | File |
|---|---|---|---|---|---|
| /NQ (front-month Nasdaq-100 futures) | Schwab price history, read-only | OHLC + volume, 24h session | 2026-09-10 09:55 ET | daily | `data/schwab/NQ_1min.parquet` |
| QQQ | Schwab price history, read-only | OHLC + volume, 04:00–20:00 ET (extended hours) | 2026-09-10 05:45 ET | daily | `data/schwab/QQQ_1min.parquet` |

- **Schwab's depth (checked 2026-10-21):** 1-minute bars go back about 6 weeks (earliest 2026-09-10) and 5-minute bars about 6.5 months (from about 2026-04-01). Nothing older is available.
- **Updates:** `bots/schwab_archiver.py` appends each night (run by `bots/cloud_runner.py` on Northflank, 18:30 ET), so the history keeps growing. Runs are logged in `data/schwab/archive_log.csv`.
- **Live stream vs history:** Schwab's live stream bars can differ from its history bars in busy minutes. On 2026-10-21, 3% of minutes differed (09:41–09:46 ET, closes up to 9 points). The archive stores the history version.
- **/NQ is the rolling front month,** so contract rolls (quarterly) show up as price jumps.
