"""Tests for the proxy service: parsing, encryption and health checks."""
import asyncio

import pytest

from uwp.config import load_settings
from uwp.db.database import Database
from uwp.db.repo.proxies import ProxiesRepo
from uwp.services.proxies import ProxyService, parse_proxy_url


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

    ok, message = await svc.test(proxy.id)

    server.close()
    await server.wait_closed()
    assert ok is True
    assert "мс" in message


async def test_test_reports_connection_refused(env, tmp_path):
    svc = ProxyService(ProxiesRepo(env), load_settings({"UWP_DATA_DIR": str(tmp_path)}))
    proxy = await svc.add("DEAD", "http://127.0.0.1:9")

    ok, message = await svc.test(proxy.id)

    assert ok is False
    assert message
