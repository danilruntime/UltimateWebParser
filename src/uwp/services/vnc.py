"""VNC login sessions driven from the bot."""

from __future__ import annotations

import logging
from datetime import timedelta

from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import VncSession
from uwp.i18n import t
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
            raise ValueError(
                t(
                    self.settings.default_language,
                    "error.profile.not_found",
                    id=profile_id,
                )
            )
        url = FALLBACK_URL
        proxy_id = profile.proxy_id
        if site_id is not None:
            site = await self.sites.get(site_id)
            if site is not None:
                url = self.parser_urls.get(site.parser_key, FALLBACK_URL)
                # Site proxy wins over the profile's own, as the spec requires.
                proxy_id = site.proxy_id or proxy_id
        if self.eager_launch:
            return await self.manager.reserve_and_launch(
                profile, url, proxy_id=proxy_id, started_by=started_by
            )
        return await self.manager.reserve(
            profile, url, started_by=started_by, proxy_id=proxy_id
        )

    async def stop(self, session_id: int) -> None:
        await self.manager.request_stop(session_id)

    async def list(self) -> list[VncSession]:
        return await self.manager.list()

    async def active_session(self, profile_id: int) -> VncSession | None:
        """The login session of ``profile_id`` that has not finished, if any.

        A ``stopping`` session still owns the profile: the stack is alive until
        the owning process kills it, so «Готово» pressed twice must find it and
        not free the profile underneath a running browser.
        """
        return await self.sessions.active_or_stopping(profile_id)

    async def extend(
        self, session_id: int, minutes: int = EXTEND_MINUTES
    ) -> VncSession:
        """Push a session's hard TTL further so the admin can keep working."""
        session = await self.sessions.get(session_id)
        if session is None:
            raise ValueError(
                t(
                    self.settings.default_language,
                    "error.vnc.session_not_found",
                    id=session_id,
                )
            )
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
