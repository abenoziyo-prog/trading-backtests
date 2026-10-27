# lanto_canonical: **FAIL**

Lanto Trades' framework (higher-timeframe FVG/IFVG bias, overnight range, NY Opening Range Move, 1m FVG/IFVG entry to PDH/PDL) codified with the canonical settings fixed before testing.

**Session:** us. **Evaluated:** 2026-10-27 01:36 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ❌ fail |
| G4_addon | ❌ fail |
| G5_robustness | ❌ fail |

**Neighbours:** 0 of 8 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 246 |
| Win rate | 15% |
| Profit factor | 0.63 (first 12m 0.57, last 6m 0.81) |
| Net P&L | −$25,010 |
| t-stat (required) | -1.93 (1.90) |
| Max drawdown | −$27,710 |
| Two-month blocks positive | 2 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| −$6,659 | −$187 | −$5,245 | $2,816 | −$9,110 | −$3,456 | −$255 | −$3,673 | $759 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $529 in the first 12 months, −$7,025 in the last 6.
- **Idea trades actually taken:** 122.
- **Max drawdown:** −$28,726 → −$27,819.
