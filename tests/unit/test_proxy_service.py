"""Tests for the proxy service: parsing, encryption and health checks."""
import asyncio

import pytest

from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.proxies import ProxiesRepo
from uwp.i18n import Lang
from uwp.services.proxies import ProxyCheck, ProxyService, parse_proxy_url


@pytest.fixture
async def env(tmp_path):
    db = await Database.in_memory()
    await db.migrate()
    yield db
    await db.close()


def test_parse_full_url():
    proxy = parse_proxy_url("http://user:p%40ss@1.2.3.4:8080")

    assert proxy.scheme == "http"
    assert proxy.host == "1.2.3.4"
    assert proxy.port == 8080
    assert proxy.username == "user"
    assert proxy.password == "p@ss"


@pytest.mark.parametrize(
    "bad",
    ["1.2.3.4:8080", "ftp://1.2.3.4:21", "http://1.2.3.4", "http://:8080", "http://host:abc"],
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_proxy_url(bad)


async def test_add_encrypts_password_when_secret_set(env, tmp_path):
    svc = ProxyService(
        ProxiesRepo(env),
        load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_SECRET_KEY": "k" * 32}),
    )

    proxy = await svc.add("DE-1", "http://user:pass@1.2.3.4:8080")

    assert proxy.name == "DE-1"
    assert proxy.password != "pass"
    assert await svc.reveal_password(proxy.id) == "pass"
    assert (await svc.list())[0].username == "user"


async def test_add_plaintext_without_secret(env, tmp_path):
    svc = ProxyService(
        ProxiesRepo(env), load_settings({"UWP_DATA_DIR": str(tmp_path)})
    )

    proxy = await svc.add("SOCKS", "socks5://1.2.3.4:1080")

    assert proxy.password is None
    assert proxy.scheme == "socks5"
    assert await svc.reveal_password(proxy.id) is None


async def test_delete(env, tmp_path):
    svc = ProxyService(ProxiesRepo(env), load_settings({"UWP_DATA_DIR": str(tmp_path)}))
    proxy = await svc.add("P", "http://1.2.3.4:8080")

    await svc.delete(proxy.id)

    assert await svc.list() == []


async def test_test_reports_latency_for_an_open_port(env, tmp_path):
    async def close_immediately(reader, writer):
        # Python 3.12 counts accepted connections: `wait_closed()` below blocks
        # until each one is gone, so a handler that ignores its socket hangs the
        # test forever. 3.14 changed this and hid the bug locally — the CI job
        # for 3.12 is what found it.
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(close_immediately, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    svc = ProxyService(ProxiesRepo(env), load_settings({"UWP_DATA_DIR": str(tmp_path)}))
    proxy = await svc.add("LOCAL", f"http://127.0.0.1:{port}")

    check = await svc.test(proxy.id)

    server.close()
    await server.wait_closed()
    assert check == ProxyCheck(ok=True, reason="ok", ms=check.ms)
    assert check.ms is not None and check.ms >= 0


async def test_test_reports_connection_refused(env, tmp_path):
    svc = ProxyService(ProxiesRepo(env), load_settings({"UWP_DATA_DIR": str(tmp_path)}))
    proxy = await svc.add("DEAD", "http://127.0.0.1:9")

    check = await svc.test(proxy.id)

    assert check.ok is False
    assert check.reason == "connection_error"
    assert check.detail


async def test_test_reports_a_missing_proxy_without_prose(env, tmp_path):
    """The bot and the CLI render the reason; the service returns the reason."""
    svc = ProxyService(ProxiesRepo(env), load_settings({"UWP_DATA_DIR": str(tmp_path)}))

    check = await svc.test(404)

    assert check == ProxyCheck(ok=False, reason="not_found")


async def test_test_reports_a_timeout(env, tmp_path, monkeypatch):
    svc = ProxyService(ProxiesRepo(env), load_settings({"UWP_DATA_DIR": str(tmp_path)}))
    proxy = await svc.add("SLOW", "http://127.0.0.1:1080")

    async def never_connects(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(asyncio, "open_connection", never_connects)

    check = await svc.test(proxy.id)

    assert check.ok is False and check.reason == "timeout"
    assert check.detail == "10"


async def test_the_rejected_url_follows_the_deployment_language(env, tmp_path):
    """``uwp proxy add`` prints the ``ValueError`` straight to the terminal."""
    svc = ProxyService(
        ProxiesRepo(env),
        load_settings({"UWP_DATA_DIR": str(tmp_path), "UWP_DEFAULT_LANGUAGE": "en"}),
    )

    with pytest.raises(ValueError, match="unsupported proxy scheme"):
        await svc.add("BAD", "ftp://1.2.3.4:21")


def test_the_check_renders_in_both_languages():
    ok = ProxyCheck(ok=True, reason="ok", ms=42)
    refused = ProxyCheck(ok=False, reason="connection_error", detail="refused")

    assert ok.text(Lang.ru) == "доступен, 42 мс"
    assert ok.text(Lang.en) == "reachable, 42 ms"
    assert refused.text(Lang.en) == "connection error: refused"
    assert ProxyCheck(ok=False, reason="not_found").text(Lang.en) == "proxy not found"
    timed_out = ProxyCheck(ok=False, reason="timeout", detail="10")

    assert timed_out.text(Lang.ru) == "таймаут 10 с"


def test_an_unknown_check_reason_raises_instead_of_rendering_none():
    """A new ``CheckReason`` must be translated, not fall into a catch-all.

    The last branch used to be "anything else is a connection error", so a
    future reason rendered "connection error: None" and looked like a real
    failure.
    """
    check = ProxyCheck(ok=False, reason="tls_error")

    with pytest.raises(ValueError, match="tls_error"):
        check.text(Lang.en)
