"""Shadow book (quant.live.shadow): lab ideas run on the bot's bars, recorded once per session, apart from the books."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from quant.live.calendar import Calendar
from quant.live.shadow import LiveContext, ShadowBook, load_ideas
from quant.live.status import shadow_section

NY = ZoneInfo("America/New_York")


def minutes(first: date, last: date, seed: int = 1) -> pd.DataFrame:
    """Random-walk 1-minute bars around the clock (futures hours: closed Fri 17:00 -> Sun 18:00)."""
    idx = pd.date_range(datetime.combine(first, time(0), NY), datetime.combine(last, time(16), NY), freq="1min", inclusive="left")
    idx = idx[~((idx.dayofweek == 5) | ((idx.dayofweek == 4) & (idx.hour >= 17)) | ((idx.dayofweek == 6) & (idx.hour < 18)))]
    c = 20000 + np.cumsum(np.random.default_rng(seed).normal(0, 4, len(idx)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame(dict(open=o, high=np.maximum(o, c) + 2, low=np.minimum(o, c) - 2, close=c), index=idx)


def book(tmp_path, m: pd.DataFrame) -> ShadowBook:
    path = tmp_path / "nq.parquet"
    m.to_parquet(path)
    return ShadowBook(tmp_path, Calendar(), path)


def test_context_shapes():
    ctx = LiveContext(minutes(date(2026, 9, 1), date(2026, 10, 24)), Calendar())
    assert ctx.us_days and ctx.nights and {"PDH", "PDL", "PDC", "london_high"} <= set(ctx.levels.columns)
    d = ctx.us_days[-1]
    assert d["mod"][0] == 570 and d["mod"][-1] == 959 and d["R0"] > 0
    n = ctx.nights[-1]
    assert n["rel"][0] == 0 and n["t"][-1].hour == 9 and n["t"][-1].minute == 29


def test_close_runs_every_idea_once(tmp_path):
    sb = book(tmp_path, minutes(date(2026, 8, 20), date(2026, 10, 23)))
    assert {m.NAME for m in sb.ideas} >= {"s3_first_signal_only", "drift_trend_gate", "gapup_fill_through"}
    assert not {m.NAME for m in sb.ideas} & {"lanto_canonical", "lanto_orm_tdo_fvg"}
    sb.close(date(2026, 10, 22), "us")
    sb.close(date(2026, 10, 22), "overnight")
    done = pd.read_csv(sb.done_path)
    assert len(done) == len(sb.ideas) and set(done.idea) == {m.NAME for m in sb.ideas}
    assert sb.close(date(2026, 10, 22), "us") == 0 and len(pd.read_csv(sb.done_path)) == len(sb.ideas)  # recorded once
    if sb.trades_path.exists():
        t = pd.read_csv(sb.trades_path)
        assert (t.day == "2026-10-22").all()  # only the session's own trades
    assert "## Shadow book" in shadow_section(sb.trades_path)


def test_missing_data_is_not_recorded(tmp_path):
    sb = book(tmp_path, minutes(date(2026, 8, 20), date(2026, 10, 21)))
    assert sb.close(date(2026, 10, 23), "us") == 0
    assert not sb.done_path.exists()  # retried later instead of being marked empty


def test_ideas_load_without_research_data():
    assert all(hasattr(m, "trades") and m.SESSION in ("us", "overnight") for m in load_ideas())
