"""Container entry point for running the NQ paper bot on a host without persistent disk (e.g. Northflank's free
sandbox). Keeps the bot's files in a database instead of a volume.

    python bots/cloud_runner.py            # CMD of the Dockerfile

  1. restore: write every saved file (state/nqbot/..., data/schwab/...) from the database to the container
  2. first run only: write the Schwab token from the SCHWAB_TOKEN_JSON secret, if no saved token exists
  3. start bots/nq_paper_bot.py (restarted if it exits; after 5 quick failures the runner exits so the
     platform restarts the container)
  4. every SYNC_S seconds: save changed files back to the database (the token refresh, bars, ledger, audit)
  5. Mon-Sat 18:30 ET: run bots/schwab_archiver.py (/NQ and QQQ 1-minute history), then save
  6. on SIGTERM (redeploy/stop): stop the bot, final save, exit
Database: STATE_DB_URL = postgresql://... or sqlite:///path (local testing); if unset, POSTGRES_URI / DATABASE_URL
(as injected by a linked Northflank PostgreSQL add-on) is used.
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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")
SYNC_DIRS = ["state/nqbot", "data/schwab"]
SKIP = {".tmp"}  # half-written files are never saved
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

    def read_all(self):
        yield from self.conn.execute("SELECT path, data FROM nqbot_files").fetchall()

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
        n = 0
        for path, data in self.store.read_all():
            f = self.root / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(bytes(data))
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


def archive_due(now: datetime, last_day: str | None) -> bool:
    return now.weekday() < 6 and (now.hour, now.minute) >= (18, 30) and last_day != now.date().isoformat()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = os.environ
    state = Path(env.setdefault("NQBOT_STATE_DIR", str(ROOT / "state" / "nqbot")))
    env.setdefault("SCHWAB_TOKEN_PATH", str(state / "schwab_token.json"))
    url = next((env[k] for k in ("STATE_DB_URL", "POSTGRES_URI", "DATABASE_URL", "POSTGRES_URI_ADMIN") if env.get(k)), None)
    if not url:
        sys.exit("no database: set STATE_DB_URL (or link the PostgreSQL add-on, which provides POSTGRES_URI)")
    sync = StateSync(FileStore(url))
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
            next_sync = 0
        if time.time() >= next_sync:
            n = sync.save()
            if n:
                log.info("saved %d changed files", n)
            next_sync = time.time() + sync_s
        time.sleep(1)
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
