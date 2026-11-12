"""Repository for Telegram admin ids."""
from uwp.db.database import Database
from uwp.timeutil import to_db, utcnow


class AdminsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self) -> list[int]:
        rows = await self.db.fetchall("SELECT telegram_id FROM admins ORDER BY telegram_id")
        return [r["telegram_id"] for r in rows]

    async def add(self, telegram_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO admins (telegram_id, added_at) VALUES (?, ?)",
            (telegram_id, to_db(utcnow())),
        )

    async def remove(self, telegram_id: int) -> None:
        await self.db.execute("DELETE FROM admins WHERE telegram_id = ?", (telegram_id,))
