# midday_range_fade: **FAIL**

On days whose 09:30-11:30 range is below the median of the previous 20 sessions, fade the first 5-minute close outside the 11:00-11:30 range back to its midpoint; stop 0.5 x the range height beyond it, entries 11:30-13:30, flat by 14:00. Lunch hours on quiet days are mean-reverting.

**Session:** us. **Evaluated:** 2026-10-28 01:35 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ❌ fail |
| G4_addon | ❌ fail |
| G5_robustness | ❌ fail |

**Neighbours:** 0 of 5 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 172 |
| Win rate | 37% |
| Profit factor | 1.00 (first 12m 1.08, last 6m 0.87) |
| Net P&L | −$138 |
| t-stat (required) | -0.79 (2.43) |
| Max drawdown | −$9,227 |
| Two-month blocks positive | 2 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| −$1,530 | −$3,540 | −$836 | −$1,578 | $1,586 | $8,294 | −$1,380 | −$870 | −$285 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $4,887 in the first 12 months, −$3,448 in the last 6.
- **Idea trades actually taken:** 69.
- **Max drawdown:** −$28,726 → −$29,379.
