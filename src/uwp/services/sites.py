"""Site management used by the bot: create, toggle, run now and edit."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import Site
from uwp.i18n import DEFAULT_LANG, Lang, t
from uwp.parsers.registry import ParserRegistry
from uwp.scheduler import Scheduler

log = logging.getLogger(__name__)

_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """ASCII slug: lowercase, runs of non-alphanumerics collapsed to ``_``."""
    return _SLUG_SEPARATORS.sub("_", name.lower()).strip("_")


class SiteService:
    """Every mutation the bot can make to a site."""

    def __init__(
        self,
        sites: SitesRepo,
        jobs: JobsRepo,
        scheduler: Scheduler,
        registry: ParserRegistry,
        lang: Lang = DEFAULT_LANG,
    ) -> None:
        self.sites = sites
        self.jobs = jobs
        self.scheduler = scheduler
        self.registry = registry
        # No admin is in context when a service raises: the deployment's
        # language is the only one available to both the CLI and the bot.
        self.lang = lang

    async def list(self) -> list[Site]:
        return await self.sites.list()

    async def get(self, site_id: int) -> Site | None:
        return await self.sites.get(site_id)

    async def create(self, parser_key: str, name: str | None = None) -> Site:
        """Create a disabled site; ``next_run_at`` stays unset until enabled."""
        parser = self.registry.get(parser_key)
        if parser is None:
            raise ValueError(t(self.lang, "error.parser.unknown", parser=parser_key))
        title = name or parser.title
        site = Site(
            slug=await self._unique_slug(slugify(title) or parser_key),
            name=title,
            parser_key=parser_key,
            enabled=False,
            interval_min_min=45,
            interval_max_min=90,
        )
        site.id = await self.sites.create(site)
        log.info("created site %s (%s)", site.slug, site.id)
        return site

    async def set_enabled(self, site_id: int, enabled: bool, now: datetime) -> Site:
        await self.sites.update(site_id, enabled=enabled)
        if enabled:
            await self.scheduler.on_enabled(site_id, now)
        else:
            await self.scheduler.on_disabled(site_id)
        return await self._require(site_id)

    async def run_now(self, site_id: int) -> int | None:
        """Queue a manual run; ``None`` when the site already has an active job."""
        await self._require(site_id)
        return await self.jobs.create(site_id, "manual", priority=10)

    async def update(self, site_id: int, **fields: object) -> Site:
        await self.sites.update(site_id, **fields)
        return await self._require(site_id)

    async def _require(self, site_id: int) -> Site:
        site = await self.sites.get(site_id)
        if site is None:
            raise ValueError(t(self.lang, "error.site.not_found", id=site_id))
        return site

    async def delete(self, site_id: int) -> None:
        await self.jobs.cancel_queued(site_id)
        await self.sites.delete(site_id)

    async def _unique_slug(self, base: str) -> str:
        existing = {site.slug for site in await self.sites.list()}
        if base not in existing:
            return base
        counter = 2
        while f"{base}_{counter}" in existing:
            counter += 1
        return f"{base}_{counter}"
