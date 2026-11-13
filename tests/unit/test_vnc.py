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
        self.clients = 0

    def client_count(self):
        return self.clients

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


class FakeSpawned:
    """Stands in for ``asyncio.subprocess.Process``."""

    def __init__(self, command, returncode=None):
        self.command = command
        self.returncode = returncode
        self.terminated = False
        self.exit_code = 0

    async def wait(self):
        return self.returncode if self.returncode is not None else self.exit_code

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminated = True
        self.returncode = -9


async def _factory_env(tmp_path):
    settings = load_settings(
        {
            "UWP_DATA_DIR": str(tmp_path),
            "UWP_VNC_PORT_MIN": "6100",
            "UWP_VNC_PORT_MAX": "6101",
            "UWP_BROWSER_PATH": "/usr/bin/chromium",
        }
    )
    profile = Profile(
        id=7,
        name="p",
        kind=ProfileKind.persistent,
        seed=1,
        platform="windows",
        brand="Chrome",
        brand_version="150",
        timezone="Europe/Moscow",
        locale="ru-RU",
        user_data_dir=str(tmp_path / "p"),
    )
    return settings, profile


async def test_default_factory_builds_the_full_stack(tmp_path, monkeypatch):
    from uwp import vnc

    settings, profile = await _factory_env(tmp_path)
    spawned: list[FakeSpawned] = []

    async def fake_spawn(command, **kw):
        process = FakeSpawned(command)
        spawned.append(process)
        return process

    monkeypatch.setattr(vnc, "_spawn", fake_spawn)
    handle = await vnc._default_process_factory(settings)(
        display=100,
        port=6100,
        password="pw",
        profile=profile,
        url="https://example.com",
        proxy_url=None,
    )

    names = [p.command[0] for p in spawned]
    assert names == [
        "x11vnc",
        "Xvfb",
        "fluxbox",
        str(settings.browser_path),
        "x11vnc",
        "websockify",
    ]

    x11vnc = spawned[-2].command
    assert "-afteraccept" in x11vnc, "x11vnc only knows -afteraccept/-gone hooks"
    assert "-after" not in x11vnc
    hook = x11vnc[x11vnc.index("-afteraccept") + 1]
    assert hook.startswith('echo "$RFB_CLIENT_COUNT"')
    # The RFB port must not collide with the published noVNC band.
    rfb_port = int(x11vnc[x11vnc.index("-rfbport") + 1])
    assert not (settings.vnc_port_min <= rfb_port <= settings.vnc_port_max)

    await handle.stop()
    assert all(p.terminated for p in spawned[1:])


async def test_default_factory_reports_storepasswd_failure(tmp_path, monkeypatch):
    from uwp import vnc

    settings, profile = await _factory_env(tmp_path)

    async def fake_spawn(command, **kw):
        return FakeSpawned(command, returncode=1)

    monkeypatch.setattr(vnc, "_spawn", fake_spawn)

    with pytest.raises(RuntimeError, match="storepasswd"):
        await vnc._default_process_factory(settings)(
            display=100,
            port=6100,
            password="pw",
            profile=profile,
            url="https://example.com",
            proxy_url=None,
        )


async def test_default_factory_kills_partial_stack_on_failure(tmp_path, monkeypatch):
    from uwp import vnc

    settings, profile = await _factory_env(tmp_path)
    spawned: list[FakeSpawned] = []

    async def fake_spawn(command, **kw):
        if command[0] == "fluxbox":
            raise FileNotFoundError("fluxbox")
        process = FakeSpawned(command)
        spawned.append(process)
        return process

    monkeypatch.setattr(vnc, "_spawn", fake_spawn)

    with pytest.raises(FileNotFoundError):
        await vnc._default_process_factory(settings)(
            display=100,
            port=6100,
            password="pw",
            profile=profile,
            url="https://example.com",
            proxy_url=None,
        )

    assert [p.command[0] for p in spawned] == ["x11vnc", "Xvfb"]
    # The storepasswd helper already exited; the running Xvfb must be killed.
    assert spawned[1].terminated


