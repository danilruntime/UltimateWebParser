"""Repository for proxy rows."""
from __future__ import annotations

from typing import Any

import aiosqlite

from uwp.db.database import Database
from uwp.db.repo._common import build_update
from uwp.domain import Proxy, ProxyScheme

_COLUMNS: dict[str, tuple[str, Any]] = {
    "name": ("name", str),
    "scheme": ("scheme", str),
    "host": ("host", str),
    "port": ("port", int),
    "username": ("username", lambda v: v),
    "password": ("password", lambda v: v),
    "country": ("country", lambda v: v),
}


def _row_to_proxy(row: aiosqlite.Row) -> Proxy:
    return Proxy(
        id=row["id"],
        name=row["name"],
        scheme=ProxyScheme(row["scheme"]),
        host=row["host"],
        port=row["port"],
        username=row["username"],
        password=row["password"],
        country=row["country"],
    )


class ProxiesRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, proxy_id: int) -> Proxy | None:
        row = await self.db.fetchone("SELECT * FROM proxies WHERE id = ?", (proxy_id,))
        return None if row is None else _row_to_proxy(row)

    async def list(self) -> list[Proxy]:
        rows = await self.db.fetchall("SELECT * FROM proxies ORDER BY id")
        return [_row_to_proxy(r) for r in rows]

    async def create(self, proxy: Proxy) -> int:
        return await self.db.execute(
            """INSERT INTO proxies (name, scheme, host, port, username, password, country)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                proxy.name,
                str(proxy.scheme),
                proxy.host,
                proxy.port,
                proxy.username,
                proxy.password,
                proxy.country,
            ),
        )

    async def update(self, proxy_id: int, **fields: Any) -> None:
        assignments, params = build_update(fields, _COLUMNS)
        await self.db.execute(
            f"UPDATE proxies SET {assignments} WHERE id = ?", (*params, proxy_id)
        )

    async def delete(self, proxy_id: int) -> None:
        await self.db.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
