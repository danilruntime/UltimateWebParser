"""VNC login sessions: a headful browser on a virtual display, exposed via noVNC.

The bot only asks for a session and shows the link; everything else (Xvfb,
window manager, browser, x11vnc, websockify) lives inside the worker.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import secrets
import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import Profile, ProfileKind, ProfileStatus, VncSession, VncStatus
from uwp.events import VNC_FAILED, VNC_STOPPED
from uwp.profiles import browser_flags
from uwp.timeutil import utcnow

log = logging.getLogger(__name__)

PASSWORD_LENGTH = 12
DISPLAY_BASE = 100
# Raw RFB listeners live in their own internal band so they can never collide
# with a published noVNC port (6000-6100).
RFB_PORT_BASE = 4900
# How long Xvfb/x11vnc may take to become ready before the login fails.
READY_TIMEOUT = 15.0
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


def vnc_url(public_host: str, port: int, password: str) -> str:
    """noVNC URL with autoconnect, scaled display and the session password."""
    return (
        f"http://{public_host}:{port}/vnc.html"
        f"?autoconnect=1&resize=scale&password={password}"
    )


def _record_count_hook(marker: Path) -> str:
    """Shell command for x11vnc's ``-afteraccept``/``-gone`` hooks.

    x11vnc already runs hooks through ``/bin/sh -c``, so only the redirect is
    needed; quoting the path protects spaces and quotes.
    """
    return f'echo "$RFB_CLIENT_COUNT" > {shlex.quote(str(marker))}'


class _Stack:
    """Default process handle: keeps the spawned processes and their files."""

    def __init__(
        self,
        processes: list[asyncio.subprocess.Process],
        password_file: Path,
        client_marker: Path | None = None,
        log_file: Any = None,
    ) -> None:
        self.processes = processes
        self.password_file = password_file
        self.client_marker = client_marker
        self.log_file = log_file

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
        store = await _spawn(["x11vnc", "-storepasswd", password, str(password_file)])
        if await store.wait() != 0:
            raise RuntimeError("x11vnc -storepasswd failed")

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
            )
            if not flag.startswith("--headless")
        ]
        browser_env = {**os.environ, "DISPLAY": screen}
        client_marker = password_file.with_suffix(".clients")
        client_marker.unlink(missing_ok=True)
        record_count = _record_count_hook(client_marker)
        log_name = password_file.parent / f"{profile.id or 'x'}-{port}.log"
        log_file = log_name.open("ab")
        # Each process announces its own failure on stderr; a shared log turns
        # the old silent "noVNC shows an empty page" into a readable reason.
        commands = [
            ["Xvfb", screen, "-screen", "0", "1920x1080x24"],
            ["fluxbox", "-display", screen],
            [executable, *args, url],
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
                str(port),
                f"localhost:{vnc_port}",
            ],
        ]
        processes: list[asyncio.subprocess.Process] = []
        stack = _Stack(processes, password_file, client_marker, log_file)
        try:
            # Xvfb first and wait for its socket: fluxbox, x11vnc and Chromium
            # all abort with "cannot open display" if they lose that race.
            processes.append(
                await _spawn(commands[0], stderr=log_file, env=None)
            )
            if not await _wait_for_path(
                Path(f"/tmp/.X11-unix/X{display}"), READY_TIMEOUT
            ):
                raise RuntimeError(
                    f"Xvfb не поднял дисплей :{display}: {_log_tail(log_name)}"
                )
            for command in commands[1:]:
                env = browser_env if command[0] == executable else None
                processes.append(await _spawn(command, stderr=log_file, env=env))
            # x11vnc binds its RFB port late; noVNC would show a blank page.
            if not await _wait_for_port(vnc_port, READY_TIMEOUT):
                raise RuntimeError(
                    f"x11vnc не слушает порт {vnc_port}: {_log_tail(log_name)}"
                )
        except BaseException:
            # Do not leak a half-started stack: kill whatever did start.
            await stack.stop()
            raise
        return stack

    return factory


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
    ) -> None:
        self.settings = settings
        self.profiles = profiles
        self.sessions = sessions
        self.process_factory = process_factory or _default_process_factory(settings)
        self.bus = bus
        self._handles: dict[int, ProcessHandle] = {}

    @staticmethod
    def _check_profile(profile: Profile) -> None:
        """Reject profiles a login session cannot use."""
        if profile.kind != ProfileKind.persistent:
            raise ValueError("VNC login requires a persistent profile")
        if profile.status != ProfileStatus.free:
            raise ValueError(f"profile is not free: {profile.status}")
        if profile.id is None:
            raise ValueError("profile is not stored")

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
    ) -> VncInfo:
        """Book a session in the database without launching anything.

        The bot owns this half: it has no GUI stack of its own, so it writes a
        ``requested`` row and the worker (same process in combined mode) picks
        it up and launches the browser.
        """
        self._check_profile(profile)
        port = free_port(
            self.settings.vnc_port_min,
            self.settings.vnc_port_max,
            await self.sessions.taken_ports(),
        )
        display = DISPLAY_BASE + (port - self.settings.vnc_port_min)
        password = secrets.token_urlsafe(9)[:PASSWORD_LENGTH]
        now = utcnow()
        expires_at = now + timedelta(minutes=self.settings.vnc_ttl_minutes)
        session_id = await self.sessions.create(
            VncSession(
                profile_id=profile.id,
                port=port,
                display=display,
                password=password,
                token=secrets.token_urlsafe(16),
                status=VncStatus.requested,
                started_at=now,
                expires_at=expires_at,
                started_by=started_by,
                url=url,
            )
        )
        await self.profiles.update(profile.id, status=str(ProfileStatus.login))
        log.info("VNC session %s reserved on port %s", session_id, port)
        return VncInfo(
            session_id=session_id,
            port=port,
            password=password,
            url=vnc_url(self.settings.public_host, port, password),
            expires_at=expires_at,
        )

    async def reserve_and_launch(
        self,
        profile: Profile,
        url: str,
        proxy_url: str | None = None,
        started_by: int | None = None,
    ) -> VncInfo:
        """Reserve a session and start it now (combined mode, CLI login)."""
        info = await self.reserve(profile, url, started_by=started_by)
        session = await self.sessions.get(info.session_id)
        if session is None:  # pragma: no cover - created a moment ago
            raise RuntimeError(f"VNC session {info.session_id} disappeared")
        await self.launch(session, proxy_url=proxy_url)
        return info

    # Backwards-compatible name used by the CLI and older call sites.
    start = reserve_and_launch

    async def launch(self, session: VncSession, proxy_url: str | None = None) -> None:
        """Start the process stack of a reserved session."""
        profile = await self.profiles.get(session.profile_id)
        if profile is None:
            await self.sessions.update(session.id, status=str(VncStatus.failed))
            raise ValueError(f"profile {session.profile_id} not found")
        try:
            started = self.process_factory(
                display=session.display,
                port=session.port,
                password=session.password,
                profile=profile,
                url=session.url or "",
                proxy_url=proxy_url,
            )
            handle = await started if inspect.isawaitable(started) else started
        except Exception as exc:
            log.exception("starting VNC session %s failed", session.id)
            await self.sessions.update(session.id, status=str(VncStatus.failed))
            await self.profiles.update(profile.id, status=str(ProfileStatus.free))
            await self._emit(
                VNC_FAILED,
                session,
                profile_id=profile.id,
                session_id=session.id,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        await self.sessions.update(session.id, status=str(VncStatus.running))
        self._handles[session.id] = handle
        log.info("VNC session %s on port %s started", session.id, session.port)

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
        handle = self._handles.pop(session_id, None)
        if handle is not None:
            try:
                await handle.stop()
            except Exception:
                log.exception("stopping VNC session %s failed", session_id)
        await self.sessions.update(session_id, status=str(VncStatus.stopped))
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
            log.info("VNC session %s stopped elsewhere; killing it here", session_id)
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
