# Strategy lab

New trading ideas are tested here before they can reach the live paper bot. Every idea faces the same data, rules and gates, so noise can't slip through.

## How an idea moves through the lab
1. **Write it:** add `ideas/<short_name>.py` (copy `ideas/_template.py`). It defines `SESSION` (`us` or `overnight`), a one-paragraph `HYPOTHESIS`, `trades(ctx, **params)` and, ideally, `NEIGHBORS` (nearby parameter sets).
2. **Open a pull request.** GitHub Actions (`.github/workflows/lab.yml`) runs the harness on the changed ideas and posts the report on the pull request.
3. **Nightly** (weekdays 22:30 UTC): ideas merged into `main` that haven't been evaluated yet are run. The results, the leaderboard and the registry are published to the **`lab` branch**. Nightly runs never commit to `main`, so they never redeploy the bot.
4. **PASS** = a candidate for **shadow trading** in the live bot: logged, never trading, for 2–4 weeks of new data. Promotion to the live books is always a pull request you merge.

**Locally:**
```bash
python -m lab.harness ideas/my_idea.py
```
```bash
python -m lab.harness --all
```

## The gates (fixed in advance; `lab/gates.py`)
| Gate | Rule | Why |
|---|---|---|
| G1 sample | ≥ 30 trades in 18 months, ≥ 10 in the last 6 | Too few trades can't be judged |
| G2 edge | PF ≥ 1.15 and t-stat ≥ 1.5 + 0.25 × log2(ideas tested so far) | The more ideas tried, the more must be demanded of each (luck correction) |
| G3 consistency | PF > 1 in both the first 12 and the last 6 months, and profitable in ≥ 6 of 9 two-month blocks | One lucky stretch isn't an edge |
| G4 add-on | Added to the live baseline (lowest priority, one position at a time), it must raise net P&L in both periods without deepening the max drawdown by more than 20% | It must improve what we already trade |
| G5 robustness | If `NEIGHBORS` are given: ≥ 60% of them pass G3 | A real edge survives small parameter changes |

**Baselines** are replayed with the live engines: the US system R with the live exit rules, and the overnight tactical book.

**Data:** NSX 1-minute bars 2025-04-10 → 2026-10-09 (CI uses the snapshot on the `data` branch).

**Rules:** as approved (1 NQ contract, $20/pt, 1 pt round trip, no slippage; same-minute stop and target = stop).

## Why the gates are strict
On 2026-10-22 a re-optimization over about 26,000 strategy × condition combinations looked excellent in-sample, then broke even on unseen months. Generating ideas is cheap; the gates and shadow trading are what make the lab worth running.
