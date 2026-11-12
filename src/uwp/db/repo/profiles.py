"""Repository for browser profile rows, including leasing."""
from datetime import datetime
from typing import Any

import aiosqlite

from uwp.db.database import Database
from uwp.db.repo._common import build_update, dt, parse_dt
from uwp.domain import Profile, ProfileKind, ProfileStatus
from uwp.timeutil import to_db

_COLUMNS: dict[str, tuple[str, Any]] = {
    "name": ("name", str),
    "kind": ("kind", str),
    "seed": ("seed", int),
    "platform": ("platform", str),
    "brand": ("brand", str),
    "brand_version": ("brand_version", str),
    "timezone": ("timezone", str),
    "locale": ("locale", str),
    "user_data_dir": ("user_data_dir", str),
    "proxy_id": ("proxy_id", lambda v: v),
    "status": ("status", str),
    "lease_until": ("lease_until", dt),
    "last_used_at": ("last_used_at", dt),
    "expires_at": ("expires_at", dt),
}


def _row_to_profile(row: aiosqlite.Row) -> Profile:
    return Profile(
        id=row["id"],
        name=row["name"],
        kind=ProfileKind(row["kind"]),
        seed=row["seed"],
        platform=row["platform"],
        brand=row["brand"],
        brand_version=row["brand_version"],
        timezone=row["timezone"],
        locale=row["locale"],
        user_data_dir=row["user_data_dir"],
        proxy_id=row["proxy_id"],
        status=ProfileStatus(row["status"]),
        lease_until=parse_dt(row["lease_until"]),
        last_used_at=parse_dt(row["last_used_at"]),
        expires_at=parse_dt(row["expires_at"]),
    )


class ProfilesRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, profile_id: int) -> Profile | None:
        row = await self.db.fetchone("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        return None if row is None else _row_to_profile(row)

    async def list(self) -> list[Profile]:
        rows = await self.db.fetchall("SELECT * FROM profiles ORDER BY id")
        return [_row_to_profile(r) for r in rows]

    async def create(self, profile: Profile) -> int:
        return await self.db.execute(
            """INSERT INTO profiles (name, kind, seed, platform, brand, brand_version,
                                     timezone, locale, user_data_dir, proxy_id, status,
                                     lease_until, last_used_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile.name,
                str(profile.kind),
                profile.seed,
                profile.platform,
                profile.brand,
                profile.brand_version,
                profile.timezone,
                profile.locale,
                profile.user_data_dir,
                profile.proxy_id,
                str(profile.status),
                dt(profile.lease_until),
                dt(profile.last_used_at),
                dt(profile.expires_at),
            ),
        )

    async def update(self, profile_id: int, **fields: Any) -> None:
        assignments, params = build_update(fields, _COLUMNS)
        await self.db.execute(
            f"UPDATE profiles SET {assignments} WHERE id = ?", (*params, profile_id)
        )

    async def delete(self, profile_id: int) -> None:
        await self.db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    async def lease(self, profile_id: int, until: datetime) -> bool:
        """Lock a free profile until ``until``; ``False`` if it was not free."""
        cursor = await self.db.conn.execute(
            "UPDATE profiles SET status = ?, lease_until = ? WHERE id = ? AND status = ?",
            (ProfileStatus.locked.value, to_db(until), profile_id, ProfileStatus.free.value),
        )
        try:
            return cursor.rowcount == 1
        finally:
            await cursor.close()

    async def release(self, profile_id: int) -> None:
        await self.db.execute(
            "UPDATE profiles SET status = ?, lease_until = NULL WHERE id = ?",
            (ProfileStatus.free.value, profile_id),
        )

    async def expired_clean(self, now: datetime) -> None:
        """Mark clean profiles whose ``expires_at`` has passed as expired."""
        await self.db.execute(
            "UPDATE profiles SET status = ? WHERE kind = ? AND status != ? "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (ProfileStatus.expired.value, ProfileKind.clean.value,
             ProfileStatus.expired.value, to_db(now)),
        )
