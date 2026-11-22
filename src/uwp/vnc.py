"""VNC login sessions: a headful browser on a virtual display, exposed via noVNC.

The bot only asks for a session and shows the link; everything else (Xvfb,
window manager, browser, x11vnc, websockify) lives inside the worker.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
import shlex
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from uwp import secretbox
from uwp.browser import prepare_profile, seed_download_preferences
from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import Profile, ProfileKind, ProfileStatus, VncSession, VncStatus
from uwp.env import browser_env
from uwp.events import VNC_FAILED, VNC_STOPPED
from uwp.profiles import browser_flags
from uwp.timeutil import utcnow

log = logging.getLogger(__name__)

PASSWORD_LENGTH = 8
DISPLAY_BASE = 100
# Raw RFB listeners live in their own internal band so they can never collide
# with a published noVNC port (6000-6100).
RFB_PORT_BASE = 4900
# How long Xvfb/x11vnc may take to become ready before the login fails.
READY_TIMEOUT = 15.0


class VncStartError(RuntimeError):
    """The VNC stack could not start; ``code`` names the reason for the chat.

    This module knows what happened but not who will read it — the message goes
    into a ``vnc_failed`` event and ``delivery`` renders it per recipient.
    ``str()`` stays an English developer line for the log.
    """

    def __init__(self, message: str, code: str, **detail: object) -> None:
        self.code = code
        self.detail = detail
        super().__init__(message)


class VncLoginError(ValueError):
    """A login session cannot be opened; ``code`` names the reason for the chat.

    A ``ValueError`` on purpose: the bot handlers around the login buttons
    already catch that type, and the CLI does too. ``str()`` stays an English
    developer line for the log; a handler must render ``code`` instead.
    """

    def __init__(self, message: str, code: str, **detail: object) -> None:
        self.code = code
        self.detail = detail
        super().__init__(message)


# Chromium refuses to navigate to its "unsafe" ports, and the default VNC band
# starts at 6000 — the X11 port. A link on 6000 opens with ERR_UNSAFE_PORT in
# the admin's own browser, so those ports are never handed out.
UNSAFE_PORTS = frozenset(
    {
        1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69,
        77, 79, 87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117,
        119, 123, 135, 137, 139, 143, 161, 179, 389, 427, 465, 512, 513,
        514, 515, 526, 530, 531, 532, 540, 548, 554, 556, 563, 587, 601,
        636, 989, 990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 5060,
        5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6697, 10080,
    }
)

# The login desktop is a plain 1080p screen: kiosk fills it edge to edge, so the
# window is pinned to the screen, not to a work area left by a taskbar.
DESKTOP_SIZE = (1920, 1080)

_FLUXBOX_INIT = """\
session.styleFile: /usr/share/fluxbox/styles/Squared_for_Debian
session.appsFile: {home}/.fluxbox/apps
session.menuFile: {home}/.fluxbox/menu
session.keyFile: {home}/.fluxbox/keys
session.screen0.defaultDeco: NONE
session.screen0.toolbar.visible: false
session.screen0.workspaces: 1
"""

# Chromium draws its own tab strip, so a fluxbox frame is dead pixels. Without
# this, a 1040-high window loses another ~20 px to a title bar.
#
# ``defaultDeco`` above is the load-bearing setting: the rule below only lands
# on windows fluxbox sees *after* it reads this file, and the browser is
# spawned right after the WM, so the rule usually loses that race (measured in
# the image: ``_NET_FRAME_EXTENTS`` stayed ``1,1,20,4`` three times out of
# three). The per-class rule stays as a second line of defence.
_FLUXBOX_APPS = """\
[app] (class=Chromium)
  [Deco] {NONE}
