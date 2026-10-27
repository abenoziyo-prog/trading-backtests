"""Container entry point for running the NQ paper bot on a host without persistent disk (e.g. Northflank's free
sandbox). Keeps the bot's files in a database instead of a volume.

    python bots/cloud_runner.py            # CMD of the Dockerfile

  1. restore: write every saved file (state/nqbot/..., data/schwab/...) from the database to the container
  2. first run only: write the Schwab token from the SCHWAB_TOKEN_JSON secret, if no saved token exists
  3. start bots/nq_paper_bot.py (restarted if it exits; after 5 quick failures the runner exits so the
     platform restarts the container)
  4. every SYNC_S seconds: save changed files back to the database (the token refresh, bars, ledger, audit)
  5. Mon-Sat 18:30 ET: run bots/schwab_archiver.py (/NQ, QQQ, /ES, SPY 1-minute history), log the database size, save
  6. trading days 09:20 and 16:20 ET: run bots/gex_snapshot.py (option chains -> dealer gamma), then save
  7. trading days 09:25-16:15 ET: keep bots/option_flow_capture.py --loop running (near-expiry chains every 5 minutes;
     the 16:20 snapshot waits for it to end, so the two never hold memory at the same time)
  8. on SIGTERM (redeploy/stop): stop the bot, final save, exit
Database: STATE_DB_URL = postgresql://... or sqlite:///path (local testing); if unset, POSTGRES_URI / DATABASE_URL
(as injected by a linked Northflank PostgreSQL add-on, e.g. NF_NQBOT_DB_POSTGRES_URI) is used.
One table, nqbot_files(path, sha256, data, updated). Read-only towards Schwab: this file has no Schwab calls.
"""
from __future__ import annotations

import hashlib
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
NY = ZoneInfo("America/New_York")
GEX_SLOTS = [(9, 20), (16, 20)]  # before the open (fresh open interest) and after the intraday capture has ended
FLOW_START, FLOW_END, FLOW_MAX_STARTS = (9, 25), (16, 15), 5
SYNC_DIRS = ["state/nqbot", "data/schwab"]
SKIP = {".tmp"}  # half-written files are never saved
# write-once archives the container never reads back: kept in the database, not restored (saves memory and time)
NO_RESTORE = ("data/schwab/flow/", "data/schwab/gex/")
RESTORE_ALWAYS = ("data/schwab/gex/gex_daily.csv", "data/schwab/flow/capture_log.csv")
log = logging.getLogger("runner")


