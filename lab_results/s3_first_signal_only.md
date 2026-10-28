# s3_first_signal_only: **FAIL**

S3 (short a 1-minute close above the London high) only on its first signal of the day. Later signals the same day add risk but almost no profit: once the London high has failed to hold price, the level has lost its meaning.

**Session:** us. **Evaluated:** 2026-10-28 01:35 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ✅ pass |
| G4_replace | ❌ fail |
| G5_robustness | ✅ pass |

**Neighbours:** 1 of 1 pass G3.

**Replacing S3 in the live book:**
- first12: net $135,291 → $138,257; net / max drawdown 9.53 → 8.55.
- last6: net $68,074 → $55,310; net / max drawdown 2.51 → 2.18.

| Metric | Value |
|---|---|
| Trades (18 months) | 260 |
| Win rate | 55% |
| Profit factor | 1.28 (first 12m 1.21, last 6m 1.37) |
| Net P&L | $50,248 |
| t-stat (required) | 1.64 (2.43) |
| Max drawdown | −$14,525 |
| Two-month blocks positive | 6 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| $7,533 | −$5,909 | $10,415 | −$2,703 | $9,987 | $3,960 | −$55 | $21,841 | $5,179 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $2,966 in the first 12 months, −$12,764 in the last 6.
- **Idea trades actually taken:** 142.
- **Max drawdown:** −$28,726 → −$25,334.
