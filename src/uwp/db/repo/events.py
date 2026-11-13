"""Repository for events and per-admin delivery tracking."""
import json
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from uwp.db.database import Database
from uwp.db.repo._common import parse_dt
from uwp.domain import Event
from uwp.timeutil import to_db, utcnow


def _row_to_event(row: aiosqlite.Row) -> Event:
    return Event(
        id=row["id"],
        kind=row["kind"],
        site_id=row["site_id"],
        run_id=row["run_id"],
        payload=json.loads(row["payload"]),
        created_at=parse_dt(row["created_at"]),
    )


class EventsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def emit(
        self,
        kind: str,
        site_id: int | None,
        run_id: int | None,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> int:
        return await self.db.execute(
            """INSERT INTO events (kind, site_id, run_id, payload, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (kind, site_id, run_id, json.dumps(payload), to_db(now or utcnow())),
        )

    async def pending(self, admin_id: int, now: datetime, ttl_days: int) -> list[Event]:
        """Events created within the TTL that were not yet delivered to ``admin_id``."""
        rows = await self.db.fetchall(
            """SELECT e.* FROM events e
               WHERE e.created_at >= ?
                 AND NOT EXISTS (SELECT 1 FROM event_deliveries d
                                 WHERE d.event_id = e.id AND d.admin_id = ?)
               ORDER BY e.id""",
            (to_db(now - timedelta(days=ttl_days)), admin_id),
        )
        return [_row_to_event(r) for r in rows]

    async def mark_delivered(self, event_id: int, admin_id: int, now: datetime | None = None) -> None:
        await self.db.execute(
            """INSERT OR IGNORE INTO event_deliveries (event_id, admin_id, delivered_at)
               VALUES (?, ?, ?)""",
            (event_id, admin_id, to_db(now or utcnow())),
        )

    async def purge(self, before: datetime) -> int:
        """Delete events created before ``before``; deliveries cascade."""
        return await self.db.execute_rowcount(
            "DELETE FROM events WHERE created_at < ?", (to_db(before),)
        )
