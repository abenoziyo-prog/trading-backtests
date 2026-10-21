"""Deep validation of the histdata NSXUSD 1-minute file.

Checks
  1. Structure    - ordering, duplicates, OHLC consistency, price grid
  2. Completeness - regular session (09:30-16:00) against the real Nasdaq trading
                    calendar (taken from ^NDX daily), full futures session, gaps
  3. Sanity       - stale runs, zero-range bars, spikes that revert (bad ticks)
  4. Cross-check  - daily open/high/low/close vs official ^NDX, plus a per-day
                    test of which hour's close matches the official 16:00 close
                    (catches timestamps shifted by an hour)

Outputs CSVs and charts in reports/nsx_validation/ and prints a summary.
    python scripts/validate_nsx.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NSX = ROOT / "data" / "free" / "NSXUSD_1min_histdata.parquet"
NDX = ROOT / "data" / "free" / "NDX_daily_yahoo.parquet"
OUT = ROOT / "reports" / "nsx_validation"
RTH_OPEN, RTH_CLOSE, EARLY_CLOSE = 570, 960, 780  # minutes after midnight


def early_close_days(trading_days: pd.DatetimeIndex) -> set:
    """US equity half days: Friday after Thanksgiving, Dec 24 and Jul 3 when they are trading days."""
    days = set()
    for d in trading_days:
        if d.month == 11 and d.dayofweek == 4 and 23 <= d.day <= 29:
            days.add(d)
        elif (d.month, d.day) in {(12, 24), (7, 3)} and d.dayofweek < 5:
            days.add(d)
    return days


def section(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * (60 - len(title))}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    d = pd.read_parquet(NSX)
    ndx = pd.read_parquet(NDX)
    idx = d.index
    local = idx.tz_localize(None)
    mod = idx.hour * 60 + idx.minute
    date = local.normalize()

    # ------------------------------------------------------------ 1. structure
    section("1. Structure")
    ohlc_bad = ((d.high < d[["open", "close"]].max(axis=1)) | (d.low > d[["open", "close"]].min(axis=1))).sum()
    print(f"bars {len(d):,} | sorted {idx.is_monotonic_increasing} | duplicate stamps {idx.duplicated().sum()} "
          f"| non-positive prices {(d[['open','high','low','close']] <= 0).any(axis=1).sum()} | OHLC inconsistent {ohlc_bad}")
    print(f"seconds always 0: {(idx.second == 0).all()}")
    on_grid = (np.abs(d.close * 4 - np.round(d.close * 4)) < 1e-6).groupby(idx.year).mean()
    print("share of closes on the 0.25 futures tick grid, by year:")
    print("  " + "  ".join(f"{y}:{v:.0%}" for y, v in on_grid.items()))

    # ------------------------------------------------------------ 2. completeness
    section("2a. Regular session vs Nasdaq calendar")
    span = (ndx.index >= date.min()) & (ndx.index <= date.max())
    trading = ndx.index[span]
    half = early_close_days(trading)
    in_rth = (mod >= RTH_OPEN) & (mod < RTH_CLOSE) & (idx.dayofweek < 5)
    rth_counts = pd.Series(1, index=date[in_rth]).groupby(level=0).size()
    expected = pd.Series([210 if t in half else 390 for t in trading], index=trading)
    present = rth_counts.reindex(trading, fill_value=0)
    # On half days, bars after 13:00 are not expected
    ratio = (present.clip(upper=expected) / expected)
    days = pd.DataFrame({"expected": expected, "present": present, "ratio": ratio})
    days.to_csv(OUT / "rth_completeness_by_day.csv")
    missing_days = days.index[days.present == 0]
    partial = days[(days.present > 0) & (days.ratio < 0.95)]
    print(f"trading days in span: {len(trading):,} (half days: {len(half)})")
    print(f"  complete (>=95%): {(days.ratio >= .95).sum():,} ({(days.ratio >= .95).mean():.1%}) | partial: {len(partial):,} "
          f"| missing entirely: {len(missing_days)}")
    print(f"  overall regular-session minutes present: {present.clip(upper=expected).sum() / expected.sum():.2%}")
    print(f"  missing days: {[str(x.date()) for x in missing_days]}")
    extra = sorted(set(rth_counts.index) - set(trading))
    print(f"  NSX regular-session bars on Nasdaq holidays (futures open): {len(extra)} days")
    yr = days.groupby(days.index.year).agg(days=("ratio", "size"), complete=("ratio", lambda x: (x >= .95).mean()),
                                            minutes=("ratio", "mean"))
    print(yr.round(3).to_string())
    heat = days.groupby([days.index.year, days.index.month]).ratio.mean().unstack()
    heat.to_csv(OUT / "rth_completeness_year_month.csv")

    # where the missing regular-session minutes are, by hour
    full_minutes = pd.MultiIndex.from_product([trading[~trading.isin(list(half))], range(RTH_OPEN, RTH_CLOSE)])
    have = pd.MultiIndex.from_arrays([date[in_rth], mod[in_rth]])
    miss = full_minutes.difference(have)
    miss_df = pd.DataFrame({"date": miss.get_level_values(0), "mod": miss.get_level_values(1)})
    by_hour = miss_df.groupby([miss_df.date.dt.year, miss_df["mod"] // 60]).size().unstack(fill_value=0)
    by_hour.to_csv(OUT / "rth_missing_minutes_year_hour.csv")
    print("missing regular-session minutes by year x hour (full days only):")
    print(by_hour.to_string())

    section("2b. Full futures session and gaps")
    session = (local + pd.Timedelta(hours=6)).normalize()  # Sun 18:00 -> Monday session
    per_session = pd.Series(1, index=session).groupby(level=0).size()
    midweek = per_session[per_session.index.dayofweek.isin([1, 2, 3])]  # Tue-Thu sessions are full 23h
    print("bars per Tue-Thu futures session (23h = 1380 max), median by year:")
    print("  " + "  ".join(f"{y}:{int(v)}" for y, v in midweek.groupby(midweek.index.year).median().items()))
    step = idx.to_series().diff()
    same = session[1:] == session[:-1]
    gaps = pd.DataFrame({"start": idx[:-1], "end": idx[1:], "minutes": (step.iloc[1:].dt.total_seconds() / 60).values})
    gaps = gaps[same & (gaps.minutes > 1)]
    # the 16:15-16:30 halt (pre-2016) and 17:00-18:00 break are scheduled, not gaps
    scheduled = (gaps.start.dt.hour * 60 + gaps.start.dt.minute).between(16 * 60 + 14, 16 * 60 + 15) & gaps.minutes.between(15, 17)
    gaps = gaps[~scheduled]
    g = gaps.groupby(gaps.start.dt.year).minutes.agg(
        n_2_5=lambda x: x.between(2, 5).sum(), n_6_30=lambda x: x.between(6, 30).sum(),
        n_31_120=lambda x: x.between(31, 120).sum(), n_gt_120=lambda x: (x > 120).sum(),
    )
    print("intra-session gaps (minutes without a bar) by year:")
    print(g.to_string())
    big = gaps[gaps.minutes > 120].sort_values("minutes", ascending=False)
    big.to_csv(OUT / "gaps_over_2h.csv", index=False)
    print(f"gaps over 2h: {len(big)}; largest:")
    print(big.head(10).to_string(index=False))

    # ------------------------------------------------------------ 3. sanity
    section("3. Price sanity")
    zero_range = (d.high == d.low).groupby(idx.year).mean()
    print("zero-range bars (high == low) by year: " + "  ".join(f"{y}:{v:.0%}" for y, v in zero_range.items()))
    c = d.close
    change = c.ne(c.shift()).cumsum()
    run = change.groupby(change).transform("size")
    stale = (run >= 30) & in_rth
    print(f"regular-session bars inside flat runs of >=30 identical closes: {stale.sum():,}")
    lr = np.log(c).diff()
    contiguous = (step <= pd.Timedelta(minutes=1)).values
    lr = lr.where(contiguous)
    vol = lr.rolling(390, min_periods=100).std()
    z = (lr / vol).abs()
    revert = (lr * lr.shift(-1) < 0) & (lr.shift(-1).abs() > 0.7 * lr.abs())
    spikes = d.assign(ret_bp=lr * 1e4, z=z)[(z > 12) & revert & (lr.abs() > 0.004)]
    spikes.to_csv(OUT / "suspect_spikes.csv")
    print(f"suspect bad ticks (|move| > 40bp, >12 sigma, reverted next minute): {len(spikes)}")
    print(spikes[["open", "high", "low", "close", "ret_bp", "z"]].head(10).round(1).to_string())
    big_moves = lr[lr.abs() > 0.01]
    print(f"1-minute moves above 1%: {len(big_moves)} (largest {big_moves.abs().max():.2%} at {big_moves.abs().idxmax()})")

    # ------------------------------------------------------------ 4. cross-check vs ^NDX
    section("4. Cross-check vs official Nasdaq-100 daily (^NDX)")
    rth = d[in_rth].copy()
    rth["date"] = date[in_rth]
    rth["mod"] = mod[in_rth]
    is_half = rth.date.isin(list(half))
    rth = rth[~is_half | (rth["mod"] < EARLY_CLOSE)]
    agg = rth.groupby("date").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                  close=("close", "last"), first_mod=("mod", "first"), last_mod=("mod", "last"))
    j = agg.join(ndx, rsuffix="_ndx", how="inner")
    for f in ["open", "high", "low", "close"]:
        j[f"{f}_bp"] = np.log(j[f] / j[f"{f}_ndx"]) * 1e4
    ok = (j.first_mod == RTH_OPEN) & (j.last_mod >= RTH_CLOSE - 1)
    jj = j[ok & ~j.index.isin(list(half))]
    stats = jj.groupby(jj.index.year)[["open_bp", "high_bp", "low_bp", "close_bp"]].agg(
        lambda x: f"{x.median():+.1f} ({x.abs().quantile(.95):.0f})")
    print("NSX vs ^NDX, median difference in bp (95th pct of |diff|), full days only:")
    print(stats.to_string())
    j.to_csv(OUT / "daily_vs_ndx.csv")

    # Which hour's close matches the official close best? (detects shifted days)
    close_at = {}
    for hour_end in (14, 15, 16):
        m = rth if hour_end < 16 else d[(mod < 17 * 60) & (idx.dayofweek < 5)].assign(date=date[(mod < 17 * 60) & (idx.dayofweek < 5)], mod=mod[(mod < 17 * 60) & (idx.dayofweek < 5)])
        sel = m[m["mod"] == hour_end * 60 + 59]
        close_at[hour_end] = sel.groupby("date").close.last()
    cmp = pd.DataFrame({h: np.abs(np.log(s / ndx.close.reindex(s.index))) * 1e4 for h, s in close_at.items()}).dropna()
    cmp = cmp[~cmp.index.isin(list(half))]
    best = cmp.idxmin(axis=1)
    clear = cmp.min(axis=1) * 3 < cmp.drop(columns=15).min(axis=1)  # winner clearly better than others
    shifted = cmp[(best != 15) & (cmp[15] > 15)]
    shifted.to_csv(OUT / "possible_shifted_days.csv")
    print(f"days where 15:59 is the best match for the 16:00 official close: {(best == 15).mean():.1%} of {len(cmp):,}")
    print(f"days where another hour matches better AND 15:59 is >15bp off (possible time shift): {len(shifted)}")
    if len(shifted):
        print(shifted.round(1).head(15).to_string())

    big_dev = jj[jj.close_bp.abs() > 25]
    big_dev.to_csv(OUT / "close_deviation_over_25bp.csv")
    print(f"full days with |close diff| > 25bp: {len(big_dev)}")

    # ------------------------------------------------------------ charts
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(heat.values, aspect="auto", cmap="RdYlGn", vmin=0.5, vmax=1)
    ax.set_yticks(range(len(heat.index)), heat.index)
    ax.set_xticks(range(12), ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    for (r, cc), v in np.ndenumerate(heat.values):
        if not np.isnan(v):
            ax.text(cc, r, f"{v:.0%}", ha="center", va="center", fontsize=7)
    ax.set_title("NSXUSD regular-session completeness (share of 09:30-16:00 minutes present)")
    fig.colorbar(im, ax=ax, fraction=0.03)
    fig.tight_layout()
    fig.savefig(OUT / "completeness_heatmap.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(jj.index, jj.close_bp, lw=0.6)
    ax.axhline(0, color="k", lw=0.5)
    ax.set_ylabel("NSX close vs ^NDX close (bp)")
    ax.set_title("Daily close difference, full regular-session days")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "close_vs_ndx.png", dpi=120)
    plt.close(fig)
    print(f"\nCSVs and charts written to {OUT}")


if __name__ == "__main__":
    main()
