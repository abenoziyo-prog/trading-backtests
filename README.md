# trading-backtests

Backtesting, walk-forward (out-of-sample) testing and paper trading for index and US equity strategies.

- **Data:** free daily bars from Yahoo Finance (via `yfinance`), adjusted for splits and dividends and cached locally in `data/cache/`.
- **Engine:** vectorized daily backtests with commissions, slippage and a configurable signal delay. Look-ahead is prevented by design.
- **Metrics:** CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, exposure, trades.
- **Walk-forward:** re-optimizes parameters on rolling training windows and trades them on the unseen year that follows.
- **Bot:** a once-a-day Alpaca rebalancer. It's dry-run and paper by default.

## Setup

```bash
cd ~/trading-backtests
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,live]"
pytest
```

## Backtests

```bash
# S&P 500 ETF, 50/200-day moving-average trend filter, with a chart
python scripts/run_backtest.py --symbol SPY --strategy sma_cross --param fast=50 --param slow=200 --plot

# Research on the index itself (no dividends in ^GSPC; use SPY for total return)
python scripts/run_backtest.py --symbol ^GSPC --strategy momentum --start 1995-01-01

# Nasdaq-100 ETF, short-term RSI dip-buying
python scripts/run_backtest.py --symbol QQQ --strategy rsi_mean_reversion --param entry=10 --param exit=70

# Stricter fills and higher costs
python scripts/run_backtest.py --symbol SPY --strategy sma_cross --delay 2 --cost-bps 2 --slippage-bps 5
```

Common symbols: `SPY`, `QQQ`, `IWM`, `DIA` (ETFs); `^GSPC` (S&P 500), `^NDX` (Nasdaq-100), `^DJI`, `^RUT`, `^VIX` (indices); any US stock ticker. Add `--refresh` to pull the latest bars.

## Walk-forward (forward) testing

```bash
python scripts/run_backtest.py --symbol SPY --strategy sma_cross --walk-forward --train-years 5 --test-years 1 --plot
```

This prints the parameters chosen for each window and their in-sample vs out-of-sample Sharpe. If the out-of-sample numbers collapse, the strategy is probably overfit.

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

## Adding a strategy

1. In `src/quant/strategies.py`, write `def my_strategy(prices, ...) -> pd.Series` that returns a target position in [-1, 1] for each day. Use only past data.
2. Add it to `STRATEGIES`. Optionally add a parameter grid to `PARAM_GRIDS` for walk-forward.
3. Run it with `--strategy my_strategy`.

## Paper trading bot (Alpaca)

1. Create a free Alpaca account and generate **paper** API keys.
2. `cp .env.example .env` and fill in the keys. Leave `ALPACA_PAPER=true`.
3. Check the plan with a dry run, which places no order:
   ```bash
   python bots/paper_bot.py --symbol SPY --strategy sma_cross --param fast=50 --param slow=200
   ```
4. Add `--execute` to submit the paper order. Schedule it once a day shortly before the close, around 15:50 New York time.

Safety rails: the bot is a dry run unless `--execute` is passed. Live trading needs both `ALPACA_PAPER=false` and `--live`. It refuses index symbols. Paper-trade for a while, and compare the fills with the backtest, before considering real money.

## Caveats

- Yahoo data is free but unofficial. For serious work, swap `quant/data.py` for a paid source such as Polygon, Databento, Alpaca or Alpha Vantage premium.
- Single stock tickers carry survivorship bias: today's list of tickers excludes companies that were delisted.
- Past performance in a backtest is not a forecast.
