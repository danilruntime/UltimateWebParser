"""Repository for site (parser instance) rows."""
import json
from datetime import datetime
from typing import Any

import aiosqlite

from uwp.db.database import Database
from uwp.db.repo._common import bool_to_db, build_update, dt, parse_dt
from uwp.domain import ExportMode, ProfileKind, Site
from uwp.timeutil import to_db

_COLUMNS: dict[str, tuple[str, Any]] = {
    "slug": ("slug", str),
    "name": ("name", str),
    "parser_key": ("parser_key", str),
    "parser_settings": ("parser_settings", json.dumps),
    "enabled": ("enabled", bool_to_db),
    "profile_mode": ("profile_mode", str),
    "profile_id": ("profile_id", lambda v: v),
    "proxy_id": ("proxy_id", lambda v: v),
    "interval_min_min": ("interval_min_min", int),
    "interval_max_min": ("interval_max_min", int),
    "next_run_at": ("next_run_at", dt),
    "export_mode": ("export_mode", str),
    "max_attempts": ("max_attempts", int),
    "last_run_at": ("last_run_at", dt),
    "last_status": ("last_status", lambda v: v),
    "consecutive_failures": ("consecutive_failures", int),
    "match_profile_to_proxy_country": ("match_profile_to_proxy_country", bool_to_db),
}


def _row_to_site(row: aiosqlite.Row) -> Site:
    return Site(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        parser_key=row["parser_key"],
        parser_settings=json.loads(row["parser_settings"]),
        enabled=bool(row["enabled"]),
        profile_mode=ProfileKind(row["profile_mode"]),
        profile_id=row["profile_id"],
        proxy_id=row["proxy_id"],
        interval_min_min=row["interval_min_min"],
        interval_max_min=row["interval_max_min"],
        next_run_at=parse_dt(row["next_run_at"]),
        export_mode=ExportMode(row["export_mode"]),
        max_attempts=row["max_attempts"],
        last_run_at=parse_dt(row["last_run_at"]),
        last_status=row["last_status"],
        consecutive_failures=row["consecutive_failures"],
        match_profile_to_proxy_country=bool(row["match_profile_to_proxy_country"]),
    )


class SitesRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, site_id: int) -> Site | None:
        row = await self.db.fetchone("SELECT * FROM sites WHERE id = ?", (site_id,))
        return None if row is None else _row_to_site(row)

    async def get_by_slug(self, slug: str) -> Site | None:
        row = await self.db.fetchone("SELECT * FROM sites WHERE slug = ?", (slug,))
        return None if row is None else _row_to_site(row)

    async def list(self) -> list[Site]:
        rows = await self.db.fetchall("SELECT * FROM sites ORDER BY id")
        return [_row_to_site(r) for r in rows]

    async def create(self, site: Site) -> int:
        return await self.db.execute(
            """INSERT INTO sites (slug, name, parser_key, parser_settings, enabled,
                                  profile_mode, profile_id, proxy_id, interval_min_min,
                                  interval_max_min, next_run_at, export_mode, max_attempts,
                                  last_run_at, last_status, consecutive_failures,
                                  match_profile_to_proxy_country)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                site.slug,
                site.name,
                site.parser_key,
                json.dumps(site.parser_settings),
                bool_to_db(site.enabled),
                str(site.profile_mode),
                site.profile_id,
                site.proxy_id,
                site.interval_min_min,
                site.interval_max_min,
                dt(site.next_run_at),
                str(site.export_mode),
                site.max_attempts,
                dt(site.last_run_at),
                site.last_status,
                site.consecutive_failures,
                bool_to_db(site.match_profile_to_proxy_country),
            ),
        )

    async def update(self, site_id: int, **fields: Any) -> None:
        assignments, params = build_update(fields, _COLUMNS)
        await self.db.execute(f"UPDATE sites SET {assignments} WHERE id = ?", (*params, site_id))

    async def delete(self, site_id: int) -> None:
        await self.db.execute("DELETE FROM sites WHERE id = ?", (site_id,))

    async def due(self, now: datetime) -> list[Site]:
        """Enabled sites whose ``next_run_at`` is set and not in the future."""
        rows = await self.db.fetchall(
            "SELECT * FROM sites WHERE enabled = 1 AND next_run_at IS NOT NULL "
            "AND next_run_at <= ? ORDER BY next_run_at",
            (to_db(now),),
        )
        return [_row_to_site(r) for r in rows]
