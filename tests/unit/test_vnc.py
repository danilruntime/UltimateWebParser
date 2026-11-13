"""Tests for VNC login sessions: port selection, lifecycle and timeouts."""
from datetime import timedelta

import pytest

from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import Profile, ProfileKind, ProfileStatus, VncStatus
from uwp.timeutil import utcnow
from uwp.vnc import VncManager, free_port, vnc_url


class FakeProc:
    def __init__(self, **kw):
        self.kw = kw
        self.stopped = False

    async def stop(self):
        self.stopped = True


def test_free_port_skips_taken():
    assert free_port(6000, 6002, taken={6000, 6001}) == 6002


def test_free_port_raises_when_exhausted():
    with pytest.raises(RuntimeError):
        free_port(6000, 6001, taken={6000, 6001})


def test_vnc_url():
    assert (
        vnc_url("example.org", 6042, "abc")
        == "http://example.org:6042/vnc.html?autoconnect=1&resize=scale&password=abc"
    )


@pytest.fixture
async def env(tmp_path):
    settings = load_settings(
        {
            "UWP_DATA_DIR": str(tmp_path),
            "UWP_VNC_PORT_MIN": "6100",
            "UWP_VNC_PORT_MAX": "6101",
        }
    )
    db = await Database.in_memory()
    await db.migrate()
    profiles = ProfilesRepo(db)
    pid = await profiles.create(
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
    yield settings, db, profiles, pid
    await db.close()


def _manager(settings, db, procs):
    def factory(**kw):
        proc = FakeProc(**kw)
        procs.append(proc)
        return proc

    return VncManager(settings, ProfilesRepo(db), VncRepo(db), process_factory=factory)


async def test_start_and_stop_session(env):
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)

    info = await mgr.start(await profiles.get(pid), "https://store.steampowered.com/login/")

    assert info.port in (6100, 6101)
    assert len(info.password) == 12
    assert info.url.startswith(f"http://{settings.public_host}:{info.port}/vnc.html?")
    assert (await profiles.get(pid)).status == ProfileStatus.login
    assert len(procs) >= 1

    await mgr.stop(info.session_id)

    assert all(p.stopped for p in procs)
    assert (await profiles.get(pid)).status == ProfileStatus.free
    session = await VncRepo(db).get(info.session_id)
    assert session.status == VncStatus.stopped


async def test_hard_ttl_stops_session(env):
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    info = await mgr.start(await profiles.get(pid), "https://example.com")

    await mgr.tick(info.expires_at + timedelta(seconds=1))

    assert all(p.stopped for p in procs)
    assert (await profiles.get(pid)).status == ProfileStatus.free
    assert (await VncRepo(db).get(info.session_id)).status == VncStatus.stopped


async def test_idle_session_is_stopped(env):
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    info = await mgr.start(await profiles.get(pid), "https://example.com")
    repo = VncRepo(db)
    last = utcnow() - timedelta(minutes=settings.vnc_idle_minutes + 1)
    await repo.update(info.session_id, last_client_at=last)

    await mgr.tick(utcnow())

    assert (await repo.get(info.session_id)).status == VncStatus.stopped


async def test_session_without_client_is_not_idle_stopped(env):
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    info = await mgr.start(await profiles.get(pid), "https://example.com")

    await mgr.tick(utcnow())

    assert (await VncRepo(db).get(info.session_id)).status == VncStatus.running


async def test_second_port_is_used_when_first_is_taken(env):
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    first = await mgr.start(await profiles.get(pid), "https://example.com")
    second_pid = await profiles.create(
        Profile(
            name="p2",
            kind=ProfileKind.persistent,
            seed=2,
            platform="windows",
            brand="Chrome",
            brand_version="150",
            timezone="Europe/Moscow",
            locale="ru-RU",
            user_data_dir=str(settings.profiles_dir / "p2"),
        )
    )

    second = await mgr.start(await profiles.get(second_pid), "https://example.com")

    assert {first.port, second.port} == {6100, 6101}
    first_session = await VncRepo(db).get(first.session_id)
    second_session = await VncRepo(db).get(second.session_id)
    assert first_session.display != second_session.display


async def test_start_requires_persistent_profile(env):
    settings, db, profiles, pid = env
    mgr = _manager(settings, db, [])
    await profiles.update(pid, kind=str(ProfileKind.clean))

    with pytest.raises(ValueError):
        await mgr.start(await profiles.get(pid), "https://example.com")


async def test_list_returns_active_sessions(env):
    settings, db, profiles, pid = env
    mgr = _manager(settings, db, [])
    info = await mgr.start(await profiles.get(pid), "https://example.com")

    active = await mgr.list()

    assert [s.id for s in active] == [info.session_id]
