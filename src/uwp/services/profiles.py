"""Profile management driven from the bot."""
from __future__ import annotations

import logging

from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.domain import Profile, ProfileKind
from uwp.i18n import Lang, t
from uwp.profiles import ProfileManager

log = logging.getLogger(__name__)


class ProfilesService:
    """Persistent login profiles; clean profiles are created per run."""

    def __init__(
        self, manager: ProfileManager, repo: ProfilesRepo, settings: Settings
    ) -> None:
        self.manager = manager
        self.repo = repo
        self.settings = settings

    @property
    def lang(self) -> Lang:
        """The service's fallback language, for the CLI and the worker."""
        return Lang(self.settings.default_language)

    async def list(self) -> list[Profile]:
        """Only the profiles an admin controls; clean ones are per-run garbage."""
        return [p for p in await self.repo.list() if p.kind == ProfileKind.persistent]

    async def get(self, profile_id: int) -> Profile | None:
        return await self.repo.get(profile_id)

    async def create(
        self, name: str, proxy_id: int | None = None, *, lang: Lang | None = None
    ) -> Profile:
        """Create a persistent profile.

        ``lang`` is the reader's language: the bot has one admin in front of it
        and passes theirs, while the CLI and the worker have only the
        deployment's. The service's own language is the fallback.
        """
        name = name.strip()
        if not name:
            raise ValueError(t(lang or self.lang, "error.profile.name_empty"))
        return await self.manager.create_persistent(
            name, proxy_id=proxy_id, lang=lang or self.lang
        )

    async def delete(self, profile_id: int, *, lang: Lang | None = None) -> None:
        """Remove a profile; ``lang`` is the reader's, as in :meth:`create`."""
        await self.manager.delete(profile_id, lang=lang or self.lang)

    async def release(self, profile_id: int) -> None:
        """Free a profile after a finished login.

        Only a profile still in ``login`` is freed; see
        :meth:`ProfilesRepo.release_login` for why.
        """
        await self.repo.release_login(profile_id)