async def test_client_count_reads_the_hook_marker(tmp_path):
    from uwp.vnc import _Stack

    marker = tmp_path / "s.clients"
    stack = _Stack([], tmp_path / "x.passwd", marker)
    assert stack.client_count() == 0  # no marker yet

    marker.write_text("3\n")
    assert stack.client_count() == 3

    marker.write_text("0\n")
    assert stack.client_count() == 0

    marker.write_text("not a number\n")
    assert stack.client_count() == 0


def test_hook_command_survives_awkward_paths(tmp_path):
    """The x11vnc hook runs through ``sh``; odd paths must not break it."""
    import subprocess

    from uwp.vnc import _record_count_hook, _Stack

    marker = tmp_path / "it's a dir" / "s.clients"
    marker.parent.mkdir()
    command = _record_count_hook(marker)

    subprocess.run(["sh", "-c", command], env={"RFB_CLIENT_COUNT": "2"}, check=True)

    assert _Stack([], tmp_path / "x.passwd", marker).client_count() == 2


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

    info = await mgr.start(
        await profiles.get(pid), "https://store.steampowered.com/login/"
    )

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


async def test_connected_client_refreshes_last_client_at(env):
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    info = await mgr.start(await profiles.get(pid), "https://example.com")
    procs[-1].clients = 1

    now = utcnow()
    await mgr.tick(now)

    session = await VncRepo(db).get(info.session_id)
    assert session.status == VncStatus.running
    # The database keeps whole seconds.
    assert session.last_client_at == now.replace(microsecond=0)


async def test_idle_countdown_restarts_after_a_client_leaves(env):
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    info = await mgr.start(await profiles.get(pid), "https://example.com")
    repo = VncRepo(db)
    await repo.update(info.session_id, last_client_at=utcnow() - timedelta(minutes=30))

    procs[-1].clients = 1
    await mgr.tick(utcnow())

    assert (await repo.get(info.session_id)).last_client_at > utcnow() - timedelta(
        minutes=1
    )


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


async def test_reserved_session_is_launched_by_tick(env):
    """Split mode: the bot reserves, the worker's tick starts the stack."""
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    repo = VncRepo(db)

    info = await mgr.reserve(await profiles.get(pid), "https://store.steampowered.com/")

    session = await repo.get(info.session_id)
    assert session.status == VncStatus.requested
    assert session.url == "https://store.steampowered.com/"
    assert procs == []

    await mgr.tick(utcnow())

    assert (await repo.get(info.session_id)).status == VncStatus.running
    assert len(procs) == 1
    assert procs[0].kw["url"] == "https://store.steampowered.com/"


async def test_stop_requested_by_another_process_is_honoured(env):
    """``uwp vnc stop`` runs in its own process: it can only mark the row."""
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    repo = VncRepo(db)
    info = await mgr.start(await profiles.get(pid), "https://example.com")

    # A second manager stands in for the CLI process: no handles of its own.
    cli = VncManager(
        settings, ProfilesRepo(db), repo, process_factory=lambda **kw: FakeProc(**kw)
    )
    await cli.request_stop(info.session_id)
    assert (await repo.get(info.session_id)).status == VncStatus.stopping

    await mgr.tick(utcnow())

    assert (await repo.get(info.session_id)).status == VncStatus.stopped
    assert all(p.stopped for p in procs)
    assert (await profiles.get(pid)).status == ProfileStatus.free


async def test_expired_reservation_is_not_launched(env):
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    repo = VncRepo(db)
    info = await mgr.reserve(await profiles.get(pid), "https://example.com")

    await mgr.tick(info.expires_at + timedelta(seconds=1))

    assert (await repo.get(info.session_id)).status == VncStatus.stopped
    assert procs == []
