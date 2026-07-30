"""Tests for the TCP/LAN transport framer and lifecycle (TcpTransport).

The [len:2 LE][payload] framer is exercised two ways: by injecting an
``asyncio.StreamReader`` (precise control over partial / coalesced / truncated
byte streams) and by a real loopback asyncio server (end-to-end open_connection
+ write + read).
"""

from __future__ import annotations

import asyncio
import ssl

import pytest

from opendisplay.exceptions import OpenDisplayConnectionError, OpenDisplayTimeoutError
from opendisplay.protocol import OD_LAN_MAX_PAYLOAD
from opendisplay.transport.ip import _PSK_IDENTITY, TcpTransport


def _openssl_has_psk() -> bool:
    """Whether this OpenSSL build offers PSK ciphersuites (not all do)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        context.set_ciphers("PSK")
    except ssl.SSLError:
        return False
    return any("PSK" in cipher["name"] for cipher in context.get_ciphers())


requires_psk = pytest.mark.skipif(not _openssl_has_psk(), reason="OpenSSL build lacks PSK ciphersuites")


class _FakeWriter:
    """Minimal StreamWriter stand-in recording written bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closed


def _framed(payload: bytes) -> bytes:
    return len(payload).to_bytes(2, "little") + payload


def _wire(reader: asyncio.StreamReader) -> TcpTransport:
    t = TcpTransport("127.0.0.1", 2446)
    t._reader = reader
    t._writer = _FakeWriter()  # type: ignore[assignment]
    return t


# ── write framing ────────────────────────────────────────────────────────────


async def test_write_command_prepends_little_endian_length() -> None:
    t = _wire(asyncio.StreamReader())
    await t.write_command(b"\x00\x40hello")
    assert bytes(t._writer.buffer) == _framed(b"\x00\x40hello")  # type: ignore[union-attr]


async def test_write_command_rejects_empty_and_oversize() -> None:
    t = _wire(asyncio.StreamReader())
    with pytest.raises(ValueError):
        await t.write_command(b"")
    with pytest.raises(ValueError):
        await t.write_command(b"\x00" * (OD_LAN_MAX_PAYLOAD + 1))


# ── read framing ─────────────────────────────────────────────────────────────


async def test_read_single_frame() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(_framed(b"\x00\x71"))
    t = _wire(reader)
    assert await t.read_response(timeout=1.0) == b"\x00\x71"


async def test_read_coalesced_frames() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(_framed(b"AAA") + _framed(b"BBBB"))
    t = _wire(reader)
    assert await t.read_response(timeout=1.0) == b"AAA"
    assert await t.read_response(timeout=1.0) == b"BBBB"


async def test_read_partial_frame_reassembles() -> None:
    reader = asyncio.StreamReader()
    t = _wire(reader)
    payload = b"partial-body"
    frame = _framed(payload)

    async def dribble() -> None:
        for byte in frame:
            reader.feed_data(bytes([byte]))
            await asyncio.sleep(0)

    feeder = asyncio.create_task(dribble())
    assert await t.read_response(timeout=1.0) == payload
    await feeder


async def test_read_large_max_payload_frame() -> None:
    reader = asyncio.StreamReader()
    payload = (bytes(range(256)) * 16)[:OD_LAN_MAX_PAYLOAD]  # exactly 4094 bytes
    assert len(payload) == OD_LAN_MAX_PAYLOAD
    reader.feed_data(_framed(payload))
    t = _wire(reader)
    assert await t.read_response(timeout=1.0) == payload


async def test_zero_length_frame_is_protocol_error_and_disconnects() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data((0).to_bytes(2, "little"))
    t = _wire(reader)
    with pytest.raises(OpenDisplayConnectionError):
        await t.read_response(timeout=1.0)
    assert not t.is_connected  # connection dropped on protocol violation


async def test_oversize_frame_is_protocol_error() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data((OD_LAN_MAX_PAYLOAD + 1).to_bytes(2, "little"))
    t = _wire(reader)
    with pytest.raises(OpenDisplayConnectionError):
        await t.read_response(timeout=1.0)


async def test_read_timeout() -> None:
    reader = asyncio.StreamReader()  # never fed
    t = _wire(reader)
    with pytest.raises(OpenDisplayTimeoutError):
        await t.read_response(timeout=0.02)


