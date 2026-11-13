"""Tests for the site list service used by the bot."""
from datetime import UTC, datetime

import pytest

from uwp.db.database import Database
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.parsers.registry import ParserRegistry
from uwp.scheduler import Scheduler
from uwp.services.sites import SiteService

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _registry():
    """The real discovered parsers; tests need ``steam_deals`` to exist."""
    return ParserRegistry.discover()


@pytest.fixture
async def env():
    db = await Database.in_memory()
    await db.migrate()
    sites, jobs = SitesRepo(db), JobsRepo(db)
    yield db, sites, jobs
    await db.close()


async def test_create_is_disabled_and_enable_schedules_now(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())

    site = await svc.create("steam_deals", name="Steam US")

    assert site.enabled is False
    assert site.slug == "steam_us"
    assert site.next_run_at is None

    site = await svc.set_enabled(site.id, True, NOW)

    assert site.enabled is True
    assert site.next_run_at == NOW


async def test_create_uses_parser_title_and_unique_slug(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())

    first = await svc.create("steam_deals")
    second = await svc.create("steam_deals")

    assert first.name
    assert first.slug != second.slug


async def test_disable_cancels_queued_jobs(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())
    site = await svc.create("steam_deals")
    job_id = await svc.run_now(site.id)

    site = await svc.set_enabled(site.id, False, NOW)

    assert site.enabled is False
    assert str((await jobs.get(job_id)).status) == "canceled"


async def test_run_now_returns_none_when_active(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())
    site = await svc.create("steam_deals")

    assert await svc.run_now(site.id) is not None
    assert await svc.run_now(site.id) is None


async def test_update_and_delete(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())
    site = await svc.create("steam_deals")

    updated = await svc.update(site.id, name="Renamed")
    assert updated.name == "Renamed"

    await svc.delete(site.id)
    assert await sites.get(site.id) is None


async def test_list_returns_sites(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())
    await svc.create("steam_deals")

    assert len(await svc.list()) == 1


async def test_run_now_on_a_missing_site_raises(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())

    with pytest.raises(ValueError):
        await svc.run_now(999)