class FileStore:
    """Files <-> one database table. Postgres via psycopg 3, or SQLite for local tests."""

    def __init__(self, url: str):
        self.pg = url.startswith(("postgres://", "postgresql://"))
        if self.pg:
            import psycopg

            self.conn = psycopg.connect(url, autocommit=True)
            self.ph = "%s"
            self.conn.execute("CREATE TABLE IF NOT EXISTS nqbot_files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                              "data BYTEA NOT NULL, updated TIMESTAMPTZ NOT NULL DEFAULT now())")
        else:
            self.conn = sqlite3.connect(url.removeprefix("sqlite:///"), isolation_level=None)
            self.ph = "?"
            self.conn.execute("CREATE TABLE IF NOT EXISTS nqbot_files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                              "data BLOB NOT NULL, updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")

    def hashes(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT path, sha256 FROM nqbot_files").fetchall())

    def paths(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT path FROM nqbot_files").fetchall()]

    def read(self, path: str) -> bytes:
        return bytes(self.conn.execute(f"SELECT data FROM nqbot_files WHERE path = {self.ph}", (path,)).fetchone()[0])

    def size_mb(self) -> float:
        q = "SELECT pg_database_size(current_database())" if self.pg else "SELECT COALESCE(SUM(LENGTH(data)), 0) FROM nqbot_files"
        return self.conn.execute(q).fetchone()[0] / 1e6

    def put(self, path: str, sha: str, data: bytes) -> None:
        p = self.ph
        self.conn.execute(f"INSERT INTO nqbot_files (path, sha256, data) VALUES ({p}, {p}, {p}) "
                          f"ON CONFLICT (path) DO UPDATE SET sha256 = excluded.sha256, data = excluded.data, "
                          f"updated = {'now()' if self.pg else 'CURRENT_TIMESTAMP'}", (path, sha, data))


class StateSync:
    def __init__(self, store: FileStore, root: Path = ROOT, dirs: list[str] = SYNC_DIRS):
        self.store, self.root, self.dirs = store, root, dirs
        self.known = store.hashes()

    def restore(self) -> int:
        """Write the saved files back, one at a time (the archive outgrows the container's memory)."""
        n = 0
        for path in self.store.paths():
            if path.startswith(NO_RESTORE) and path not in RESTORE_ALWAYS:
                continue
            f = self.root / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(self.store.read(path))
            if f.name.endswith(".json") or f.name.endswith(".env"):
                f.chmod(0o600)
            n += 1
        return n

    def save(self) -> int:
        n = 0
        for d in self.dirs:
            for f in (self.root / d).rglob("*"):
                if not f.is_file() or f.suffix in SKIP:
                    continue
                rel = f.relative_to(self.root).as_posix()
                data = f.read_bytes()
                sha = hashlib.sha256(data).hexdigest()
                if self.known.get(rel) != sha:
                    self.store.put(rel, sha, data)
                    self.known[rel] = sha
                    n += 1
        return n


def db_url(env) -> str | None:
    """STATE_DB_URL, else a linked Northflank add-on's internal connection string (POSTGRES_URI, or the
    prefixed NF_<ADDON>_POSTGRES_URI), else DATABASE_URL. Public (EXTERNAL_*) and JDBC variants are never used."""
    if env.get("STATE_DB_URL"):
        return env["STATE_DB_URL"]
    keys = sorted(k for k in env if k.endswith("POSTGRES_URI") and "EXTERNAL" not in k and "JDBC" not in k and env[k])
    return env[keys[0]] if keys else env.get("DATABASE_URL") or None


def archive_due(now: datetime, last_day: str | None) -> bool:
    return now.weekday() < 6 and (now.hour, now.minute) >= (18, 30) and last_day != now.date().isoformat()


def gex_due(now: datetime, done: set[str], cal) -> str | None:
    """The GEX slot to run now (once per trading day and slot, within 2 hours of its time), or None."""
    if not cal.is_trading_day(now.date()):
        return None
    for h, m in GEX_SLOTS:
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = f"{now.date()} {h:02d}:{m:02d}"
        if start <= now < start + timedelta(hours=2) and key not in done:
            return key
    return None


def flow_due(now: datetime, cal) -> bool:
    """The intraday option capture should be running now (trading day, 09:25-16:15 ET)."""
    return cal.is_trading_day(now.date()) and FLOW_START <= (now.hour, now.minute) < FLOW_END


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = os.environ
    state = Path(env.setdefault("NQBOT_STATE_DIR", str(ROOT / "state" / "nqbot")))
    env.setdefault("SCHWAB_TOKEN_PATH", str(state / "schwab_token.json"))
    url = db_url(env)
    if not url:
        sys.exit("no database: set STATE_DB_URL (or link the PostgreSQL add-on, which provides POSTGRES_URI)")
    store = FileStore(url)
    sync = StateSync(store)
    log.info("restored %d files from the database", sync.restore())
    token = Path(env["SCHWAB_TOKEN_PATH"])
    if not token.exists() and env.get("SCHWAB_TOKEN_JSON"):
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(env["SCHWAB_TOKEN_JSON"])
        token.chmod(0o600)
        log.info("wrote the initial Schwab token from SCHWAB_TOKEN_JSON")
    (ROOT / "data" / "schwab").mkdir(parents=True, exist_ok=True)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sync_s, bot, fails, last_arch = float(env.get("SYNC_S", 60)), None, [], None
    from quant.live.calendar import Calendar
    cal, gex_done, flow, flow_starts = Calendar(), set(), None, {}
    next_sync = 0.0
    while not stopping:
        if bot is None or bot.poll() is not None:
            if bot is not None:
                fails = [t for t in fails if time.time() - t < 600] + [time.time()]
                log.warning("bot exited with code %s (%d failures in 10 min)", bot.returncode, len(fails))
                if len(fails) >= 5:
                    break
                time.sleep(10)
            bot = subprocess.Popen([sys.executable, str(ROOT / "bots" / "nq_paper_bot.py")], cwd=ROOT)
            log.info("bot started (pid %d)", bot.pid)
        now = datetime.now(NY)
        first_archive = last_arch is None and not any((ROOT / "data" / "schwab").glob("*.parquet"))
        if first_archive or archive_due(now, last_arch):  # first start: back-fill Schwab's ~6 weeks right away
            last_arch = now.date().isoformat()
            r = subprocess.run([sys.executable, str(ROOT / "bots" / "schwab_archiver.py")], cwd=ROOT)
            log.info("archiver finished with code %d", r.returncode)
            try:
                log.info("database size %.0f MB", store.size_mb())
            except Exception:
                log.exception("database size query failed")
            next_sync = 0
        if flow is not None and flow.poll() is not None:
            log.info("option flow capture exited with code %d", flow.returncode)
            flow, next_sync = None, 0
        day = now.date().isoformat()
        if flow is None and flow_due(now, cal) and flow_starts.get(day, 0) < FLOW_MAX_STARTS:
            flow_starts[day] = flow_starts.get(day, 0) + 1
            flow = subprocess.Popen([sys.executable, str(ROOT / "bots" / "option_flow_capture.py"), "--loop"], cwd=ROOT)
            log.info("option flow capture started (pid %d)", flow.pid)
        slot = gex_due(now, gex_done, cal) if flow is None else None  # never alongside the capture (memory)
        if slot:
            gex_done.add(slot)
            try:
                r = subprocess.run([sys.executable, str(ROOT / "bots" / "gex_snapshot.py")], cwd=ROOT, timeout=600)
                log.info("GEX snapshot %s finished with code %d", slot, r.returncode)
            except subprocess.TimeoutExpired:
                log.warning("GEX snapshot %s timed out", slot)
            next_sync = 0
        if time.time() >= next_sync:
            n = sync.save()
            if n:
                log.info("saved %d changed files", n)
            next_sync = time.time() + sync_s
        time.sleep(1)
    if flow is not None and flow.poll() is None:
        flow.terminate()
    if bot is not None and bot.poll() is None:
        bot.terminate()
        try:
            bot.wait(20)
        except subprocess.TimeoutExpired:
            bot.kill()
    log.info("final save: %d files", sync.save())
    sys.exit(1 if fails and len(fails) >= 5 else 0)


if __name__ == "__main__":
    main()