[end]
"""

# An empty menu file: a right click on the desktop must not offer a terminal,
# a file manager or a second browser.
_FLUXBOX_MENU = ""

# Chords that must never reach Chromium. Measured in the image: kiosk mode
# alone still opens a full 1050x760 window with the complete browser UI on
# Ctrl+N, so the window manager grabs the chord first. Reload (F5, Ctrl+R) and
# history (Alt+Left/Right) are deliberately absent: a half-finished login is
# retried through them.
_GRABBED_CHORDS = (
    # Windows and tabs.
    "Control n",
    "Control Shift n",
    "Control t",
    "Control Shift t",
    "Control w",
    "Control Shift w",
    "Control Shift q",
    "Mod1 F4",
    # DevTools and view-source.
    "Control Shift i",
    "Control Shift j",
    "Control Shift c",
    "F12",
    "Control u",
    # File dialogs, printing, downloads, history.
    "Control s",
    "Control p",
    "Control o",
    "Control h",
    "Control j",
    # Bookmarks.
    "Control d",
    "Control Shift o",
    "Control Shift b",
    # Location bar and search.
    "Control l",
    "Control k",
    "Control e",
    "Mod1 d",
    "F6",
    "Control f",
    "F3",
    "Control g",
    # Window chrome and stored data.
    "F11",
    "F1",
    "Control Shift Delete",
    "Control Shift m",
    # Context menu from the keyboard.
    "Shift F10",
    "Menu",
    # Zoom and home.
    "Control equal",
    "Control minus",
    "Control 0",
    "Mod1 Home",
)

# ``:Exec true`` is the only no-op fluxbox accepts here: ``:Noop``, ``:None``
# and ``:MacroCmd`` are rejected with "Invalid key/modifier" (measured), and a
# rejected line is never grabbed at all.
_FLUXBOX_KEYS = "".join(f"{chord} :Exec true\n" for chord in _GRABBED_CHORDS)


def _write_fluxbox_home(home: Path) -> Path:
    """Write the WM config fluxbox reads from ``$HOME/.fluxbox``.

    fluxbox honours ``$HOME/.fluxbox/init``; ``-rc`` picks up ``appsFile`` but
    ignores the rest, so every file must live here. The keys file is what keeps
    Ctrl+N and the DevTools shortcuts away from Chromium.
    """
    config = home / ".fluxbox"
    try:
        config.mkdir(parents=True, exist_ok=True)
        (config / "apps").write_text(_FLUXBOX_APPS)
        (config / "menu").write_text(_FLUXBOX_MENU)
        (config / "keys").write_text(_FLUXBOX_KEYS)
        (config / "init").write_text(_FLUXBOX_INIT.format(home=home))
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    return home


class ProcessHandle(Protocol):
    """A started VNC stack; ``stop`` must be idempotent."""

    async def stop(self) -> None: ...


@dataclass
class VncInfo:
    """What the bot needs to show the admin."""

    session_id: int
    port: int
    password: str
    url: str
    expires_at: datetime


def free_port(min_port: int, max_port: int, taken: set[int]) -> int:
    """Return the first port in ``[min_port, max_port]`` that is not taken.

    Ports Chromium treats as unsafe are skipped: noVNC is opened in the admin's
    own browser, and ``http://host:6000`` fails with ``ERR_UNSAFE_PORT``.
    """
    for port in range(min_port, max_port + 1):
        if port not in taken and port not in UNSAFE_PORTS:
            return port
    raise RuntimeError("no free VNC port")


def vnc_url(
    public_host: str, port: int, password: str, scheme: str = "http"
) -> str:
    """noVNC URL with autoconnect, scaled display and the session password.

    The parameters live in the fragment, not the query string: noVNC reads both
    (fragment wins), but a browser never sends the fragment to websockify, so
    the password stays out of its access log and out of any ``Referer`` header.

    ``scheme`` is ``https`` when the operator fronts the band with a TLS reverse
    proxy; the default loopback deployment stays on plain HTTP.
    """
    return (
        f"{scheme}://{public_host}:{port}/vnc.html"
        f"#autoconnect=1&resize=scale&password={password}"
    )


def _record_count_hook(marker: Path) -> str:
    """Shell command for x11vnc's ``-afteraccept``/``-gone`` hooks.

    x11vnc already runs hooks through ``/bin/sh -c``, so only the redirect is
    needed; quoting the path protects spaces and quotes.
    """
    return f'echo "$RFB_CLIENT_COUNT" > {shlex.quote(str(marker))}'


# The login browser and the window manager must not inherit the worker's
# environment: ``UWP_BOT_TOKEN``/``UWP_SECRET_KEY`` there would let a page on
# the noVNC desktop take over the bot, and the desktop can start its own
# processes. ``browser_env`` is the same allowlist the parse browser gets.
def child_env(**overrides: str) -> dict[str, str]:
    """A minimal environment for the X11 stack, with ``DISPLAY`` and HOME set."""
    return browser_env(**overrides)


class _Stack:
    """Default process handle: keeps the spawned processes and their files."""

    def __init__(
        self,
        processes: list[asyncio.subprocess.Process],
        password_file: Path,
        client_marker: Path | None = None,
        log_file: Any = None,
        *,
        wm_home: Path | None = None,
    ) -> None:
        self.processes = processes
        self.password_file = password_file
        self.client_marker = client_marker
        self.log_file = log_file
        self.wm_home = wm_home

    def client_count(self) -> int:
        """Live client count recorded by the x11vnc accept/gone hooks."""
        if self.client_marker is None or not self.client_marker.exists():
            return 0
        try:
            return int(self.client_marker.read_text().strip() or 0)
        except ValueError:
            return 0

    async def stop(self) -> None:
        for process in reversed(self.processes):
            if process.returncode is not None:
                continue
            try:
                process.terminate()
            except ProcessLookupError:  # already gone
                pass
        for process in reversed(self.processes):
            if process.returncode is not None:
                continue
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        self.password_file.unlink(missing_ok=True)
        if self.client_marker is not None:
            self.client_marker.unlink(missing_ok=True)
        if self.log_file is not None:
            self.log_file.close()
        if self.wm_home is not None:
            shutil.rmtree(self.wm_home, ignore_errors=True)


async def _spawn(
    command: list[str],
    *,
    stderr: Any = asyncio.subprocess.DEVNULL,
    **kw: Any,
) -> asyncio.subprocess.Process:
    log.debug("starting %s", command[0])
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=stderr,
        **kw,
    )


async def _wait_for_path(path: Path, timeout: float) -> bool:
    """Poll until ``path`` exists (Xvfb creates its socket asynchronously)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.exists():
            return True
        await asyncio.sleep(0.05)
    return False


