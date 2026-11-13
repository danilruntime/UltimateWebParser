"""Repository for the job queue."""
import sqlite3
from datetime import datetime, timedelta

import aiosqlite

from uwp.db.database import Database
from uwp.db.repo._common import parse_dt
from uwp.domain import Job, JobKind, JobStatus
from uwp.timeutil import to_db, utcnow


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        id=row["id"],
        site_id=row["site_id"],
        kind=JobKind(row["kind"]),
        status=JobStatus(row["status"]),
        priority=row["priority"],
        attempt=row["attempt"],
        claimed_by=row["claimed_by"],
        claimed_at=parse_dt(row["claimed_at"]),
        heartbeat_at=parse_dt(row["heartbeat_at"]),
        not_before=parse_dt(row["not_before"]),
        error=row["error"],
        created_at=parse_dt(row["created_at"]),
    )


class JobsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, job_id: int) -> Job | None:
        row = await self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return None if row is None else _row_to_job(row)

    async def create(
        self,
        site_id: int,
        kind: JobKind | str,
        priority: int = 0,
        not_before: datetime | None = None,
    ) -> int | None:
        """Queue a job; ``None`` if the site already has a queued/running job."""
        try:
            return await self.db.execute(
                """INSERT INTO jobs (site_id, kind, status, priority, not_before, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (site_id, str(kind), JobStatus.queued.value, priority,
                 None if not_before is None else to_db(not_before), to_db(utcnow())),
            )
        except sqlite3.IntegrityError:
            # ``ux_jobs_active`` guarantees at most one active job per site.
            return None

    async def claim(self, worker_id: str, now: datetime) -> Job | None:
        """Take the highest-priority due queued job and mark it running."""
        stamp = to_db(now)
        async with self.db.transaction():
            row = await self.db.fetchone(
                """UPDATE jobs
                   SET status = ?, claimed_by = ?, claimed_at = ?, heartbeat_at = ?
                   WHERE id = (SELECT id FROM jobs
                               WHERE status = ?
                                 AND (not_before IS NULL OR not_before <= ?)
                               ORDER BY priority DESC, created_at, id
                               LIMIT 1)
                   RETURNING id""",
                (JobStatus.running.value, worker_id, stamp, stamp,
                 JobStatus.queued.value, stamp),
            )
        if row is None:
            return None
        return await self.get(row["id"])

    async def list_for_site(self, site_id: int, limit: int = 20) -> list[Job]:
        """Recent jobs of a site, newest first."""
        rows = await self.db.fetchall(
            "SELECT * FROM jobs WHERE site_id = ? ORDER BY id DESC LIMIT ?",
            (site_id, limit),
        )
        return [_row_to_job(r) for r in rows]

    async def heartbeat(self, job_id: int, now: datetime) -> None:
        await self.db.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (to_db(now), job_id)
        )

    async def heartbeat_all(self, worker_id: str, now: datetime) -> None:
        """Refresh every job this worker owns that is still running."""
        await self.db.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE claimed_by = ? AND status = ?",
            (to_db(now), worker_id, JobStatus.running.value),
        )

    async def set_not_before(self, job_id: int, when: datetime) -> None:
        """Delay a queued job until ``when``."""
        await self.db.execute(
            "UPDATE jobs SET not_before = ? WHERE id = ?", (to_db(when), job_id)
        )

    async def finish(self, job_id: int, status: JobStatus | str, error: str | None = None) -> None:
        await self.db.execute(
            "UPDATE jobs SET status = ?, error = ? WHERE id = ?",
            (str(status), error, job_id),
        )

    async def requeue(self, job_id: int, error: str) -> None:
        """Return a running job to the queue and count the attempt."""
        await self.db.execute(
            """UPDATE jobs
               SET status = ?, error = ?, attempt = attempt + 1,
                   claimed_by = NULL, claimed_at = NULL, heartbeat_at = NULL
               WHERE id = ?""",
            (JobStatus.queued.value, error, job_id),
        )

    async def orphans(self, now: datetime, threshold: timedelta) -> list[Job]:
        """Running jobs whose heartbeat is older than ``threshold``."""
        cutoff = to_db(now - threshold)
        rows = await self.db.fetchall(
            "SELECT * FROM jobs WHERE status = ? AND heartbeat_at IS NOT NULL "
            "AND heartbeat_at < ? ORDER BY id",
            (JobStatus.running.value, cutoff),
        )
        return [_row_to_job(r) for r in rows]

    async def cancel_queued(self, site_id: int) -> None:
        await self.db.execute(
            "UPDATE jobs SET status = ? WHERE site_id = ? AND status = ?",
            (JobStatus.canceled.value, site_id, JobStatus.queued.value),
        )
