"""Proxy management: parse URLs, store credentials, check reachability."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from urllib.parse import unquote, urlsplit

from uwp import secretbox
from uwp.config import Settings
from uwp.db.repo.proxies import ProxiesRepo
from uwp.domain import Proxy, ProxyScheme

log = logging.getLogger(__name__)

TEST_TIMEOUT_SECONDS = 10


def parse_proxy_url(url: str) -> Proxy:
    """Parse ``scheme://[user:pass@]host:port`` into a :class:`Proxy`."""
    parts = urlsplit((url or "").strip())
    try:
        scheme = ProxyScheme(parts.scheme.lower())
    except ValueError:
        raise ValueError(f"неподдерживаемая схема прокси: {parts.scheme or '—'}") from None
    if not parts.hostname:
        raise ValueError("в адресе прокси нет хоста")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("некорректный порт прокси") from None
    if port is None:
        raise ValueError("в адресе прокси нет порта")
    return Proxy(
        name=parts.hostname,
        scheme=scheme,
        host=parts.hostname,
        port=port,
        username=unquote(parts.username) if parts.username is not None else None,
        password=unquote(parts.password) if parts.password is not None else None,
    )


class ProxyService:
    """CRUD and diagnostics for proxies, with optional password encryption."""

    def __init__(self, repo: ProxiesRepo, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    async def resolved(self, proxy_id: int) -> Proxy | None:
        """The proxy ready for use: password decrypted, or ``None`` if gone."""
        proxy = await self.repo.get(proxy_id)
        if proxy is None or proxy.password is None:
            return proxy
        return proxy.model_copy(
            update={"password": secretbox.decrypt(proxy.password, self.settings.secret_key)}
        )

    async def add(self, name: str, url: str) -> Proxy:
        """Store a proxy from a URL, encrypting the password when a key is set."""
        proxy = parse_proxy_url(url)
        proxy.name = name.strip() or proxy.host
        if proxy.password and self.settings.secret_key:
            proxy.password = secretbox.encrypt(proxy.password, self.settings.secret_key)
        try:
            proxy.id = await self.repo.create(proxy)
        except sqlite3.IntegrityError:
            # ``proxies.name`` is UNIQUE; an admin reusing a name deserves a
            # sentence, not a traceback in the log.
            raise ValueError(f"прокси с именем «{proxy.name}» уже есть") from None
        return proxy

    async def list(self) -> list[Proxy]:
        return await self.repo.list()

    async def get(self, proxy_id: int) -> Proxy | None:
        return await self.repo.get(proxy_id)

    async def delete(self, proxy_id: int) -> None:
        await self.repo.delete(proxy_id)

    async def reveal_password(self, proxy_id: int) -> str | None:
        """Return the plain password of a stored proxy (decrypting if needed)."""
        proxy = await self.repo.get(proxy_id)
        if proxy is None or proxy.password is None:
            return None
        return self._decrypt(proxy.password)

    async def test(self, proxy_id: int) -> tuple[bool, str]:
        """Open a TCP connection to the proxy and report latency or the error."""
        proxy = await self.repo.get(proxy_id)
        if proxy is None:
            return False, "прокси не найден"
        started = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(proxy.host, proxy.port),
                timeout=TEST_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return False, f"таймаут {TEST_TIMEOUT_SECONDS} с"
        except OSError as exc:
            return False, f"ошибка соединения: {exc.strerror or exc}"
        else:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            del reader
            return True, f"доступен, {int((time.monotonic() - started) * 1000)} мс"

    def _decrypt(self, value: str) -> str:
        return secretbox.decrypt(value, self.settings.secret_key)
