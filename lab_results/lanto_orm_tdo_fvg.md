# lanto_orm_tdo_fvg: **FAIL**

Trade the direction of the New York Opening Range Move (sweep-and-reclaim or break-and-hold of the overnight range), only from the discount/premium side of the True Day Open, on a 1-minute FVG retest toward PDH/PDL. Lanto's higher-timeframe bias is dropped: in the grid it reduced performance.

**Session:** us. **Evaluated:** 2026-10-27 01:36 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ❌ fail |
| G4_addon | ✅ pass |
| G5_robustness | ❌ fail |

**Neighbours:** 2 of 8 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 150 |
| Win rate | 11% |
| Profit factor | 1.81 (first 12m 1.90, last 6m 1.64) |
| Net P&L | $21,150 |
| t-stat (required) | 1.20 (2.00) |
| Max drawdown | −$8,076 |
| Two-month blocks positive | 5 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| $1,288 | −$1,438 | −$2,163 | −$2,293 | $390 | $19,327 | $4,161 | $5,468 | −$3,590 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $4,072 in the first 12 months, $4,037 in the last 6.
- **Idea trades actually taken:** 89.
- **Max drawdown:** −$28,726 → −$27,852.
