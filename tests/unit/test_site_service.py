"""Tests for the site list service used by the bot."""
from datetime import UTC, datetime

import pytest

from uwp.db.database import Database
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.i18n import Lang
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


async def test_a_default_site_name_follows_the_deployment_language(env):
    """An English admin adding Steam should not get a Russian site name."""
    _db, sites, jobs = env
    english = SiteService(
        sites, jobs, Scheduler(sites, jobs), registry=_registry(), lang=Lang.en
    )

    site = await english.create("steam_deals")

    assert site.name == "Steam — deals"


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

    # ``update`` is the settings screen's door to every column but ``name``:
    # a bare ``**fields`` here would be an unvalidated second way to store a
    # name Telegram's button rules reject, so it must refuse and point at
    # ``rename``.
    with pytest.raises(TypeError, match="rename"):
        await svc.update(site.id, name="x")
    assert (await sites.get(site.id)).name == site.name

    updated = await svc.update(site.id, interval_min_min=10)
    assert updated.interval_min_min == 10

    renamed = await svc.rename(site.id, "Renamed")
    assert renamed.name == "Renamed"

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


async def test_the_unknown_parser_error_follows_the_deployment_language(env):
    """The CLI and the bot print this text verbatim; English used to see Russian."""
    _db, sites, jobs = env
    russian = SiteService(
        sites, jobs, Scheduler(sites, jobs), registry=_registry(), lang=Lang.ru
    )
    english = SiteService(
        sites, jobs, Scheduler(sites, jobs), registry=_registry(), lang=Lang.en
    )

    with pytest.raises(ValueError, match="неизвестный парсер: nope"):
        await russian.create("nope")
    with pytest.raises(ValueError, match="unknown parser: nope"):
        await english.create("nope")


async def test_the_unknown_parser_error_follows_the_admin_not_the_service(env):
    """The bot builds one service per deployment; the admin in front of it differs.

    Same half-fix as ``ProfilesService.create``: an explicit language must reach
    the raise, otherwise a ``ru`` deployment shows a Russian refusal to an ``en``
    admin who picked a parser the registry does not know.
    """
    _db, sites, jobs = env
    service = SiteService(
        sites, jobs, Scheduler(sites, jobs), registry=_registry(), lang=Lang.ru
    )

    with pytest.raises(ValueError, match="unknown parser: nope"):
        await service.create("nope", lang=Lang.en)


async def test_create_can_name_the_site_in_the_admins_language(env):
    """The bot's service is built for the deployment; the admin differs.

    ``de6e5f0`` made ``self.lang`` decide the default name, which is only true
    when the deployment and the admin agree. The handler passes its own
    language, and the explicit argument has to win.
    """
    _db, sites, jobs = env
    russian = SiteService(
        sites, jobs, Scheduler(sites, jobs), registry=_registry(), lang=Lang.ru
    )

    site = await russian.create("steam_deals", lang=Lang.en)

    assert site.name == "Steam — deals"


def test_clean_site_name_collapses_whitespace():
    from uwp.services.sites import clean_site_name

    assert clean_site_name("  Steam \n US\t(скидки) ") == "Steam US (скидки)"


def test_clean_site_name_rejects_an_empty_name_in_english():
    from uwp.services.sites import clean_site_name

    with pytest.raises(ValueError, match="cannot be empty"):
        clean_site_name("   \n ", Lang.en)


def test_clean_site_name_rejects_a_name_that_is_only_invisible_characters():
    """``\\u200b`` is not whitespace, so ``split()`` keeps it and ``not cleaned``
    is false; a button would then read as an empty name."""
    from uwp.services.sites import clean_site_name

    with pytest.raises(ValueError, match="cannot be empty"):
        clean_site_name("\u200b", Lang.en)
    with pytest.raises(ValueError, match="cannot be empty"):
        clean_site_name("  \ufeff  ", Lang.en)


def test_clean_site_name_rejects_a_name_over_the_button_limit():
    from uwp.services.sites import MAX_SITE_NAME, clean_site_name

    assert clean_site_name("x" * MAX_SITE_NAME) == "x" * MAX_SITE_NAME
    with pytest.raises(ValueError, match="64"):
        clean_site_name("x" * (MAX_SITE_NAME + 1))


async def test_create_refuses_an_empty_explicit_name(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())

    with pytest.raises(ValueError, match="пустым"):
        await svc.create("steam_deals", name=" ")

    assert await svc.list() == []


async def test_create_stores_the_given_name(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())

    site = await svc.create("steam_deals", name="  Мои   скидки ")

    assert site.name == "Мои скидки"
    assert site.slug == "steam_deals"


async def test_rename_changes_the_name_and_keeps_the_slug(env):
    _db, sites, jobs = env
    svc = SiteService(sites, jobs, Scheduler(sites, jobs), registry=_registry())
    site = await svc.create("steam_deals")
    slug = site.slug

    renamed = await svc.rename(site.id, "Подарки", lang=Lang.ru)

    assert renamed.name == "Подарки"
    assert renamed.slug == slug
    assert (await sites.get(site.id)).name == "Подарки"
