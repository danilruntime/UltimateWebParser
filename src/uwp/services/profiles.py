"""Profile management driven from the bot."""
from __future__ import annotations

import logging

from uwp.config import Settings
from uwp.db.repo.profiles import ProfilesRepo
from uwp.domain import Profile, ProfileKind
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

    async def list(self) -> list[Profile]:
        """Only the profiles an admin controls; clean ones are per-run garbage."""
        return [p for p in await self.repo.list() if p.kind == ProfileKind.persistent]

    async def get(self, profile_id: int) -> Profile | None:
        return await self.repo.get(profile_id)

    async def create(self, name: str, proxy_id: int | None = None) -> Profile:
        name = name.strip()
        if not name:
            raise ValueError("имя профиля не может быть пустым")
        return await self.manager.create_persistent(name, proxy_id=proxy_id)

    async def delete(self, profile_id: int) -> None:
        await self.manager.delete(profile_id)

    async def release(self, profile_id: int) -> None:
        """Free a profile after a finished login."""
        await self.manager.release(profile_id)
