# trading-backtests

Research and paper trading for Nasdaq-100 futures (/NQ), US session and overnight.

- **Research:** 1-minute backtests on NSX/NQ data (`scripts/`, shared code in `src/quant/`). Approved rules: US session
  09:30–16:00 ET with no entries after 15:30, 1 NQ contract at $20/pt, 1 pt round trip, no slippage, a stop and a target
  in the same minute counts as the stop.
- **Strategy lab:** pre-registered ideas (`ideas/`) judged by fixed gates (`lab/`, see `lab/README.md`). A GitHub Actions
  job runs new ideas nightly.
- **Paper bot:** `bots/nq_paper_bot.py` trades the live books on Schwab market data with simulated fills. It never sends
  orders. It runs 24/7 on Northflank (`deploy/NORTHFLANK.md`), next to the Schwab archiver, the GEX snapshot and the
  5-minute option and VIX capture (`bots/cloud_runner.py`). Each session close writes a status report (P&L, live vs
  backtest, kill-switch distance, shadow book, P&L by GEX, VIX and expected-move state at entry).
- **Options and volatility research:** `docs/GEX_FRAMEWORK.md` (pre-registered hypotheses), `scripts/vix_strategy_study.py`,
  `scripts/vix_rule_of_16.py`.

## Setup

```bash
cd ~/trading-backtests
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,nqbot]"
pytest
```

## Free 1-minute data, no keys

```bash
python scripts/download_free.py histdata          # Nasdaq-100, Nov 2010 -> now (prices only)
python scripts/download_free.py yahoo QQQ NQ=F    # last ~30 days, with real volume
```
| File (`data/free/`) | What it is | Volume |
|---|---|---|
| `NSXUSD_1min_histdata.parquet` | Nasdaq-100 bid bars from histdata.com, about 5.3M bars on the CME session (Sun–Fri 18:00–17:00 New York). It tracks NQ futures almost exactly (1-minute correlation 0.985) but trades about 1% below the front contract, i.e. at index level, with no roll jumps. | none (always 0) |
| `QQQ_1min_yahoo.parquet/.csv` | QQQ including pre- and post-market | yes |
| `NQF_1min_yahoo.parquet/.csv` | NQ front-month futures | yes |

Timestamps in the histdata files are New York local time, not the "EST without daylight saving" its docs claim. From 2019, some weeks between the US and European clock changes are stamped an hour behind. The loader detects these from the CME 17:00 maintenance break and corrects them (`align_to_cme_break`). The raw zips stay in `data/raw/histdata/`.

For real volume and trade counts over long periods, use the Alpaca and Databento downloaders below. They need free keys.

## 1-minute history (QQQ via Alpaca, NQ via Databento)

```bash
pip install -e ".[minute]"
cp .env.example .env   # add ALPACA_* and/or DATABENTO_API_KEY
```

Every run first validates the request and prints a **plan**: the window clamped to what the source accepts, the fields, the estimated number of requests, and the cost for Databento. Add `--plan` to stop there without downloading anything.

**QQQ (or any US stock or ETF): free with Alpaca paper-account keys**
```bash
python scripts/download_minute.py alpaca --symbol QQQ --months 24 --plan   # validate only, no keys needed
python scripts/download_minute.py alpaca --symbol QQQ --months 24
```
| Field | Meaning |
|---|---|
| `open`, `high`, `low`, `close` | Prices adjusted for splits and dividends |
| `volume` | Shares traded in the minute, all US exchanges |
| `trade_count` | Number of trades (transactions) in the minute |
| `vwap` | Volume-weighted average price of the minute |

Limits that are checked: history starts 2016-01-01. The free plan can't query the latest 15 minutes of all-exchange (SIP) data, so the end is clamped. Pages hold up to 10,000 bars (the API maximum). The rate limit is 200 calls a minute, and 24 months is about 70 calls. Pre- and post-market bars (04:00–20:00) are included. If SIP is refused, it falls back to the single-exchange IEX feed and says so, but IEX volume is only about 2–3% of the market.

**NQ futures: Databento, paid from the sign-up credit**
```bash
python scripts/download_minute.py databento --symbol NQ --months 24 --plan             # free price check
python scripts/download_minute.py databento --symbol NQ --months 24 --max-cost 5 --confirm
python scripts/download_minute.py databento --symbol NQ --months 24 --trades --plan    # price the extras
```
| Data | Fields | Default |
|---|---|---|
| 1-minute bars (`ohlcv-1m`) | `open`, `high`, `low`, `close`, `volume`, `instrument_id` | always |
| Daily statistics | `settlement_price`, `open_interest`, `cleared_volume`, `opening_price`, `session_high`, `session_low` | on (`--no-stats` to skip) |
| Trades, aggregated per minute | `trade_count`, `buy_volume`, `sell_volume` (aggressor side) | off (`--trades`; much larger, check the price) |

- Databento's 1-minute bars have **no trade count**, and minutes with no trades have no bar. Trade counts and buy/sell pressure come from the trades data.
- The window is checked against the dataset's actual available range (one free metadata call). The cost is priced on exactly the window that will be downloaded.
- `NQ.v.0` is the front-month contract, rolling to the next one when trading volume moves. Use `--roll calendar` to roll on fixed dates, or `--roll open_interest` to follow open interest.
- Prices are **not back-adjusted**, so they jump at each quarterly roll. The summary lists the roll dates, and `instrument_id` changes on the roll bar.
- Nothing is spent without `--confirm`. Downloads estimated above `--max-cost` are refused, and cached months are never downloaded or billed twice.

Files land in `data/minute/<source>/<symbol>/` (cached by month) and `data/exports/` (combined parquet and CSV, plus `<ROOT>_daily_stats` for the Databento statistics). Add `--rth` to export only 09:30–16:00 New York bars.

In Python:
```python
from quant.minute_data import plan_alpaca, load_alpaca_minutes, load_databento_minutes, rth_only
print(plan_alpaca("QQQ", months=24).describe())
qqq = rth_only(load_alpaca_minutes("QQQ", months=24))
nq, nq_daily = load_databento_minutes("NQ", months=24, with_trades=False)
```

## Session highs and lows (Asia, London, NY AM, NY PM)

```bash
python scripts/session_levels.py      # daily, session, 4h, 1h, 30m, 15m levels for NSX, DXY, QQQ, VIX
python scripts/plot_sessions.py --start 2025-12-09 --end 2025-12-10 --tf 15
```
Outputs go to `reports/levels/` (see `reports/levels/README.md`). They include high/low times, when each level was taken out, and QQQ VWAP and volume POC. Session windows are defined in `src/quant/sessions.py`.

## Caveats

- Yahoo data is free but unofficial; it is used here only for cross-checks (SPX/NDX daily) and short 1-minute windows.
- Past performance in a backtest is not a forecast.
