"""Repository for Telegram admin ids."""

from __future__ import annotations

from uwp.db.database import Database
from uwp.timeutil import to_db, utcnow


class AdminsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self) -> list[int]:
        rows = await self.db.fetchall(
            "SELECT telegram_id FROM admins ORDER BY telegram_id"
        )
        return [r["telegram_id"] for r in rows]

    async def with_settings(self) -> list[tuple[int, bool, str | None]]:
        """Every stored admin with notification preference and chosen language."""
        rows = await self.db.fetchall(
            "SELECT telegram_id, notify, language FROM admins ORDER BY telegram_id"
        )
        return [
            (int(row["telegram_id"]), bool(row["notify"]), row["language"])
            for row in rows
        ]

    async def language(self, telegram_id: int) -> str | None:
        """The admin's chosen language code, or None when they never chose one."""
        row = await self.db.fetchone(
            "SELECT language FROM admins WHERE telegram_id = ?", (telegram_id,)
        )
        return None if row is None else row["language"]

    async def set_language(self, telegram_id: int, language: str) -> None:
        await self.db.execute(
            "UPDATE admins SET language = ? WHERE telegram_id = ?",
            (language, telegram_id),
        )

    async def set_notify(self, telegram_id: int, notify: bool) -> None:
        await self.db.execute(
            "UPDATE admins SET notify = ? WHERE telegram_id = ?",
            (1 if notify else 0, telegram_id),
        )

    async def add(self, telegram_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO admins (telegram_id, added_at) VALUES (?, ?)",
            (telegram_id, to_db(utcnow())),
        )

    async def remove(self, telegram_id: int) -> None:
        await self.db.execute(
            "DELETE FROM admins WHERE telegram_id = ?", (telegram_id,)
        )
