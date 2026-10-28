# nr_day_orb: **FAIL**

When the previous US session's range was the smallest of the last 4 (NR4), trade the first 5-minute close outside the 15-minute opening range in its direction; stop at the other side of the range, target 2R. Compression tends to release into a trend day.

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
| Trades (18 months) | 101 |
| Win rate | 42% |
| Profit factor | 0.76 (first 12m 0.73, last 6m 0.84) |
| Net P&L | −$29,904 |
| t-stat (required) | -0.53 (2.43) |
| Max drawdown | −$35,912 |
| Two-month blocks positive | 3 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| −$13,819 | −$2,205 | −$3,690 | −$14,248 | $1,899 | $8,333 | $10,691 | −$12,694 | −$4,171 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** −$2,468 in the first 12 months, $2,458 in the last 6.
- **Idea trades actually taken:** 49.
- **Max drawdown:** −$28,726 → −$28,769.
