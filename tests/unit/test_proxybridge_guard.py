"""Unit tests for the loopback proxy bridge's target guard.

The bridge is an unauthenticated HTTP proxy on 127.0.0.1: any process in the
container can use it (Chromium cannot send ``Proxy-Authorization`` for a
``CONNECT`` tunnel, which is why the bridge exists at all). It therefore must
not become a way to reach loopback services, the Docker socket or a cloud
metadata endpoint through the admin's paid proxy.
"""

from __future__ import annotations

import asyncio

import pytest

from uwp.domain import Proxy, ProxyScheme
from uwp.proxybridge import ProxyBridge, ProxyError, _parse_target


def _proxy() -> Proxy:
    return Proxy(name="p", scheme=ProxyScheme.http, host="127.0.0.1", port=1)


class _RecordingWriter:
    def __init__(self):
        self.closed = False
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "target",
    [
        "127.0.0.1:80",
        "127.9.9.9:8080",
        "10.0.0.5:443",
        "192.168.1.1:443",
        "172.16.0.1:443",
        "169.254.169.254:80",
        "0.0.0.0:80",
        "[::1]:443",
        "[fd00::1]:443",
        "localhost:8080",
        "metadata.google.internal:80",
        "docker.internal:2375",
        # glibc's inet_aton accepts all four; Chromium resolves them later, so
        # the guard has to canonicalise before it decides.
        "127.1:80",
        "0x7f000001:80",
        "2130706433:80",
        "0177.0.0.1:80",
        "0:80",
        "[::ffff:127.0.0.1]:80",
        # ``is_global`` alone is not enough: these are routable-looking
        # literals that `ipaddress` reports as global (`::`-compatible and
        # NAT64) or that are simply not unicast (multicast), and the upstream
        # proxy is the one that would resolve them.
        "[::127.0.0.1]:80",
        "[::7f00:1]:80",
        "[64:ff9b::7f00:1]:80",
        "[64:ff9b::a9fe:a9fe]:80",
        "224.0.0.1:80",
        "239.255.255.250:1900",
        "[ff02::1]:80",
    ],
)
def test_internal_connect_targets_are_refused(target):
    with pytest.raises(ProxyError, match="internal"):
        _parse_target(target)


@pytest.mark.parametrize(
    "target",
    [
        "example.com:443",
        "8.8.8.8:53",
        "[2001:4860:4860::8888]:443",
        # A NAT64 literal that carries a public IPv4 address stays allowed;
        # only the one that encodes a special-purpose address is refused.
        "[64:ff9b::808:808]:443",
        "[::ffff:8.8.8.8]:443",
    ],
)
def test_public_connect_targets_are_allowed(target):
    assert _parse_target(target)


async def test_an_internal_connect_never_reaches_the_upstream():
    """The guard runs before dialling, so no request leaves the container."""
    bridge = ProxyBridge(_proxy())
    reader = asyncio.StreamReader()
    reader.feed_data(b"CONNECT 169.254.169.254:80 HTTP/1.1\r\n\r\n")
    reader.feed_eof()
    writer = _RecordingWriter()

    await bridge._handle(reader, writer)

    assert b"200" not in writer.data
    assert writer.closed


async def test_an_internal_plain_request_is_refused_too():
    bridge = ProxyBridge(_proxy())
    reader = asyncio.StreamReader()
    reader.feed_data(b"GET http://127.0.0.1:2375/containers/json HTTP/1.1\r\n\r\n")
    reader.feed_eof()
    writer = _RecordingWriter()

    await bridge._handle(reader, writer)

    assert b"200" not in writer.data
    assert writer.closed


@pytest.mark.parametrize(
    "target",
    [
        # ``urlsplit`` raises ValueError rather than returning a bad host; it
        # escaped `_handle` and became an unhandled exception in the loop.
        "http://[::1/vnc.html",
        "http://exa mple.com/",
    ],
)
async def test_a_malformed_absolute_uri_does_not_escape_the_handler(target):
    bridge = ProxyBridge(_proxy())
    reader = asyncio.StreamReader()
    reader.feed_data(f"GET {target} HTTP/1.1\r\n\r\n".encode())
    reader.feed_eof()
    writer = _RecordingWriter()

    await bridge._handle(reader, writer)

    assert b"200" not in writer.data
    assert writer.closed


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1\x00.example.com",  # NUL byte: inet_aton raises ValueError
        "a" * 300,  # not a literal, not internal
    ],
)
def test_an_unparseable_host_is_not_a_crash(host):
    from uwp.proxybridge import _is_internal_host

    assert _is_internal_host(host) is False
