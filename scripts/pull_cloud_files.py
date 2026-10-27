"""Download the cloud bot's saved files (the PostgreSQL table bots/cloud_runner.py writes) to this machine. Read-only.

    STATE_DB_URL=postgresql://... python scripts/pull_cloud_files.py data/schwab/flow/ data/schwab/gex/
    python scripts/pull_cloud_files.py --list data/schwab/flow/        # counts and sizes only

Reach the database with Northflank's port forwarding (northflank forward addon ...; the add-on stays private) and use
the forwarded address in STATE_DB_URL. Files land at the same relative paths under the repository (data/ and state/ are
gitignored); a file whose local copy already has the same content is skipped. Nothing in the database is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prefixes", nargs="+", help="path prefixes, e.g. data/schwab/flow/")
    ap.add_argument("--list", action="store_true", help="show counts and sizes, download nothing")
    ap.add_argument("--out", type=Path, default=ROOT)
    a = ap.parse_args()
    url = os.environ.get("STATE_DB_URL")
    if not url:
        sys.exit("set STATE_DB_URL to the (forwarded) PostgreSQL address")
    import psycopg

    with psycopg.connect(url) as conn:
        conn.read_only = True
        for prefix in a.prefixes:
            rows = conn.execute("SELECT path, sha256, length(data) FROM nqbot_files WHERE path LIKE %s ORDER BY path",
                                (prefix.replace("%", r"\%") + "%",)).fetchall()
            print(f"{prefix}: {len(rows)} files, {sum(r[2] for r in rows) / 1e6:.1f} MB")
            if a.list:
                continue
            got = 0
            for path, sha, _ in rows:
                f = a.out / path
                if f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() == sha:
                    continue
                data = conn.execute("SELECT data FROM nqbot_files WHERE path = %s", (path,)).fetchone()[0]
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(bytes(data))
                got += 1
            print(f"  downloaded {got}, up to date {len(rows) - got}")


if __name__ == "__main__":
    main()
