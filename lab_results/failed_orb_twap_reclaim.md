# failed_orb_twap_reclaim: **FAIL**

After a 5-minute close outside the 15-minute opening range, a later 5-minute close back across the session TWAP marks a failed breakout: trade toward the other side of the range, stop at the extreme made since the breakout, entries until 12:00. Trapped breakout traders fuel the reversal.

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
| Trades (18 months) | 278 |
| Win rate | 50% |
| Profit factor | 0.95 (first 12m 0.82, last 6m 1.16) |
| Net P&L | −$11,553 |
| t-stat (required) | -0.67 (2.43) |
| Max drawdown | −$51,315 |
| Two-month blocks positive | 4 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| $9,130 | −$780 | −$3,856 | $196 | −$22,786 | −$8,061 | $9,972 | $8,363 | −$3,732 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** −$24,013 in the first 12 months, −$3,919 in the last 6.
- **Idea trades actually taken:** 135.
- **Max drawdown:** −$28,726 → −$33,129.
