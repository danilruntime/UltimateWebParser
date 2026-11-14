"""Tests for VNC login sessions: port selection, lifecycle and timeouts."""

from datetime import timedelta

import pytest

from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import (
    Profile,
    ProfileKind,
    ProfileStatus,
    Proxy,
    ProxyScheme,
    VncSession,
    VncStatus,
)
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
    assert free_port(6000, 6002, taken={6001}) == 6002


def test_free_port_never_hands_out_chromium_unsafe_ports():
    """http://host:6000 fails with ERR_UNSAFE_PORT in the admin's browser."""
    assert free_port(6000, 6001, taken=set()) == 6001


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


async def _pretend_ready(monkeypatch, vnc):
    """Skip the real Xvfb/x11vnc readiness probes in unit tests."""

    async def ready(*_args, **_kwargs):
        return True

    monkeypatch.setattr(vnc, "_wait_for_path", ready)
    monkeypatch.setattr(vnc, "_wait_for_port", ready)


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
    await _pretend_ready(monkeypatch, vnc)
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

    # The desktop is Windows 10 at 1080p: the screen is full size and the
    # browser is pinned to the 1040-high work area left by the taskbar.
    xvfb = next(p.command for p in spawned if p.command[0] == "Xvfb")
    assert "1920x1080x24" in xvfb
    browser = next(
        p.command for p in spawned if p.command[0] == str(settings.browser_path)
    )
    assert "--window-size=1920,1040" in browser
    assert "--window-position=0,0" in browser
    # Measured: --start-maximized breaks the size when --fingerprint is set.
    assert not any("--start-maximized" in p.command for p in spawned)

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


async def test_default_factory_gives_fluxbox_its_own_home(tmp_path, monkeypatch):
    """The toolbar settings only load if fluxbox sees the generated $HOME."""
    import os

    from uwp import vnc

    settings, profile = await _factory_env(tmp_path)
    envs: dict[str, dict[str, str] | None] = {}

    async def fake_spawn(command, **kw):
        envs.setdefault(command[0], kw.get("env"))
        return FakeSpawned(command)

    monkeypatch.setattr(vnc, "_spawn", fake_spawn)
    await _pretend_ready(monkeypatch, vnc)
    handle = await vnc._default_process_factory(settings)(
        display=100,
        port=6100,
        password="pw",
        profile=profile,
        url="https://example.com",
        proxy_url=None,
    )

    wm_home = handle.wm_home
    assert wm_home is not None
    assert envs["fluxbox"] is not None
    assert envs["fluxbox"]["HOME"] == str(wm_home)
    assert (wm_home / ".fluxbox" / "init").exists()
    # Chromium keeps the real HOME: its profile paths depend on it.
    assert envs[str(settings.browser_path)]["HOME"] == os.environ["HOME"]

    await handle.stop()
    assert not wm_home.exists()


async def test_password_file_is_removed_when_the_wm_home_fails(tmp_path, monkeypatch):
    """A failed fluxbox config write must not leave a stored password behind."""
    from pathlib import Path

    from uwp import vnc

    settings, profile = await _factory_env(tmp_path)

    async def fake_spawn(command, **kw):
        if command[0] == "x11vnc" and "-storepasswd" in command:
            # The real helper writes the password file; mimic that on disk.
            Path(command[-1]).write_text("stored-password")
        return FakeSpawned(command)

    def broken_fluxbox_home(home, toolbar_height):
        raise OSError("no space left on device")

    monkeypatch.setattr(vnc, "_spawn", fake_spawn)
    monkeypatch.setattr(vnc, "_write_fluxbox_home", broken_fluxbox_home)
    await _pretend_ready(monkeypatch, vnc)

    with pytest.raises(OSError, match="no space"):
        await vnc._default_process_factory(settings)(
            display=100,
            port=6100,
            password="pw",
            profile=profile,
            url="https://example.com",
            proxy_url=None,
        )

    assert not (settings.data_dir / "vnc" / "7-6100.passwd").exists()


