"""Worker loop: claims jobs, runs parsers and keeps job/site state consistent."""
import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from uwp.config import Settings
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.proxies import ProxiesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import ErrorKind, Job, JobStatus, ProfileKind, RunStatus, Site
from uwp.events import RUN_FAILED, RUN_FINISHED, SITE_PROBLEM, EventBus
from uwp.runner import RunOutcome
from uwp.scheduler import Scheduler
from uwp.timeutil import utcnow

log = logging.getLogger(__name__)

# Kinds worth another attempt: the site or the network may recover.
RETRYABLE = frozenset({ErrorKind.transient, ErrorKind.blocked})
# Seconds of backoff per already failed attempt.
RETRY_BACKOFF_SECONDS = 60
# Consecutive failures after which the admin hears that a site looks broken.
PROBLEM_AFTER_FAILURES = 3


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
        bus: EventBus | None = None,
    ) -> None:
        self.settings = settings
        self.jobs = jobs
        self.sites = sites
        self.profiles = profiles
        self.proxies = proxies
        self.runner = runner
        self.scheduler = scheduler
        self.worker_id = worker_id
        self.bus = bus

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
            await self.jobs.finish(job.id, JobStatus.canceled)
            return True

        site = await self.sites.get(job.site_id)
        if site is None:
            await self.jobs.finish(job.id, JobStatus.failed, "site not found")
            return True

        proxy = None
        if site.proxy_id is not None:
            proxy = await self.proxies.get(site.proxy_id)

        beat = asyncio.create_task(self._beat(job.id))
        try:
            outcome = await self.runner.run(site, job, proxy, cancel=asyncio.Event())
        except Exception as exc:  # the runner itself must not kill the worker
            log.exception("job %s of site %s crashed the runner", job.id, site.slug)
            outcome = RunOutcome(
                status=RunStatus.failed,
                error_kind=ErrorKind.parser_error,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

        await self._finish(job, site, outcome, now)
        return True

    async def _beat(self, job_id: int) -> None:
        """Refresh a running job's heartbeat until cancelled.

        A run can last up to ``run_timeout`` (300 s), far longer than
        ``orphan_threshold`` (90 s), so the heartbeat cannot wait for the
        main loop to come around.
        """
        interval = max(1, self.settings.heartbeat_interval)
        while True:
            await asyncio.sleep(interval)
            try:
                await self.jobs.heartbeat(job_id, utcnow())
            except Exception:
                log.exception("heartbeat of job %s failed", job_id)

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
        failures = site.consecutive_failures + 1 if failed else 0
        await self.sites.update(
            site.id,
            last_run_at=now,
            last_status=str(outcome.status),
            consecutive_failures=failures,
        )
        # A retryable failure is retried silently; the admin hears about it only
        # once the attempts are exhausted.
        if not restarted:
            await self._notify(site, outcome, failures)

    async def _notify(self, site: Site, outcome: RunOutcome, failures: int) -> None:
        """Queue the admin notification of a finished run."""
        if self.bus is None:
            return
        base = {
            "site": site.name,
            "site_slug": site.slug,
            "site_id": site.id,
            "status": str(outcome.status),
            "rows": len(outcome.rows),
            "new": outcome.new_count,
            "run_id": outcome.run_id,
        }
        try:
            if outcome.status in (RunStatus.ok, RunStatus.partial):
                payload = dict(base, report_path=str(outcome.report_path or ""))
                await self.bus.emit(RUN_FINISHED, site.id, outcome.run_id, payload)
            elif outcome.status == RunStatus.failed:
                payload = dict(base, text=outcome.error or "неизвестная ошибка",
                               error_kind=str(outcome.error_kind or ""))
                if outcome.artifacts_dir is not None:
                    screenshot = outcome.artifacts_dir / "screenshot.png"
                    if screenshot.is_file():
                        payload["screenshot"] = str(screenshot)
                await self.bus.emit(RUN_FAILED, site.id, outcome.run_id, payload)
                if failures >= PROBLEM_AFTER_FAILURES:
                    await self.bus.emit(
                        SITE_PROBLEM,
                        site.id,
                        outcome.run_id,
                        dict(base, text=f"{failures} провала подряд"),
                    )
        except Exception:  # a notification is never worth crashing the worker
            log.exception("queueing a notification for site %s failed", site.slug)

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
