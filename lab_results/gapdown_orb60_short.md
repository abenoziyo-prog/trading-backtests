# gapdown_orb60_short: **FAIL**

On gap-down opens (open more than 0.10 x previous range below the previous close), short the first fresh close below the 60-minute opening range; stop at the range midpoint, target 1R. Down-day momentum should carry breakdowns further than on normal days.

**Session:** us. **Evaluated:** 2026-10-24 09:05 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ✅ pass |
| G3_consistency | ❌ fail |
| G4_addon | ✅ pass |
| G5_robustness | ❌ fail |

**Neighbours:** 1 of 5 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 65 |
| Win rate | 63% |
| Profit factor | 1.69 (first 12m 1.55, last 6m 1.98) |
| Net P&L | $34,937 |
| t-stat (required) | 1.90 (1.75) |
| Max drawdown | −$8,539 |
| Two-month blocks positive | 5 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| $14,291 | $2,711 | −$5,508 | $10,007 | −$285 | −$2,962 | $6,949 | $10,641 | −$906 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $16,764 in the first 12 months, $12,350 in the last 6.
- **Idea trades actually taken:** 61.
- **Max drawdown:** −$28,726 → −$28,972.