async def test_fluxbox_home_is_removed_when_the_stack_fails(tmp_path, monkeypatch):
    """A per-session config must not pile up under data/vnc on a failed start."""
    from uwp import vnc

    settings, profile = await _factory_env(tmp_path)

    async def fake_spawn(command, **kw):
        if command[0] == "fluxbox":
            raise FileNotFoundError("fluxbox")
        return FakeSpawned(command)

    monkeypatch.setattr(vnc, "_spawn", fake_spawn)
    await _pretend_ready(monkeypatch, vnc)

    with pytest.raises(FileNotFoundError):
        await vnc._default_process_factory(settings)(
            display=100,
            port=6100,
            password="pw",
            profile=profile,
            url="https://example.com",
            proxy_url=None,
        )

    vnc_dir = settings.data_dir / "vnc"
    assert not (vnc_dir / "wm-7-6100").exists()


async def test_default_factory_reports_storepasswd_failure(tmp_path, monkeypatch):
    from uwp import vnc

    settings, profile = await _factory_env(tmp_path)

    async def fake_spawn(command, **kw):
        return FakeSpawned(command, returncode=1)

    monkeypatch.setattr(vnc, "_spawn", fake_spawn)
    await _pretend_ready(monkeypatch, vnc)

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
    await _pretend_ready(monkeypatch, vnc)

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


def test_desktop_geometry_matches_windows_10():
    from uwp.vnc import DESKTOP_SIZE, TASKBAR_HEIGHT, WORKAREA

    assert DESKTOP_SIZE == (1920, 1080)
    # fluxbox adds 2 px of border, so 38 px renders a 40 px taskbar.
    assert TASKBAR_HEIGHT == 38
    assert WORKAREA == (1920, 1080 - 40)


def test_fluxbox_home_sets_the_toolbar_and_strips_decorations(tmp_path):
    from uwp.vnc import _write_fluxbox_home

    home = _write_fluxbox_home(tmp_path / "wm", toolbar_height=38)

    init = (home / ".fluxbox" / "init").read_text()
    assert "session.screen0.toolbar.height: 38" in init
    assert "session.screen0.toolbar.visible: true" in init
    assert "session.screen0.toolbar.placement: BottomCenter" in init
    # Measured in the image: the ``apps`` rule loses a race when fluxbox and
    # Chromium start back to back, leaving a 1/1/20/4 frame. ``defaultDeco``
    # is read from the config before any window is mapped, so it always wins.
    assert "session.screen0.defaultDeco: NONE" in init
    apps = (home / ".fluxbox" / "apps").read_text()
    assert "[Deco] {NONE}" in apps
    assert "class=Chromium" in apps


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


async def test_sweep_orphans_fails_rows_left_by_a_dead_worker(env):
    """After a restart no process owns these rows; the port and profile are stuck."""
    _settings, db, profiles, pid = env
    repo = VncRepo(db)
    session_id = await repo.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="p",
            token="t",
            status=VncStatus.running,
            expires_at=utcnow() + timedelta(minutes=30),
        )
    )
    await profiles.update(pid, status=str(ProfileStatus.login))

    freed = await repo.sweep_orphans()

    assert freed == [pid]
    assert (await repo.get(session_id)).status == VncStatus.failed


async def test_sweep_orphans_leaves_finished_sessions_alone(env):
    _settings, db, _profiles, pid = env
    repo = VncRepo(db)
    session_id = await repo.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="p",
            token="t",
            status=VncStatus.stopped,
            expires_at=utcnow() + timedelta(minutes=30),
        )
    )

    assert await repo.sweep_orphans() == []
    assert (await repo.get(session_id)).status == VncStatus.stopped


async def test_sweep_orphans_leaves_a_booking_made_by_the_bot_alone(env):
    """A ``requested`` row may belong to the bot process, not to a dead worker.

    In the split deployment the bot books a session and the worker launches it
    on its next tick. Failing that row at worker startup would destroy a live
    booking, so ``requested`` is resumable, not orphaned.
    """
    _settings, db, _profiles, pid = env
    repo = VncRepo(db)
    session_id = await repo.create(
        VncSession(
            profile_id=pid,
            port=6100,
            display=100,
            password="p",
            token="t",
            status=VncStatus.requested,
            expires_at=utcnow() + timedelta(minutes=30),
        )
    )

    assert await repo.sweep_orphans() == []
    assert (await repo.get(session_id)).status == VncStatus.requested


