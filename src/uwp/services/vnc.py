"""VNC login sessions driven from the bot."""

from __future__ import annotations

import logging
from datetime import timedelta

from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.sites import SitesRepo
from uwp.db.repo.vnc import VncRepo
from uwp.domain import Site, VncSession
from uwp.i18n import Lang, t
from uwp.timeutil import utcnow
from uwp.vnc import VncInfo, VncManager

log = logging.getLogger(__name__)

EXTEND_MINUTES = 30


class VncService:
    """The bot-facing view of ``VncManager`` plus session bookkeeping."""

    def __init__(
        self,
        manager: VncManager,
        profiles: ProfilesRepo,
        sites: SitesRepo,
        settings: Settings,
        sessions: VncRepo,
        parser_auth_urls: dict[str, str] | None = None,
        eager_launch: bool = True,
    ) -> None:
        self.manager = manager
        self.profiles = profiles
        self.sites = sites
        self.settings = settings
        self.sessions = sessions
        # The pages a VNC login opens, per parser key.
        self.parser_auth_urls = parser_auth_urls or {}
        # In split mode the bot process has no GUI stack: it books the session
        # and the worker launches it on its next tick.
        self.eager_launch = eager_launch

    @property
    def lang(self) -> Lang:
        """The deployment language: the fallback when no reader is in context."""
        return Lang(self.settings.default_language)

    async def _login_site(
        self, profile_id: int, site_id: int | None, lang: Lang
    ) -> Site:
        """The site whose auth page the login opens.

        A login started from a site card names it; the profile card does not,
        so a profile attached to exactly one site lends that site's page and
        proxy. Two attached sites are refused rather than guessed: their
        proxies can differ, and a login through the wrong country looks like a
        hijacked account.
        """
        if site_id is not None:
            site = await self.sites.get(site_id)
            if site is None:
                raise ValueError(t(lang, "error.site.not_found", id=site_id))
            return site
        attached = await self.sites.by_profile(profile_id)
        if not attached:
            raise ValueError(t(lang, "error.vnc.profile_without_site"))
        if len(attached) > 1:
            raise ValueError(t(lang, "error.vnc.profile_many_sites"))
        return attached[0]

    async def request(
        self,
        profile_id: int,
        site_id: int | None = None,
        started_by: int | None = None,
        *,
        lang: Lang | None = None,
    ) -> VncInfo:
        """Open a login session for a persistent profile.

        ``lang`` is the reader's language: the bot passes the admin's, while the
        CLI passes nothing and gets the deployment's.
        """
        reader = lang or self.lang
        profile = await self.profiles.get(profile_id)
        if profile is None:
            raise ValueError(
                t(
                    reader,
                    "error.profile.not_found",
                    id=profile_id,
                )
            )
        site = await self._login_site(profile_id, site_id, reader)
        url = self.parser_auth_urls.get(site.parser_key)
        if url is None:
            raise ValueError(
                t(reader, "error.vnc.site_parser_unavailable", parser=site.parser_key)
            )
        # Site proxy wins over the profile's own, as the spec requires.
        proxy_id = site.proxy_id or profile.proxy_id
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
        self,
        session_id: int,
        minutes: int = EXTEND_MINUTES,
        *,
        lang: Lang | None = None,
    ) -> VncSession:
        """Push a session's hard TTL further so the admin can keep working.

        ``lang`` is the reader's language for the missing-session refusal.
        """
        session = await self.sessions.get(session_id)
        if session is None:
            raise ValueError(
                t(
                    lang or self.lang,
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