async def test_truncated_body_raises_connection_error() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data((10).to_bytes(2, "little") + b"abc")  # promises 10, sends 3
    reader.feed_eof()
    t = _wire(reader)
    with pytest.raises(OpenDisplayConnectionError):
        await t.read_response(timeout=1.0)


async def test_read_when_not_connected_raises() -> None:
    t = TcpTransport("127.0.0.1", 2446)
    with pytest.raises(OpenDisplayConnectionError):
        await t.read_response(timeout=0.1)


# ── end-to-end loopback ──────────────────────────────────────────────────────


async def test_roundtrip_against_real_server() -> None:
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = await reader.readexactly(2)
        length = int.from_bytes(header, "little")
        body = await reader.readexactly(length)
        received.append(body)
        # Echo a distinct framed response.
        writer.write(_framed(b"\x00\x71"))
        await writer.drain()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        t = TcpTransport("127.0.0.1", port, timeout=2.0)
        await t.connect()
        assert t.is_connected
        await t.write_command(b"\x00\x40ping")
        assert await t.read_response(timeout=2.0) == b"\x00\x71"
        await t.disconnect()
        assert not t.is_connected

    assert received == [b"\x00\x40ping"]


async def test_connect_refused_raises_connection_error() -> None:
    # Port 1 is privileged and closed; connect must fail fast with a neutral error.
    t = TcpTransport("127.0.0.1", 1, timeout=1.0)
    with pytest.raises(OpenDisplayConnectionError):
        await t.connect()


async def test_connect_when_already_connected_is_a_noop() -> None:
    t = _wire(asyncio.StreamReader())
    writer = t._writer
    await t.connect()  # must not dial out or replace the live link
    assert t._writer is writer


async def test_connect_timeout_raises_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never_connects(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio, "open_connection", never_connects)
    t = TcpTransport("127.0.0.1", 2446, timeout=0.02)
    with pytest.raises(OpenDisplayTimeoutError):
        await t.connect()


# ── TLS-PSK ──────────────────────────────────────────────────────────────────


def test_ssl_context_is_psk_only_tls12() -> None:
    context = TcpTransport("h", 2447, tls=True, psk=b"k")._build_ssl_context()
    assert context.check_hostname is False
    assert context.verify_mode is ssl.CERT_NONE
    assert context.minimum_version is ssl.TLSVersion.TLSv1_2
    assert context.maximum_version is ssl.TLSVersion.TLSv1_2


@requires_psk
def test_ssl_context_offers_only_psk_ciphersuites() -> None:
    # set_ciphers() cannot filter TLS 1.3 suites, but the 1.2 version pin makes
    # them unreachable, so every negotiable (TLS 1.2) suite must be PSK — the
    # handshake can then never ask for a certificate.
    context = TcpTransport("h", 2447, tls=True, psk=b"k")._build_ssl_context()
    negotiable = [c["name"] for c in context.get_ciphers() if c["protocol"] != "TLSv1.3"]
    assert negotiable, "no TLS 1.2 ciphersuites enabled"
    assert all("PSK" in name for name in negotiable)


def test_psk_callback_returns_identity_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []
    monkeypatch.setattr(
        ssl.SSLContext,
        "set_psk_client_callback",
        lambda _self, callback: captured.append(callback),
    )
    TcpTransport("h", 2447, tls=True, psk=b"secret")._build_ssl_context()
    assert captured
    assert captured[0](None) == (_PSK_IDENTITY, b"secret")  # type: ignore[operator]


def test_psk_callback_without_key_yields_empty_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    # tls=True with psk=None must not blow up building the context; the empty
    # key simply fails the handshake at the peer.
    captured: list[object] = []
    monkeypatch.setattr(
        ssl.SSLContext,
        "set_psk_client_callback",
        lambda _self, callback: captured.append(callback),
    )
    TcpTransport("h", 2447, tls=True)._build_ssl_context()
    assert captured[0](None) == (_PSK_IDENTITY, b"")  # type: ignore[operator]


