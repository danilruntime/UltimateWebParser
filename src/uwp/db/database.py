"""Async SQLite connection wrapper with numbered SQL migrations."""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

MIGRATIONS_PACKAGE = "uwp.migrations"
_MIGRATION_NAME = re.compile(r"^(\d+)_.*\.sql$")

# The journal-mode switch takes an exclusive lock that ``busy_timeout`` does not
# cover (measured: 17 of 50 concurrent opens on a fresh file failed within a
# millisecond), so the loser retries briefly instead of crashing the container.
_CONNECT_RETRY_DELAY = 0.05
_CONNECT_DEADLINE_SECONDS = 5.0

# The file lock only serialises two processes that start together, and the
# holder applies everything it finds. Most migrations take milliseconds, but
# ``004_items_absent.sql`` builds an index over the whole history, and on a large
# database on a slow bind mount that can take minutes — so the deadline is not
# sized to the fast case. It exists to turn a *stuck* holder into one visible
# error instead of a silent hang; the waiter that gives up exits and is restarted
# by ``restart: unless-stopped``, and the next attempt finds the migration done.
_MIGRATION_LOCK_DEADLINE_SECONDS = 900.0


def _is_memory(path: Path | str) -> bool:
    """True for the private in-memory database.

    ``Path(":memory:") == ":memory:"`` is False, so comparing the raw attribute
    wrote a ``:memory:.migrate.lock`` file into the working directory when the
    caller passed a ``Path``.
    """
    return str(path) == ":memory:"


def _sidecars(path: Path) -> tuple[Path, Path]:
    return Path(f"{path}-wal"), Path(f"{path}-shm")


