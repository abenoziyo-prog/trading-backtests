# GEX, option flow and VIX: how we use them, and how we prove it first

Status: **pre-registered 2026-10-27, no results yet.** Nothing here becomes a trading rule until it passes the tests below. Each definition is frozen. A change means a new dated version of this file, and the earlier version keeps counting toward the number of tests.

## 1. The data we have (and do not have)

| Source | What | When | File |
|---|---|---|---|
| `bots/gex_snapshot.py` | Full chains, up to 30 days, ~60 strikes each side: QQQ, NDX, SPY, SPX. Net GEX, gamma flip, call/put walls, 0DTE GEX | 09:20 and 16:20 ET | `data/schwab/gex/` (`gex_daily.csv` + raw parquet) |
| `bots/option_flow_capture.py` | Near-expiry chains (0 to 3 days, ~40 strikes each side) for the same four symbols. Per contract: cumulative volume, last trade (price, size, time), bid/ask, IV, greeks, OI | Every 5 min, 09:30 to 16:15 ET | `data/schwab/flow/<SYM>/<day>/<HHMM>.csv.gz` |
| `bots/schwab_archiver.py` | 1-minute bars: /NQ, QQQ, /ES, SPY | Daily, 18:30 ET | `data/schwab/*_1min.parquet` |

What Schwab does **not** give us, and what that means:

- **There are no option trades (time & sales).** Flow is reconstructed from the change in each contract's cumulative volume between 5-minute captures. We know how many contracts traded, but not who started the trade.
- **Open interest does not change during the day.** The exchanges (OCC) publish it once, overnight. The OI in every intraday capture is the previous close's figure. So "OI change during the day" cannot be observed directly. Two observable substitutes:
  1. **Intraday volume** at each strike (the 5-minute captures).
  2. **The next morning's ΔOI against the previous day's volume.** This is the *opening ratio* (ΔOI / volume). Near +1 means the day's trades opened new positions. Near −1 means they closed old ones. It tells us whether a wall built during the day is real positioning or just churn.
- **There is no history.** Every test starts from the first capture (2026-10-28). The one shortcut is a paid historical-GEX source (FlashAlpha Alpha covers QQQ/SPY from 2017). It would let us test the daily-level hypotheses (R, W) on the 18 months of NQ minutes we already have, but not the flow hypotheses (F).

Who started a trade is estimated two ways in `src/quant/live/flow.py`. Both are reported, and neither is trusted until F0 passes:

- **tick:** the contract's last trade at the capture, compared with that capture's bid and ask. At or above the ask counts as bought, at or below the bid as sold.
- **iv:** the sign of the change in implied volatility while the contract traded.

Sign convention (SqueezeMetrics "naive GEX"): dealers are assumed long the calls and short the puts that customers hold. This is an assumption about the sign. Every regime result is also reported with the sign of puts flipped, so that it is visible if the conclusion depends on the assumption.

## 2. Mapping levels to /NQ and /ES

Levels are expressed in the index/ETF and converted at each capture:

- **QQQ level → NQ:** multiply by the live ratio NQ / QQQ, using the NQ 1-minute close at the capture minute and the QQQ spot in the capture.
- **NDX level → NQ:** add the live basis (NQ − NDX).
- **SPY/SPX → ES:** the same two methods.

QQQ and NDX walls usually differ by a few NQ points. A wall counts as **confirmed** when both map within 0.1% of each other.

## 3. Hypotheses

Each hypothesis is defined against a **placebo**, because every strike is a round number and price reacts to round numbers anyway. A level matters only if it beats other strikes at the same distance from price.

### Regime (one observation per day; uses the 09:20 snapshot)

| ID | Hypothesis | Measure | Control |
|---|---|---|---|
| R1 | With positive net GEX, the NQ US-session range is smaller | RTH range / ATR(20) | Same measure in the negative regime |
| R2 | With positive net GEX, the session mean-reverts; with negative, it trends | Variance ratio VR(12) of 5-min returns; lag-1 autocorrelation | By regime |
| R3 | Distance below the flip predicts the size of the move | \|close − open\| / ATR vs spot-vs-flip % | Rank correlation |
| R4 | SPX and NDX regimes combined: both negative gives the largest NQ ranges. When they disagree, NQ's beta to ES changes | Range by the four combinations; NQ/ES beta per day | — |

**Historical check, 2026-10-28** (`scripts/gex_regime_history.py`, `reports/gex/GEX_REGIME_HISTORY.md`). S&P GEX from
SqueezeMetrics (free, daily since 2011) stands in for the Nasdaq. The rules were pre-registered in the script, with
design 2011–2020 and the 2021–2026 holdout judged once.
- **R1 passes.** With the previous day's GEX in the bottom third of its 252-day range, NQ's US-session range was 1.23×
  the top third's, measured relative to the VIX expected move (holdout t = +7.3; design 1.34×, t = +11.9). Negative GEX
  vs positive gave 1.17× (t = +4.4). The same held for range / ATR20. So GEX says something about the day's size that
  VIX alone does not.
