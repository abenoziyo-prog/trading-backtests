"""Download SqueezeMetrics' free daily S&P 500 GEX history (and DIX) for the trade tags. No Schwab call.

    python bots/fetch_spx_gex.py

Run by bots/cloud_runner.py once a day after 18:30 ET (SqueezeMetrics publishes after the close) and on a start with no
copy. The whole file (one row per trading day since 2011-05-02, ~220 KB) is replaced only when the download parses and
is not shorter than the saved copy. File: data/schwab/gex/spx_gex_squeezemetrics.csv (columns date, price, dix, gex).
Used by src/quant/live/gex_tags.py; the regime test behind it: scripts/gex_regime_history.py.
"""
from __future__ import annotations

import csv
import io
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://squeezemetrics.com/monitor/static/DIX.csv"
OUT = ROOT / "data" / "schwab" / "gex" / "spx_gex_squeezemetrics.csv"


def valid(text: str) -> int:
    """Number of data rows if the file has the expected columns and numeric GEX values, else 0."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not {"date", "price", "dix", "gex"} <= set(rows[0]):
        return 0
    try:
        [float(r["gex"]) for r in rows[-5:]]
    except ValueError:
        return 0
    return len(rows)


def main() -> None:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (nq paper bot; daily GEX history)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode()
    n = valid(text)
    old = valid(OUT.read_text()) if OUT.exists() else 0
    if n == 0 or n < old:
        sys.exit(f"SqueezeMetrics GEX not saved: {n} valid rows (saved copy: {old})")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(OUT)
    print(f"SqueezeMetrics GEX: {n} rows, last {text.strip().splitlines()[-1][:40]}")


if __name__ == "__main__":
    main()
