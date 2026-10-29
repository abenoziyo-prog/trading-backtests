# noise_long: **FAIL**

Intraday momentum (Zarattini, Aziz & Barbon 2024): when a 5-minute candle closes above max(open, previous close) x (1 + 1.5 x the average absolute move from the open at that minute over the previous 14 sessions), the move tends to continue. Long only; stop 0.30 x previous range, target 2R, else exit 16:00. One trade a day. Profitable at every candle size in 2016-2024, the 2024-25 train year and the unseen 2025-26 test.

**Session:** us. **Evaluated:** 2026-10-29 01:55 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ✅ pass |
| G4_addon | ❌ fail |
| G5_robustness | ✅ pass |

**Neighbours:** 4 of 5 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 90 |
| Win rate | 53% |
| Profit factor | 1.43 (first 12m 1.10, last 6m 2.14) |
| Net P&L | $35,340 |
| t-stat (required) | 1.62 (2.48) |
| Max drawdown | −$13,465 |
| Two-month blocks positive | 7 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| −$3,295 | $10,159 | $2,916 | −$7,218 | $2,396 | $672 | $750 | $14,460 | $14,500 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $1,348 in the first 12 months, −$137 in the last 6.
- **Idea trades actually taken:** 21.
- **Max drawdown:** −$28,726 → −$30,936.
