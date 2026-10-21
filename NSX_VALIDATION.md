# NSXUSD 1-minute: validation report

Run on 2026-10-18 with `scripts/validate_nsx.py`, which writes its CSVs and charts to `reports/nsx_validation/`. The reference is the official Nasdaq-100 daily open, high, low and close from Yahoo (`^NDX`): 3,999 trading days from Nov 2010 to Oct 2026.

## Verdict

**Usable for backtesting, with three rules:**
1. **Use 2019-01 onward for intraday or precise work.** Those years match the official index to within about 2 bp at the close, have no contract-roll jumps, and are 96–100% complete outside 2023.
2. **Exclude or flag Feb–Jul 2023**, which is missing whole hours.
3. **Before 2019 the series is NQ-futures-priced.** It's fine for longer-horizon or pattern work, but it has a −10 to −15 bp gap to the index and roll steps of up to 19 bp in 2011–2012.

| Check | Result |
|---|---|
| Structure (ordering, duplicates, OHLC logic, prices > 0) | ✅ Clean: 0 issues in 5,298,913 bars |
| Regular-session completeness (09:30–16:00) | ✅ 98.3% of minutes. 96.4% of days are at least 95% complete. ⚠️ 2023 only 78% |
| Missing trading days | ⚠️ 6 of 3,999: 2017-02-21 to 24, 2020-11-30, 2026-09-25 |
| Timestamp alignment | ✅ The release-day jump lands in the 08:30 bar every year. From 2019 the 15:59 bar sits 1.8 bp (median) from the official close |
| Bad ticks | ✅ 0 confirmed. 1 flagged spike (2022-07-13 08:29) is the minute before that day's CPI release |
| Stale or frozen prices | ✅ No runs of 30+ identical closes in the regular session |
| Agreement with official index (close) | ✅ From 2019 the median is within ±1 bp and 95% of days within 5–11 bp. ⚠️ Before 2019 the median is −10 to −15 bp (+10 bp in 2018), which is the futures-vs-index gap |

## 1. The data has two regimes

| | 2010 – early 2017 | 2017–2018 | 2019 → now |
|---|---|---|---|
| Price source | **NQ futures prices** (on the 0.25 tick grid) | transition (grid share 31–53%) | **Index-level** prices (off-grid) |
| Gap to official close | −10 to −15 bp (+10 bp in 2018) | +1 to +10 bp | **−1 to +0.2 bp** |
| Quarterly roll jumps | yes, 10–19 bp in 2011–12, smaller later | small | **none** |
| Daily session (New York) | 18:00 → 17:00 with a 16:15–16:30 halt | same | 18:00 → **16:14** |
| Bars per full Tue–Thu session | 1,240–1,340 | 1,328–1,365 | 1,335 |
| Zero-range bars (high = low) | 6–18% | 1–3% | 0% |

The gap before 2019 follows the textbook futures-vs-index sawtooth: it jumps at each quarterly roll and narrows toward expiry. **From 2019, the futures' 16:15–17:00 trading after the cash close is never included.**

## 2. Completeness

The heatmap below shows the share of 09:30–16:00 minutes present each month:

![completeness](reports/nsx_validation/completeness_heatmap.png)

- **2023:** 111 partial days, mainly late Feb to end of Jul (median 69% complete in that span; whole hours such as 10:00 or 12:00 are absent). The overnight session in those months has 439 gaps of 31–120 minutes and 74 gaps over 2 hours.
- **Feb 2017:** Feb 21–24 is missing entirely.
- **Missing days:** 2020-11-30 and 2026-09-25 (one day each).
- **Minor gaps:** Oct–Nov 2024 is 95–97% complete, Nov 2020 95%, Sep 2026 95%. Outside 2023, 25 days fall below 95% and only 5 below 80%.
- **Pre-2016 overnight hours:** many 2–5 minute gaps (14k–22k a year). That is low overnight liquidity, not lost data: a minute with no trades has no bar.
- **Gaps over 2 hours outside 2023 are mostly real market events:** the March 2020 limit-down halts, holiday sessions, and the Thanksgiving 2025 early close.
- **Half days** (34 in the span) are checked against 09:30–13:00.

## 3. Price sanity
- No impossible bars, no zero or negative prices, no frozen-price stretches in the regular session.
- 62 one-minute moves above 1% in 16 years. The largest (3.5%) is 2022-10-13 08:30, a CPI release. These are real moves.
- 1 automatic "bad tick" flag (2022-07-13 08:29, +73 bp then reversed). It is the minute before that day's CPI print. Treat it as suspect, not confirmed bad.

## 4. Timestamp checks
- **Bar labels:** on 8:30 economic-release days, the big bar is the 08:30 bar in about 90% of cases in every year. So bars are stamped with their start minute, consistently from 2010 to 2026.
- **Hour shifts:** from 2019, the 15:59 close beats the 14:59 close as a match for the official 16:00 close on 93.8% of days (median 1.8 bp vs 18.2 bp). Only 2 days (2022-05-18, 2026-06-26) look odd, and no consecutive-day clusters (which would mean a shifted week) remain after the loader's correction. Before 2019, 13 isolated days match another hour better. They are mostly quarterly expiry Fridays, where the closing auction differs from the last futures print.

## Recommended handling in backtests
```python
import pandas as pd
nsx = pd.read_parquet("data/free/NSXUSD_1min_histdata.parquet")
clean = nsx.loc["2019-01-01":]                                     # index-level regime
clean = clean[~((clean.index >= "2023-01-19") & (clean.index < "2023-12-01"))]   # drop the 2023 hole (conservative)
```
- Don't assume continuous minutes. Re-index to a session calendar, or use bar-to-bar returns only within the same session.
- For futures-specific work (the 16:15–17:00 trading, real volume, roll handling), use Databento NQ instead.

## Files
| File | Contents |
|---|---|
| `rth_completeness_by_day.csv` | Expected vs present regular-session minutes for every trading day |
| `rth_completeness_year_month.csv` | Data behind the heatmap |
| `rth_missing_minutes_year_hour.csv` | Where missing minutes fall, by year and hour |
| `gaps_over_2h.csv` | All intra-session gaps over 2 hours |
| `daily_vs_ndx.csv` | Daily OHLC vs ^NDX, with differences in bp |
| `close_deviation_over_25bp.csv` | Full days whose close differs from ^NDX by more than 25 bp |
| `possible_shifted_days.csv`, `suspect_spikes.csv` | Candidates from the timing and bad-tick checks |
| `completeness_heatmap.png`, `close_vs_ndx.png` | Charts |
