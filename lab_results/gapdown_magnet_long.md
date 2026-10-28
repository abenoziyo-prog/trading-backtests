# gapdown_magnet_long: **FAIL**

If the previous session gapped down by at least 0.10 x its previous range and never traded back to the close before the gap, go long at the close of today's first 5-minute candle with that unfilled close as the target; stop 0.25 x previous range; flat at 16:00. An open gap below a prior close acts as a magnet: shorts from the gap cover into it.

**Session:** us. **Evaluated:** 2026-10-28 01:35 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ❌ fail |
| G4_addon | ❌ fail |
| G5_robustness | ❌ fail |

**Neighbours:** 0 of 4 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 60 |
| Win rate | 42% |
| Profit factor | 1.33 (first 12m 2.66, last 6m 0.19) |
| Net P&L | $21,428 |
| t-stat (required) | 1.17 (2.43) |
| Max drawdown | −$32,928 |
| Two-month blocks positive | 6 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| $14,256 | $7,686 | $2,107 | $13,931 | $2,273 | $9,887 | −$2,744 | −$18,273 | −$7,695 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $31,492 in the first 12 months, −$16,017 in the last 6.
- **Idea trades actually taken:** 33.
- **Max drawdown:** −$28,726 → −$31,086.
