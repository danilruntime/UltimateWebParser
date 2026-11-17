"""Repository for Telegram admin ids."""

from __future__ import annotations

from uwp.db.database import Database
from uwp.i18n import normalize
from uwp.timeutil import to_db, utcnow


def _known_language(stored: str | None) -> str | None:
    """Two-letter code for a stored value, or None when we cannot translate it.

    The column is plain text, so a hand-edited ``de`` or a pasted ``en-US`` must
    read as "no opinion" here rather than raise inside ``Lang()`` at message
    time: the repository is where untrusted rows become typed values.
    """
    lang = normalize(stored)
    return None if lang is None else lang.value


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
            (
                int(row["telegram_id"]),
                bool(row["notify"]),
                _known_language(row["language"]),
            )
            for row in rows
        ]

    async def language(self, telegram_id: int) -> str | None:
        """The admin's chosen language code, or None when they never chose one."""
        row = await self.db.fetchone(
            "SELECT language FROM admins WHERE telegram_id = ?", (telegram_id,)
        )
        return None if row is None else _known_language(row["language"])

    async def set_language(self, telegram_id: int, language: str) -> None:
        """Store the choice even for an id that only exists in ``UWP_ADMIN_IDS``.

        Such an admin passes the gate but has no row; an UPDATE-only statement
        would accept the button press and discard the result silently.
        """
        await self.db.execute(
            """INSERT INTO admins (telegram_id, notify, language, added_at)
               VALUES (?, 1, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET language = excluded.language""",
            (telegram_id, language, to_db(utcnow())),
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
