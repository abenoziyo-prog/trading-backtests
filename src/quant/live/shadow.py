"""Shadow book: every strategy-lab idea, paper-traded on the bot's own /NQ bars, apart from the live books.

At each session close the bot builds a LiveContext (the same data shapes the lab hands an idea: us_days, nights,
levels, features, rth) from its Schwab 1-minute bars and runs each idea's own trades() code, so a shadow trade is
exactly what the lab backtest would record for that session. Results go to ledger/shadow_trades.csv; they never
enter the live books' P&L or take a position slot from them, and nothing is ever sent to a broker.

Ideas run after their session: US ideas at the 16:00 close, overnight ideas at the 09:30 night close. No alerts.
"""
from __future__ import annotations

import csv
import importlib.util
import logging
import sys
from datetime import date, datetime, time, timedelta
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from quant.live import research_night, research_us
from quant.live.calendar import Calendar

NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[3]
IDEAS_DIR = ROOT / "ideas"
# lanto_* rebuild Daily/4H/1H bars from the full NSX history files; they cannot run on the bot's bars
EXCLUDE = {"lanto_canonical", "lanto_orm_tdo_fvg"}
SHADOW_START = date(2026, 10, 21)   # first session of the paper bot: the catch-up starts here
FIELDS = ["day", "idea", "session", "side", "entry_time", "exit_time", "pnl_usd", "r_multiple", "recorded"]
DONE_FIELDS = ["day", "idea", "trades", "recorded"]
log = logging.getLogger("nqbot.shadow")


