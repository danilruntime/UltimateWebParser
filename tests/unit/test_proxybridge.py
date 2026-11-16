"""Unit tests for the local proxy bridge and its upstream handshakes."""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import pytest

from uwp.domain import Proxy, ProxyScheme
from uwp.proxybridge import ProxyBridge, ProxyError, _socks5_address, parse_proxy_url


def _proxy(**kwargs) -> Proxy:
    base = {"name": "p", "scheme": ProxyScheme.http, "host": "127.0.0.1", "port": 1}
    return Proxy(**{**base, **kwargs})


class _RecordingWriter:
    """Just enough of a StreamWriter for the error paths."""

    def __init__(self):
        self.closed = False

    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        self.closed = True


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


async def test_closing_one_side_of_the_tunnel_ends_the_other():
    """A half-closed tunnel must not keep a task alive for the whole run."""
    release = asyncio.Event()

    async def upstream(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await reader.read()  # the client goes away first
            # The remote server has nothing more to send, but it keeps its
            # write side open: a half-closed tunnel, not a closed one.
            await release.wait()
        finally:
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
        w.close()
        await w.wait_closed()

        # The bridge's client->upstream pipe ends; the upstream->client pipe
        # must be cancelled rather than left waiting for an idle upstream.
        for _ in range(30):  # ~3 s ceiling, no fixed sleep when it finishes early
            if not bridge._tasks:
                break
            await asyncio.sleep(0.1)
        assert len(bridge._tasks) == 0
    finally:
        release.set()
        await bridge.stop()
        server.close()
        await server.wait_closed()


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
    async def close_immediately(reader, writer):
        # 3.12's `wait_closed()` waits for accepted connections; 3.14 does not.
        writer.close()
        await writer.wait_closed()

    probe = await asyncio.start_server(close_immediately, "127.0.0.1", 0)
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


async def test_a_silent_upstream_times_out_with_a_proxy_error(monkeypatch, caplog):
    """A proxy that accepts and then says nothing must not burn the run timeout."""
    monkeypatch.setattr("uwp.proxybridge.PROXY_CONNECT_TIMEOUT_SECONDS", 0.2)

    silent = asyncio.Event()

    async def upstream(reader, writer):
        try:
            await silent.wait()  # accepts the TCP connection, then says nothing
        finally:
            writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(_proxy(port=up_port))
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        # The bridge must give up and close the client side well before this read
        # would naturally end; a hang shows up here as a TimeoutError.
        assert await asyncio.wait_for(r.read(), 3) == b""
        assert "timed out" in caplog.text
        w.close()
        await w.wait_closed()
    finally:
        silent.set()
        await bridge.stop()
        server.close()
        await server.wait_closed()


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


@pytest.mark.parametrize(
    "target",
    ["example.com:99999", "example.com:0", f"{'a' * 70}.com:443", ":443"],
)
async def test_hostile_connect_target_is_reported_not_crashed(target, caplog):
    """Untrusted page data must not raise outside ``_handle``'s catch list."""
    bridge = ProxyBridge(_proxy(scheme=ProxyScheme.socks5, port=1))
    reader = asyncio.StreamReader()
    reader.feed_data(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
    reader.feed_eof()
    writer = _RecordingWriter()

    await bridge._handle(reader, writer)  # must not raise

    assert writer.closed
    assert "failed" in caplog.text.lower()


async def test_an_oversized_request_head_gets_its_own_message(caplog):
    """``readuntil`` trips its 64 KiB default before the explicit check could."""
    bridge = ProxyBridge(_proxy())
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"CONNECT example.com:443 HTTP/1.1\r\nX: " + b"a" * (70 * 1024) + b"\r\n\r\n"
    )
    reader.feed_eof()
    writer = _RecordingWriter()

    with caplog.at_level(logging.WARNING):
        await bridge._handle(reader, writer)

    assert writer.closed
    assert "too large" in caplog.text


def _self_signed(tmp_path) -> tuple[Path, Path]:
    """A throwaway cert for TLS tests; ``cryptography`` is already a dependency."""
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    key_path = tmp_path / "key.pem"
    cert_path = tmp_path / "cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


async def test_https_upstream_speaks_tls(tmp_path):
    """An ``https://`` proxy must not receive credentials in clear text."""
    import ssl

    cert_path, key_path = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)
    client_ctx = ssl.create_default_context(cafile=str(cert_path))
    seen = {}

    async def upstream(reader, writer):
        seen["tls"] = writer.get_extra_info("ssl_object") is not None
        seen["head"] = (await reader.readuntil(b"\r\n\r\n")).decode()
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0, ssl=server_ctx)
    up_port = server.sockets[0].getsockname()[1]
    bridge = ProxyBridge(
        _proxy(
            scheme=ProxyScheme.https,
            host="localhost",
            port=up_port,
            username="user",
            password="secret",
        ),
        ssl_context=client_ctx,
    )
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert b"200" in await r.readuntil(b"\r\n\r\n")
        w.close()
        assert seen["tls"] is True
        assert "Proxy-Authorization: Basic dXNlcjpzZWNyZXQ=" in seen["head"]
    finally:
        await bridge.stop()
        server.close()


async def test_the_production_construction_verifies_an_https_proxy(tmp_path, monkeypatch):
    """``runner.py``/``vnc.py`` build the bridge with no ``ssl_context``.

    The test above injects one, so it cannot catch a removed default context.
    Here the certificate is trusted through ``SSL_CERT_FILE``, exactly the way
    an operator would trust a private proxy CA, and the bridge is built the way
    production builds it.
    """
    import ssl

    cert_path, key_path = _self_signed(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)
    seen = {}

    async def upstream(reader, writer):
        seen["tls"] = writer.get_extra_info("ssl_object") is not None
        seen["head"] = (await reader.readuntil(b"\r\n\r\n")).decode()
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0, ssl=server_ctx)
    up_port = server.sockets[0].getsockname()[1]
    # No ``ssl_context=``: this is the production path.
    bridge = ProxyBridge(
        _proxy(
            scheme=ProxyScheme.https,
            host="localhost",
            port=up_port,
            username="user",
            password="secret",
        )
    )
    await bridge.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", bridge.port)
        w.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        assert b"200" in await r.readuntil(b"\r\n\r\n")
        w.close()
        assert seen["tls"] is True
        assert "Proxy-Authorization: Basic dXNlcjpzZWNyZXQ=" in seen["head"]
    finally:
        await bridge.stop()
        server.close()


@pytest.mark.parametrize(
    "host,expected_prefix",
    [("1.2.3.4", b"\x01\x01\x02\x03\x04"), ("::1", b"\x04" + b"\x00" * 15 + b"\x01")],
)
def test_socks5_address_packs_ip_literals(host, expected_prefix):
    assert _socks5_address(host, 443).startswith(expected_prefix)


def test_socks5_address_uses_a_domain_name_for_hosts():
    assert _socks5_address("example.com", 443) == b"\x03\x0bexample.com\x01\xbb"


def test_socks5_address_rejects_an_impossible_host():
    with pytest.raises(ProxyError):
        _socks5_address("a" * 70 + ".com", 443)


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
