"""Repository for scraped items (used for "new" detection and change mode)."""
import json
from datetime import datetime
from typing import Any

from uwp.db.database import Database
from uwp.timeutil import to_db


class ItemsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert(
        self, site_id: int, external_key: str, payload: dict[str, Any], now: datetime
    ) -> bool:
        """Insert or refresh an item; returns ``True`` when the row is new."""
        stamp = to_db(now)
        async with self.db.transaction():
            row = await self.db.fetchone(
                "SELECT id FROM items WHERE site_id = ? AND external_key = ?",
                (site_id, external_key),
            )
            if row is None:
                await self.db.execute(
                    """INSERT INTO items (site_id, external_key, payload, first_seen_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (site_id, external_key, json.dumps(payload), stamp, stamp),
                )
                return True
            await self.db.execute(
                "UPDATE items SET payload = ?, last_seen_at = ? WHERE id = ?",
                (json.dumps(payload), stamp, row["id"]),
            )
            return False

    async def previous_payload(self, site_id: int, external_key: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT payload FROM items WHERE site_id = ? AND external_key = ?",
            (site_id, external_key),
        )
        return None if row is None else json.loads(row["payload"])

    async def count(self, site_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM items WHERE site_id = ?", (site_id,)
        )
        return row["n"]
