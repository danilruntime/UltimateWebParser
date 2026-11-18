"""First-run seeding shared by ``uwp init`` and every process start.

A fresh install must be usable right after ``docker compose up``: the admin
rows come from ``UWP_ADMIN_IDS`` and the example site exists so the bot has
something to switch on.
"""

import logging
import sqlite3

from uwp.config import Settings
from uwp.db.database import Database
from uwp.db.repo.admins import AdminsRepo
from uwp.db.repo.sites import SitesRepo
from uwp.domain import Site
from uwp.i18n import Lang, t

log = logging.getLogger(__name__)

SEED_PARSER = "steam_deals"
SEED_SITE_SLUG = "steam_skidki"


def seed_site_name(settings: Settings) -> str:
    """The example site's name in the deployment's language.

    A literal here named an English deployment's first site «Steam — скидки»
    while the parser picker next to it said "Steam — deals" — one screen, two
    names for one thing. The slug never changes: it is what the docs and
    ``uwp site enable steam_skidki`` point at.
    """
    return t(Lang(settings.default_language), "parser.steam_deals.title")


async def is_fresh(db: Database) -> bool:
    """True when the database has no schema yet, i.e. this is the first start."""
    try:
        row = await db.fetchone("SELECT MAX(version) AS version FROM schema_version")
    except sqlite3.Error:  # the table itself does not exist yet
        return True
    return row is None or row["version"] is None


async def seed(db: Database, settings: Settings) -> bool:
    """Create the admin rows and the example site if they are missing.

    Returns ``True`` when the example site was created by this call.
    """
    admins = AdminsRepo(db)
    for admin_id in settings.admin_ids:
        await admins.add(admin_id)

    sites = SitesRepo(db)
    if await sites.list():
        return False
    await sites.create(
        Site(
            slug=SEED_SITE_SLUG,
            name=seed_site_name(settings),
            parser_key=SEED_PARSER,
            enabled=False,
            interval_min_min=45,
            interval_max_min=90,
        )
    )
    log.info("seeded the example site %s", SEED_SITE_SLUG)
    return True
