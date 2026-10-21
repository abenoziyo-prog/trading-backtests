"""Download keyless 1-minute data into data/free/.

    python scripts/download_free.py histdata            # Nasdaq-100 index (NSXUSD), 2010 -> now, prices only
    python scripts/download_free.py dxy                 # US Dollar Index (DXY), 2010 -> now, rebuilt from FX
    python scripts/download_free.py yahoo QQQ NQ=F ^VIX DX-Y.NYB   # last ~30 days (volume where traded)
    python scripts/download_free.py vix-daily           # official Cboe VIX daily OHLC since 1990
"""
from __future__ import annotations

import argparse

from quant import free_data as fd


def report(df, name: str) -> None:
    days = df.index.normalize().nunique()
    print(f"\n{name}: {len(df):,} bars, {days:,} days, {df.index[0]} -> {df.index[-1]}")
    print(f"  close range {df['close'].min():,.2f} - {df['close'].max():,.2f}")
    if "volume" in df:
        print(f"  total volume {df['volume'].sum():,.0f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="source", required=True)
    ph = sub.add_parser("histdata")
    ph.add_argument("--pair", default="nsxusd", help="nsxusd (Nasdaq-100) or spxusd (S&P 500); for the dollar index use the dxy command")
    ph.add_argument("--refresh", action="store_true")
    sub.add_parser("vix-daily", help="official Cboe VIX daily OHLC since 1990")
    sub.add_parser("dxy", help="US Dollar Index 1-min: rebuilt from 6 FX pairs (2010->now) + histdata DX futures (2018->now)")
    py = sub.add_parser("yahoo")
    py.add_argument("symbols", nargs="+", help="e.g. QQQ NQ=F")
    py.add_argument("--days", type=int, default=29)
    py.add_argument("--rth", action="store_true", help="regular session only (no pre/post market)")
    args = p.parse_args()

    if args.source == "vix-daily":
        df = fd.load_cboe_vix_daily()
        print(f"Cboe VIX daily: {len(df):,} days, {df.index[0].date()} -> {df.index[-1].date()}")
        print(f"  saved {fd.save(df, 'VIX_daily_cboe')} (+ .csv)")
        return
    if args.source == "dxy":
        import time
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = fd.USER_AGENT
        for pair in ["udxusd", *fd.DXY_WEIGHTS]:
            fd.HISTDATA_FIRST_YEAR.setdefault(pair, 2010)
            for period in fd.histdata_periods(pair):
                fd.download_histdata_zip(session, pair, period, log=lambda m: None)
                time.sleep(0.2)
        dxy, dow, rebuilt = fd.build_dxy()
        report(rebuilt, "DXY_1min_rebuilt (spot, from FX)")
        print(f"  saved {fd.save(rebuilt, 'DXY_1min_rebuilt', csv=False)}")
        fut = dxy[dxy.source == "histdata_dxy"].drop(columns="source")
        report(fut, "DXY_futures_1min_histdata")
        print(f"  saved {fd.save(fut, 'DXY_futures_1min_histdata', csv=False)}")
        report(dow, "DOW_1min_histdata_2010_2018 (shipped by histdata as UDXUSD)")
        print(f"  saved {fd.save(dow, 'DOW_1min_histdata_2010_2018', csv=False)}")
        return
    if args.source == "histdata":
        if args.pair.lower() == "udxusd":
            raise SystemExit("histdata's UDXUSD is Dow Jones data before Dec 2018; use the 'dxy' command instead.")
        df = fd.load_histdata(args.pair, refresh=args.refresh)
        name = f"{args.pair.upper()}_1min_histdata"
        report(df, name)
        # The full history is large; parquet only (use pandas to export a slice to CSV).
        print(f"  saved {fd.save(df, name, csv=False)}")
    else:
        for symbol in args.symbols:
            df = fd.load_yahoo_minutes(symbol, days=args.days, prepost=not args.rth)
            if df.empty:
                print(f"\n{symbol}: no data returned")
                continue
            name = f"{fd.file_stem(symbol)}_1min_yahoo"
            report(df, name)
            print(f"  saved {fd.save(df, name)} (+ .csv)")


if __name__ == "__main__":
    main()
