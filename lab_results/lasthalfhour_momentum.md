# lasthalfhour_momentum: **FAIL**

At 15:30 ET go long if NQ is above the previous close at 10:00 ET (overnight + first half hour positive), short if below; exit at the 16:00 close. Stop 0.15 x previous US range. Late-day rebalancing and hedging flows tend to follow the morning's direction.

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
| Trades (18 months) | 372 |
| Win rate | 45% |
| Profit factor | 1.07 (first 12m 0.85, last 6m 1.55) |
| Net P&L | $10,055 |
| t-stat (required) | 0.55 (2.43) |
| Max drawdown | −$19,135 |
| Two-month blocks positive | 5 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| −$7,627 | $4,724 | −$2,851 | −$2,207 | −$8,827 | $2,791 | $8,799 | $11,400 | $3,853 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** −$3,106 in the first 12 months, $19,473 in the last 6.
- **Idea trades actually taken:** 224.
- **Max drawdown:** −$28,726 → −$27,819.