async def _wait_for_port(port: int, timeout: float) -> bool:
    """Poll until something accepts connections on ``127.0.0.1:port``."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True
    return False


async def _wait_for_browser(
    process: asyncio.subprocess.Process, user_data_dir: Path, timeout: float
) -> bool:
    """Wait until the browser claims its profile, or report that it died.

    A refused ``ProcessSingleton`` makes Chromium exit within a second while
    Xvfb, x11vnc and websockify stay up, so the session looked healthy and
    noVNC showed an empty desktop with no reason. Measured on ForSteam: the
    browser logged "profile appears to be in use" and exited.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    lock = user_data_dir / "SingletonLock"
    while asyncio.get_running_loop().time() < deadline:
        if process.returncode is not None:
            return False
        if lock.is_symlink():
            return True
        await asyncio.sleep(0.05)
    return process.returncode is None


def _port_is_free(host: str, port: int) -> bool:
    """Tell whether ``websockify`` could bind the noVNC port right now.

    Two deployments on one machine share the published band; the loser's
    websockify exits with ``OSError: [Errno 98] Address already in use`` while
    the rest of the stack stays healthy. Asking the kernel first turns that into
    a readable error before anything is started.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # A listener still owns the port; ``SO_REUSEADDR`` only forgives sockets
        # left in TIME_WAIT, so this does not hide a live collision.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


async def _wait_for_websockify(
    process: asyncio.subprocess.Process, port: int, timeout: float
) -> bool:
    """Wait for the noVNC listener, or report that it could not take the port.

    ``websockify`` dies at once with ``OSError: [Errno 98] Address already in
    use`` when something else owns the port — typically the other deployment's
    docker-proxy. Xvfb, Chromium and x11vnc stay healthy, so without this check
    the session was recorded as started and the admin got a link that resets
    the connection.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if process.returncode is not None:
            return False
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        # An accept only proves *someone* listens. The bind error lands a moment
        # after the socket exists, so give the child a chance to reveal it.
        await asyncio.sleep(0.2)
        return process.returncode is None
    # The published port is the whole point of this process: no listener means
    # the link would not work, whatever the process is still doing.
    return False


