"""Proxy management: parse URLs, store credentials, check reachability."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
from urllib.parse import unquote, urlsplit

from cryptography.fernet import Fernet, InvalidToken

from uwp.config import Settings
from uwp.db.repo.proxies import ProxiesRepo
from uwp.domain import Proxy, ProxyScheme

log = logging.getLogger(__name__)

TEST_TIMEOUT_SECONDS = 10
_FERNET_PREFIX = "gAAAAA"


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


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


class ProxyService:
    """CRUD and diagnostics for proxies, with optional password encryption."""

    def __init__(self, repo: ProxiesRepo, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    async def add(self, name: str, url: str) -> Proxy:
        """Store a proxy from a URL, encrypting the password when a key is set."""
        proxy = parse_proxy_url(url)
        proxy.name = name.strip() or proxy.host
        if proxy.password and self.settings.secret_key:
            proxy.password = _fernet(self.settings.secret_key).encrypt(
                proxy.password.encode()
            ).decode()
        proxy.id = await self.repo.create(proxy)
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
        if not value.startswith(_FERNET_PREFIX):
            return value
        if not self.settings.secret_key:
            raise ValueError("пароль зашифрован, но UWP_SECRET_KEY не задан")
        try:
            return _fernet(self.settings.secret_key).decrypt(value.encode()).decode()
        except InvalidToken:
            log.warning("cannot decrypt a proxy password: wrong UWP_SECRET_KEY")
            raise ValueError("не удалось расшифровать пароль: проверьте UWP_SECRET_KEY") from None
