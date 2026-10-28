# drift_trend_gate: **FAIL**

Take the overnight drift long only when the previous US close is above its 20-session average. The drift is mostly the market's upward bias; its losses cluster in down-trends (Feb-Mar 2026: -$31K).

**Session:** overnight. **Evaluated:** 2026-10-28 01:35 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ✅ pass |
| G4_replace | ❌ fail |
| G5_robustness | ✅ pass |

**Neighbours:** 2 of 2 pass G3.

**Replacing DRIFT in the live book:**
- first12: net $61,278 → $61,329; net / max drawdown 1.61 → 3.29.
- last6: net $68,969 → $34,476; net / max drawdown 3.23 → 1.06.

| Metric | Value |
|---|---|
| Trades (18 months) | 244 |
| Win rate | 45% |
| Profit factor | 1.55 (first 12m 1.59, last 6m 1.48) |
| Net P&L | $95,805 |
| t-stat (required) | 2.30 (2.43) |
| Max drawdown | −$32,621 |
| Two-month blocks positive | 6 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| $17,541 | $23,818 | $20,493 | −$2,871 | −$2,170 | $4,518 | $27,085 | −$2,006 | $9,397 |

**Added to the live overnight baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $50 in the first 12 months, −$34,493 in the last 6.
- **Idea trades actually taken:** 244.
- **Max drawdown:** −$38,063 → −$32,621.
