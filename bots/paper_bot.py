"""Once-a-day rebalancing bot for Alpaca.

Computes the latest target position for one strategy on one tradeable symbol
and moves the account toward it with a single market order.

Safety:
  * DRY RUN by default: it only prints the plan. Pass --execute to send an order.
  * Paper trading by default (ALPACA_PAPER=true in .env).
  * Live trading requires BOTH ALPACA_PAPER=false and the --live flag.

Run it shortly before the close (e.g. 15:50 New York time) so the daily bar is
nearly complete, matching the backtest's delay=1 assumption.

Example:
    python bots/paper_bot.py --symbol SPY --strategy sma_cross --param fast=50 --param slow=200
    python bots/paper_bot.py --symbol SPY --strategy sma_cross --execute
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from quant.data import load_prices
from quant.params import parse_params
from quant.strategies import STRATEGIES


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="SPY", help="tradeable ticker (SPY, QQQ, AAPL) - not an index like ^GSPC")
    p.add_argument("--strategy", default="sma_cross", choices=sorted(STRATEGIES))
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--allocation", type=float, default=1.0, help="fraction of account equity at full position")
    p.add_argument("--execute", action="store_true", help="actually submit the order (otherwise dry run)")
    p.add_argument("--live", action="store_true", help="required in addition to ALPACA_PAPER=false")
    args = p.parse_args()

    if args.symbol.startswith("^"):
        sys.exit("Indices can't be traded directly; use an ETF such as SPY (S&P 500) or QQQ (Nasdaq-100).")
    if not 0 < args.allocation <= 1:
        sys.exit("--allocation must be in (0, 1]")

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    paper = os.getenv("ALPACA_PAPER", "true").strip().lower() != "false"
    if not key or not secret:
        sys.exit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env (see .env.example)")
    if not paper and not args.live:
        sys.exit("ALPACA_PAPER=false but --live was not passed; refusing to trade a live account.")

    from alpaca.common.exceptions import APIError
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    prices = load_prices(args.symbol, refresh=True)
    params = parse_params(args.param)
    target = float(STRATEGIES[args.strategy](prices, **params).iloc[-1])
    last_close, last_date = float(prices["close"].iloc[-1]), prices.index[-1].date()

    client = TradingClient(key, secret, paper=paper)
    equity = float(client.get_account().equity)
    try:
        current_qty = float(client.get_open_position(args.symbol).qty)
    except APIError:
        current_qty = 0.0

    target_qty = int(target * args.allocation * equity / last_close)
    delta = target_qty - current_qty

    mode = "PAPER" if paper else "LIVE"
    print(f"[{mode}] {args.strategy} on {args.symbol} | last bar {last_date} close {last_close:.2f}")
    print(f"  signal {target:+.2f} | equity ${equity:,.2f} | position {current_qty:g} -> target {target_qty}")

    if delta == 0:
        print("  No change needed.")
        return
    side = OrderSide.BUY if delta > 0 else OrderSide.SELL
    print(f"  Plan: {side.value.upper()} {abs(delta):g} {args.symbol} (market, day)")
    if not args.execute:
        print("  Dry run - pass --execute to submit.")
        return

    order = client.submit_order(
        MarketOrderRequest(symbol=args.symbol, qty=abs(delta), side=side, time_in_force=TimeInForce.DAY)
    )
    print(f"  Submitted order {order.id} ({order.status})")


if __name__ == "__main__":
    main()