- **R2 fails.** The trend vs mean-reversion measures, VR(12) and |close − open| / range, differ little and not
  significantly. Low gamma makes days bigger but not more directional.
- **Effect by year:** present in 12 of 15 years. It is weak in 2023, 2024 and 2026 (to date) and strong in 2018 and 2025.
Live use stays pending: a range expectation (stops, targets, size) is the natural use, and it needs its own
pre-registered test against the live strategies.

### Walls and pins (intraday levels)

| ID | Hypothesis | Measure | Control |
|---|---|---|---|
| W1 | Price is "stuck" near walls | Share of RTH minutes within ±0.15% of the call or put wall | Placebo strikes at the same distance from the open (other side; low-GEX strikes) |
| W2 | Walls reject on the first touch | First touch: does price revert 0.3% before it extends 0.3% through? | Same rule at placebo strikes |
| W3 | Large 0DTE gamma pins the close | \|close − max-0DTE-gamma strike\| on days in the top third of 0DTE GEX share; distance at 15:00 vs at the close | Nearest other strike; bottom-third days |
| W4 | A wall confirmed by OI build is stronger | W1/W2 split by the strike's opening ratio and ΔOI | Walls with no build |
| W5 *(exploratory)* | The intraday 0DTE wall moves (from volume-weighted GEX) before price does | Wall shift direction at t vs NQ return t+1..t+6 | Shuffled shifts |

### Flow → price (the hard part)

The obstacle is two-way causality. Options react to price at least as much as price reacts to options. So the *same-interval* correlation proves nothing about prediction. It is used only to check that the trade-side estimates carry information.

| ID | Hypothesis | Measure | Pass |
|---|---|---|---|
| F0 | The side estimates are informative | Same-interval correlation of signed customer delta flow with the return; agreement of the tick and iv estimates | Correlation > 0, t > 3; agreement > 60%. **If F0 fails, F1/F3 use unsigned volume only** |
| F1 | Signed customer delta flow at t predicts the NQ return at t+1..t+3 | Regression of r(t+1..t+3) on flow(t), controlling for r(t), r(t−1). Standard errors clustered by day | Coefficient t > 3 on the later sessions (holdout) |
| F2 | The hedging dealers are forced into continues the move in negative gamma and fades it in positive gamma | Return autocorrelation at the 5-min level, split by the sign of net GEX at the start of the interval | Opposite-signed autocorrelations, both significant |
| F3 | A surge in 0DTE share together with a one-sided flow imbalance precedes range expansion | Realized range over the next 30 min | Intervals without the surge |
| F4 *(exploratory)* | Customer flow in SPX that diverges from flow in NDX/QQQ leads NQ against ES | NQ − β·ES return at t+1 | — |

### VIX (added 2026-10-27, same rules)

The definitions are in `src/quant/live/vix_tags.py`, and the same code tags live trades, shadow trades and the backtest.
- **Sources:**
  - `bots/schwab_archiver.py` keeps 1-minute bars of $VIX, $VIX3M, $VIX9D and $VVIX.
  - The 5-minute capture refreshes today's bars of the same four, so same-day trades get tagged.
- **Buckets (frozen):**
  - VIX level at the prior close: < 15, 15–20, 20–25, ≥ 25;
  - VIX move since the prior close: down > 5%, flat, up > 5%;
  - NQ/VIX coupling, the 60-minute correlation of 1-minute changes before entry: coupled (< −0.6), loose, decoupled (≥ −0.2);
  - term structure: VIX/VIX3M ≥ 1 = backwardation.

**First look (exploratory, on the build window; `scripts/vix_strategy_study.py`, `reports/vix/VIX_STUDY.md`):**
- Daily NQ/VIX correlation is −0.75. The rolling 20-day correlation has a median of −0.80 and a maximum of −0.02.
- The prior VIX close correlates +0.43 with NQ's session range: about 0.9% below VIX 15, about 1.9% at 25 and above.
- 5-minute changes correlate −0.78, but neither series leads the other at 5 minutes (|corr| < 0.05).
- The live strategies lost money only with VIX < 15: 186 trades, PF 0.92, spread over four separate episodes. The best results came at VIX 20–25 (PF 1.90).

