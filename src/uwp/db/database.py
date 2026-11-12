"""Async SQLite connection wrapper with numbered SQL migrations."""
import asyncio
import logging
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


class Database:
    """Thin wrapper around ``aiosqlite`` with autocommit and explicit transactions.

    The connection runs with ``isolation_level=None`` so every statement commits
    immediately unless it is wrapped in :meth:`transaction`.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._tx_lock = asyncio.Lock()

    @classmethod
    async def in_memory(cls) -> "Database":
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
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        if self.path != ":memory:":
            await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a statement and return ``lastrowid``."""
        cursor = await self.conn.execute(sql, params)
        try:
            return cursor.lastrowid or 0
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
        """Run the enclosed statements atomically (``BEGIN IMMEDIATE`` ... ``COMMIT``)."""
        async with self._tx_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            await self.conn.execute("COMMIT")

    async def migrate(self) -> None:
        """Apply pending migrations from ``uwp.migrations`` in filename order."""
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = await self.fetchone("SELECT MAX(version) AS version FROM schema_version")
        current = row["version"] if row is not None and row["version"] is not None else 0
        for version, name, sql in _load_migrations():
            if version <= current:
                continue
            log.info("applying migration %s", name)
            # ``executescript`` commits any open transaction itself, so the version
            # row is appended to the script to keep both atomic.
            async with self._tx_lock:
                await self.conn.executescript(
                    f"BEGIN IMMEDIATE;\n{sql}\n"
                    f"INSERT INTO schema_version (version) VALUES ({version});\nCOMMIT;"
                )
            current = version


def _load_migrations() -> list[tuple[int, str, str]]:
    migrations = []
    for entry in files(MIGRATIONS_PACKAGE).iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match is None:
            continue
        migrations.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    migrations.sort(key=lambda m: m[0])
    return migrations
