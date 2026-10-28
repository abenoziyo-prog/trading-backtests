# evening_fade_big_day: **FAIL**

When the previous US session's range was at least 1.5 x the average of the 20 before it, fade that session's direction (open -> close) at the 18:00 reopen; stop 0.25 x that range, exit at 20:00. After an outsized day, the first evening hours tend to give some of the move back.

**Session:** overnight. **Evaluated:** 2026-10-28 01:35 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ✅ pass |
| G4_addon | ❌ fail |
| G5_robustness | ❌ fail |

**Neighbours:** 0 of 4 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 36 |
| Win rate | 42% |
| Profit factor | 1.33 (first 12m 1.10, last 6m 1.66) |
| Net P&L | $7,167 |
| t-stat (required) | 0.38 (2.43) |
| Max drawdown | −$6,157 |
| Two-month blocks positive | 6 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| $0 | $382 | $961 | $3,814 | −$524 | −$3,363 | $3,882 | $237 | $1,777 |

**Added to the live overnight baseline (lowest priority, one position at a time):**
- **Change in net P&L:** −$5,541 in the first 12 months, $5,897 in the last 6.
- **Idea trades actually taken:** 36.
- **Max drawdown:** −$19,128 → −$19,970.