def _log_tail(path: Path, limit: int = 600) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-limit:]


def _default_process_factory(
    settings: Settings,
) -> Any:
    """Build the real Xvfb + browser + x11vnc + websockify stack."""

    async def factory(
        display: int,
        port: int,
        password: str,
        profile: Profile,
        url: str,
        proxy_url: str | None,
    ) -> ProcessHandle:
        password_file = settings.data_dir / "vnc" / f"{profile.id or 'x'}-{port}.passwd"
        password_file.parent.mkdir(parents=True, exist_ok=True)
        # Even trusted helpers (Xvfb, x11vnc, websockify) get the same minimal
        # environment; nothing in the stack has a reason to see the bot token.
        # Xvfb and x11vnc do not need ``DISPLAY``: the former creates the screen,
        # the latter gets ``-display`` on its command line.
        base_env = child_env()
        # A crashed parse run can leave Chromium's single-instance lock in the
        # shared persistent profile; the login browser would then refuse to
        # start with "Failed to create a ProcessSingleton". A lock that belongs
        # to a browser which is really alive raises a readable error instead.
        prepare_profile(Path(profile.user_data_dir))
        # Measured: kiosk mode hides the "Save as" dialog, and a profile without
        # ``download.default_directory`` therefore cancels every download
        # (``interrupt_reason=40``). Seed the folder before Chromium starts.
        seed_download_preferences(
            Path(profile.user_data_dir), settings.downloads_dir
        )
        # A second deployment (combined plus split) shares the published band;
        # the loser's websockify would die and leave a link that resets the
        # connection. Refuse before spending time on Xvfb and Chromium.
        if not _port_is_free(settings.vnc_listen_host, port):
            raise VncStartError(
                f"port {port} is already taken: a second UWP stack looks alive",
                "error.vnc.port_taken",
                port=port,
            )
        store = await _spawn(
            ["x11vnc", "-storepasswd", password, str(password_file)], env=base_env
        )
        if await store.wait() != 0:
            raise VncStartError(
                "x11vnc -storepasswd failed", "error.vnc.storepasswd_failed"
            )

        # The RFB listener stays inside the container on a private band; only
        # the websockify/noVNC port is published.
        vnc_port = RFB_PORT_BASE + (port - settings.vnc_port_min)
        screen = f":{display}"
        executable = (
            str(settings.browser_path)
            if settings.browser_path
            else (shutil.which("chromium") or "chromium")
        )
        args = [
            flag
            for flag in browser_flags(
                _identity(profile),
                Path(profile.user_data_dir),
                proxy_url,
                headless=False,
                no_sandbox=settings.no_sandbox,
                # Measured: kiosk alone with ``--fingerprint`` lands a
                # 945x1060 window in the corner, exactly like
                # ``--start-maximized``; the geometry has to be pinned. Kiosk
                # then keeps it undecorated and full-screen.
                window_size=DESKTOP_SIZE,
            )
            if not flag.startswith("--headless")
        ]
        browser_env = child_env(DISPLAY=screen)
        client_marker = password_file.with_suffix(".clients")
        client_marker.unlink(missing_ok=True)
        record_count = _record_count_hook(client_marker)
        log_name = password_file.parent / f"{profile.id or 'x'}-{port}.log"
        log_file = log_name.open("ab")
        # Each process announces its own failure on stderr; a shared log turns
        # the old silent "noVNC shows an empty page" into a readable reason.
        commands = [
            ["Xvfb", screen, "-screen", "0", f"{DESKTOP_SIZE[0]}x{DESKTOP_SIZE[1]}x24"],
            ["fluxbox", "-display", screen],
            # Kiosk: one full-screen window, no tab strip, no location bar and
            # no context menu. It also refuses DevTools itself ("DevTools not
            # allowed on this page", measured), which a policy cannot do
            # without breaking Playwright.
            [executable, *args, "--kiosk", url],
            [
                "x11vnc",
                "-display",
                screen,
                "-rfbauth",
                str(password_file),
                "-rfbport",
                str(vnc_port),
                "-localhost",
                "-forever",
                "-shared",
                "-afteraccept",
                record_count,
                "-gone",
                record_count,
            ],
            [
                "websockify",
                "--web",
                "/usr/share/novnc",
                f"{settings.vnc_listen_host}:{port}",
                f"localhost:{vnc_port}",
            ],
        ]
        processes: list[asyncio.subprocess.Process] = []
        browser_process: asyncio.subprocess.Process | None = None
        websockify_process: asyncio.subprocess.Process | None = None
        stack: _Stack | None = None
        try:
            # fluxbox reads its config from ``$HOME/.fluxbox``, so it gets a
            # throwaway HOME; the browser keeps the real one for its profile
            # paths.
            wm_home = _write_fluxbox_home(
                password_file.parent / f"wm-{profile.id or 'x'}-{port}"
            )
            wm_env = child_env(HOME=str(wm_home), DISPLAY=screen)
            stack = _Stack(
                processes, password_file, client_marker, log_file, wm_home=wm_home
            )
            # Xvfb first and wait for its socket: fluxbox, x11vnc and Chromium
            # all abort with "cannot open display" if they lose that race.
            processes.append(
                await _spawn(commands[0], stderr=log_file, env=base_env)
            )
            if not await _wait_for_path(
                Path(f"/tmp/.X11-unix/X{display}"), READY_TIMEOUT
            ):
                raise VncStartError(
                    f"Xvfb did not bring up display :{display}",
                    "error.vnc.xvfb_failed",
                    display=display,
                    detail=_log_tail(log_name),
                )
            for command in commands[1:]:
                if command[0] == executable:
                    env = browser_env
                elif command[0] == "fluxbox":
                    env = wm_env
                else:
                    env = base_env
                process = await _spawn(command, stderr=log_file, env=env)
                processes.append(process)
                if command[0] == executable:
                    browser_process = process
                elif command[0] == "websockify":
                    websockify_process = process
            # A browser that died on the profile lock leaves Xvfb and x11vnc
            # healthy, so the stack would report success over an empty desktop.
            assert browser_process is not None, "the command list always starts a browser"
            if not await _wait_for_browser(
                browser_process, Path(profile.user_data_dir), READY_TIMEOUT
            ):
                raise VncStartError(
                    "the login browser did not start",
                    "error.vnc.browser_failed",
                    detail=_log_tail(log_name, 2000),
                )
            # A taken noVNC port kills websockify but leaves the X stack up, so
            # the published link would reset the connection with no explanation.
            assert websockify_process is not None, "the command list always starts websockify"
            if not await _wait_for_websockify(
                websockify_process, port, READY_TIMEOUT
            ):
                raise VncStartError(
                    f"websockify did not take port {port}",
                    "error.vnc.websockify_failed",
                    port=port,
                    detail=_log_tail(log_name, 2000),
                )
            if not await _wait_for_port(vnc_port, READY_TIMEOUT):
                raise VncStartError(
                    f"x11vnc is not listening on port {vnc_port}",
                    "error.vnc.x11vnc_failed",
                    port=vnc_port,
                    detail=_log_tail(log_name),
                )
        except BaseException:
            # Do not leak a half-started stack: kill whatever did start.
            if stack is not None:
                await stack.stop()
            else:
                password_file.unlink(missing_ok=True)
                log_file.close()
            raise
        assert stack is not None
        return stack

    return factory


