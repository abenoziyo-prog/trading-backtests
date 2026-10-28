# Strategy library additions (2026-10-28): TOM and NOISE-L

Two strategies from the 2026-10-28 research, written as exact rules. Both are in the **shadow book**: the bot runs them
on its live bars after each session and logs their trades in the status report, with **no alerts and no P&L in the
live books**. Promotion to a live paper book is a pull request you merge, by the pre-set rules in §4.

Research: `reports/new_ideas_2026-10-28/REPORT.md` (TOM), `reports/deep_research_2026-10-28/REPORT.md` (NOISE-L).
Code: `ideas/tom_long.py`, `ideas/noise_long.py`.

## Common rules (approved, unchanged)
- US session 09:30–16:00 ET; no entries after 15:30; flat at 16:00.
- 1 NQ contract, $20/pt, 1 pt round trip, no slippage. If the stop and target are hit in the same minute, it's a stop.
- **R0** = previous US session's range (PDH − PDL). **PDC** = previous US close.
- One trade per day per strategy; no re-entries.
- **Each runs as its own book (one contract each), never inside system R's one-position picker.** TOM enters at
  09:30 and would block system R on its days; NOISE-L buys the same trend days as ORB-C and L2.

## 1. TOM: turn-of-month long
| | Rule |
|---|---|
| Days | Last trading day of the month, and the first 3 trading days of the next (exchange calendar, known in advance) |
| Entry | Long at the 09:30 open |
| Stop | Entry − 0.50 × R0 |
| Target | None |
| Exit | Stop, else 16:00 close |
| Frequency | 4 days a month |

**Why:** month-end flows (pensions, payroll, rebalancing) concentrate index gains around the turn of the month
(Lakonishok & Smidt 1988; McConnell & Xu 2008).

## 2. NOISE-L: noise-boundary momentum, long only
| | Rule |
|---|---|
| Inputs | σ(m) = average of \|close(m) / open − 1\| at minute-of-day m over the previous 14 sessions |
| Upper boundary | max(today's 09:30 open, PDC) × (1 + 1.5 × σ(m)), at the candle's last minute m |
| Candles | 5 minutes, clock-aligned from 09:30 |
| Entry | Long at the close of the first 5-minute candle that closes above the boundary (candles ending by 15:30) |
| Stop | Entry − 0.30 × R0 |
| Target | Entry + 2 × 0.30 × R0 (2R) |
| Exit | Stop, target, else 16:00 close |
| Frequency | About 5 days a month |

**Why:** intraday time-series momentum (Zarattini, Aziz & Barbon 2024): moves that break out of the normal range for
that time of day tend to continue. The short side has a weaker edge historically and lost in the 2025–26 rally, so
it isn't traded.

## 3. Evidence (1 NQ, approved costs)
| | TOM | NOISE-L (5-min) |
|---|---|---|
| 12m (2025-10-10 → 2026-10-09): trades / PF / net | 48 / 2.18 / $70.7K | 61 / 1.40 / $25.6K |
| 8m (from 2026-02-10) | 32 / 3.37 / $74.6K | 42 / 1.82 / $30.4K |
| 6m (from 2026-04-10) | 24 / 1.94 / $29.7K | 31 / 2.14 / $29.7K |
| Longer history (never used to choose settings) | NDX 2010–2025: TOM days +6 bps vs other days, t < 1 | NSX 2016–2024: PF 1.44, $100.3K, 8 of 9 years positive (3-min) |
| 18m win rate / avg win / avg loss | 57% / $4,041 / −$2,839 | 53% / $2,435 / −$1,942 |
| 18m max drawdown / worst losing streak | −$13.6K / 5 | −$13.5K / 6 |
| Lab gates (2026-10-28) | FAIL: G2 (t 2.20 < 2.48), G5 (2 of 4 neighbours) | FAIL: G2 (t 1.62), G4 (overlaps system R) |

**Expectations:** size to the long history, not to the last 12 months. TOM's long-run edge is about +6 bps a day
(≈ $280 a trade), not the $1,470 of the last year. NOISE-L's long-run average is about $180 a trade.

## 4. Shadow period and promotion (fixed now)
- **Review date:** Fri 2026-12-11 (6 weeks), or after 20 shadow trades of a strategy, whichever comes later for that
  strategy (TOM: about 8 trades by then; review again at 20).
- **Promote to a live paper book (its own contract) only if all hold:**
  1. Shadow net P&L > 0.
  2. Shadow drawdown no deeper than the backtest's (TOM −$13.6K, NOISE-L −$13.5K).
  3. Shadow fills match the backtest rules (spot-check 5 trades against the bars).
- **Kill switch (live or shadow), same rule as the live books:** stop the strategy if its drawdown exceeds 1.5 × the
  backtest max drawdown (TOM −$20.4K, NOISE-L −$20.2K), or its losing streak exceeds the worst streak + 3 (TOM 8,
  NOISE-L 9).
- **Never** change parameters during the shadow period. A change means a new idea and a new test.