def _chmod_private(path: Path) -> None:
    """Best-effort ``0600``; a missing file or a foreign owner is not fatal."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class Database:
    """Thin wrapper around ``aiosqlite`` with autocommit and explicit transactions.

    The connection runs with ``isolation_level=None`` so every statement commits
    immediately unless it is wrapped in :meth:`transaction`.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._tx_lock = asyncio.Lock()
        # Which task holds ``_tx_lock``; ``None`` when free. A plain bool would
        # make a *waiting* task look like a nested caller and refuse it.
        self._tx_owner: asyncio.Task[Any] | None = None

    @classmethod
    async def in_memory(cls) -> Database:
        """Open a private in-memory database (for tests)."""
        db = cls(":memory:")
        await db.connect()
        return db

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return
        if not _is_memory(self.path):
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            # The file holds proxy passwords, VNC passwords and session rows.
            # Create it private before SQLite writes anything, then re-assert
            # the mode: an existing world-readable file keeps its permissions.
            Path(self.path).touch(mode=0o600, exist_ok=True)
            _chmod_private(Path(self.path))
        conn = await aiosqlite.connect(self.path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        try:
            # ``busy_timeout`` must come before ``journal_mode=WAL``: switching
            # the journal needs a lock, and two processes opening the file at
            # once (bot and worker in split mode, or the healthcheck) would
            # otherwise raise "database is locked" and flap a healthy
            # container.
            await conn.execute("PRAGMA busy_timeout=5000")
            if not _is_memory(self.path):
                await self._switch_to_wal(conn)
                # The sidecars are a full copy of the pages (secrets included)
                # and SQLite creates them with the process umask, not the mode
                # of the database file.
                for sidecar in _sidecars(Path(self.path)):
                    _chmod_private(sidecar)
            await conn.execute("PRAGMA foreign_keys=ON")
        except BaseException:
            # ``aiosqlite`` starts a non-daemon thread per connection. A failed
            # PRAGMA used to leave it running with no handle to close it, and
            # the interpreter never exited — measured: `timeout 30` had to kill
            # the process. Closing here makes the exception the only symptom.
            await conn.close()
            raise
        self._conn = conn

    async def _switch_to_wal(self, conn: aiosqlite.Connection) -> None:
        """Switch the journal, retrying while another process holds the lock.

        ``PRAGMA journal_mode=WAL`` takes an exclusive lock for the duration of
        the switch, and SQLite does not run the busy handler for it: a second
        process gets ``database is locked`` immediately, however large
        ``busy_timeout`` is. Bot and worker start together on the first
        ``docker compose up``, so one of them used to die there (measured: 17
        failures in 50 fresh-file races). The retry ends at a deadline and
        re-raises, so a genuinely stuck database still fails instead of hanging.
        """
        deadline = asyncio.get_running_loop().time() + _CONNECT_DEADLINE_SECONDS
        while True:
            try:
                await conn.execute("PRAGMA journal_mode=WAL")
                return
            except aiosqlite.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                await asyncio.sleep(_CONNECT_RETRY_DELAY)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            if not _is_memory(self.path):
                for sidecar in _sidecars(Path(self.path)):
                    _chmod_private(sidecar)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a statement and return ``cursor.lastrowid``.

        SQLite reports the rowid of the most recent successful insert on the
        connection, so for ``UPDATE``/``DELETE`` (and other non-inserting
        statements) this is a stale value, not an affected-row count. The result
        is also coerced with ``or 0``, so "no insert" and "insert with rowid 0"
        both yield ``0``. Use :meth:`execute_rowcount` for affected-row counts.
        """
        cursor = await self.conn.execute(sql, params)
        try:
            return cursor.lastrowid or 0
        finally:
            await cursor.close()

    async def execute_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a statement and return the number of affected rows."""
        cursor = await self.conn.execute(sql, params)
        try:
            return cursor.rowcount
        finally:
            await cursor.close()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(sql, params)
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(sql, params)
        try:
            return list(await cursor.fetchall())
        finally:
            await cursor.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Run the enclosed statements atomically (``BEGIN IMMEDIATE`` ... ``COMMIT``).

        Not reentrant *from the same task*: nesting it inside itself would wait on
        ``_tx_lock``, which the outer call already holds, forever (measured: a
        nested ``seed()`` never returned). A second task is different — it is
        supposed to wait for its turn, so only the owner is refused.
        """
        current = asyncio.current_task()
        if self._tx_owner is current:
            raise RuntimeError("transaction() is already active in this task")
        async with self._tx_lock:
            # Set inside the lock: the waiter must not claim ownership before
            # the previous holder releases it.
            self._tx_owner = current
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                try:
                    yield
                except BaseException:
                    await self.conn.execute("ROLLBACK")
                    raise
                await self.conn.execute("COMMIT")
            finally:
                self._tx_owner = None

    @asynccontextmanager
    async def _migration_lock(self) -> AsyncIterator[None]:
        """Hold ``<db>.migrate.lock`` for the whole migration pass.

        ``executescript`` commits any open transaction, so the "read
        MAX(version), then apply" guard cannot live inside one SQLite
        transaction: a second process (bot and worker in split mode start
        together) reads the stale version and dies on "table already exists".
        A file lock outlives that commit. The file is created beside the
        database and never removed — deleting it would let two processes hold
        locks on different inodes.
        """
        if _is_memory(self.path):
            # A private in-memory database has exactly one connection, so there
            # is no second migrator to serialise against.
            yield
            return
        lock_path = Path(f"{self.path}.migrate.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = await asyncio.to_thread(
            os.open, lock_path, os.O_CREAT | os.O_RDWR, 0o600
        )
        _chmod_private(lock_path)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _MIGRATION_LOCK_DEADLINE_SECONDS
        # A large `items` table makes `004_items_absent.sql` take minutes on a
        # slow bind mount, and a silent 15-minute wait looks like a hung start.
        # Say it once, at a point where the operator is still watching the log.
        warned = False
        try:
            while True:
                try:
                    await asyncio.to_thread(
                        fcntl.flock, handle, fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                    break
                except BlockingIOError:
                    # A released lock is granted on the next probe; a process
                    # that died holding it has its lock released by the kernel.
                    # Only a live-but-stuck holder reaches the deadline, and it
                    # must not look like a healthy startup.
                    waited = _MIGRATION_LOCK_DEADLINE_SECONDS - (deadline - loop.time())
                    if waited >= 10 and not warned:
                        warned = True
                        log.warning(
                            "waiting for %s to finish migrating %s (%.0fs so far)",
                            lock_path,
                            self.path,
                            waited,
                        )
                    if loop.time() >= deadline:
                        raise RuntimeError(
                            f"another process is still migrating {self.path}; "
                            f"gave up after {_MIGRATION_LOCK_DEADLINE_SECONDS:g}s"
                        ) from None
                    await asyncio.sleep(0.05)
            yield
        finally:
            # Closing the descriptor releases the lock. Unlocking first would
            # open a window in which the next process reads a version the
            # previous one has not finished applying.
            await asyncio.to_thread(os.close, handle)

    async def migrate(self) -> None:
        """Apply pending migrations from ``uwp.migrations`` in filename order."""
        async with self._migration_lock():
            await self.conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = await self.fetchone("SELECT MAX(version) AS version FROM schema_version")
            current = row["version"] if row is not None and row["version"] is not None else 0
            migrations = _load_migrations()
            newest = migrations[-1][0] if migrations else 0
            if current > newest:
                # The install page promises this: an older image must not write
                # through a schema it does not understand. Migrations are
                # append-only, so a higher number can only come from a newer
                # build.
                raise RuntimeError(
                    f"{self.path} has schema version {current}, but this build "
                    f"only understands up to {newest}: it was migrated by a newer "
                    f"release, and a downgrade is not supported"
                )
            for version, name, sql in migrations:
                if version <= current:
                    continue
                log.info("applying migration %s", name)
                await self._apply_migration(version, sql)
                current = version

    async def _apply_migration(self, version: int, sql: str) -> None:
        """Apply one migration script and record its version atomically.

        ``executescript`` implicitly commits any open transaction, so the script
        is wrapped in an explicit ``BEGIN IMMEDIATE`` ... ``COMMIT``. On failure
        the open transaction is rolled back explicitly so the connection stays
        usable, and the original error is re-raised.
        """
        async with self._tx_lock:
            try:
                await self.conn.executescript(
                    f"BEGIN IMMEDIATE;\n{sql}\n"
                    f"INSERT INTO schema_version (version) VALUES ({version});\nCOMMIT;"
                )
            except BaseException:
                if self.conn.in_transaction:
                    await self.conn.execute("ROLLBACK")
                raise


def _load_migrations() -> list[tuple[int, str, str]]:
    migrations = []
    for entry in files(MIGRATIONS_PACKAGE).iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match is None:
            continue
        migrations.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    migrations.sort(key=lambda m: m[0])
    return migrations