class LiveContext:
    """The lab's idea context (lab/data.py), built from 1-minute bars (NY time) instead of the NSX research files."""
    us, night = research_us, research_night

    def __init__(self, minutes: pd.DataFrame, cal: Calendar):
        self.m, self.cal = minutes.sort_index(), cal

    @cached_property
    def _mod(self) -> np.ndarray:
        return np.asarray(self.m.index.hour * 60 + self.m.index.minute)

    @cached_property
    def rth(self) -> pd.DataFrame:
        return self.m[(self._mod >= 570) & (self._mod < 960)]

    @cached_property
    def _rth_by_day(self) -> dict:
        return {d: g for d, g in self.rth.groupby(self.rth.index.tz_localize(None).normalize())}

    @cached_property
    def levels(self) -> pd.DataFrame:
        rows = {}
        for day in sorted(self._rth_by_day):
            prev = pd.Timestamp(self.cal.previous_trading_day(day.date()))
            p = self._rth_by_day.get(prev)
            if p is None or len(p) < 200:
                continue
            lon = self.m[(self.m.index >= pd.Timestamp(datetime.combine(day.date(), time(2)), tz=NY))
                         & (self.m.index < pd.Timestamp(datetime.combine(day.date(), time(5)), tz=NY))]
            rows[day] = dict(PDH=p.high.max(), PDL=p.low.min(), PDC=p.close.iloc[-1], prev_day=prev, prev_open=p.open.iloc[0],
                             london_high=lon.high.max() if len(lon) >= 0.8 * 180 else np.nan)
        return pd.DataFrame.from_dict(rows, orient="index")

    @cached_property
    def us_days(self) -> list[dict]:
        days = []
        for day, r in self.levels.iterrows():
            dm = self._rth_by_day.get(day)
            if dm is None or len(dm) < 200 or not (r.PDH - r.PDL) > 0:
                continue
            days.append(dict(day=day, R0=r.PDH - r.PDL, t=dm.index, mod=np.asarray(dm.index.hour * 60 + dm.index.minute),
                             o=dm.open.to_numpy(), h=dm.high.to_numpy(), l=dm.low.to_numpy(), c=dm.close.to_numpy()))
        return days

    @cached_property
    def nights(self) -> list[dict]:
        out, idx = [], self.m.index
        for day, r in self.levels.iterrows():
            if not (r.PDH - r.PDL) > 0:
                continue
            t0 = pd.Timestamp(datetime.combine((day - pd.Timedelta(days=1)).date(), time(18)), tz=NY)
            t1 = pd.Timestamp(datetime.combine(day.date(), time(9, 30)), tz=NY)
            g = self.m.iloc[idx.searchsorted(t0):idx.searchsorted(t1)]
            if len(g) < 800:
                continue
            out.append(dict(day=day, t0=t0, rel=((g.index - t0).total_seconds() // 60).astype(int).to_numpy(), t=g.index,
                            o=g.open.to_numpy(), h=g.high.to_numpy(), l=g.low.to_numpy(), c=g.close.to_numpy(),
                            PDH=r.PDH, PDL=r.PDL, PDC=r.PDC, R0=r.PDH - r.PDL, prev_open=r.prev_open, vix=np.nan, dxy_chg=np.nan))
        return out

    @cached_property
    def features(self) -> pd.DataFrame:
        f = pd.DataFrame({d["day"]: dict(gap_R=(d["o"][0] - self.levels.loc[d["day"], "PDC"]) / d["R0"]) for d in self.us_days}).T
        f["gap_down"], f["gap_up"], f["flat_open"] = f.gap_R < -0.10, f.gap_R > 0.10, f.gap_R.abs() <= 0.10
        return f


def load_minutes(state_dir: Path, archive: Path, since: date) -> pd.DataFrame:
    """1-minute /NQ bars from `since`: the daily Schwab archive, topped up with the bot's own saved bars."""
    parts = []
    if archive.exists():
        a = pd.read_parquet(archive, columns=["open", "high", "low", "close"])
        parts.append(a[a.index >= pd.Timestamp(since, tz=NY)])
    for p in sorted((state_dir / "bars").glob("*.csv")):
        if date.fromisoformat(p.stem) >= since:
            b = pd.read_csv(p)
            b.index = pd.to_datetime(b.pop("time"), utc=True).dt.tz_convert(NY)
            parts.append(b[["open", "high", "low", "close"]].astype(float))
    if not parts:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    m = pd.concat(parts)
    m.index = m.index.tz_convert(NY)
    return m[~m.index.duplicated(keep="last")].sort_index()   # the bot's own (settled) bar wins


def load_ideas(ideas_dir: Path = IDEAS_DIR, exclude: set[str] = EXCLUDE) -> list:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))   # ideas import the lab package
    out = []
    for p in sorted(ideas_dir.glob("*.py")):
        if p.name.startswith("_") or p.stem in exclude:
            continue
        spec = importlib.util.spec_from_file_location(f"shadow_{p.stem}", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.NAME = getattr(m, "NAME", p.stem)
        out.append(m)
    return out


class ShadowBook:
    def __init__(self, state_dir: Path, cal: Calendar, archive: Path, ideas: list | None = None, history_days: int = 70):
        self.state, self.cal, self.archive, self.history_days = state_dir, cal, archive, history_days
        self.dir = state_dir / "ledger"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.trades_path, self.done_path = self.dir / "shadow_trades.csv", self.dir / "shadow_done.csv"
        self.ideas = load_ideas() if ideas is None else ideas

    def _done(self) -> set[tuple[str, str]]:
        if not self.done_path.exists():
            return set()
        with self.done_path.open() as f:
            return {(r["idea"], r["day"]) for r in csv.DictReader(f)}

    def _append(self, path: Path, fields: list[str], rows: list[dict]) -> None:
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if new:
                w.writeheader()
            w.writerows(rows)

    def context(self, upto: date) -> LiveContext:
        m = load_minutes(self.state, self.archive, upto - timedelta(days=self.history_days))
        # the session's own data only: nothing after its close can leak into a decision
        return LiveContext(m[m.index < pd.Timestamp(datetime.combine(upto, time(16)), tz=NY)], self.cal)

    def close(self, day: date, session: str) -> int:
        """Record every idea of `session` ("us" or "overnight") for trading day `day`, once. Returns trades added."""
        todo = [m for m in self.ideas if m.SESSION == session and (m.NAME, day.isoformat()) not in self._done()]
        if not todo:
            return 0
        ctx, key, added = self.context(day), pd.Timestamp(day), 0
        have = {d["day"] for d in (ctx.us_days if session == "us" else ctx.nights)}
        if key not in have:  # bars missing or incomplete: try again later, never record an empty session
            log.warning("shadow book: no complete %s data for %s yet", session, day)
            return 0
        now = datetime.now(NY).strftime("%Y-%m-%d %H:%M")
        for m in todo:
            try:
                t = m.trades(ctx, **dict(getattr(m, "PARAMS", {})))
            except Exception:
                log.exception("shadow idea %s failed for %s", m.NAME, day)
                continue
            t = t[pd.to_datetime(t["day"]).dt.tz_localize(None).dt.normalize() == key] if len(t) else t
            rows = [dict(day=day.isoformat(), idea=m.NAME, session=session, side="long" if r.side > 0 else "short",
                         entry_time=pd.Timestamp(r.entry_time).tz_convert(NY).isoformat(),
                         exit_time=pd.Timestamp(r.exit_time).tz_convert(NY).isoformat(),
                         pnl_usd=round(float(r.pnl_usd), 2), r_multiple=round(float(r.r_multiple), 4), recorded=now)
                    for r in t.itertuples()]
            self._append(self.trades_path, FIELDS, rows)
            self._append(self.done_path, DONE_FIELDS, [dict(day=day.isoformat(), idea=m.NAME, trades=len(rows), recorded=now)])
            added += len(rows)
            for r in rows:
                log.info("shadow %s %s %s %s -> %s  $%.2f", day, m.NAME, r["side"], r["entry_time"][11:16], r["exit_time"][11:16], r["pnl_usd"])
        return added

    def catch_up(self, today: date) -> int:
        """Fill every finished session since SHADOW_START that is missing (first start, or after a restart)."""
        added, d, now = 0, SHADOW_START, datetime.now(NY)
        while d <= today:
            if self.cal.is_trading_day(d):
                if datetime.combine(d, time(9, 31), NY) <= now:
                    added += self.close(d, "overnight")
                if datetime.combine(d, self.cal.close_time(d), NY) + timedelta(minutes=1) <= now:
                    added += self.close(d, "us")
            d += timedelta(days=1)
        return added
