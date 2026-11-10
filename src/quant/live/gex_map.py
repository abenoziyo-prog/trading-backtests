"""Live ES dealer map: turns successive 0DTE chain snapshots into one HTML page that updates in place.

Session keeps the snapshots of one run and, for each new one, builds the page data: the levels moved onto the future
(index: level + basis; ETF: strike x ratio), gamma / vanna / charm per strike, and what changed since the previous snapshot, about five minutes
ago and the first snapshot. Open interest does not change during the day, so "new positioning" is read from volume:
contracts traded per strike between two snapshots. Volume does not say who bought and who sold.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

TEMPLATE = Path(__file__).with_name("gex_map_template.html")
HALF_WIDTH = 0.0265  # share of the index price each side of it shown on the strike ladders (200 points on SPX)
MAX_ROWS = 90        # strikes on a ladder: the nearest ones with open interest or volume when the window holds more
THIN_OI = 20000      # contracts of open interest below which the page warns that the levels are unreliable
TOP = 8              # strikes listed in the "what changed" table
KEEP_S = 420         # seconds of snapshots kept for the "last 5 minutes" comparison (the first one is always kept)
HIST = 480           # points drawn on the level history; a longer run is thinned evenly
POLL_MS = 250        # how often the open page looks for new data
# page values shown with their change against the previous fetch
DELTA_KEYS = ("futLast", "spot", "indexQuote", "flip", "callWall", "putWall", "cwSize", "pwSize", "net", "cg", "pg", "vex", "chx", "callVol", "putVol")


def _lvl(snap: dict, key: str) -> float | None:
    """One level of a snapshot's summary row on the future: level * ratio + basis."""
    v = snap["z"].get(key)
    return None if v is None or pd.isna(v) else float(v) * snap["ratio"] + snap["basis"]


