"""Repository for run history rows."""
from typing import Any

import aiosqlite

from uwp.db.database import Database
from uwp.db.repo._common import build_update, dt, parse_dt
from uwp.domain import Run, RunStatus

_COLUMNS: dict[str, tuple[str, Any]] = {
    "job_id": ("job_id", int),
    "site_id": ("site_id", int),
    "started_at": ("started_at", dt),
    "finished_at": ("finished_at", dt),
    "status": ("status", str),
    "rows_count": ("rows_count", int),
    "new_count": ("new_count", int),
    "report_path": ("report_path", lambda v: v),
    "artifact_dir": ("artifact_dir", lambda v: v),
    "error": ("error", lambda v: v),
    "duration_ms": ("duration_ms", lambda v: v),
    "profile_id": ("profile_id", lambda v: v),
    "proxy_id": ("proxy_id", lambda v: v),
}


def _row_to_run(row: aiosqlite.Row) -> Run:
    return Run(
        id=row["id"],
        job_id=row["job_id"],
        site_id=row["site_id"],
        started_at=parse_dt(row["started_at"]),
        finished_at=parse_dt(row["finished_at"]),
        status=RunStatus(row["status"]),
        rows_count=row["rows_count"],
        new_count=row["new_count"],
        report_path=row["report_path"],
        artifact_dir=row["artifact_dir"],
        error=row["error"],
        duration_ms=row["duration_ms"],
        profile_id=row["profile_id"],
        proxy_id=row["proxy_id"],
    )


class RunsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, run_id: int) -> Run | None:
        row = await self.db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
        return None if row is None else _row_to_run(row)

    async def create(self, run: Run) -> int:
        return await self.db.execute(
            """INSERT INTO runs (job_id, site_id, started_at, finished_at, status,
                                 rows_count, new_count, report_path, artifact_dir, error,
                                 duration_ms, profile_id, proxy_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.job_id,
                run.site_id,
                dt(run.started_at),
                dt(run.finished_at),
                str(run.status),
                run.rows_count,
                run.new_count,
                run.report_path,
                run.artifact_dir,
                run.error,
                run.duration_ms,
                run.profile_id,
                run.proxy_id,
            ),
        )

    async def update(self, run_id: int, **fields: Any) -> None:
        assignments, params = build_update(fields, _COLUMNS)
        await self.db.execute(f"UPDATE runs SET {assignments} WHERE id = ?", (*params, run_id))

    async def recent(self, limit: int = 10) -> list[Run]:
        """Most recent runs across all sites, newest first."""
        rows = await self.db.fetchall(
            "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
        )
        return [_row_to_run(r) for r in rows]

    async def list_for_site(self, site_id: int, limit: int = 20) -> list[Run]:
        """Most recent runs of a site, newest first."""
        rows = await self.db.fetchall(
            "SELECT * FROM runs WHERE site_id = ? ORDER BY started_at DESC, id DESC LIMIT ?",
            (site_id, limit),
        )
        return [_row_to_run(r) for r in rows]
