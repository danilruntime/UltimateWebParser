"""Tests for the run scheduler: random intervals, catch-up and cancellation."""
import random
from datetime import UTC, datetime, timedelta

import pytest

from uwp.db.database import Database
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import Site
from uwp.scheduler import Scheduler, next_run_at

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
async def db():
    d = await Database.in_memory()
    await d.migrate()
    yield d
    await d.close()


def test_next_run_within_range():
    # The range 45..90 holds only 46 distinct values, so uniqueness is capped;
    # check that a large sample covers most of it and never leaves the bounds.
    values = {next_run_at(NOW, 45, 90, random.Random(i)) for i in range(1000)}
    assert all(NOW + timedelta(minutes=45) <= v <= NOW + timedelta(minutes=90) for v in values)
    assert len(values) >= 40


def test_fixed_interval():
    assert next_run_at(NOW, 60, 60) == NOW + timedelta(minutes=60)


async def _site(repo, **kw):
    return await repo.create(
        Site(
            slug="s",
            name="S",
            parser_key="steam_deals",
            interval_min_min=45,
            interval_max_min=90,
            **kw,
        )
    )


async def test_tick_creates_one_job_for_due_site(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await _site(sites, enabled=True, next_run_at=NOW - timedelta(hours=3))
    sched = Scheduler(sites, jobs)

    created = await sched.tick(NOW)

    assert len(created) == 1
    job = await jobs.get(created[0])
    assert job.site_id == sid
    assert str(job.kind) == "scheduled"
    assert str(job.status) == "queued"

    # The interval is recomputed from ``now``: one catch-up, not a backlog.
    site = await sites.get(sid)
    assert NOW < site.next_run_at <= NOW + timedelta(minutes=90)
    assert await sched.tick(NOW) == []


async def test_tick_skips_site_with_active_job(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await _site(sites, enabled=True, next_run_at=NOW - timedelta(minutes=1))
    await jobs.create(sid, "manual")
    sched = Scheduler(sites, jobs)

    assert await sched.tick(NOW) == []
    site = await sites.get(sid)
    assert site.next_run_at > NOW  # still advanced, no retry storm


async def test_disabled_site_is_skipped(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    await _site(sites, enabled=False, next_run_at=NOW - timedelta(hours=1))
    sched = Scheduler(sites, jobs)

    assert await sched.tick(NOW) == []


async def test_on_disabled_cancels_queued_jobs(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await _site(sites, enabled=True, next_run_at=NOW)
    sched = Scheduler(sites, jobs)
    await sched.tick(NOW)

    await sched.on_disabled(sid)

    job = (await jobs.list_for_site(sid))[0]
    assert str(job.status) == "canceled"


async def test_on_enabled_schedules_immediately(db):
    sites, jobs = SitesRepo(db), JobsRepo(db)
    sid = await _site(sites, enabled=True, next_run_at=NOW + timedelta(hours=5))
    sched = Scheduler(sites, jobs)

    await sched.on_enabled(sid, NOW)

    site = await sites.get(sid)
    assert site.next_run_at == NOW
    assert len(await sched.tick(NOW)) == 1
