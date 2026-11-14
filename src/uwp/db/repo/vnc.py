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
    "url": ("url", lambda v: v),
    "proxy_id": ("proxy_id", lambda v: v),
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
        url=row["url"],
        proxy_id=row["proxy_id"],
    )


class VncRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, session_id: int) -> VncSession | None:
        row = await self.db.fetchone(
            "SELECT * FROM vnc_sessions WHERE id = ?", (session_id,)
        )
        return None if row is None else _row_to_session(row)

    async def create(self, session: VncSession) -> int:
        return await self.db.execute(
            """INSERT INTO vnc_sessions (profile_id, port, display, password, token,
                                         status, started_at, expires_at, started_by,
                                         last_client_at, url, proxy_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                session.url,
                session.proxy_id,
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

    async def active_or_stopping(self, profile_id: int) -> VncSession | None:
        """The login session of ``profile_id`` that has not finished yet.

        ``stopping`` counts: the admin pressed «Готово», but the worker has not
        killed the stack yet. Treating it as gone would free the profile while a
        Chromium is still running on its user-data-dir.
        """
        rows = await self.db.fetchall(
            """SELECT * FROM vnc_sessions
               WHERE profile_id = ? AND status IN (?, ?, ?)
               ORDER BY id DESC LIMIT 1""",
            (
                profile_id,
                VncStatus.requested.value,
                VncStatus.running.value,
                VncStatus.stopping.value,
            ),
        )
        return _row_to_session(rows[0]) if rows else None

    async def pending_start(self) -> list[VncSession]:
        """Sessions the bot asked for but no process has launched yet."""
        rows = await self.db.fetchall(
            "SELECT * FROM vnc_sessions WHERE status = ? ORDER BY id",
            (VncStatus.requested.value,),
        )
        return [_row_to_session(r) for r in rows]

    async def pending_stop(self) -> list[VncSession]:
        """Sessions an admin asked to stop; the owner kills the processes."""
        rows = await self.db.fetchall(
            "SELECT * FROM vnc_sessions WHERE status = ? ORDER BY id",
            (VncStatus.stopping.value,),
        )
        return [_row_to_session(r) for r in rows]

    async def taken_ports(self) -> set[int]:
        """Ports whose stack may still be listening.

        ``stopping`` counts: the admin pressed «Готово», the row flipped, but
        the worker has not killed websockify yet. Reusing the port would make
        the new session fail to bind and time out with ``VNC_FAILED``.
        """
        rows = await self.db.fetchall(
            "SELECT port FROM vnc_sessions WHERE status IN (?, ?, ?)",
            (
                VncStatus.requested.value,
                VncStatus.running.value,
                VncStatus.stopping.value,
            ),
        )
        return {r["port"] for r in rows}

    async def sweep_orphans(self) -> list[int]:
        """Fail sessions no live process owns and return their profile ids.

        Called once at worker startup. Only one worker may own a database
        (``WORKER_ID`` is a constant), so every ``running``/``stopping`` row at
        this point belongs to a previous process lifetime.

        ``requested`` is deliberately *not* swept: in the split deployment the
        bot process books a session and this worker launches it on the next
        tick, so such a row may be alive and waiting. ``reconcile`` resumes it
        and ``taken_ports`` already reserves its port.

        Failing the rows and reading their profile ids is one ``RETURNING``
        statement, not a SELECT followed by an UPDATE: the connection is in
        autocommit mode and split mode shares the database with the bot, so a
        session the bot books in between would otherwise be failed without its
        profile coming back, leaving that profile stuck in ``login`` forever.
        A row inserted concurrently is now either swept with its profile
        returned or not swept at all.
        """
        unfinished = (
            VncStatus.running.value,
            VncStatus.stopping.value,
        )
        rows = await self.db.fetchall(
            """UPDATE vnc_sessions SET status = ?
               WHERE status IN (?, ?) RETURNING profile_id""",
            (VncStatus.failed.value, *unfinished),
        )
        # ``RETURNING`` yields one row per updated session, so a profile with two
        # unfinished sessions would repeat; ``DISTINCT`` did not. Keep the
        # distinct contract with order preserved.
        return list(dict.fromkeys(row["profile_id"] for row in rows))
