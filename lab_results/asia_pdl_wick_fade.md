# asia_pdl_wick_fade: **FAIL**

During Asia (18:00-02:00 ET), when a 1-minute candle trades below the previous US low (PDL) but closes back above it, buy: a wick-only touch of PDL usually rejects. Stop 0.2 x previous range, hold to 09:29.

**Session:** overnight. **Evaluated:** 2026-10-24 09:05 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ✅ pass |
| G4_addon | ✅ pass |
| G5_robustness | ❌ fail |

**Neighbours:** 1 of 4 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 87 |
| Win rate | 33% |
| Profit factor | 1.54 (first 12m 1.17, last 6m 2.05) |
| Net P&L | $37,863 |
| t-stat (required) | 1.10 (1.50) |
| Max drawdown | −$13,602 |
| Two-month blocks positive | 7 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| −$3,276 | $5,366 | $697 | −$6,492 | $3,206 | $7,560 | $18,527 | $5,813 | $6,464 |

**Added to the live overnight baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $10,024 in the first 12 months, $34,616 in the last 6.
- **Idea trades actually taken:** 78.
- **Max drawdown:** −$19,128 → −$16,768.
