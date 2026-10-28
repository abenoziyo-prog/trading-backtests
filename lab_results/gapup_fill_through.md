# gapup_fill_through: **FAIL**

On a gap-up of at least 0.10 x previous range, when price first trades back to the previous close 10+ minutes after the open, short at the close of that minute; stop 0.5 x gap above the previous close, target 0.5 x previous range below it. A late fill means the gap-up buyers are trapped: the fill turns into a breakdown instead of support.

**Session:** us. **Evaluated:** 2026-10-28 01:35 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ❌ fail |
| G4_addon | ❌ fail |
| G5_robustness | ✅ pass |

**Neighbours:** 3 of 4 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 65 |
| Win rate | 38% |
| Profit factor | 1.19 (first 12m 1.39, last 6m 0.99) |
| Net P&L | $10,123 |
| t-stat (required) | 1.02 (2.43) |
| Max drawdown | −$11,896 |
| Two-month blocks positive | 5 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| −$7,303 | −$316 | $278 | $7,254 | $3,320 | $7,137 | −$1,104 | −$2,376 | $3,234 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $11,447 in the first 12 months, −$3,043 in the last 6.
- **Idea trades actually taken:** 63.
- **Max drawdown:** −$28,726 → −$31,112.