async def _stop_quietly(closeable: Any) -> None:
    """Stop a bridge/handle without letting one failure mask the real error."""
    try:
        await closeable.stop()
    except Exception:
        log.exception("stopping %s failed", type(closeable).__name__)


def _identity(profile: Profile):
    from uwp.profiles import identity_from_profile

    return identity_from_profile(profile)


class VncManager:
    """Starts, tracks and stops VNC login sessions."""

    def __init__(
        self,
        settings: Settings,
        profiles: ProfilesRepo,
        sessions: VncRepo,
        process_factory: Any = None,
        bus: Any = None,
        proxies: Any = None,
        bridge_factory: Any = None,
    ) -> None:
        self.settings = settings
        self.profiles = profiles
        self.sessions = sessions
        self.process_factory = process_factory or _default_process_factory(settings)
        self.bus = bus
        self.proxies = proxies
        self.bridge_factory = bridge_factory
        self._handles: dict[int, ProcessHandle] = {}
        # The loopback bridge a session's browser talks to; stopped with it.
        self._bridges: dict[int, Any] = {}

    @staticmethod
    def check_profile(profile: Profile) -> None:
        """Reject profiles a login session cannot use; the code reaches the chat."""
        if profile.kind != ProfileKind.persistent:
            raise VncLoginError(
                "VNC login requires a persistent profile",
                "error.vnc.profile_not_persistent",
            )
        if profile.status != ProfileStatus.free:
            raise VncLoginError(
                f"profile is not free: {profile.status}",
                "error.vnc.profile_busy",
            )
        if profile.id is None:
            raise VncLoginError("profile is not stored", "error.vnc.profile_unsaved")

    async def _emit(self, kind: str, session: VncSession, **payload: Any) -> None:
        """Queue a VNC notification; never let it break the session itself."""
        if self.bus is None:
            return
        try:
            await self.bus.emit(kind, None, None, payload)
        except Exception:
            log.exception("queueing %s for VNC session %s failed", kind, session.id)

    async def reserve(
        self,
        profile: Profile,
        url: str,
        started_by: int | None = None,
        proxy_id: int | None = None,
    ) -> VncInfo:
        """Book a session in the database without launching anything.

        The bot owns this half: it has no GUI stack of its own, so it writes a
        ``requested`` row and the worker (same process in combined mode) picks
        it up and launches the browser.
        """
        self.check_profile(profile)
        port = free_port(
            self.settings.vnc_port_min,
            self.settings.vnc_port_max,
            await self.sessions.taken_ports(),
        )
        display = DISPLAY_BASE + (port - self.settings.vnc_port_min)
        # RFB authenticates with the first 8 bytes of the password; a longer
        # string would show the admin characters the server ignores.
        password = secrets.token_urlsafe(9)[:PASSWORD_LENGTH]
        now = utcnow()
        expires_at = now + timedelta(minutes=self.settings.vnc_ttl_minutes)
        session_id = await self.sessions.create(
            VncSession(
                profile_id=profile.id,
                port=port,
                display=display,
                password=(
                    secretbox.encrypt(password, self.settings.secret_key)
                    if self.settings.secret_key
                    else password
                ),
                status=VncStatus.requested,
                started_at=now,
                expires_at=expires_at,
                started_by=started_by,
                url=url,
                proxy_id=proxy_id,
            )
        )
        await self.profiles.update(profile.id, status=str(ProfileStatus.login))
        log.info("VNC session %s reserved on port %s", session_id, port)
        return VncInfo(
            session_id=session_id,
            port=port,
            password=password,
            url=vnc_url(
                self.settings.public_host,
                port,
                password,
                scheme=self.settings.public_scheme,
            ),
            expires_at=expires_at,
        )

    async def reserve_and_launch(
        self,
        profile: Profile,
        url: str,
        proxy_url: str | None = None,
        started_by: int | None = None,
        proxy_id: int | None = None,
    ) -> VncInfo:
        """Reserve a session and start it now (combined mode, CLI login)."""
        info = await self.reserve(
            profile, url, started_by=started_by, proxy_id=proxy_id
        )
        session = await self.sessions.get(info.session_id)
        if session is None:  # pragma: no cover - created a moment ago
            raise RuntimeError(f"VNC session {info.session_id} disappeared")
        await self.launch(session, proxy_url=proxy_url)
        return info

    # Backwards-compatible name used by the CLI and older call sites.
    start = reserve_and_launch

    async def launch(self, session: VncSession, proxy_url: str | None = None) -> None:
        """Start the process stack of a reserved session exactly once.

        Two callers race for the same session: ``reserve_and_launch`` in the
        process that owns the bot and the worker's 5 s tick, which still sees
        the row as ``requested`` while the first launch waits for Xvfb. A
        second stack on the same display and port dies with "Server is already
        active" and "Address already in use", but its handle replaced the live
        one, so the stop killed nothing and Chromium kept ``SingletonLock`` on
        the profile.

        The claim is a conditional UPDATE, not a check in memory: the CLI books
        a login from its own process while the worker runs in the container, so
        an in-process flag would not be enough.
        """
        if session.id in self._handles:
            log.info("VNC session %s is already running; not launching again", session.id)
            return
        if not await self.sessions.claim(session.id):
            log.info("VNC session %s was launched by another caller", session.id)
            return
        profile = await self.profiles.get(session.profile_id)
        if profile is None:
            await self.sessions.update(
                session.id, status=str(VncStatus.failed), password=""
            )
            # A profile deleted between ``reserve`` and this launch. The raise
            # happens before the ``try`` below, so it travels to the login
            # handler: give it a code instead of an English developer line.
            raise VncLoginError(
                f"profile {session.profile_id} not found",
                "error.profile.not_found",
                id=session.profile_id,
            )
        bridge = None
        try:
            if proxy_url is None and session.proxy_id is not None:
                proxy_url, bridge = await self._start_bridge(session.proxy_id)
            started = self.process_factory(
                display=session.display,
                port=session.port,
                # The row holds a Fernet token when UWP_SECRET_KEY is set;
                # x11vnc needs the plain password.
                password=secretbox.decrypt(
                    session.password, self.settings.secret_key
                ),
                profile=profile,
                url=session.url or "",
                proxy_url=proxy_url,
            )
            handle = await started if inspect.isawaitable(started) else started
        except Exception as exc:
            if bridge is not None:
                await _stop_quietly(bridge)
            log.exception("starting VNC session %s failed", session.id)
            await self.sessions.update(
                session.id, status=str(VncStatus.failed), password=""
            )
            await self.profiles.update(profile.id, status=str(ProfileStatus.free))
            await self._emit(
                VNC_FAILED,
                session,
                profile_id=profile.id,
                session_id=session.id,
                error=f"{type(exc).__name__}: {exc}",
                # A sentence we wrote travels as a code, never as Russian text.
                error_code=getattr(exc, "code", None),
                error_detail=getattr(exc, "detail", None) or {},
            )
            raise
        if bridge is not None:
            self._bridges[session.id] = bridge
        self._handles[session.id] = handle
        log.info("VNC session %s on port %s started", session.id, session.port)

    async def _start_bridge(self, proxy_id: int) -> tuple[str | None, Any]:
        """A loopback bridge for ``proxy_id``, or ``None`` with a loud warning.

        The login browser must leave through the same proxy as the parse runs:
        logging in from one IP and scraping from another is the exact signal the
        design avoids. A proxy that cannot be resolved must not silently fall
        back to a direct connection, so the session is failed instead.
        """
        if self.proxies is None:
            log.warning("no proxy service in the VNC manager; proxy %s not applied", proxy_id)
            return None, None
        proxy = await self.proxies.resolved(proxy_id)
        if proxy is None:
            raise VncLoginError(f"proxy {proxy_id} not found", "error.proxy.not_found")
        factory = self.bridge_factory
        if factory is None:
            from uwp.proxybridge import ProxyBridge

            factory = ProxyBridge
        bridge = factory(proxy)
        return await bridge.start(), bridge

    async def request_stop(self, session_id: int) -> None:
        """Ask for a stop; the process that owns the stack performs it.

        A separate ``uwp vnc stop`` process has no handles, so marking the row
        ``stopping`` and letting the worker's tick kill it is the only path that
        works in both combined and split deployments.
        """
        session = await self.sessions.get(session_id)
        if session is None or session.status in (VncStatus.stopped, VncStatus.failed):
            return
        if session_id in self._handles:
            await self.stop(session_id)
            return
        await self.sessions.update(session_id, status=str(VncStatus.stopping))
        log.info("VNC session %s marked for stop", session_id)

    async def stop(self, session_id: int) -> None:
        """Stop a session and free its profile; safe to call twice."""
        session = await self.sessions.get(session_id)
        if session is None or session.status in (VncStatus.stopped, VncStatus.failed):
            return
        await self.sessions.update(session_id, status=str(VncStatus.stopping))
        bridge = self._bridges.pop(session_id, None)
        if bridge is not None:
            await _stop_quietly(bridge)
        handle = self._handles.pop(session_id, None)
        if handle is not None:
            try:
                await handle.stop()
            except Exception:
                log.exception("stopping VNC session %s failed", session_id)
        await self.sessions.update(
            session_id, status=str(VncStatus.stopped), password=""
        )
        # A stopped row is history; its secret has no use. Clearing it keeps a
        # copy of ``data/uwp.db`` from leaking every password ever issued.
        profile = await self.profiles.get(session.profile_id)
        if profile is not None and profile.status == ProfileStatus.login:
            await self.profiles.update(profile.id, status=str(ProfileStatus.free))

    async def reconcile(self, now: datetime) -> None:
        """Launch requested sessions and finish stops other processes asked for."""
        for session in await self.sessions.pending_stop():
            log.info("VNC session %s was stopped elsewhere", session.id)
            await self.stop(session.id)
        for session in await self.sessions.pending_start():
            if session.expires_at <= now:
                await self.stop(session.id)
                continue
            try:
                await self.launch(session)
            except Exception:  # already recorded as failed; keep the loop alive
                log.exception("launching VNC session %s failed", session.id)

    async def _reap(self) -> None:
        """Kill stacks whose session row another process already finished.

        Without this a split deployment leaks an Xvfb + Chromium + web stack
        every time the admin presses «Готово» in the bot process.
        """
        if not self._handles:
            return
        alive = {session.id for session in await self.sessions.active()}
        for session_id in [sid for sid in self._handles if sid not in alive]:
            handle = self._handles.pop(session_id, None)
            bridge = self._bridges.pop(session_id, None)
            log.info("VNC session %s stopped elsewhere; killing it here", session_id)
            if bridge is not None:
                await _stop_quietly(bridge)
            if handle is None:
                continue
            try:
                await handle.stop()
            except Exception:
                log.exception("stopping VNC session %s failed", session_id)

    async def tick(self, now: datetime) -> None:
        """Start what was requested, stop what expired or sat idle."""
        await self.reconcile(now)
        await self._reap()
        idle_after = timedelta(minutes=self.settings.vnc_idle_minutes)
        for session in await self.sessions.active():
            if session.expires_at <= now:
                log.info("VNC session %s expired", session.id)
                await self._emit(VNC_STOPPED, session, profile_id=session.profile_id)
                await self.stop(session.id)
                continue
            handle = self._handles.get(session.id)
            if handle is not None:
                try:
                    clients = handle.client_count()
                except Exception:  # noqa: BLE001 - a broken handle must not stall tick
                    clients = 0
                if clients > 0:
                    await self.sessions.update(session.id, last_client_at=now)
                    continue
            if (
                session.last_client_at is not None
                and session.last_client_at + idle_after <= now
            ):
                log.info("VNC session %s idle", session.id)
                await self._emit(VNC_STOPPED, session, profile_id=session.profile_id)
                await self.stop(session.id)

    async def list(self) -> list[VncSession]:
        """Sessions that are still running."""
        return await self.sessions.active()
