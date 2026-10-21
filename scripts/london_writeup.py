"""Write reports/london/REPORT.md from the London research outputs.

    python scripts/london_writeup.py     # after london_research.py, london_model.py, london_report.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

O = Path(__file__).resolve().parents[1] / "reports" / "london"
FEATS = ["asia_range_R", "pre_range_R", "or_range_R", "prev_ret_al", "close_loc_al", "gap_al", "pos_pd_al", "pre_sweep_al",
         "vix_prev", "vix_chg", "dxy_al", "R0_pct", "entry_hour", "monday", "friday"]
DESC = {"asia_range_R": "Asia range (20:00–00:00) ÷ R0", "pre_range_R": "Pre-London range (00:00–02:00) ÷ R0",
        "or_range_R": "London opening range (02:00–03:00) ÷ R0", "prev_ret_al": "Yesterday's US move (close − open) ÷ R0, × side",
        "close_loc_al": "Yesterday's close location in its range (−0.5…+0.5), × side", "gap_al": "Entry vs yesterday's close ÷ R0, × side",
        "pos_pd_al": "Entry vs yesterday's mid-range ÷ R0, × side", "pre_sweep_al": "Pre-London swept Asia high (+1) / low (−1), × side",
        "vix_prev": "VIX previous close", "vix_chg": "VIX previous-day change (%)", "dxy_al": "DXY change since 16:00 (%), × side",
        "R0_pct": "Yesterday's range as % of price", "entry_hour": "Entry time (hours after midnight ET)", "monday": "Monday (1/0)",
        "friday": "Friday (1/0)"}


def usd(x):
    return "–" if pd.isna(x) else (f"−${abs(x):,.0f}" if x < 0 else f"${x:,.0f}")


def pf(x):
    return "–" if pd.isna(x) else ("∞" if np.isinf(x) else f"{x:.2f}")


def main() -> None:
    g = pd.read_csv(O / "grid.csv")
    eqs, opt = pd.read_csv(O / "equations.csv"), pd.read_csv(O / "optimized.csv")
    tf, rob = pd.read_csv(O / "best_by_tf.csv", keep_default_na=False), pd.read_csv(O / "robust_results.csv")
    P = g.pivot_table(index=["family", "config", "tf"], columns="period", values=["trades", "profit_factor", "net_pnl"])

    def fam_top(fam, n=5):
        if fam not in P.index.get_level_values(0):
            return "_No settings._"
        x = P.loc[fam]
        x = x[(x[("trades", "opt: last 6m")] >= 25) & (x[("profit_factor", "opt: last 6m")] > 1)]
        x = x.sort_values(("net_pnl", "opt: last 6m"), ascending=False).head(n)
        if x.empty:
            return "_No setting had ≥ 25 trades and PF > 1 in the last 6 months._"
        L = ["| Setting | Candle | 6m trades | 6m PF | 6m net | Prior 12m PF | Prior 12m net |", "|---|---|---|---|---|---|---|"]
        for (cfg, c), r in x.iterrows():
            L.append(f"| `{cfg}` | {'time-based' if c == 0 else f'{c}m'} | {r[('trades', 'opt: last 6m')]:.0f} | "
                     f"{pf(r[('profit_factor', 'opt: last 6m')])} | {usd(r[('net_pnl', 'opt: last 6m')])} | "
                     f"{pf(r[('profit_factor', 'check: prior 12m')])} | {usd(r[('net_pnl', 'check: prior 12m')])} |")
        return "\n".join(L)

    def tf_md():
        L = ["| Family | Best setting (6m) | Candle | 6m trades | 6m PF | 6m net | Prior 12m PF | Prior 12m net | 18m net |",
             "|---|---|---|---|---|---|---|---|---|"]
        for (fam, base), x in tf.groupby(["family", "base"], sort=False):
            for c in x.tf.unique():
                y = x[x.tf == c].set_index("period")
                a, b, cc = y.loc["opt: last 6m"], y.loc["check: prior 12m"], y.loc["18m"]
                L.append(f"| {fam} | `{base.split('|', 1)[1]}` | {c} | {a.trades:.0f} | {pf(a.profit_factor)} | {usd(a.net_pnl)} | "
                         f"{pf(b.profit_factor)} | {usd(b.net_pnl)} | {usd(cc.net_pnl)} |")
        return "\n".join(L)

    def eq_md():
        fams = eqs.family.tolist()
        L = ["| Variable | " + " | ".join(fams) + " |", "|---|" + "---|" * len(fams)]
        L.append("| **Intercept b0** | " + " | ".join(f"{v:+.3f}" for v in eqs.b0) + " |")
        for f in FEATS:
            L.append(f"| {DESC[f]} | " + " | ".join(f"{v:+.3f}" for v in eqs[f"w_{f}"]) + " |")
        L.append("| **Trade if score >** | " + " | ".join(f"{v:+.3f}" for v in eqs.theta) + " |")
        return "\n".join(L)

    def opt_md():
        L = ["| Family | Version | Period | Trades | % Win | PF | Net P&L | Max DD | t |", "|---|---|---|---|---|---|---|---|---|"]
        for _, r in opt.iterrows():
            L.append(f"| {r.family} | {r.version} | {r.period} | {r.trades} | {r.win_rate * 100:.0f}% | {pf(r.profit_factor)} | "
                     f"{usd(r.net_pnl)} | {usd(r.max_drawdown)} | {r.t_stat:.2f} |")
        return "\n".join(L)

    def rob_md():
        L = ["| System | Window | Trades | Trades/day | Long / short | % Win | PF | Net P&L | Avg win | Avg loss | Max DD | Sharpe | t | Profitable days |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, r in rob[~rob.system.str.contains("standalone")].iterrows():
            nm = r.system.strip()
            nm = f"**{nm}**" if nm.startswith("Robust") else f"↳ {nm}"
            L.append(f"| {nm} | {r.window} | {r.trades} | {r.trades_per_day:.2f} | {r.long_trades} / {r.short_trades} | "
                     f"{r.win_rate * 100:.0f}% | {pf(r.profit_factor)} | {usd(r.net_pnl)} | {usd(r.avg_win)} | {usd(r.avg_loss)} | "
                     f"{usd(r.max_drawdown)} | {r.sharpe_daily_ann:.2f} | {r.t_stat:.2f} | {r.profitable_days_pct * 100:.0f}% |")
        return "\n".join(L)

    eqcands = "\n".join(f"- **{r.family}:** `{r.cand}`" for _, r in eqs.iterrows())
    s6 = opt[(opt.family == "LONDON SYSTEM") & (opt.period == "opt: last 6m")].iloc[0]
    s12 = opt[(opt.family == "LONDON SYSTEM") & (opt.period == "check: prior 12m")].iloc[0]
    r = rob.set_index(["system", "window"])
    R18, R6, RC = (r.loc[("Robust London system", w)] for w in ("18m", "6m (optimization period)", "prior 12m (check)"))
    d6 = opt[(opt.family == "DRIFT") & (opt.version == "base") & (opt.period == "opt: last 6m")].iloc[0]
    dc = opt[(opt.family == "DRIFT") & (opt.version == "base") & (opt.period == "check: prior 12m")].iloc[0]
    rep = f"""# NSX London-session strategies: research, multi-variable equation, 6-month optimization

