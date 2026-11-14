"""Unit tests for the local proxy bridge and its upstream handshakes."""

from __future__ import annotations

import asyncio
import base64
import logging

import pytest

from uwp.domain import Proxy, ProxyScheme
from uwp.proxybridge import ProxyBridge, ProxyError, parse_proxy_url


def _proxy(**kwargs) -> Proxy:
    base = {"name": "p", "scheme": ProxyScheme.http, "host": "127.0.0.1", "port": 1}
    return Proxy(**{**base, **kwargs})


def test_parse_proxy_url_with_auth():
    p = parse_proxy_url("http://u:p%40ss@1.2.3.4:8080")
    assert p.scheme == ProxyScheme.http and p.host == "1.2.3.4" and p.port == 8080
    assert p.username == "u" and p.password == "p@ss"


def test_parse_socks5():
    p = parse_proxy_url("socks5://1.2.3.4:1080")
    assert p.scheme == ProxyScheme.socks5 and p.username is None


def test_parse_https_without_credentials():
    p = parse_proxy_url("https://proxy.example:8443")
    assert (
        p.scheme == ProxyScheme.https and p.host == "proxy.example" and p.port == 8443
    )
    assert p.username is None and p.password is None


@pytest.mark.parametrize(
    "url",
    [
        "ftp://1.2.3.4:21",
        "http://1.2.3.4",
        "http://:8080",
        "not a url",
        "http://1.2.3.4:notaport",
    ],
)
def test_parse_proxy_url_rejects_malformed(url):
    with pytest.raises(ValueError):
        parse_proxy_url(url)


async def test_connect_forwards_proxy_authorization():
    seen = {}

    async def upstream(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        seen["head"] = head.decode()
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(
        Proxy(
            name="up",
            scheme=ProxyScheme.http,
            host="127.0.0.1",
            port=up_port,
            username="user",
            password="secret",
        )
    )
    url = await bridge.start()
    try:
        port = int(url.rsplit(":", 1)[1])
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        await w.drain()
        resp = await r.readuntil(b"\r\n\r\n")
        assert b"200" in resp
        assert "Proxy-Authorization: Basic dXNlcjpzZWNyZXQ=" in seen["head"]
        w.close()
    finally:
        await bridge.stop()
        server.close()


async def test_start_returns_loopback_url_and_port():
    bridge = ProxyBridge(_proxy())
    url = await bridge.start()
    try:
        assert url == f"http://127.0.0.1:{bridge.port}"
        assert bridge.port > 0
    finally:
        await bridge.stop()


async def test_connect_pipes_bytes_both_ways():
    async def upstream(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        data = await reader.readexactly(5)
        writer.write(data.upper())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(port=up_port))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        await r.readuntil(b"\r\n\r\n")
        w.write(b"hello")
        await w.drain()
        assert await asyncio.wait_for(r.readexactly(5), 5) == b"HELLO"
        w.close()
    finally:
        await bridge.stop()
        server.close()


async def test_connect_without_credentials_sends_no_authorization():
    seen = {}

    async def upstream(reader, writer):
        seen["head"] = (await reader.readuntil(b"\r\n\r\n")).decode()
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(port=up_port))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert b"200" in await r.readuntil(b"\r\n\r\n")
        assert "Proxy-Authorization" not in seen["head"]
        w.close()
    finally:
        await bridge.stop()
        server.close()


async def test_upstream_rejection_closes_client_and_logs_without_password(caplog):
    async def upstream(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(port=up_port, username="user", password="s3cr3t"))
    await bridge.start()
    try:
        with caplog.at_level(logging.WARNING, logger="uwp.proxybridge"):
            r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
            w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
            await w.drain()
            assert await asyncio.wait_for(r.read(), 5) == b""
            w.close()
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)
        assert "s3cr3t" not in caplog.text
    finally:
        await bridge.stop()
        server.close()


async def test_unreachable_upstream_closes_client():
    # Grab a free port and close it so nothing listens there.
    probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    dead_port = probe.sockets[0].getsockname()[1]
    probe.close()
    await probe.wait_closed()
    bridge = ProxyBridge(_proxy(port=dead_port))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert await asyncio.wait_for(r.read(), 5) == b""
        w.close()
    finally:
        await bridge.stop()


async def test_plain_http_is_forwarded_as_absolute_uri():
    seen = {}

    async def upstream(reader, writer):
        seen["head"] = (await reader.readuntil(b"\r\n\r\n")).decode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(port=up_port, username="user", password="secret"))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"GET http://example.com/page HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await w.drain()
        resp = await asyncio.wait_for(r.read(), 5)
        w.close()
        assert b"200 OK" in resp and resp.endswith(b"ok")
        assert seen["head"].startswith("GET http://example.com/page HTTP/1.1")
        assert "Proxy-Authorization: Basic dXNlcjpzZWNyZXQ=" in seen["head"]
    finally:
        await bridge.stop()
        server.close()


