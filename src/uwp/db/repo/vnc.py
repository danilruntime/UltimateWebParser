"""Repository for VNC login sessions."""
from typing import Any

import aiosqlite

from uwp.db.database import Database
from uwp.db.repo._common import build_update, dt, parse_dt
from uwp.domain import VncSession, VncStatus

_COLUMNS: dict[str, tuple[str, Any]] = {
    "profile_id": ("profile_id", int),
    "port": ("port", int),
    "display": ("display", int),
    "password": ("password", lambda v: v),
    "token": ("token", lambda v: v),
    "status": ("status", str),
    "started_at": ("started_at", dt),
    "expires_at": ("expires_at", dt),
    "started_by": ("started_by", lambda v: v),
    "last_client_at": ("last_client_at", dt),
}


def _row_to_session(row: aiosqlite.Row) -> VncSession:
    return VncSession(
        id=row["id"],
        profile_id=row["profile_id"],
        port=row["port"],
        display=row["display"],
        password=row["password"],
        token=row["token"],
        status=VncStatus(row["status"]),
        started_at=parse_dt(row["started_at"]),
        expires_at=parse_dt(row["expires_at"]),
        started_by=row["started_by"],
        last_client_at=parse_dt(row["last_client_at"]),
    )


class VncRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, session_id: int) -> VncSession | None:
        row = await self.db.fetchone("SELECT * FROM vnc_sessions WHERE id = ?", (session_id,))
        return None if row is None else _row_to_session(row)

    async def create(self, session: VncSession) -> int:
        return await self.db.execute(
            """INSERT INTO vnc_sessions (profile_id, port, display, password, token,
                                         status, started_at, expires_at, started_by,
                                         last_client_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.profile_id,
                session.port,
                session.display,
                session.password,
                session.token,
                str(session.status),
                dt(session.started_at),
                dt(session.expires_at),
                session.started_by,
                dt(session.last_client_at),
            ),
        )

    async def update(self, session_id: int, **fields: Any) -> None:
        assignments, params = build_update(fields, _COLUMNS)
        await self.db.execute(
            f"UPDATE vnc_sessions SET {assignments} WHERE id = ?", (*params, session_id)
        )

    async def active(self) -> list[VncSession]:
        """Sessions that are still supposed to be running."""
        rows = await self.db.fetchall(
            "SELECT * FROM vnc_sessions WHERE status IN (?, ?) ORDER BY id",
            (VncStatus.requested.value, VncStatus.running.value),
        )
        return [_row_to_session(r) for r in rows]

    async def taken_ports(self) -> set[int]:
        rows = await self.db.fetchall(
            "SELECT port FROM vnc_sessions WHERE status IN (?, ?)",
            (VncStatus.requested.value, VncStatus.running.value),
        )
        return {r["port"] for r in rows}