Run with these scripts, in order: `scripts/london_research.py`, `london_model.py`, `london_report.py`, `london_writeup.py`. **NSX only, last 18 months (2025-04-10 → 2026-10-09).**

**Approved rules:**
- Multiple trades per day; if the stop and target are hit in the same minute, it counts as a stop.
- 1 NQ contract ($20/pt), 1 pt round trip, no slippage.
- Every London trade is **flat by 09:29 ET**, so it never overlaps the US system.

**Definitions** (all times ET):
- **Asia:** 20:00–00:00.
- **Pre-London:** 00:00–02:00.
- **London opening range:** 02:00–03:00.
- **R0:** yesterday's US range (1-day look-back).

**Periods:**
- **Optimization:** the last 6 months (2026-04-10 → 2026-10-09), as you asked.
- **Check:** the prior 12 months (2025-04-10 → 2026-04-09). The optimizer never saw them.

## Summary
- **What was tested:** 1,239 settings (5 strategy families × parameters × 1/2/3/5/10-minute candles), plus a 15-variable equation per family.
- **The multi-variable equation fits the last 6 months extremely well but doesn't carry over.**
  - The equation-filtered London system makes **{usd(s6.net_pnl)}, PF {pf(s6.profit_factor)}** on the 6 months it was fitted on.
  - With the same weights on the prior 12 months: **{usd(s12.net_pnl)}, PF {pf(s12.profit_factor)}**.
  - Across all settings, 6-month results don't predict the prior 12 (correlation −0.16).
