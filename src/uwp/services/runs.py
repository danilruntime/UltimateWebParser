"""Run history and retries driven from the bot."""
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.runs import RunsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import Run


class RunsService:
    """Recent runs of a site plus the «retry» action."""

    def __init__(self, runs: RunsRepo, sites: SitesRepo, jobs: JobsRepo) -> None:
        self.runs = runs
        self.sites = sites
        self.jobs = jobs

    async def recent(self, site_id: int, limit: int = 10) -> list[Run]:
        return await self.runs.list_for_site(site_id, limit=limit)

    async def retry(self, site_id: int) -> int | None:
        """Queue a manual run; ``None`` when one is already active."""
        return await self.jobs.create(site_id, "manual", priority=10)