| ID | Hypothesis | Measure | Control |
|---|---|---|---|
| V1 | The live strategies earn less with VIX < 15 | Avg P&L per trade and PF by level, live and shadow | 15–25 |
| V2 | When NQ and VIX decouple (both up, or correlation ≥ −0.2), the next 30–60 min carry an unusual move | NQ realized range in the next 60 min | Coupled intervals |
| V3 | Backwardation (VIX/VIX3M ≥ 1) marks trend days: bigger ranges, worse mean reversion | Range/ATR, VR(12) | Contango days |
| V4 | GEX and VIX interact: negative gamma with VIX rising is when breakouts work best | ORB-C/GF-1 P&L by regime × VIX move | Other combinations |
| V5 *(exploratory)* | A VIX9D/VIX spike (short-dated fear) precedes intraday reversals | NQ return in the next 30 min after the spike | Matched non-spike times |

The count of hypotheses rises to 20 (6 exploratory). The required t is about 3.0.

### 0DTE dealer gamma vs intraday price (section Z, added 2026-10-28)

Same-day expiries only, from our own 5-minute captures over the most recent sessions. Paid history was declined, so
this builds forward. Rules, definitions and statistics are in `scripts/zero_dte_study.py`; the gamma estimate is in
`quant.live.flow.dealer_gamma_0dte`.

**Dealer gamma, `g_total`** = opening open interest with the naive sign, plus −(customers' net contracts bought since
the open, tick proxy) × gamma. The open-interest-only, tick-only and IV-proxy versions are reported alongside. State:
dealers long 0DTE gamma if `g_total` > 0, short if < 0. Size: |`g_total`| percentile within the same half hour.

| ID | Hypothesis (on /NQ after each capture, 10:00–15:30) |
|---|---|
| Z1 | The 30-minute range is larger when dealers are short 0DTE gamma than when long |
| Z2 | Moves continue over the next 15 minutes when dealers are short and reverse when long |
| Z3 | Size matters: within each state, the top third of \|gamma\| shows Z1's effect more strongly than the bottom third |
| Z4 | On dealer-long days, price closes nearer the 14:30 pin strike than at 14:30, more than for strikes two away |
| Z5 | The hedge flow that gamma forces (−gamma × last 15-minute move) points the way of the next 15 minutes |

**Verdict rules:**
- Verdicts start at 40 sessions (about late December 2026).
- Sessions 1–20 are the design sample and are used only for data checks.
- Sessions 21 onward are judged: PASS = the predicted direction with |t| ≥ 3.0 across sessions.

## 4. How a hypothesis passes

1. **Frozen definitions.** The ones above; any change is versioned and dated.
2. **Design and holdout split.**
   - The first 40 sessions may only be used to check data quality and to settle the definitions marked exploratory.
   - Confirmatory tests run on sessions after that.
   - Daily hypotheses (R, W) need **at least 60 sessions** for a first verdict (around late January 2027, counting from 2026-10-28). Interval hypotheses (F) have about 80 observations a day but must be clustered by day, so they also need 40+ sessions.
3. **Number of tests counted.** 20 hypotheses above (6 exploratory). The required t is corrected for all of them, in the same way as the lab's batch correction: about 3.0 for 20 at 5%.
4. **Placebos are mandatory** for every level test (W). Both sign conventions are reported for every regime test (R).
5. **Value test before any use.** A passing hypothesis becomes a filter or a level only if it improves a live strategy. Net ≥ base − 5% **and** net/maxDD ≥ base in both halves: the lab's G4 replace gate. Until then it runs in the shadow book only.

## 5. Where it can plug in once proven

- **Regime filter (R1/R2):**
  - mean-reversion ideas (midday fade, S3) only in positive gamma;
  - breakout ideas (ORB-C, GF-1) sized up only in negative gamma or below the flip.
- **Walls as levels (W1–W4):** targets for longs into the call wall, stops beyond the put wall, fades of the first touch. Confirmed walls (QQQ and NDX agree, OI built) only.
- **Close pin (W3):** a late-day mean-reversion idea toward the 0DTE pin strike, 15:00–15:55.
- **Flow (F1/F3):** a 5-minute filter that blocks entries against strong one-sided customer delta flow, or a trigger for range expansion.

## 6. Running it

- **Pull the cloud data:** `python scripts/pull_cloud_files.py data/schwab/flow/ data/schwab/gex/`. It needs the Northflank port-forward; see the script's header.
- **Build per-interval flow:** `quant.live.flow.intervals(load_day(...))` gives volumes, delta dollars (unsigned and signed by each estimate), 0DTE share, net GEX and forced dealer hedging.
- **Get the opening ratio:** `oi_change(today, next_day)`.
- **Storage:** captures take about 7 MB a day, about 150 MB a month in the PostgreSQL add-on. The runner logs the database size each evening. Move old months off before the add-on fills up.