async def _psk_server(psk: bytes, identities: list[str]) -> asyncio.Server:
    """Loopback TLS-PSK server that echoes one framed response."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("PSK")

    def _server_callback(identity: str | None) -> bytes:
        identities.append(identity or "")
        return psk

    context.set_psk_server_callback(_server_callback, _PSK_IDENTITY)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = await reader.readexactly(2)
        await reader.readexactly(int.from_bytes(header, "little"))
        writer.write(_framed(b"\x00\x71"))
        await writer.drain()

    return await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)


@requires_psk
async def test_tls_psk_roundtrip_against_real_server() -> None:
    identities: list[str] = []
    server = await _psk_server(b"sh4red-k3y", identities)
    port = server.sockets[0].getsockname()[1]
    async with server:
        t = TcpTransport("127.0.0.1", port, timeout=5.0, tls=True, psk=b"sh4red-k3y")
        await t.connect()
        assert t.is_connected
        await t.write_command(b"\x00\x40ping")
        assert await t.read_response(timeout=5.0) == b"\x00\x71"
        await t.disconnect()
    assert identities == [_PSK_IDENTITY]  # firmware-expected identity on the wire


@requires_psk
async def test_tls_psk_mismatch_raises_connection_error() -> None:
    identities: list[str] = []
    server = await _psk_server(b"the-right-key", identities)
    port = server.sockets[0].getsockname()[1]
    async with server:
        t = TcpTransport("127.0.0.1", port, timeout=5.0, tls=True, psk=b"the-wrong-key")
        with pytest.raises(OpenDisplayConnectionError):
            await t.connect()
        assert not t.is_connected


# ── disconnect / teardown ────────────────────────────────────────────────────


async def test_disconnect_without_a_connection_is_a_noop() -> None:
    t = TcpTransport("127.0.0.1", 2446)
    await t.disconnect()  # must not raise
    assert not t.is_connected


async def test_disconnect_swallows_close_errors() -> None:
    class _AngryWriter(_FakeWriter):
        def close(self) -> None:
            raise OSError("half-open link")

    t = TcpTransport("127.0.0.1", 2446)
    t._writer = _AngryWriter()  # type: ignore[assignment]
    await t.disconnect()  # best-effort: never raises
    assert not t.is_connected


async def test_disconnect_swallows_wait_closed_timeout() -> None:
    class _StuckWriter(_FakeWriter):
        async def wait_closed(self) -> None:
            await asyncio.sleep(10)

    t = TcpTransport("127.0.0.1", 2446, timeout=0.02)
    t._writer = _StuckWriter()  # type: ignore[assignment]
    await t.disconnect()
    assert not t.is_connected


# ── write / read error paths ─────────────────────────────────────────────────


async def test_write_when_not_connected_raises() -> None:
    t = TcpTransport("127.0.0.1", 2446)
    with pytest.raises(OpenDisplayConnectionError):
        await t.write_command(b"\x00\x40")


async def test_write_failure_raises_connection_error() -> None:
    class _BrokenWriter(_FakeWriter):
        async def drain(self) -> None:
            raise ConnectionResetError("peer went away")

    t = _wire(asyncio.StreamReader())
    t._writer = _BrokenWriter()  # type: ignore[assignment]
    with pytest.raises(OpenDisplayConnectionError):
        await t.write_command(b"\x00\x40")


async def test_eof_before_header_raises_connection_error() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x01")  # one byte of a 2-byte prefix, then EOF
    reader.feed_eof()
    t = _wire(reader)
    with pytest.raises(OpenDisplayConnectionError):
        await t.read_response(timeout=1.0)


async def test_body_timeout_raises_timeout_error() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data((10).to_bytes(2, "little"))  # header only; body never arrives
    t = _wire(reader)
    with pytest.raises(OpenDisplayTimeoutError):
        await t.read_response(timeout=0.02)


# ── Transport conformance no-ops ─────────────────────────────────────────────


async def test_notification_and_cache_hooks_are_noops() -> None:
    t = TcpTransport("h", 2446)
    assert t.drain_notifications() == 0
    assert await t.clear_cache() is False


def test_class_attributes() -> None:
    t = TcpTransport("h", 2446)
    assert t.max_frame == OD_LAN_MAX_PAYLOAD
    assert t.supports_write_without_response is False
    assert t.device_name is None
