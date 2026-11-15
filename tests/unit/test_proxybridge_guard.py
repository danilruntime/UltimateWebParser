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
    ],
)
def test_internal_connect_targets_are_refused(target):
    with pytest.raises(ProxyError, match="internal"):
        _parse_target(target)


@pytest.mark.parametrize(
    "target",
    ["example.com:443", "8.8.8.8:53", "[2001:4860:4860::8888]:443"],
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