async def test_socks5_connect_with_username_password():
    seen = {}

    async def upstream(reader, writer):
        greeting = await reader.readexactly(2)
        methods = await reader.readexactly(greeting[1])
        seen["methods"] = methods
        writer.write(b"\x05\x02")
        await writer.drain()
        auth = await reader.readexactly(2)
        user = await reader.readexactly(auth[1])
        plen = await reader.readexactly(1)
        password = await reader.readexactly(plen[0])
        seen["auth"] = (user, password)
        writer.write(b"\x01\x00")
        await writer.drain()
        head = await reader.readexactly(4)
        # head is VER CMD RSV ATYP; the address follows it. Reading a fifth byte
        # here would swallow the length of a domain name.
        atyp = head[3:4]
        if atyp == b"\x03":
            n = await reader.readexactly(1)
            host = await reader.readexactly(n[0])
        elif atyp == b"\x01":
            host = await reader.readexactly(4)
        else:
            host = await reader.readexactly(16)
        port = await reader.readexactly(2)
        seen["target"] = (head[3], host, int.from_bytes(port, "big"))
        # VER REP RSV ATYP=IPv4 + 4-byte address + 2-byte port (10 bytes total).
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        # Echo whatever the tunnel carries; the client payload only arrives after
        # the bridge has forwarded the SOCKS5 reply, so never read a fixed size here.
        while chunk := await reader.read(1024):
            writer.write(chunk[::-1])
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(
        _proxy(
            scheme=ProxyScheme.socks5, port=up_port, username="user", password="secret"
        )
    )
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert b"200" in await r.readuntil(b"\r\n\r\n")
        w.write(b"abcd")
        await w.drain()
        assert await asyncio.wait_for(r.readexactly(4), 5) == b"dcba"
        w.close()
        assert seen["methods"] == b"\x00\x02"
        assert seen["auth"] == (b"user", b"secret")
        assert seen["target"] == (3, b"example.com", 443)  # ATYP 3 = domain name
    finally:
        await bridge.stop()
        server.close()


async def test_socks5_connect_without_credentials():
    seen = {}

    async def upstream(reader, writer):
        greeting = await reader.readexactly(2)
        seen["methods"] = await reader.readexactly(greeting[1])
        writer.write(b"\x05\x00")
        await writer.drain()
        # VER CMD RSV ATYP arrive together; the address follows the fourth byte.
        head = await reader.readexactly(4)
        atyp = head[3:4]
        if atyp == b"\x03":
            n = await reader.readexactly(1)
            seen["host"] = await reader.readexactly(n[0])
        elif atyp == b"\x01":
            seen["host"] = await reader.readexactly(4)
        else:
            seen["host"] = await reader.readexactly(16)
        await reader.readexactly(2)
        seen["atyp"] = atyp
        # VER REP RSV ATYP=IPv4 + 4-byte address + 2-byte port (10 bytes total).
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        # Echo whatever the tunnel carries; the client payload only arrives after
        # the bridge has forwarded the SOCKS5 reply, so never read a fixed size here.
        while chunk := await reader.read(1024):
            writer.write(chunk[::-1])
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(scheme=ProxyScheme.socks5, port=up_port))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert b"200" in await r.readuntil(b"\r\n\r\n")
        w.write(b"abcd")
        await w.drain()
        assert await asyncio.wait_for(r.readexactly(4), 5) == b"dcba"
        w.close()
        assert seen["methods"] == b"\x00"
        assert seen["atyp"] == b"\x03" and seen["host"] == b"example.com"
    finally:
        await bridge.stop()
        server.close()


async def test_socks5_rejection_raises_proxy_error():
    async def upstream(reader, writer):
        greeting = await reader.readexactly(2)
        await reader.readexactly(greeting[1])
        writer.write(b"\x05\xff")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(scheme=ProxyScheme.socks5, port=up_port))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert await asyncio.wait_for(r.read(), 5) == b""
        w.close()
    finally:
        await bridge.stop()
        server.close()


async def test_stop_closes_live_connections_and_is_idempotent():
    async def upstream(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await reader.read()  # hold the tunnel open until the bridge closes it
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(port=up_port))
    await bridge.start()
    r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
    w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
    await w.drain()
    await r.readuntil(b"\r\n\r\n")
    await bridge.stop()
    await bridge.stop()
    assert await asyncio.wait_for(r.read(), 5) == b""
    w.close()
    server.close()


def test_proxy_error_is_runtime_error():
    assert issubclass(ProxyError, RuntimeError)


def test_basic_header_encoding():
    # Guard the exact header value used by the tests above.
    assert base64.b64encode(b"user:secret").decode() == "dXNlcjpzZWNyZXQ="
