"""Local HTTP proxy bridge that authenticates to an upstream http/https/socks5 proxy.

Chromium cannot send ``Proxy-Authorization`` for ``CONNECT`` tunnels, so the browser
points at this loopback proxy and the bridge adds the credentials on the way out.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import ssl
from urllib.parse import unquote, urlsplit

from uwp.domain import Proxy, ProxyScheme

log = logging.getLogger(__name__)

_HEAD_LIMIT = 64 * 1024
_PIPE_CHUNK = 64 * 1024
PROXY_CONNECT_TIMEOUT_SECONDS = 15


class ProxyError(RuntimeError):
    """The upstream proxy refused the connection or the handshake failed."""


def parse_proxy_url(url: str) -> Proxy:
    """Parse ``scheme://[user:pass@]host:port`` into a :class:`Proxy`."""
    parts = urlsplit(url.strip())
    try:
        scheme = ProxyScheme(parts.scheme.lower())
    except ValueError:
        raise ValueError(f"unsupported proxy scheme: {parts.scheme!r}") from None
    if not parts.hostname:
        raise ValueError("proxy URL has no host")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("proxy URL has an invalid port") from None
    if port is None:
        raise ValueError("proxy URL has no port")
    username = unquote(parts.username) if parts.username is not None else None
    password = unquote(parts.password) if parts.password is not None else None
    return Proxy(
        name=parts.hostname,
        scheme=scheme,
        host=parts.hostname,
        port=port,
        username=username,
        password=password,
    )


def _basic_auth(proxy: Proxy) -> str | None:
    if proxy.username is None:
        return None
    token = f"{proxy.username}:{proxy.password or ''}".encode()
    return "Basic " + base64.b64encode(token).decode()


def _describe(proxy: Proxy) -> str:
    """Upstream description for logs; never includes the password."""
    return f"{proxy.scheme}://{proxy.host}:{proxy.port}"


def _parse_target(target: str) -> tuple[str, int]:
    """Split ``host:port`` and reject values that cannot be encoded downstream.

    The target comes from the page Chromium is loading, so it is untrusted: a
    port above 65535 or a 70-character label must become a :class:`ProxyError`,
    not an ``OverflowError`` escaping into the event loop.
    """
    host, _, port_text = target.rpartition(":")
    if not host or not port_text.isdigit():
        raise ProxyError("CONNECT target must be host:port")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ProxyError(f"CONNECT target port out of range: {port}")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]  # IPv6 literal, as sent by Chromium
    return host, port


def _socks5_address(host: str, port: int) -> bytes:
    """Encode a SOCKS5 ``DST.ADDR``/``DST.PORT`` pair.

    IP literals use the packed forms; everything else must survive ``idna``
    (a label longer than 63 bytes does not) and fit the one-byte length field.
    """
    try:
        return b"\x01" + ipaddress.IPv4Address(host).packed + port.to_bytes(2, "big")
    except ipaddress.AddressValueError:
        pass
    try:
        return b"\x04" + ipaddress.IPv6Address(host).packed + port.to_bytes(2, "big")
    except ipaddress.AddressValueError:
        pass
    try:
        name = host.encode("idna")
    except UnicodeError as exc:
        raise ProxyError(f"CONNECT target host is not encodable: {exc}") from exc
    if not 0 < len(name) <= 255:
        raise ProxyError("CONNECT target host is too long")
    return b"\x03" + bytes([len(name)]) + name + port.to_bytes(2, "big")


