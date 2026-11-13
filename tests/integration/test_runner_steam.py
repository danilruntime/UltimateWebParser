"""Integration: a real browser run of the Steam parser end to end.

Run with ``UWP_TEST_BROWSER=/path/to/chrome pytest -m integration``. Skipped by
default, so the normal suite never needs a browser binary.
"""
import os

import pytest

from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.items import ItemsRepo
from uwp.db.repo.jobs import JobsRepo
from uwp.db.repo.profiles import ProfilesRepo
from uwp.db.repo.runs import RunsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import RunStatus, Site
from uwp.parsers.registry import ParserRegistry
from uwp.profiles import ProfileManager
from uwp.runner import Runner
from uwp.timeutil import utcnow

pytestmark = pytest.mark.integration
BROWSER = os.environ.get("UWP_TEST_BROWSER")


@pytest.mark.skipif(not BROWSER, reason="UWP_TEST_BROWSER not set")
async def test_steam_run_produces_rows(tmp_path):
    settings = load_settings(
        {
            "UWP_DATA_DIR": str(tmp_path),
            "UWP_BROWSER_PATH": BROWSER,
            "UWP_NO_SANDBOX": "true",
            "UWP_RUN_TIMEOUT": "180",
        }
    )
    db = await Database.in_memory()
    await db.migrate()
    try:
        profiles = ProfilesRepo(db)
        runner = Runner(
            settings,
            ProfileManager(settings, profiles),
            ParserRegistry.discover(),
            RunsRepo(db),
            ItemsRepo(db),
        )

        # The runs table has foreign keys to both the site and the job, so the
        # run has to start from real rows rather than hand-built models.
        site = Site(
            slug="steam",
            name="Steam",
            parser_key="steam_deals",
            interval_min_min=1,
            interval_max_min=2,
            parser_settings={"max_pages": 1},
        )
        site.id = await SitesRepo(db).create(site)
        jobs = JobsRepo(db)
        await jobs.create(site.id, "manual")
        job = await jobs.claim("integration", utcnow())

        outcome = await runner.run(site, job, proxy=None)

        assert outcome.status == RunStatus.ok, outcome.error
        assert len(outcome.rows) >= 10
        assert any(row.get("discount_percent", 0) > 0 for row in outcome.rows)
    finally:
        await db.close()
