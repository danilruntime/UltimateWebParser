"""Proxy management: parse URLs, store credentials, check reachability.

The reachability check returns a :class:`ProxyCheck`, not a sentence: the CLI
and the bot's "Check" button both show it, and each renders it in the language
of its own reader. A Russian string built here used to reach an English chat
verbatim (the handler ignored its ``lang``).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urlsplit

from uwp import secretbox
from uwp.config import Settings
from uwp.db.repo.proxies import ProxiesRepo
from uwp.domain import Proxy, ProxyScheme
from uwp.i18n import Lang, t

log = logging.getLogger(__name__)

TEST_TIMEOUT_SECONDS = 10

CheckReason = Literal["ok", "not_found", "timeout", "connection_error"]

# Every reason has a sentence; the render function below exhausts this set.
_CHECK_KEYS = {
    "ok": "check.proxy.reachable",
    "not_found": "error.proxy.not_found",
    "timeout": "error.proxy.timeout",
    "connection_error": "error.proxy.connection",
}


@dataclass(frozen=True)
class ProxyCheck:
    """The outcome of opening a TCP connection to a stored proxy."""

    ok: bool
    reason: CheckReason
    # Free-form text for a human: the OS error, or the timeout in seconds.
    # ``None`` for ``ok`` and ``not_found``: their sentence needs no extra data.
    detail: str | None = None
    ms: int | None = None

    def text(self, lang: Lang | str) -> str:
        """The one-line verdict for a chat or a terminal, in ``lang``.

        A new reason without a key raises here, as ``Column.title_for`` does: a
        catch-all would render "connection error: None" and look like a real
        failure.
        """
        try:
            key = _CHECK_KEYS[self.reason]
        except KeyError:
            raise ValueError(
                f"no sentence for a proxy check reason: {self.reason}"
            ) from None
        if self.reason == "ok":
            return t(lang, key, ms=self.ms)
        if self.reason == "timeout":
            return t(lang, key, seconds=self.detail)
        if self.reason == "connection_error":
            return t(lang, key, detail=self.detail)
        return t(lang, key)


def parse_proxy_url(url: str, lang: Lang | str = Lang.ru) -> Proxy:
    """Parse ``scheme://[user:pass@]host:port`` into a :class:`Proxy`."""
    parts = urlsplit((url or "").strip())
    try:
        scheme = ProxyScheme(parts.scheme.lower())
    except ValueError:
        raise ValueError(
            t(lang, "error.proxy.bad_scheme", scheme=parts.scheme or "—")
        ) from None
    if not parts.hostname:
        raise ValueError(t(lang, "error.proxy.no_host"))
    try:
        port = parts.port
    except ValueError:
        raise ValueError(t(lang, "error.proxy.bad_port")) from None
    if port is None:
        raise ValueError(t(lang, "error.proxy.no_port"))
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

    @property
    def lang(self) -> Lang:
        """The deployment language for the service's own ``ValueError`` text."""
        return Lang(self.settings.default_language)

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
        proxy = parse_proxy_url(url, self.lang)
        proxy.name = name.strip() or proxy.host
        if proxy.password and self.settings.secret_key:
            proxy.password = secretbox.encrypt(proxy.password, self.settings.secret_key)
        try:
            proxy.id = await self.repo.create(proxy)
        except sqlite3.IntegrityError:
            # ``proxies.name`` is UNIQUE; an admin reusing a name deserves a
            # sentence, not a traceback in the log.
            raise ValueError(
                t(self.lang, "error.proxy.duplicate", name=proxy.name)
            ) from None
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

    async def test(self, proxy_id: int) -> ProxyCheck:
        """Open a TCP connection to the proxy and report latency or the error."""
        proxy = await self.repo.get(proxy_id)
        if proxy is None:
            return ProxyCheck(ok=False, reason="not_found")
        started = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(proxy.host, proxy.port),
                timeout=TEST_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return ProxyCheck(
                ok=False, reason="timeout", detail=str(TEST_TIMEOUT_SECONDS)
            )
        except OSError as exc:
            return ProxyCheck(
                ok=False,
                reason="connection_error",
                detail=exc.strerror or str(exc),
            )
        else:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            del reader
            elapsed = int((time.monotonic() - started) * 1000)
            return ProxyCheck(ok=True, reason="ok", ms=elapsed)

    def _decrypt(self, value: str) -> str:
        return secretbox.decrypt(value, self.settings.secret_key)
