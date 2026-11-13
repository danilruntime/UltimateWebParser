"""Worker loop: claims jobs, runs parsers and keeps job/site state consistent."""
import asyncio
import logging
from datetime import datetime, timedelta

from uwp.config import Settings
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.proxies import ProxiesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import ErrorKind, Job, JobStatus, ProfileKind, RunStatus, Site
from uwp.runner import RunOutcome
from uwp.scheduler import Scheduler
from uwp.timeutil import utcnow

log = logging.getLogger(__name__)

# Kinds worth another attempt: the site or the network may recover.
RETRYABLE = frozenset({ErrorKind.transient, ErrorKind.blocked})
# Seconds of backoff per already failed attempt.
RETRY_BACKOFF_SECONDS = 60


class Worker:
    """Turns queued jobs into parser runs and retries the recoverable ones."""

    def __init__(
        self,
        settings: Settings,
        jobs: JobsRepo,
        sites: SitesRepo,
        profiles: ProfilesRepo,
        proxies: ProxiesRepo,
        runner: object,
        scheduler: Scheduler,
        worker_id: str,
    ) -> None:
        self.settings = settings
        self.jobs = jobs
        self.sites = sites
        self.profiles = profiles
        self.proxies = proxies
        self.runner = runner
        self.scheduler = scheduler
        self.worker_id = worker_id

    async def recover(self, now: datetime) -> None:
        """Requeue jobs of dead workers and release stale profile leases."""
        threshold = timedelta(seconds=self.settings.orphan_threshold)
        for job in await self.jobs.orphans(now, threshold):
            site = await self.sites.get(job.site_id)
            if (
                site is not None
                and site.profile_mode == ProfileKind.persistent
                and site.profile_id is not None
            ):
                await self.profiles.release(site.profile_id)
            log.warning("requeueing orphaned job %s of site %s", job.id, job.site_id)
            await self.jobs.requeue(job.id, "orphaned: heartbeat timed out")
        await self.profiles.release_expired(now)

    async def process_one(self, now: datetime) -> bool:
        """Claim and run at most one job; ``False`` when the queue was empty."""
        job = await self.jobs.claim(self.worker_id, now)
        if job is None:
            return False

        # A cancel may have arrived between queueing and claiming.
        current = await self.jobs.get(job.id)
        if current is not None and current.status == JobStatus.canceled:
            return True

        site = await self.sites.get(job.site_id)
        if site is None:
            await self.jobs.finish(job.id, JobStatus.failed, "site not found")
            return True

        proxy = None
        if site.proxy_id is not None:
            proxy = await self.proxies.get(site.proxy_id)

        try:
            outcome = await self.runner.run(site, job, proxy, cancel=asyncio.Event())
        except Exception as exc:  # the runner itself must not kill the worker
            log.exception("job %s of site %s crashed the runner", job.id, site.slug)
            outcome = RunOutcome(
                status=RunStatus.failed,
                error_kind=ErrorKind.parser_error,
                error=f"{type(exc).__name__}: {exc}",
            )

        await self._finish(job, site, outcome, now)
        return True

    async def _finish(
        self, job: Job, site: Site, outcome: RunOutcome, now: datetime
    ) -> None:
        restarted = await self._maybe_retry(job, site, outcome, now)
        if not restarted:
            if outcome.status == RunStatus.canceled:
                status = JobStatus.canceled
            elif outcome.status in (RunStatus.ok, RunStatus.partial):
                status = JobStatus.done
            else:
                status = JobStatus.failed
            await self.jobs.finish(job.id, status, outcome.error)

        failed = outcome.status == RunStatus.failed
        await self.sites.update(
            site.id,
            last_run_at=now,
            last_status=str(outcome.status),
            consecutive_failures=site.consecutive_failures + 1 if failed else 0,
        )

    async def _maybe_retry(
        self, job: Job, site: Site, outcome: RunOutcome, now: datetime
    ) -> bool:
        if outcome.status != RunStatus.failed:
            return False
        if outcome.error_kind not in RETRYABLE:
            return False
        if job.attempt >= site.max_attempts:
            return False
        delay = timedelta(seconds=RETRY_BACKOFF_SECONDS * (job.attempt + 1))
        await self.jobs.requeue(job.id, outcome.error or "retry")
        await self.jobs.set_not_before(job.id, now + delay)
        return True

    async def heartbeat(self, job_id: int, now: datetime) -> None:
        """Refresh the lease of a running job."""
        await self.jobs.heartbeat(job_id, now)

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Main loop: recover, schedule, then run up to ``max_parallel_runs`` jobs."""
        beat_every = timedelta(seconds=self.settings.heartbeat_interval)
        last_beat: datetime | None = None
        while not stop.is_set():
            now = utcnow()
            try:
                await self.recover(now)
                await self.scheduler.tick(now)
                if last_beat is None or now - last_beat >= beat_every:
                    await self.jobs.heartbeat_all(self.worker_id, now)
                    last_beat = now
                ran = await asyncio.gather(
                    *(
                        self.process_one(now)
                        for _ in range(max(1, self.settings.max_parallel_runs))
                    )
                )
            except Exception:  # keep the loop alive whatever a single tick does
                log.exception("worker iteration failed")
                ran = []
            if not any(ran):
                await _sleep(stop, 1.0)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    """Sleep, but wake up as soon as ``stop`` is set."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
