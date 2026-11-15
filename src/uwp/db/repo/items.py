"""Repository for scraped items (used for "new" detection and change mode)."""
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from uwp.db.database import Database
from uwp.timeutil import to_db


@dataclass(frozen=True)
class UpsertResult:
    """What storing one row meant for the run's diff.

    ``created`` is the first time the site ever produced the key; ``returned``
    is a key the *previous* run did not see coming back. Both count as new for
    the report, which is why ``is_new`` exists.
    """

    created: bool
    returned: bool

    @property
    def is_new(self) -> bool:
        return self.created or self.returned


class ItemsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert(
        self, site_id: int, external_key: str, payload: dict[str, Any], now: datetime
    ) -> UpsertResult:
        """Insert or refresh an item and report whether it counts as new."""
        stamp = to_db(now)
        async with self.db.transaction():
            row = await self.db.fetchone(
                "SELECT id, absent FROM items WHERE site_id = ? AND external_key = ?",
                (site_id, external_key),
            )
            if row is None:
                await self.db.execute(
                    """INSERT INTO items
                           (site_id, external_key, payload, first_seen_at, last_seen_at, absent)
                       VALUES (?, ?, ?, ?, ?, 0)""",
                    (site_id, external_key, json.dumps(payload), stamp, stamp),
                )
                return UpsertResult(created=True, returned=False)
            # ``absent`` is 1 only when a finished run established that the row
            # was gone; NULL means presence was never recorded (rows stored
            # before the migration), which must not read as a comeback.
            returned = row["absent"] == 1
            await self.db.execute(
                "UPDATE items SET payload = ?, last_seen_at = ?, absent = 0 WHERE id = ?",
                (json.dumps(payload), stamp, row["id"]),
            )
            return UpsertResult(created=False, returned=returned)

    async def settle(self, site_id: int, seen: set[str]) -> int:
        """Record this run's presence and return how many rows disappeared now.

        Rows not in ``seen`` become ``absent``; legacy rows (``absent`` NULL,
        stored before presence was tracked) are settled the same way but are
        not counted: their presence was never established, so calling them
        vanished would report the whole history on the first run after the
        upgrade. Once settled they behave like any other row, so a comeback is
        still new.
        """
        async with self.db.transaction():
            missing = await self.db.fetchall(
                "SELECT external_key, absent FROM items WHERE site_id = ?", (site_id,)
            )
            gone = [row["external_key"] for row in missing if row["external_key"] not in seen]
            vanished = sum(1 for row in missing if row["external_key"] not in seen and row["absent"] == 0)
            if gone:
                placeholders = ", ".join("?" for _ in gone)
                await self.db.execute(
                    f"UPDATE items SET absent = 1"
                    f" WHERE site_id = ? AND external_key IN ({placeholders})",
                    (site_id, *gone),
                )
            return vanished

    async def previous_payload(self, site_id: int, external_key: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT payload FROM items WHERE site_id = ? AND external_key = ?",
            (site_id, external_key),
        )
        return None if row is None else json.loads(row["payload"])

    async def keys(self, site_id: int) -> set[str]:
        """Every ``external_key`` already stored for the site."""
        rows = await self.db.fetchall(
            "SELECT external_key FROM items WHERE site_id = ?", (site_id,)
        )
        return {row["external_key"] for row in rows}

    async def count(self, site_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM items WHERE site_id = ?", (site_id,)
        )
        return row["n"]
