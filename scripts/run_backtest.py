"""Run a backtest or walk-forward test from the command line.

Examples:
    python scripts/run_backtest.py --symbol SPY --strategy sma_cross --param fast=50 --param slow=200 --plot
    python scripts/run_backtest.py --symbol ^GSPC --strategy momentum --start 1995-01-01
    python scripts/run_backtest.py --symbol QQQ --strategy rsi_mean_reversion --walk-forward --plot
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from quant import engine, metrics
from quant.data import load_prices
from quant.params import parse_params
from quant.strategies import PARAM_GRIDS, STRATEGIES
from quant.walkforward import walk_forward

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


def plot(curves: dict[str, pd.Series], title: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_eq, ax_dd) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    for name, returns in curves.items():
        equity = (1 + returns).cumprod()
        ax_eq.plot(equity.index, equity, label=name, linewidth=1.2)
        ax_dd.plot(equity.index, equity / equity.cummax() - 1, label=name, linewidth=1.0)
    ax_eq.set_yscale("log")
    ax_eq.set_ylabel("Growth of $1 (log)")
    ax_eq.set_title(title)
    ax_eq.legend(loc="upper left")
    ax_eq.grid(alpha=0.3)
    ax_dd.set_ylabel("Drawdown")
    ax_dd.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0%}"))
    ax_dd.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="SPY", help="Yahoo symbol: SPY, AAPL, ^GSPC, ^NDX ...")
    p.add_argument("--strategy", default="sma_cross", choices=sorted(STRATEGIES))
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--start", default="2000-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--cost-bps", type=float, default=1.0, help="commission per unit turnover, bps")
    p.add_argument("--slippage-bps", type=float, default=2.0, help="slippage per unit turnover, bps")
    p.add_argument("--delay", type=int, default=1, help="bars between signal and position (>=1)")
    p.add_argument("--benchmark", default="buy_and_hold", choices=sorted(STRATEGIES))
    p.add_argument("--rf", type=float, default=0.0, help="annual risk-free rate for Sharpe, e.g. 0.03")
    p.add_argument("--refresh", action="store_true", help="re-download prices instead of using the cache")
    p.add_argument("--plot", action="store_true", help=f"save a chart to {REPORTS_DIR.name}/")
    p.add_argument("--walk-forward", action="store_true", help="optimize on rolling train windows, test out of sample")
    p.add_argument("--train-years", type=int, default=5)
    p.add_argument("--test-years", type=int, default=1)
    args = p.parse_args()

    prices = load_prices(args.symbol, start=args.start, end=args.end, refresh=args.refresh)
    if prices.empty:
        sys.exit(f"No prices for {args.symbol} in the requested range")
    run_kwargs = dict(cost_bps=args.cost_bps, slippage_bps=args.slippage_bps, delay=args.delay)
    strategy = STRATEGIES[args.strategy]

    bench = engine.run(prices, STRATEGIES[args.benchmark](prices), **run_kwargs)

    if args.walk_forward:
        grid = PARAM_GRIDS.get(args.strategy)
        if not grid:
            sys.exit(f"No PARAM_GRIDS entry for {args.strategy!r}")
        wf = walk_forward(
            prices, strategy, grid,
            train_years=args.train_years, test_years=args.test_years, **run_kwargs,
        )
        print(f"\nWalk-forward windows ({args.train_years}y train / {args.test_years}y test):")
        print(wf.windows.to_string(index=False))
        oos_bench = bench.returns.loc[wf.returns.index]
        label = f"{args.strategy} (WF)"
        table = {label: wf.stats(args.rf), args.benchmark: metrics.summary(oos_bench, bench.position, args.rf)}
        curves = {label: wf.returns, args.benchmark: oos_bench}
        suffix = "_walkforward"
    else:
        params = parse_params(args.param)
        result = engine.run(prices, strategy(prices, **params), **run_kwargs)
        label = args.strategy
        table = {label: result.stats(args.rf), args.benchmark: bench.stats(args.rf)}
        curves = {label: result.returns, args.benchmark: bench.returns}
        suffix = ""
        if params:
            print("Params:", ", ".join(f"{k}={v}" for k, v in params.items()))

    print(f"\n{args.symbol}  |  costs {args.cost_bps + args.slippage_bps:g} bps/turnover  |  delay {args.delay}\n")
    print(metrics.format_table(table))

    if args.plot:
        safe = args.symbol.replace("^", "IDX_")
        path = REPORTS_DIR / f"{safe}_{args.strategy}{suffix}.png"
        plot(curves, f"{args.symbol} - {label} vs {args.benchmark}", path)
        print(f"\nChart saved to {path}")


if __name__ == "__main__":
    main()
