# trading-backtests

Python 3.12 package `quant` (src layout) for backtesting index and US equity strategies.
Activate `.venv` first; run tests with `pytest`.

## Conventions
- Strategies return a target position in [-1, 1] per day and use only causal calculations (rolling, ewm, `shift(+n)`). Never `shift(-n)` or anything that reads future bars.
- The engine's `delay` must stay >= 1 so a signal never earns the return of the bar it was computed on.
- Backtests always include costs (default 1 bps commission + 2 bps slippage per unit turnover). Don't report zero-cost results as headline numbers.
- Judge a strategy by its walk-forward (out-of-sample) results, not by the best in-sample parameters.
- Index symbols (^GSPC, ^NDX) are for research only. The bot trades ETFs (SPY, QQQ).
- `bots/nq_paper_bot.py` (Schwab /NQ paper bot) is data-only: it must never send orders. Keep `ReadOnlyClient` in `src/quant/live/schwab_feed.py` and the order-code test in `tests/test_live_bot.py`; never add order endpoints. Claude does not run it against Schwab (live mode or `bots/schwab_login.py`); the user does. Replay mode is fine. The same applies to `bots/schwab_archiver.py`, `bots/gex_snapshot.py` and `bots/option_flow_capture.py`: market data only (`ReadOnlyClient.MARKET_DATA` = price history and option chains), run by the cloud runner, not by Claude.
