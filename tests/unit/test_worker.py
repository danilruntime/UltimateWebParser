"""Tests for the worker loop: recovery, retries, backoff and job outcomes."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from uwp.config import Settings
from uwp.db.database import Database
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.proxies import ProxiesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import (
    ErrorKind,
    Profile,
    ProfileKind,
    ProfileStatus,
    RunStatus,
    Site,
)
from uwp.runner import RunOutcome
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
        ProxiesRepo(db),
        runner,
        scheduler or FakeScheduler(),
        "w1",
    )


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
