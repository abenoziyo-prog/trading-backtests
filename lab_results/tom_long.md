# tom_long: **FAIL**

Long at the 09:30 open on the last trading day of the month and the first 3 of the next, exit 16:00, stop 0.5 x previous range: month-end flows concentrate index gains in these days (Lakonishok & Smidt 1988; McConnell & Xu 2008).

**Session:** us. **Evaluated:** 2026-10-29 01:55 UTC (NSX 2025-04-10 → 2026-10-09; 1 NQ contract, $20/pt, 1 pt round trip, no slippage).

| Gate | Result |
|---|---|
| G1_sample | ✅ pass |
| G2_edge | ❌ fail |
| G3_consistency | ✅ pass |
| G4_addon | ✅ pass |
| G5_robustness | ❌ fail |

**Neighbours:** 2 of 4 pass G3.

| Metric | Value |
|---|---|
| Trades (18 months) | 72 |
| Win rate | 57% |
| Profit factor | 1.88 (first 12m 1.85, last 6m 1.94) |
| Net P&L | $77,653 |
| t-stat (required) | 2.20 (2.48) |
| Max drawdown | −$13,570 |
| Two-month blocks positive | 8 of 9 |

**Two-month blocks:**

| Apr 25 | Jun 25 | Aug 25 | Oct 25 | Dec 25 | Feb 26 | Apr 26 | Jun 26 | Aug 26 |
|---|---|---|---|---|---|---|---|---|
| $2,940 | $450 | $3,540 | $5,870 | −$9,790 | $44,983 | $6,864 | $15,358 | $7,440 |

**Added to the live us baseline (lowest priority, one position at a time):**
- **Change in net P&L:** $26,485 in the first 12 months, $23,861 in the last 6.
- **Idea trades actually taken:** 72.
- **Max drawdown:** −$28,726 → −$22,345.