def _f(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


class Session:
    def __init__(self, future: str = "/ES", every_s: float = 60, index: str = "SPX"):
        self.future, self.every_s, self.index = future, every_s, index
        self.snaps: list[dict] = []   # the first snapshot, then the last KEEP_S seconds: dict(t, z, vol, fut, basis, ratio)
        self.n = 0                    # snapshots taken in this run
        self.hist: list[dict] = []    # one point per snapshot for the level history
        self.last_m: dict | None = None   # the previous snapshot's page values, for the per-fetch deltas

    def _back(self, minutes: float) -> dict | None:
        """The snapshot closest to `minutes` before the latest one (None when the run is younger than that)."""
        now = self.snaps[-1]["t"]
        old = [s for s in self.snaps[:-1] if (now - s["t"]).total_seconds() >= minutes * 60 - 20]
        return old[-1] if old else None

    def add(self, now: datetime, z: dict, table: pd.DataFrame, fut_last: float, basis: float, basis_note: str,
            index_quote: float | None = None, fetch_s: float | None = None, ratio: float = 1.0) -> dict:
        """Register one snapshot (z = zero_dte row and table = strike_table, both computed at the index price
        (fut_last - basis) / ratio) and return the data the page shows. A level on the future is level * ratio + basis:
        an index uses ratio 1 and its basis, an ETF (SPY, QQQ) basis 0 and the price ratio future / ETF."""
        vol = table[["call_volume", "put_volume"]].copy()
        cur = dict(t=now, z=z, vol=vol, fut=fut_last, basis=basis, ratio=ratio)
        self.snaps = self.snaps[:1] + [s for s in self.snaps[1:] if (now - s["t"]).total_seconds() <= KEEP_S] + [cur]
        self.n += 1
        self.hist.append(dict(t=now.strftime("%H:%M:%S"), fut=_f(fut_last), net=_f((z.get("net_gex") or 0) / 1e9, 3),
                              flip=_f(_lvl(cur, "gamma_flip")), cw=_f(_lvl(cur, "call_wall")), pw=_f(_lvl(cur, "put_wall"))))
        first, prev, five = self.snaps[0], (self.snaps[-2] if len(self.snaps) > 1 else None), self._back(5)

        def dv(old: dict | None, col: str) -> pd.Series:
            if old is None:
                return pd.Series(0.0, index=table.index)
            return (table[col] - old["vol"][col].reindex(table.index).fillna(0.0)).clip(lower=0.0)

        t = table.copy()
        for tag, old in (("1", prev), ("5", five), ("0", first if len(self.snaps) > 1 else None)):
            t["dc" + tag], t["dp" + tag] = dv(old, "call_volume"), dv(old, "put_volume")
        spot = float(z["spot"])
        near = t[(t.index >= spot * (1 - HALF_WIDTH)) & (t.index <= spot * (1 + HALF_WIDTH))]
        if len(near) > MAX_ROWS:   # a dense chain (NDX lists a strike every 5 points): keep the nearest live strikes
            live = near[(near.call_oi + near.put_oi + near.call_volume + near.put_volume) > 0]
            keep = (live.index.to_series() - spot).abs().sort_values(kind="stable").index[:MAX_ROWS]
            near = near.loc[sorted(keep)]
        rows = [dict(k=float(k), cg=_f(r.call_gex / 1e6, 1), pg=_f(r.put_gex / 1e6, 1), net=_f((r.call_gex + r.put_gex) / 1e6, 1),
                     vx=_f(r.vanna / 1e6), ch=_f(r.charm / 1e6), co=int(r.call_oi), po=int(r.put_oi), cv=int(r.call_volume),
                     pv=int(r.put_volume), dc1=int(r.dc1), dp1=int(r.dp1), dc5=int(r.dc5), dp5=int(r.dp5), dc0=int(r.dc0), dp0=int(r.dp0))
                for k, r in near.iterrows()]
        win = "5" if five is not None else "1"
        act = (t["dc" + win] + t["dp" + win]).sort_values(ascending=False, kind="stable")
        movers = [dict(k=float(k), dc=int(t.at[k, "dc" + win]), dp=int(t.at[k, "dp" + win]), dc0=int(t.at[k, "dc0"]), dp0=int(t.at[k, "dp0"]),
                       cv=int(t.at[k, "call_volume"]), pv=int(t.at[k, "put_volume"]), co=int(t.at[k, "call_oi"]), po=int(t.at[k, "put_oi"]))
                  for k in act.index[:TOP] if act[k] > 0]
        step = -(-len(self.hist) // HIST)   # a long run is thinned, counted back from the newest point
        hist = self.hist[::-1][::step][::-1]
        m = dict(asof=now.isoformat(timespec="seconds"), clock=now.strftime("%H:%M:%S"), day=now.strftime("%a %d %b %Y"),
                 expiry=z.get("expiry"), spot=_f(spot), indexQuote=_f(index_quote), basis=_f(basis), ratio=_f(ratio, 5), basisNote=basis_note,
                 future=self.future, index=self.index, thin=bool((z.get("call_oi") or 0) + (z.get("put_oi") or 0) < THIN_OI),
                 futLast=_f(fut_last), flip=_f(z.get("gamma_flip")), callWall=_f(z.get("call_wall")),
                 putWall=_f(z.get("put_wall")), cwSize=z.get("call_wall_size"), pwSize=z.get("put_wall_size"), net=_f(z.get("net_gex"), 0), regime=z.get("regime"),
                 callOi=int(z.get("call_oi") or 0), putOi=int(z.get("put_oi") or 0),
                 callVol=int(table.call_volume.sum()), putVol=int(table.put_volume.sum()),
                 topCall=z.get("top_call_oi") or "", topPut=z.get("top_put_oi") or "",
                 cg=_f(table.call_gex.sum() / 1e6, 1), pg=_f(table.put_gex.sum() / 1e6, 1), vex=_f(table.vanna.sum() / 1e6),
                 chx=_f(table.charm.sum() / 1e6), fetchS=_f(fetch_s, 1), every=_f(self.every_s, 1),
                 snaps=self.n, first=first["t"].strftime("%H:%M"), win=("5 minutes" if win == "5" else "1 snapshot"),
                 prevClock=prev["t"].strftime("%H:%M:%S") if prev else None)
        m["prev"] = {k: self.last_m.get(k) for k in DELTA_KEYS} if self.last_m else None
        self.last_m = m
        return dict(M=m, R=rows, movers=movers, H=hist, notes=self.notes(win))

    def notes(self, win: str) -> list[str]:
        """Plain sentences on what moved: against five minutes ago when the run is that old, and against the start."""
        cur = self.snaps[-1]
        out: list[str] = []
        for label, old in (("in the last 5 minutes", self._back(5)), (f"since {self.snaps[0]['t']:%H:%M}", self.snaps[0] if len(self.snaps) > 1 else None)):
            if old is None or old is cur:
                continue
            z, o = cur["z"], old["z"]
            if z.get("regime") != o.get("regime") and z.get("regime") and o.get("regime"):
                out.append(f"Regime changed from {o['regime']} to {z['regime']} {label}.")
            for key, name in (("call_wall", "Call wall"), ("put_wall", "Put wall")):
                if z.get(key) is not None and o.get(key) is not None and z[key] != o[key]:
                    out.append(f"{name} moved from {_lvl(old, key):,.2f} to {_lvl(cur, key):,.2f} {label}.")
            if z.get("gamma_flip") is not None and o.get("gamma_flip") is not None:
                d = _lvl(cur, "gamma_flip") - _lvl(old, "gamma_flip")
                if abs(d) >= 3:
                    out.append(f"Gamma flip moved {d:+,.2f} points to {_lvl(cur, 'gamma_flip'):,.2f} {label}.")
            if z.get("net_gex") is not None and o.get("net_gex") is not None:
                d = (z["net_gex"] - o["net_gex"]) / 1e9
                if abs(d) >= 0.25:
                    out.append(f"Net gamma changed {d:+.2f}bn to {z['net_gex'] / 1e9:+.2f}bn {label}.")
            d = cur["fut"] - old["fut"]
            if abs(d) >= 5:
                out.append(f"{self.future.lstrip(chr(47))} moved {d:+,.2f} points {label}.")
        seen, uniq = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq


def data_name(page: str) -> str:
    """The data file an open page polls: es_map.html -> es_map.data.js."""
    return page.rsplit(".", 1)[0] + ".data.js"


def _blob(data: dict) -> str:
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


def render(data: dict, page: str = "es_map.html") -> str:
    """The page as one standalone HTML file holding the latest data. Once open it does not reload: it loads the data
    file next to it every POLL_MS and redraws when that holds a new snapshot."""
    html = TEMPLATE.read_text(encoding="utf-8")
    return html.replace("/*DATA*/null", _blob(data)).replace("__DATA_JS__", data_name(page)).replace("__POLL__", str(POLL_MS))


def _put(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write(path: Path, data: dict) -> None:
    """Write the data file (what an open page reads) and the page itself (for a fresh open). Each in one step, a
    temporary file then a rename, so the browser never loads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _put(path.with_name(data_name(path.name)), "__gex(" + _blob(data) + ");")
    _put(path, render(data, path.name))


def page_name(future: str, index: str = "") -> str:
    """File name of one map: /ES from SPX -> es_map.html (the same for /NQ from NDX), /ES from SPY -> es_spy_map.html."""
    etf = "" if index.upper() in ("", "SPX", "NDX") else "_" + index.lower()
    return future.strip("/").lower() + etf + "_map.html"


def write_frame(path: Path, maps: list[tuple[str, str]]) -> None:
    """One page that shows the maps, given as (future, index), two to a row: the first on the left. Each updates itself."""
    panes = "".join(f'<iframe src="{page_name(f, i)}" title="{f.strip("/")} dealer map from {i}"></iframe>' for f, i in maps)
    title = " and ".join(dict.fromkeys(f.strip("/") for f, _ in maps))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} dealer maps (live)</title>
<style>
html,body{{margin:0;height:100%;background:#f6f6f4}}
@media (prefers-color-scheme:dark){{html,body{{background:#111110}}}}
.panes{{display:grid;grid-template-columns:{"1fr 1fr" if len(maps) > 1 else "1fr"};gap:1px;background:#8a898455}}
iframe{{width:100%;min-width:0;height:100vh;border:0;background:#f6f6f4;display:block}}
@media (prefers-color-scheme:dark){{iframe{{background:#111110}}}}
@media (max-width:900px){{.panes{{grid-template-columns:1fr}}iframe{{height:92vh}}}}
</style></head><body><div class="panes">{panes}</div></body></html>
""", encoding="utf-8")
