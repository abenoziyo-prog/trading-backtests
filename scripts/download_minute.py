"""Download 1-minute market data into data/minute/ (parquet, cached by month).

Every run first prints a validated request plan (window clamped to what the
source accepts, estimated requests, and for Databento the cost per schema).

Examples:
    # QQQ, last 24 months: OHLC + volume + trade_count + vwap (free Alpaca keys)
    python scripts/download_minute.py alpaca --symbol QQQ --months 24 --plan   # validate only
    python scripts/download_minute.py alpaca --symbol QQQ --months 24

    # NQ futures: bars + daily settlement/open interest; price check first (free)
    python scripts/download_minute.py databento --symbol NQ --months 24 --plan
    python scripts/download_minute.py databento --symbol NQ --months 24 --max-cost 5 --confirm

    # Add per-minute trade counts and buy/sell aggressor volume (bigger download)
    python scripts/download_minute.py databento --symbol NQ --months 24 --trades --plan

Add --rth to export only 09:30-16:00 New York bars.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from quant import minute_data as md

EXPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "exports"


def summarize(df, label: str) -> None:
    if df.empty:
        print(f"\n{label}: no bars returned")
        return
    days = df.index.normalize().nunique()
    print(f"\n{label}: {len(df):,} bars over {days} trading days")
    print(f"  first {df.index[0]}  last {df.index[-1]}")
    print(f"  columns: {', '.join(df.columns)}")
    print(f"  close range {df['close'].min():,.2f} - {df['close'].max():,.2f}  |  total volume {df['volume'].sum():,.0f}")
    if "instrument_id" in df:
        rolls = df.index[df["instrument_id"].diff().fillna(0) != 0]
        if len(rolls):
            print(f"  contract rolls (prices jump here, not back-adjusted): {', '.join(str(r.date()) for r in rolls)}")


def export(df, name: str) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"{name}.parquet"
    df.to_parquet(path)
    df.to_csv(path.with_suffix(".csv"))
    print(f"  saved {path} (+ .csv)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="source", required=True)

    pa = sub.add_parser("alpaca", help="US stocks/ETFs via Alpaca (free)")
    pa.add_argument("--symbol", default="QQQ")
    pa.add_argument("--feed", default="sip", choices=["sip", "iex"])

    pdb = sub.add_parser("databento", help="CME futures via Databento (uses sign-up credit)")
    pdb.add_argument("--symbol", default="NQ", help="futures root: NQ, ES, MNQ, MES ...")
    pdb.add_argument("--roll", default="volume", choices=["volume", "calendar", "open_interest"])
    pdb.add_argument("--no-stats", action="store_true", help="skip daily settlement / open interest / cleared volume")
    pdb.add_argument("--trades", action="store_true", help="add per-minute trade_count and buy/sell aggressor volume")
    pdb.add_argument("--max-cost", type=float, default=5.0, help="refuse downloads estimated above this, USD")
    pdb.add_argument("--confirm", action="store_true", help="required to spend credit")

    for sp in (pa, pdb):
        sp.add_argument("--months", type=int, default=24)
        sp.add_argument("--start", default=None, help="YYYY-MM-DD, overrides --months")
        sp.add_argument("--end", default=None)
        sp.add_argument("--plan", action="store_true", help="validate and print the request plan, download nothing")
        sp.add_argument("--rth", action="store_true", help="export regular-session bars only")
        sp.add_argument("--refresh", action="store_true", help="ignore the cache")

    args = p.parse_args()

    try:
        if args.source == "alpaca":
            plan = md.plan_alpaca(args.symbol, args.start, args.end, args.months, args.feed)
        else:
            plan = md.plan_databento(
                args.symbol, args.start, args.end, args.months, args.roll,
                with_stats=not args.no_stats, with_trades=args.trades,
            )
    except ValueError as exc:
        sys.exit(f"Invalid request: {exc}")

    print(plan.describe())
    if args.plan:
        print("\nPlan only: nothing downloaded.")
        return

    stats = None
    if args.source == "alpaca":
        bars = md.load_alpaca_minutes(
            args.symbol, args.start, args.end, args.months, feed=args.feed, refresh=args.refresh, log_plan=False,
        )
        name = f"{plan.symbol}_1min"
    else:
        if plan.total_cost > args.max_cost:
            sys.exit(f"\nEstimated ${plan.total_cost:.2f} exceeds --max-cost {args.max_cost:.2f}; "
                     "narrow the range, drop --trades, or raise the cap.")
        if not args.confirm:
            sys.exit("\nRe-run with --confirm to spend credit on this download.")
        bars, stats = md.load_databento_minutes(
            args.symbol, args.start, args.end, args.months, args.roll,
            with_stats=not args.no_stats, with_trades=args.trades, refresh=args.refresh, log_plan=False,
        )
        name = f"{args.symbol.upper()}_{args.roll}_1min"

    if args.rth:
        bars = md.rth_only(bars)
        name += "_rth"
    summarize(bars, name)
    if not bars.empty:
        export(bars, name)
    if stats is not None and not stats.empty:
        print(f"\nDaily statistics: {len(stats)} days, columns: {', '.join(stats.columns)}")
        export(stats, f"{args.symbol.upper()}_daily_stats")


if __name__ == "__main__":
    main()
