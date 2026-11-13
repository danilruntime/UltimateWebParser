"""Scheduling of parser runs at randomized intervals."""
import logging
import random
from datetime import datetime, timedelta

from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.sites import SitesRepo

log = logging.getLogger(__name__)


def next_run_at(
    now: datetime,
    interval_min: int,
    interval_max: int,
    rng: random.Random | None = None,
) -> datetime:
    """Return ``now`` plus a random delay in ``[interval_min, interval_max]`` minutes."""
    rng = rng or random
    return now + timedelta(minutes=rng.randint(interval_min, interval_max))


class Scheduler:
    """Turns due sites into queued jobs and keeps ``next_run_at`` moving.

    ``next_run_at`` is always recomputed from the current time, so a long
    downtime produces exactly one catch-up run instead of a backlog.
    """

    def __init__(self, sites: SitesRepo, jobs: JobsRepo, rng: random.Random | None = None) -> None:
        self.sites = sites
        self.jobs = jobs
        self.rng = rng

    async def tick(self, now: datetime) -> list[int]:
        """Queue jobs for due sites and return the ids of the jobs created."""
        created: list[int] = []
        for site in await self.sites.due(now):
            await self.sites.update(
                site.id,
                next_run_at=next_run_at(
                    now, site.interval_min_min, site.interval_max_min, self.rng
                ),
            )
            job_id = await self.jobs.create(site.id, "scheduled")
            if job_id is None:
                log.debug("site %s already has an active job, skipping", site.slug)
                continue
            created.append(job_id)
        return created

    async def on_enabled(self, site_id: int, now: datetime) -> None:
        """Run an enabled site immediately."""
        await self.sites.update(site_id, next_run_at=now)

    async def on_disabled(self, site_id: int) -> None:
        """Drop queued jobs of a disabled site."""
        await self.jobs.cancel_queued(site_id)
