"""VNC login sessions driven from the bot."""

from __future__ import annotations

import logging
from datetime import timedelta

from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import VncSession
from uwp.timeutil import utcnow
from uwp.vnc import VncInfo, VncManager

log = logging.getLogger(__name__)

EXTEND_MINUTES = 30
FALLBACK_URL = "https://example.com"


class VncService:
    """The bot-facing view of ``VncManager`` plus session bookkeeping."""

    def __init__(
        self,
        manager: VncManager,
        profiles: ProfilesRepo,
        sites: SitesRepo,
        settings: Settings,
        sessions: VncRepo,
        parser_urls: dict[str, str] | None = None,
        eager_launch: bool = True,
    ) -> None:
        self.manager = manager
        self.profiles = profiles
        self.sites = sites
        self.settings = settings
        self.sessions = sessions
        self.parser_urls = parser_urls or {}
        # In split mode the bot process has no GUI stack: it books the session
        # and the worker launches it on its next tick.
        self.eager_launch = eager_launch

    async def request(
        self,
        profile_id: int,
        site_id: int | None = None,
        started_by: int | None = None,
    ) -> VncInfo:
        """Open a login session for a persistent profile."""
        profile = await self.profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"профиль {profile_id} не найден")
        url = FALLBACK_URL
        proxy_id = profile.proxy_id
        if site_id is not None:
            site = await self.sites.get(site_id)
            if site is not None:
                url = self.parser_urls.get(site.parser_key, FALLBACK_URL)
                # Site proxy wins over the profile's own, as the spec requires.
                proxy_id = site.proxy_id or proxy_id
        proxy_url = self._proxy_url(proxy_id)
        if self.eager_launch:
            return await self.manager.reserve_and_launch(
                profile, url, proxy_url=proxy_url, started_by=started_by
            )
        return await self.manager.reserve(profile, url, started_by=started_by)

    async def stop(self, session_id: int) -> None:
        await self.manager.request_stop(session_id)

    async def list(self) -> list[VncSession]:
        return await self.manager.list()

    async def active_session(self, profile_id: int) -> VncSession | None:
        """The login session of ``profile_id`` that is still running, if any."""
        for session in await self.manager.list():
            if session.profile_id == profile_id:
                return session
        return None

    async def extend(
        self, session_id: int, minutes: int = EXTEND_MINUTES
    ) -> VncSession:
        """Push a session's hard TTL further so the admin can keep working."""
        session = await self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"сессия {session_id} не найдена")
        expires = max(session.expires_at, utcnow()) + timedelta(minutes=minutes)
        await self.sessions.update(session_id, expires_at=expires)
        session.expires_at = expires
        return session

    async def remaining_minutes(self, session: VncSession) -> int:
        return max(0, int((session.expires_at - utcnow()).total_seconds() // 60))

    async def mark_done(self, session_id: int) -> None:
        """The admin says login is finished: stop the stack unless already gone.

        Use the request path, not ``stop``: in split mode the bot process does
        not own the processes, so it must leave the kill to the worker. The
        worker frees the profile once the stack is really gone.
        """
        session = await self.sessions.get(session_id)
        if session is None:
            return
        await self.manager.request_stop(session_id)

    def _proxy_url(self, proxy_id: int | None) -> str | None:
        """A loopback bridge URL for the profile's proxy.

        The bridge (Task 8) is not built yet, so a session with a proxy runs
        without it and says so in the log rather than silently going direct.
        """
        if proxy_id is None:
            return None
        log.warning(
            "profile proxy %s is not applied to the VNC session: proxy bridge is unavailable",
            proxy_id,
        )
        return None


def session_text(info: VncInfo, remaining: int) -> str:
    """Russian card shown right after a VNC session starts."""
    return (
        "🖥 <b>Вход на сайт</b>\n\n"
        f"Ссылка: {info.url}\n"
        f"Пароль: <code>{info.password}</code>\n"
        f"Осталось: {remaining} мин.\n\n"
        "Откройте ссылку, войдите на сайт и нажмите «Готово»."
    )