class ProxyBridge:
    """Loopback HTTP proxy forwarding to ``proxy`` with credentials applied."""

    def __init__(self, proxy: Proxy, ssl_context: ssl.SSLContext | None = None) -> None:
        self._proxy = proxy
        # Only an ``https://`` upstream gets TLS; for http/socks5 it is unused.
        self._ssl_context = ssl_context
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()
        self._port = 0

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> str:
        """Bind ``127.0.0.1:0`` and return the proxy URL for the browser."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self._port = self._server.sockets[0].getsockname()[1]
        log.info(
            "proxy bridge listening on 127.0.0.1:%d -> %s",
            self._port,
            _describe(self._proxy),
        )
        return f"http://127.0.0.1:{self._port}"

    async def stop(self) -> None:
        """Close the listener and every live connection; safe to call twice."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for writer in list(self._connections):
            writer.close()
        for task in list(self._tasks):
            task.cancel()
        if server is not None:
            await server.wait_closed()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._connections.clear()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._connections.add(writer)
        try:
            await self._serve(reader, writer)
        except (
            ProxyError,
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as exc:
            log.warning(
                "proxy bridge upstream %s failed: %s", _describe(self._proxy), exc
            )
        except asyncio.CancelledError:
            raise
        finally:
            self._connections.discard(writer)
            writer.close()
            if task is not None:
                self._tasks.discard(task)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            return
        if len(head) > _HEAD_LIMIT:
            raise ProxyError("request head too large")
        request_line, _, headers = head.partition(b"\r\n")
        parts = request_line.decode("latin-1").split()
        if len(parts) < 3:
            raise ProxyError("malformed request line")
        method, target, version = parts[0], parts[1], parts[2]
        if method.upper() == "CONNECT":
            await self._connect(target, reader, writer)
        else:
            await self._forward_plain(method, target, version, headers, reader, writer)

    async def _open_upstream(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # An ``https://`` proxy is spoken to over TLS. Plain TCP here would send
        # ``Proxy-Authorization`` — i.e. the admin's proxy password — in clear.
        context = self._ssl_context
        if self._proxy.scheme is ProxyScheme.https and context is None:
            context = ssl.create_default_context()
        try:
            async with asyncio.timeout(PROXY_CONNECT_TIMEOUT_SECONDS):
                return await asyncio.open_connection(
                    self._proxy.host,
                    self._proxy.port,
                    ssl=context if self._proxy.scheme is ProxyScheme.https else None,
                    server_hostname=(
                        self._proxy.host
                        if self._proxy.scheme is ProxyScheme.https
                        else None
                    ),
                )
        except TimeoutError as exc:
            raise ProxyError("upstream timed out while connecting") from exc
        except OSError as exc:
            raise ProxyError(f"cannot reach upstream: {exc}") from exc

    async def _connect(
        self, target: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        host, port = _parse_target(target)
        up_reader, up_writer = await self._open_upstream()
        try:
            if self._proxy.scheme is ProxyScheme.socks5:
                await self._socks5_handshake(up_reader, up_writer, host, port)
            else:
                await self._http_connect(up_reader, up_writer, target)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(
                self._pipe(reader, up_writer),
                self._pipe(up_reader, writer),
            )
        finally:
            up_writer.close()

    async def _http_connect(
        self,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
        target: str,
    ) -> None:
        lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
        auth = _basic_auth(self._proxy)
        if auth is not None:
            lines.append(f"Proxy-Authorization: {auth}")
        up_writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        try:
            async with asyncio.timeout(PROXY_CONNECT_TIMEOUT_SECONDS):
                await up_writer.drain()
                response = await up_reader.readuntil(b"\r\n\r\n")
        except TimeoutError as exc:
            raise ProxyError("upstream timed out during CONNECT") from exc
        except asyncio.IncompleteReadError as exc:
            raise ProxyError("upstream closed during CONNECT") from exc
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
        fields = status_line.split()
        if len(fields) < 2 or fields[1] != "200":
            raise ProxyError(f"upstream CONNECT rejected: {status_line}")

    async def _socks5_handshake(
        self,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
        host: str,
        port: int,
    ) -> None:
        auth = _basic_auth(self._proxy)
        try:
            async with asyncio.timeout(PROXY_CONNECT_TIMEOUT_SECONDS):
                methods = b"\x00\x02" if auth is not None else b"\x00"
                up_writer.write(b"\x05" + bytes([len(methods)]) + methods)
                await up_writer.drain()
                version, method = await up_reader.readexactly(2)
                if version != 5 or method == 0xFF:
                    raise ProxyError("upstream SOCKS5 rejected authentication methods")
                if method == 0x02:
                    if auth is None:
                        raise ProxyError(
                            "upstream SOCKS5 demanded credentials but none are configured"
                        )
                    user = (self._proxy.username or "").encode()
                    password = (self._proxy.password or "").encode()
                    up_writer.write(
                        b"\x01"
                        + bytes([len(user)])
                        + user
                        + bytes([len(password)])
                        + password
                    )
                    await up_writer.drain()
                    _, status = await up_reader.readexactly(2)
                    if status != 0:
                        raise ProxyError("upstream SOCKS5 authentication failed")
                elif method != 0x00:
                    raise ProxyError(
                        f"upstream SOCKS5 chose unsupported method {method}"
                    )
                name = _socks5_address(host, port)
                up_writer.write(b"\x05\x01\x00" + name)
                await up_writer.drain()
                version, reply, _, atyp = await up_reader.readexactly(4)
                if version != 5 or reply != 0:
                    raise ProxyError(
                        f"upstream SOCKS5 connect failed with code {reply}"
                    )
                if atyp == 0x01:
                    await up_reader.readexactly(4)
                elif atyp == 0x03:
                    length = await up_reader.readexactly(1)
                    await up_reader.readexactly(length[0])
                elif atyp == 0x04:
                    await up_reader.readexactly(16)
                else:
                    raise ProxyError(
                        f"upstream SOCKS5 sent unknown address type {atyp}"
                    )
                await up_reader.readexactly(2)
        except TimeoutError as exc:
            raise ProxyError("upstream timed out during SOCKS5 handshake") from exc

    async def _forward_plain(
        self,
        method: str,
        target: str,
        version: str,
        headers: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if not target.lower().startswith(("http://", "https://")):
            raise ProxyError("plain requests must use an absolute URI")
        up_reader, up_writer = await self._open_upstream()
        try:
            kept = [
                line
                for line in headers.split(b"\r\n")
                if line and not line.lower().startswith(b"proxy-authorization:")
            ]
            auth = _basic_auth(self._proxy)
            if auth is not None:
                kept.append(f"Proxy-Authorization: {auth}".encode("latin-1"))
            head = f"{method} {target} {version}\r\n".encode("latin-1")
            up_writer.write(head + b"\r\n".join(kept) + b"\r\n\r\n")
            await up_writer.drain()
            await asyncio.gather(
                self._pipe(reader, up_writer),
                self._pipe(up_reader, writer),
            )
        finally:
            up_writer.close()

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(_PIPE_CHUNK):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.write_eof()
            except (OSError, RuntimeError):
                pass
