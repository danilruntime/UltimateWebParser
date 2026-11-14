"""Tests for the worker loop: recovery, retries, backoff and job outcomes."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from uwp.config import Settings
from uwp.db.database import Database
from uwp.db.repo.admins import AdminsRepo
from uwp.db.repo.events import EventsRepo
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.proxies import ProxiesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import (
    ErrorKind,
    JobStatus,
    Profile,
    ProfileKind,
    ProfileStatus,
    RunStatus,
    Site,
)
from uwp.events import EventBus
from uwp.runner import RunOutcome
from uwp.services.proxies import ProxyService
from uwp.worker import Worker

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


class FakeRunner:
    def __init__(self, outcome=None):
        self.outcome = outcome or RunOutcome(status=RunStatus.ok)
        self.calls = 0
        self.seen = []

    async def run(self, site, job, proxy, cancel=None):
        self.calls += 1
        self.seen.append((site, job, proxy, cancel))
        return self.outcome


class FakeScheduler:
    def __init__(self):
        self.ticks = 0

    async def tick(self, now):
        self.ticks += 1
        return []


@pytest.fixture
async def db():
    d = await Database.in_memory()
    await d.migrate()
    yield d
    await d.close()


@pytest.fixture
async def env(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await sites.create(
        Site(
            slug="s",
            name="S",
            parser_key="steam_deals",
            interval_min_min=1,
            interval_max_min=2,
            enabled=True,
        )
    )
    return db, sites, jobs, sid


def _worker(settings, db, jobs, sites, runner, scheduler=None):
    return Worker(
        settings,
        jobs,
        sites,
        ProfilesRepo(db),
        ProxyService(ProxiesRepo(db), settings),
        runner,
        scheduler or FakeScheduler(),
        "w1",
    )


async def test_the_runner_gets_the_decrypted_proxy_password(env):
    """An encrypted proxy password must reach the upstream proxy, not the ciphertext."""
    db, sites, jobs, sid = env
    settings = Settings(secret_key="k" * 32)
    service = ProxyService(ProxiesRepo(db), settings)
    proxy = await service.add("p", "http://user:s3cr3t@127.0.0.1:8080")
    assert proxy.password.startswith("gAAAAA")  # stored encrypted
    await sites.update(sid, proxy_id=proxy.id)
    await jobs.create(sid, "manual")
    runner = FakeRunner()
    worker = _worker(settings, db, jobs, sites, runner)

    await worker.process_one(NOW)

    seen_proxy = runner.seen[0][2]
    assert seen_proxy.password == "s3cr3t"


async def test_orphaned_running_job_is_requeued(env):
    db, sites, jobs, sid = env
    jid = await jobs.create(sid, "scheduled")
    await jobs.claim("dead", now=NOW - timedelta(minutes=10))
    worker = _worker(Settings(), db, jobs, sites, FakeRunner())

    await worker.recover(NOW)

    job = await jobs.get(jid)
    assert str(job.status) == "queued"
    assert job.attempt == 1
    assert job.claimed_by is None


async def test_recover_releases_persistent_profile(env):
    db, sites, jobs, sid = env
    profiles = ProfilesRepo(db)
    pid = await profiles.create(
        Profile(
            name="p",
            kind=ProfileKind.persistent,
            seed=1,
            platform="linux",
            brand="Chrome",
            brand_version="150",
            timezone="UTC",
            locale="en-US",
            user_data_dir="/tmp/p",
        )
    )
    await profiles.lease(pid, until=NOW + timedelta(hours=1))
    await sites.update(sid, profile_mode="persistent", profile_id=pid)
    jid = await jobs.create(sid, "scheduled")
    await jobs.claim("dead", now=NOW - timedelta(minutes=10))
    worker = _worker(Settings(), db, jobs, sites, FakeRunner())

    await worker.recover(NOW)

    assert (await profiles.get(pid)).status == ProfileStatus.free
    assert str((await jobs.get(jid)).status) == "queued"


async def test_ok_run_marks_job_done(env):
    db, sites, jobs, sid = env
    await jobs.create(sid, "scheduled")
    runner = FakeRunner(RunOutcome(status=RunStatus.ok))
    worker = _worker(Settings(), db, jobs, sites, runner)

    assert await worker.process_one(NOW) is True

    job = (await jobs.list_for_site(sid))[0]
    assert str(job.status) == "done"
    assert runner.calls == 1


async def test_process_one_returns_false_when_idle(env):
    db, sites, jobs, _sid = env
    worker = _worker(Settings(), db, jobs, sites, FakeRunner())

    assert await worker.process_one(NOW) is False


async def test_transient_failure_is_requeued_with_backoff(env):
    db, sites, jobs, sid = env
    await jobs.create(sid, "scheduled")
    outcome = RunOutcome(
        status=RunStatus.failed, error_kind=ErrorKind.transient, error="502"
    )
    worker = _worker(Settings(), db, jobs, sites, FakeRunner(outcome))

    assert await worker.process_one(NOW) is True

    job = (await jobs.list_for_site(sid))[0]
    assert str(job.status) == "queued"
    assert job.attempt == 1
    assert job.not_before == NOW + timedelta(seconds=60)


async def test_retries_stop_after_max_attempts(env):
    db, sites, jobs, sid = env
    await sites.update(sid, max_attempts=1)
    await jobs.create(sid, "scheduled")
    await jobs.claim("w1", now=NOW)
    await jobs.requeue((await jobs.list_for_site(sid))[0].id, "boom")  # attempt = 1
    outcome = RunOutcome(
        status=RunStatus.failed, error_kind=ErrorKind.blocked, error="403"
    )
    worker = _worker(Settings(), db, jobs, sites, FakeRunner(outcome))

    await worker.process_one(NOW)

    job = (await jobs.list_for_site(sid))[0]
    assert str(job.status) == "failed"
    assert job.attempt == 1


async def test_login_required_fails_without_retry(env):
    db, sites, jobs, sid = env
    await jobs.create(sid, "scheduled")
    outcome = RunOutcome(
        status=RunStatus.failed, error_kind=ErrorKind.login_required, error="login"
    )
    worker = _worker(Settings(), db, jobs, sites, FakeRunner(outcome))

    await worker.process_one(NOW)

    job = (await jobs.list_for_site(sid))[0]
    assert str(job.status) == "failed"
    assert job.attempt == 0


async def test_canceled_status_is_recorded(env):
    db, sites, jobs, sid = env
    await jobs.create(sid, "scheduled")
    runner = FakeRunner(RunOutcome(status=RunStatus.canceled))
    worker = _worker(Settings(), db, jobs, sites, runner)

    await worker.process_one(NOW)

    assert str((await jobs.list_for_site(sid))[0].status) == "canceled"


async def test_site_snapshot_is_updated_after_run(env):
    db, sites, jobs, sid = env
    await jobs.create(sid, "scheduled")
    worker = _worker(Settings(), db, jobs, sites, FakeRunner())

    await worker.process_one(NOW)

    site = await sites.get(sid)
    assert site.last_run_at is not None
    assert site.last_status == "ok"
    assert site.consecutive_failures == 0


async def test_heartbeat_runs_while_job_executes(env):
    """A run longer than ``orphan_threshold`` must keep its heartbeat fresh."""
    db, sites, jobs, sid = env
    settings = Settings(heartbeat_interval=1, orphan_threshold=1)
    await jobs.create(sid, "scheduled")

    class SlowRunner(FakeRunner):
        async def run(self, site, job, proxy, cancel=None):
            await asyncio.sleep(2.2)
            return self.outcome

    worker = _worker(settings, db, jobs, sites, SlowRunner())
    beats: list[tuple[int, datetime]] = []
    original = jobs.heartbeat

    async def spy(job_id, now):
        beats.append((job_id, now))
        await original(job_id, now)

    jobs.heartbeat = spy

    await worker.process_one(NOW)

    job = (await jobs.list_for_site(sid))[0]
    assert str(job.status) == "done"
    # ``finish`` clears the stamp, so observe the beats themselves.
    assert beats and all(job_id == job.id for job_id, _ in beats)
    assert beats[0][1] > NOW
    # A 2.2 s run at a 1 s interval must be beaten more than once.
    assert len(beats) >= 2


async def test_canceled_job_is_finished_not_left_running(env):
    db, sites, jobs, sid = env
    jid = await jobs.create(sid, "scheduled")
    claimed = await jobs.claim("w1", now=NOW)

    async def claim(worker_id, now):
        return claimed

    jobs.claim = claim
    # A cancel lands after the claim, so the fresh row is already canceled.
    await db.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (jid,))
    runner = FakeRunner()
    worker = _worker(Settings(), db, jobs, sites, runner)

    assert await worker.process_one(NOW) is True

    assert str((await jobs.get(jid)).status) == "canceled"
    assert runner.calls == 0


async def test_run_forever_stops_on_event(env):
    db, sites, jobs, sid = env
    await jobs.create(sid, "scheduled")
    scheduler = FakeScheduler()
    worker = _worker(Settings(), db, jobs, sites, FakeRunner(), scheduler)
    stop = asyncio.Event()

    task = asyncio.create_task(worker.run_forever(stop))
    for _ in range(100):
        if (await jobs.list_for_site(sid))[0].status in ("done", "failed"):
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert scheduler.ticks >= 1
    assert str((await jobs.list_for_site(sid))[0].status) == "done"


class SlowRunner:
    """A runner that suspends long enough for a sibling cancellation to land."""

    def __init__(self, delay: float = 0.2):
        self.delay = delay
        self.started = asyncio.Event()
        self.finished = asyncio.Event()

    async def run(self, site, job, proxy, cancel=None):
        # Set before the sleep so the crashing slot can land its failure while
        # this run is in flight, not before it has claimed its job.
        self.started.set()
        await asyncio.sleep(self.delay)
        self.finished.set()
        return RunOutcome(status=RunStatus.ok)


async def test_a_crashed_slot_does_not_abort_a_sibling_run(env):
    """A crashing slot must not cancel — or detach — a sibling that owns a job."""
    db, sites, jobs, sid = env
    runner = SlowRunner()
    worker = _worker(Settings(max_parallel_runs=2), db, jobs, sites, runner)
    job_id = await jobs.create(sid, "manual")
    stop = asyncio.Event()
    real_process_one = worker.process_one
    calls = 0

    async def flaky(now):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Crash only once the sibling is inside ``runner.run`` with a
            # claimed job, which is the state a cancellation would strand.
            await asyncio.wait_for(runner.started.wait(), 5)
            stop.set()  # one iteration is enough; the loop must not spin
            raise RuntimeError("boom")
        return await real_process_one(now)

    worker.process_one = flaky

    async with asyncio.timeout(10):
        await worker.run_forever(stop)

    # The other slot owned a real claimed job. It must complete normally:
    # under TaskGroup it was cancelled mid-run and the row stayed `running`.
    assert runner.started.is_set()
    assert runner.finished.is_set()
    job = await jobs.get(job_id)
    assert job is not None
    assert job.status == JobStatus.done
    assert job.claimed_by is None
    # The site must be able to queue again: `ux_jobs_active` is the real victim
    # of a stranded row.
    assert await jobs.create(sid, "manual") is not None


async def test_ok_run_emits_a_finished_event(env):
    db, sites, jobs, sid = env
    await jobs.create(sid, "scheduled")
    settings = Settings()
    bus = EventBus(EventsRepo(db), AdminsRepo(db), settings)
    # ``events.run_id`` is a foreign key, so an unsaved run id cannot be used.
    outcome = RunOutcome(status=RunStatus.ok, rows=[{"external_key": "1"}], new_count=1)
    worker = _worker(settings, db, jobs, sites, FakeRunner(outcome))
    worker.bus = bus

    await worker.process_one(NOW)

    events = await bus.pending(111, NOW)
    assert [event.kind for event in events] == ["run_finished"]
    payload = events[0].payload
    assert payload["site"] == "S" and payload["rows"] == 1 and payload["new"] == 1


async def test_failed_run_emits_a_failure_and_then_a_problem(env):
    db, sites, jobs, sid = env
    settings = Settings()
    bus = EventBus(EventsRepo(db), AdminsRepo(db), settings)
    outcome = RunOutcome(
        status=RunStatus.failed,
        error_kind=ErrorKind.login_required,
        error="login wall",
    )
    worker = _worker(settings, db, jobs, sites, FakeRunner(outcome))
    worker.bus = bus
    # Two failures already behind the site, so this third one is a «problem».
    await sites.update(sid, consecutive_failures=2)

    await jobs.create(sid, "scheduled")
    await worker.process_one(NOW)

    kinds = [event.kind for event in await bus.pending(111, NOW)]
    assert kinds == ["run_failed", "site_problem"]
    assert (await sites.get(sid)).consecutive_failures == 3


async def test_a_retried_failure_is_not_announced(env):
    db, sites, jobs, sid = env
    settings = Settings()
    bus = EventBus(EventsRepo(db), AdminsRepo(db), settings)
    outcome = RunOutcome(
        status=RunStatus.failed, error_kind=ErrorKind.transient, error="timeout"
    )
    worker = _worker(settings, db, jobs, sites, FakeRunner(outcome))
    worker.bus = bus
    await jobs.create(sid, "scheduled")

    await worker.process_one(NOW)

    # The job went back to the queue for a retry, so nothing is reported yet.
    assert [event.kind for event in await bus.pending(111, NOW)] == []
    assert str((await jobs.list_for_site(sid))[0].status) == "queued"


async def test_a_retry_marks_the_site_as_retrying(env):
    """The card must not say «ошибка» while a retry is still pending."""
    db, sites, jobs, sid = env
    settings = Settings()
    bus = EventBus(EventsRepo(db), AdminsRepo(db), settings)
    outcome = RunOutcome(
        status=RunStatus.failed, error_kind=ErrorKind.transient, error="timeout"
    )
    worker = _worker(settings, db, jobs, sites, FakeRunner(outcome))
    worker.bus = bus
    await jobs.create(sid, "scheduled")

    await worker.process_one(NOW)

    site = await sites.get(sid)
    assert site.last_status == "retrying"
    assert site.consecutive_failures == 1


async def test_an_export_failure_still_notifies(env):
    """A run that produced rows but no file is news, not silence."""
    db, sites, jobs, sid = env
    settings = Settings()
    bus = EventBus(EventsRepo(db), AdminsRepo(db), settings)
    outcome = RunOutcome(
        status=RunStatus.ok,
        rows=[{"external_key": "1", "title": "x"}],
        report_status="failed",
        report_error="OSError: disk full",
    )
    worker = _worker(settings, db, jobs, sites, FakeRunner(outcome))
    worker.bus = bus
    await jobs.create(sid, "scheduled")

    await worker.process_one(NOW)

    events = await bus.pending(111, NOW)
    assert [event.kind for event in events] == ["run_finished"]
    assert events[0].payload["report_status"] == "failed"
    assert "disk full" in events[0].payload["report_error"]


async def test_a_skipped_export_in_changes_mode_is_silent(env):
    db, sites, jobs, sid = env
    settings = Settings()
    bus = EventBus(EventsRepo(db), AdminsRepo(db), settings)
    await sites.update(sid, export_mode="changes")
    outcome = RunOutcome(
        status=RunStatus.ok,
        rows=[{"external_key": "1", "title": "x"}],
        report_status="skipped",
    )
    worker = _worker(settings, db, jobs, sites, FakeRunner(outcome))
    worker.bus = bus
    await jobs.create(sid, "scheduled")

    await worker.process_one(NOW)

    assert await bus.pending(111, NOW) == []
