"""VNC login sessions: a headful browser on a virtual display, exposed via noVNC.

The bot only asks for a session and shows the link; everything else (Xvfb,
window manager, browser, x11vnc, websockify) lives inside the worker.
"""
import asyncio
import inspect
import logging
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import Profile, ProfileKind, ProfileStatus, VncSession, VncStatus
from uwp.profiles import browser_flags
from uwp.timeutil import to_db, utcnow

log = logging.getLogger(__name__)

PASSWORD_LENGTH = 12
DISPLAY_BASE = 100
# The raw RFB port sits below the published noVNC port.
RFB_PORT_OFFSET = 100


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
    """Return the first port in ``[min_port, max_port]`` that is not taken."""
    for port in range(min_port, max_port + 1):
        if port not in taken:
            return port
    raise RuntimeError("no free VNC port")


def vnc_url(public_host: str, port: int, password: str) -> str:
    """noVNC URL with autoconnect, scaled display and the session password."""
    return (
        f"http://{public_host}:{port}/vnc.html"
        f"?autoconnect=1&resize=scale&password={password}"
    )


class _Stack:
    """Default process handle: keeps the spawned processes and their files."""

    def __init__(self, processes: list[asyncio.subprocess.Process], password_file: Path) -> None:
        self.processes = processes
        self.password_file = password_file

    def client_count(self) -> int:
        """Number of clients connected to websockify, as far as we can tell.

        Without a websockify status port we cannot know for sure, so the stack
        reports ``0`` and idle stop is disabled rather than arbitrary.
        """
        return 0

    async def stop(self) -> None:
        for process in reversed(self.processes):
            if process.returncode is not None:
                continue
            process.terminate()
        for process in reversed(self.processes):
            if process.returncode is not None:
                continue
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        self.password_file.unlink(missing_ok=True)


async def _spawn(command: list[str], **kw: Any) -> asyncio.subprocess.Process:
    log.debug("starting %s", command[0])
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **kw,
    )


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
        await _spawn(["x11vnc", "-storepasswd", password, str(password_file)])

        # The RFB port is derived from the published noVNC port and never
        # exposed outside the worker container.
        vnc_port = port - RFB_PORT_OFFSET
        screen = f":{display}"
        executable = settings.browser_path or shutil.which("chromium") or "chromium"
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
        processes = [
            await _spawn(["Xvfb", screen, "-screen", "0", "1920x1080x24"]),
            await _spawn(["fluxbox", "-display", screen]),
            await _spawn([executable, *args, url], env=browser_env),
            await _spawn(
                ["x11vnc", "-display", screen, "-rfbauth", str(password_file),
                 "-rfbport", str(vnc_port), "-localhost", "-forever", "-shared"]
            ),
        ]
        processes.append(
            await _spawn(
                ["websockify", "--web", "/usr/share/novnc",
                 "--idle-timeout", str(settings.vnc_idle_minutes * 60),
                 str(port), f"localhost:{vnc_port}"]
            )
        )
        return _Stack(processes, password_file)

    return factory


def _identity(profile: Profile):
    from uwp.profiles import identity_from_seed

    return identity_from_seed(profile.seed)


class VncManager:
    """Starts, tracks and stops VNC login sessions."""

    def __init__(
        self,
        settings: Settings,
        profiles: ProfilesRepo,
        sessions: VncRepo,
        process_factory: Any = None,
    ) -> None:
        self.settings = settings
        self.profiles = profiles
        self.sessions = sessions
        self.process_factory = process_factory or _default_process_factory(settings)
        self._handles: dict[int, ProcessHandle] = {}

    async def start(
        self,
        profile: Profile,
        url: str,
        proxy_url: str | None = None,
        started_by: int | None = None,
    ) -> VncInfo:
        """Start a login session for ``profile`` and return its connection info."""
        if profile.kind != ProfileKind.persistent:
            raise ValueError("VNC login requires a persistent profile")
        if profile.status != ProfileStatus.free:
            raise ValueError(f"profile is not free: {profile.status}")
        if profile.id is None:
            raise ValueError("profile is not stored")

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
                status=VncStatus.running,
                started_at=now,
                expires_at=expires_at,
                started_by=started_by,
            )
        )
        await self.profiles.update(profile.id, status=str(ProfileStatus.login))

        try:
            started = self.process_factory(
                display=display,
                port=port,
                password=password,
                profile=profile,
                url=url,
                proxy_url=proxy_url,
            )
            handle = await started if inspect.isawaitable(started) else started
        except Exception:
            log.exception("starting VNC session %s failed", session_id)
            await self.sessions.update(session_id, status=str(VncStatus.failed))
            await self.profiles.update(profile.id, status=str(ProfileStatus.free))
            raise
        self._handles[session_id] = handle
        log.info("VNC session %s on port %s started", session_id, port)
        return VncInfo(
            session_id=session_id,
            port=port,
            password=password,
            url=vnc_url(self.settings.public_host, port, password),
            expires_at=expires_at,
        )

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

    async def tick(self, now: datetime) -> None:
        """Stop sessions past their TTL or idle for too long."""
        idle_after = timedelta(minutes=self.settings.vnc_idle_minutes)
        for session in await self.sessions.active():
            if session.expires_at <= now:
                log.info("VNC session %s expired", session.id)
                await self.stop(session.id)
                continue
            handle = self._handles.get(session.id)
            if handle is not None:
                try:
                    clients = handle.client_count()
                except Exception:  # noqa: BLE001 - a broken handle must not stall tick
                    clients = 0
                if clients > 0:
                    await self.sessions.update(session.id, last_client_at=to_db(now))
                    continue
            if session.last_client_at is not None and session.last_client_at + idle_after <= now:
                log.info("VNC session %s idle", session.id)
                await self.stop(session.id)

    async def list(self) -> list[VncSession]:
        """Sessions that are still running."""
        return await self.sessions.active()