- **One edge is real and holds in both periods: the European-open drift.** NSX rises on average in the early hours of European trading, as the NY Fed research found for S&P futures.
  - Long 02:00 → 03:00 averaged **+4.85 pts gross, +3.85 net** per day over 373 days.
  - The best version (long at 01:00, stop 0.25 × R0, exit 09:29) made {usd(d6.net_pnl)} in the last 6 months (PF {pf(d6.profit_factor)}) and {usd(dc.net_pnl)} in the prior 12 (PF {pf(dc.profit_factor)}).
- **Recommended: the robust London system.** It combines the drift long with a short-only London breakout of the Asia low, the only breakout setting that held on every candle size and in both periods.
  - **Results:** 18 months **{usd(R18.net_pnl)}, PF {pf(R18.profit_factor)}, max drawdown {usd(R18.max_drawdown)}**; last 6 months {usd(R6.net_pnl)} (PF {pf(R6.profit_factor)}); prior 12 months {usd(RC.net_pnl)} (PF {pf(RC.profit_factor)}).
  - **Caveat:** it's simpler and more stable, but not fully out of sample. The short-breakout cluster was picked partly because it held in both periods.

![London curves](robust_curves.png)

## 1. Research
- **European-open drift.** The NY Fed's *The Overnight Drift* (Boyarchenko, Larsen, Whelan, Staff Report 917, rev. 2022) finds that US equity futures' largest positive returns come **02:00–03:00 ET**, about 1.48 bp a day. The drift is positive in 20 of 23 years and significant on every weekday. It's stronger after US **sell-offs**, consistent with dealers unwinding inventory from the previous US close. ([NY Fed](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr917.pdf))
- **Asia-range sweeps and the London opening range on NQ.**
  - **Herman Trading (17 years of NQ):** London (02:00–05:00) sweeps the Asia high on 60% of days and the low on 49%. If pre-London already took the Asia high, London takes it again 77% of the time. ([Asia–London study](https://www.hermantrading.pro/backtest-library/asia-london-session-study-nq))
  - **Its playbook:** a large Asia range tends to flip the pre-London direction; a small one tends to follow it. ([playbook](https://www.hermantrading.pro/backtest-library/nq-london-session-playbook))
  - **TradingStats:** published break-rate statistics on the London range. ([London breakout stats](https://tradingstats.net/london-breakout-strategy/))
  - **Caveat:** these are probability studies, not P&L after costs.
- **London breakout.** The classic Asia-range breakout from FX. No published NQ backtest with costs was found. On EUR/USD the long side often lost. ([Quantified Strategies](https://www.quantifiedstrategies.com/london-breakout-strategies/), [overview](https://newyorkcityservers.com/blog/london-breakout-strategy))
- **"Judas swing."** London pushes through an Asia extreme, closes back inside, then reverses, typically 02:00–05:00 or 03:00–05:30 ET. There are no published performance numbers. ([rules](https://www.theinnercircletraders.com/ict-judas-swing/), [kill zones](https://chartinglens.com/blog/ict-kill-zones-guide))

## 2. The five strategy families (1,239 settings)
| Family | Logic | Parameters |
|---|---|---|
| DRIFT | European-open drift | entry 01:00 / 02:00; exit 03:00 / 05:00 / 09:29; long, reversal of yesterday's US move, or long only after a US down day; stop none / 0.25 / 0.5 R0 |
| ASIA_BO | London breakout of the Asia range | range 20:00–00:00 or 20:00–02:00; from 02:00 / 03:00; entries until 05:00 / 08:00; both / long / short; stop opposite side / midpoint; target 1R / 2R / 09:29 |
| ASIA_SWP | Asia-range sweep and reversal ("Judas") | same ranges; window 02:00–05:00 or 03:00–05:30; both / long / short; stop beyond the swept extreme + 0.05 R0; target range midpoint / opposite side / 1R / 2R |
| LON_OR | 02:00–03:00 opening-range breakout | from 03:00; entries until 05:00 / 08:00; both / long / short; stop opposite / midpoint; target 1R / 2R / 09:29 |
| PD_SWP | PDH / PDL sweep and reversal in London | 02:00–08:00; both / long / short; target 0.25 R0 / 0.5 R0 / previous close |

The breakout and sweep families ran on 1/2/3/5/10-minute candles. DRIFT is time-based, so candle size doesn't apply.

## 3. Best settings per family, ranked on the last 6 months, with the prior-12-month check
### DRIFT
{fam_top("DRIFT")}

### ASIA_BO
{fam_top("ASIA_BO")}

### ASIA_SWP
{fam_top("ASIA_SWP")}

### LON_OR
{fam_top("LON_OR")}

### PD_SWP
{fam_top("PD_SWP")}

## 4. Each family's best setting on 1 / 2 / 3 / 5 / 10-minute candles
{tf_md()}

## 5. The multi-variable equation (fitted on the last 6 months)
**Fitted on:**
{eqcands}

**Method:** each trade's R-multiple is regressed (ridge, λ = 10) on the 15 variables below, each standardized to z = (x − mean) / sd using the last 6 months. Variables marked "× side" are signed so that positive means "in favour of this trade".

**The equation:** score = b0 + Σ wᵢ·zᵢ. A trade is taken only when the score is above θ, with θ chosen to maximize 6-month net P&L while keeping at least 25 trades.

{eq_md()}

**Reading it:** a larger |w| means the variable mattered more in the last 6 months.
- **Strongest variables:** VIX (its level and its previous-day change) for DRIFT and LON_OR, the pre-London sweep direction for the breakouts, and Friday for the Asia sweep.
- **Signs flip between families,** which is a typical sign of fitting noise.

### Optimized results vs the check
{opt_md()}

**What the check shows:**
- **The equation lifts every family in the last 6 months**, but mostly not in the prior 12.
- **DRIFT and ASIA_BO do worse with the equation** out of sample than without it.
- **LON_OR is the exception:** it improves out of sample.
- **The combined equation system** drops from PF {pf(s6.profit_factor)} to {pf(s12.profit_factor)}.

## 6. Recommended: robust London system (5-minute candles)
**Rules** (fixed, no equation):
1. **DRIFT long:** buy at 01:00 ET, stop at 0.25 × yesterday's US range, exit at 09:29 ET.
2. **ASIA_BO short:** from 03:00 ET, the first 5-minute close below the 20:00–02:00 low, after the previous candle closed inside the range. Stop at the range midpoint; entries until 08:00; exit at 09:29.
3. **One position at a time.** In practice the short trades on days the drift long was stopped out, so the two complement each other: the long earns on the usual up-drift, and the short catches the down-days.

{rob_md()}

**On every candle size the system is consistent:**
- 18 months: $116–123K, PF 1.38–1.41.
- Prior 12 months: PF 1.19–1.24.
- Last 6 months: PF 1.65–1.73.

## Caveats
- **Some selection bias remains.** The drift is the only edge with outside evidence behind it (decades of S&P futures data). The short-breakout leg was chosen partly because it held in both periods, so it isn't fully out of sample.
- **Regime:** the last 6 months were unusually strong for the drift (PF {pf(d6.profit_factor)} vs {pf(dc.profit_factor)} before). Expect something nearer the prior-12-month level.
- **Fills:** overnight NQ liquidity is thinner than in the US session. No slippage is assumed, per your rules, but real 01:00 fills would cost something.
- **Not in the live bot yet.** It currently runs only the US system (09:30–16:00). Adding London would need the bot to trade from 01:00 ET.

## Files (`reports/london/`)
| File | Contents |
|---|---|
| `trades.parquet` | every trade of all 1,239 settings, with the 15 entry variables |
| `grid.csv` | every setting × period: trades, win %, PF, net, max drawdown, t-stat, minutes held |
| `top_by_family_6m.csv`, `best_by_tf.csv` | rankings and candle-size table |
| `equations.csv` | each family's equation: b0, weights, means, sds, θ |
| `optimized.csv`, `system_trades.csv` | equation results and trades (last 6m vs prior 12m) |
| `robust_results.csv`, `robust_trades.csv` | the recommended system, all windows |
| `london_curves.png`, `robust_curves.png` | P&L curves |
"""
    (O / "REPORT.md").write_text(rep)
    print("written", len(rep.splitlines()), "lines")


if __name__ == "__main__":
    main()