async def test_sweep_orphans_reports_a_profile_once(env):
    """``RETURNING`` yields a row per session; the caller needs one per profile."""
    _settings, db, _profiles, pid = env
    repo = VncRepo(db)
    for port in (6100, 6101):
        await repo.create(
            VncSession(
                profile_id=pid,
                port=port,
                display=port - 6000,
                password="p",
                token=f"t{port}",
                status=VncStatus.running,
                expires_at=utcnow() + timedelta(minutes=30),
            )
        )

    assert await repo.sweep_orphans() == [pid]


class FakeBridge:
    """Stands in for the loopback proxy bridge of a login session."""

    def __init__(self, proxy):
        self.proxy = proxy
        self.url = f"http://127.0.0.1:{7000 + len(proxy.name)}"
        self.stopped = False

    async def start(self):
        return self.url

    async def stop(self):
        self.stopped = True


class FakeProxies:
    """Resolves proxy rows, already decrypted, for the VNC manager."""

    def __init__(self, proxy=None):
        self.proxy = proxy

    async def resolved(self, proxy_id):
        return self.proxy


def _manager(settings, db, procs, proxies=None, bridges=None):
    def factory(**kw):
        proc = FakeProc(**kw)
        procs.append(proc)
        return proc

    def bridge_factory(proxy):
        bridge = FakeBridge(proxy)
        if bridges is not None:
            bridges.append(bridge)
        return bridge

    return VncManager(
        settings,
        ProfilesRepo(db),
        VncRepo(db),
        process_factory=factory,
        proxies=proxies,
        bridge_factory=bridge_factory,
    )


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


async def test_a_stopping_session_keeps_its_port(env):
    """A port is not free until the stack that listened on it is gone."""
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    mgr = _manager(settings, db, procs)
    repo = VncRepo(db)
    first = await mgr.start(await profiles.get(pid), "https://example.com")

    cli = VncManager(
        settings, ProfilesRepo(db), repo, process_factory=lambda **kw: FakeProc(**kw)
    )
    await cli.request_stop(first.session_id)
    assert (await repo.get(first.session_id)).status == VncStatus.stopping

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
    second = await mgr.reserve(
        await profiles.get(second_pid), "https://example.com"
    )

    assert second.port != first.port


async def _stored_proxy(db, name="P"):
    """A real proxy row, so the session's foreign key holds."""
    from uwp.db.repo.proxies import ProxiesRepo

    proxy = Proxy(name=name, scheme=ProxyScheme.http, host="127.0.0.1", port=3128)
    proxy.id = await ProxiesRepo(db).create(proxy)
    return proxy


async def test_a_session_with_a_proxy_launches_a_bridge(env):
    """The login browser must leave through the same proxy as the parse runs."""
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    bridges: list[FakeBridge] = []
    proxy = await _stored_proxy(db)
    mgr = _manager(
        settings, db, procs, proxies=FakeProxies(proxy), bridges=bridges
    )

    info = await mgr.reserve_and_launch(
        await profiles.get(pid), "https://example.com", proxy_id=proxy.id
    )

    assert len(bridges) == 1
    assert bridges[0].proxy is proxy
    assert procs[0].kw["proxy_url"] == bridges[0].url
    await mgr.stop(info.session_id)
    assert bridges[0].stopped


async def test_a_reserved_session_remembers_its_proxy(env):
    """Split mode: the bot books the session, the worker launches the stack."""
    settings, db, profiles, pid = env
    procs: list[FakeProc] = []
    bridges: list[FakeBridge] = []
    proxy = await _stored_proxy(db)
    mgr = _manager(
        settings, db, procs, proxies=FakeProxies(proxy), bridges=bridges
    )

    info = await mgr.reserve(
        await profiles.get(pid), "https://example.com", proxy_id=proxy.id
    )
    assert (await VncRepo(db).get(info.session_id)).proxy_id == proxy.id

    await mgr.tick(utcnow())

    assert len(bridges) == 1
    assert procs[0].kw["proxy_url"] == bridges[0].url


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
