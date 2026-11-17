"""Tests for the VNC service: URL selection, extension and listing."""
from datetime import timedelta

import pytest

from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import (
    Profile,
    ProfileKind,
    ProfileStatus,
    Proxy,
    ProxyScheme,
    Site,
    VncSession,
    VncStatus,
)
from uwp.services.vnc import VncService
from uwp.timeutil import utcnow


class FakeManager:
    def __init__(self):
        self.calls = []
        self.stopped = []
        self.launched = []

    async def reserve_and_launch(
        self, profile, url, proxy_url=None, started_by=None, proxy_id=None
    ):
        self.calls.append((profile.id, url))
        self.proxy_ids = getattr(self, "proxy_ids", []) + [proxy_id]
        return "info"

    async def reserve(self, profile, url, started_by=None, proxy_id=None):
        self.calls.append((profile.id, url))
        self.proxy_ids = getattr(self, "proxy_ids", []) + [proxy_id]
        return "info"

    async def launch(self, session, proxy_url=None):
        self.launched.append(session)

    async def request_stop(self, session_id):
        self.stopped.append(session_id)

    async def stop(self, session_id):
        self.stopped.append(session_id)

    async def list(self):
        return []


@pytest.fixture
async def env(tmp_path):
    db = await Database.in_memory()
    await db.migrate()
    yield db, ProfilesRepo(db), SitesRepo(db), VncRepo(db), tmp_path
    await db.close()


async def _profile(repo, tmp_path):
    return await repo.create(
        Profile(
            name="p",
            kind=ProfileKind.persistent,
            seed=1,
            platform="windows",
            brand="Chrome",
            brand_version="150",
            timezone="Europe/Moscow",
            locale="ru-RU",
            user_data_dir=str(tmp_path / "p"),
            status=ProfileStatus.free,
        )
    )


async def _site(repo):
    return await repo.create(
        Site(
            slug="s",
            name="S",
            parser_key="steam_deals",
            interval_min_min=1,
            interval_max_min=2,
        )
    )


async def test_request_uses_the_parser_url(env):
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    sid = await _site(sites)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(
        manager,
        profiles,
        sites,
        settings,
        sessions,
        parser_urls={"steam_deals": "https://store.steampowered.com/"},
    )

    info = await svc.request(pid, site_id=sid, started_by=7)

    assert info == "info"
    assert manager.calls == [(pid, "https://store.steampowered.com/")]


async def test_request_without_site_uses_example(env):
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(manager, profiles, sites, settings, sessions)

    await svc.request(pid)

    assert manager.calls == [(pid, "https://example.com")]


async def test_request_passes_the_site_proxy_to_the_session(env):
    """The site's proxy wins over the profile's, and the session remembers it."""
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    site = await _site(sites)
    proxy = Proxy(name="P", scheme=ProxyScheme.http, host="127.0.0.1", port=3128)
    from uwp.db.repo.proxies import ProxiesRepo

    proxy_id = await ProxiesRepo(_db).create(proxy)
    await sites.update(site, proxy_id=proxy_id)
    await profiles.update(pid, proxy_id=None)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(manager, profiles, sites, settings, sessions)

    await svc.request(pid, site_id=site)

    assert manager.proxy_ids == [proxy_id]


async def test_request_rejects_a_missing_profile(env):
    _db, profiles, sites, sessions, tmp_path = env
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    with pytest.raises(ValueError):
        await svc.request(999)


async def test_the_missing_profile_error_follows_the_deployment_language(env):
    """The bot shows this text in the admin's own chat, so it must be translated."""
    _db, profiles, sites, sessions, tmp_path = env
    settings = load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_DEFAULT_LANGUAGE": "en"}
    )
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    with pytest.raises(ValueError, match="profile 999 not found"):
        await svc.request(999)


async def test_the_missing_session_error_follows_the_deployment_language(env):
    _db, profiles, sites, sessions, tmp_path = env
    settings = load_settings(
        {"UWP_DATA_DIR": str(tmp_path), "UWP_DEFAULT_LANGUAGE": "en"}
    )
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    with pytest.raises(ValueError, match="session 5 not found"):
        await svc.extend(5)


async def test_stop_delegates_to_the_manager(env):
    _db, profiles, sites, sessions, tmp_path = env
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(manager, profiles, sites, settings, sessions)

    await svc.stop(5)

    assert manager.stopped == [5]


async def test_extend_adds_30_minutes(env):
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    now = utcnow()
    session_id = await sessions.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="pw",
            status=VncStatus.running,
            started_at=now,
            expires_at=now,
        )
    )
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    extended = await svc.extend(session_id, minutes=30)

    assert extended.expires_at >= now + timedelta(minutes=29)


async def test_deferred_request_only_books_a_session(env):
    """Split mode: the bot has no GUI stack and must not pretend to start one."""
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(
        manager, profiles, sites, settings, sessions, eager_launch=False
    )

    await svc.request(pid)

    assert manager.calls == [(pid, "https://example.com")]
    assert manager.launched == []


async def test_a_stopping_session_still_counts_as_active(env):
    """«Готово» twice: the second press must not free a profile whose stack is alive."""
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    await profiles.update(pid, status=ProfileStatus.login)
    now = utcnow()
    await sessions.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="pw",
            status=VncStatus.stopping,
            started_at=now,
            expires_at=now + timedelta(minutes=30),
        )
    )
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(manager, profiles, sites, settings, sessions)

    assert await svc.active_session(pid) is not None


async def test_active_session_ignores_finished_sessions(env):
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    now = utcnow()
    await sessions.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="pw",
            status=VncStatus.stopped,
            started_at=now,
            expires_at=now + timedelta(minutes=30),
        )
    )
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    svc = VncService(FakeManager(), profiles, sites, settings, sessions)

    assert await svc.active_session(pid) is None


async def test_mark_done_leaves_the_kill_to_the_owner(env):
    """Split mode: the bot must not «finish» a session the worker still runs."""
    _db, profiles, sites, sessions, tmp_path = env
    pid = await _profile(profiles, tmp_path)
    await profiles.update(pid, status=ProfileStatus.login)
    now = utcnow()
    session_id = await sessions.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="pw",
            status=VncStatus.running,
            started_at=now,
            expires_at=now + timedelta(minutes=30),
        )
    )
    settings = load_settings({"UWP_DATA_DIR": str(tmp_path)})
    manager = FakeManager()
    svc = VncService(manager, profiles, sites, settings, sessions)

    await svc.mark_done(session_id)

    assert manager.stopped == [session_id]
    # The profile stays busy until the stack is really down.
    assert (await profiles.get(pid)).status == ProfileStatus.login
