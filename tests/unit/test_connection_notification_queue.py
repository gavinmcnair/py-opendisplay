"""Tests for notification-queue draining and timeout recovery (C6)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from opendisplay.exceptions import BLETimeoutError
from opendisplay.transport.connection import BLEConnection


def test_drain_notifications_discards_all_queued() -> None:
    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    conn._notification_queue.put_nowait(b"a")
    conn._notification_queue.put_nowait(b"b")

    assert conn.drain_notifications() == 2
    assert conn._notification_queue.empty()


def test_drain_notifications_empty_queue_is_noop() -> None:
    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    assert conn.drain_notifications() == 0


def test_drain_notifications_logs_command_type_of_each_dropped_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warning names every dropped frame's command, so a desync can be traced
    to the exchange that leaked it instead of only being reported as a count."""
    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    conn._notification_queue.put_nowait(b"\x00\x40\x00\x01")  # stray READ_CONFIG chunk
    conn._notification_queue.put_nowait(b"\x00\x50\x00")  # duplicated AUTHENTICATE reply

    with caplog.at_level(logging.WARNING, logger="opendisplay.transport.connection"):
        assert conn.drain_notifications() == 2

    assert "READ_CONFIG (0x0040) (4 B)" in caplog.text
    assert "AUTHENTICATE (0x0050) (3 B)" in caplog.text


def test_drain_notifications_resolves_ack_and_nack_forms_to_their_command() -> None:
    """A response carries the ACK high bit or the 0xFF NACK prefix; both must
    resolve to the originating command rather than logging as unknown codes."""
    assert BLEConnection._describe_dropped_frame(b"\x80\x40\x00") == "READ_CONFIG ACK (0x8040) (3 B)"
    assert BLEConnection._describe_dropped_frame(b"\xff\x40\x00\x00") == "READ_CONFIG NACK (0xff40) (4 B)"
    # Not a known command: reported as bare hex rather than guessed at.
    assert BLEConnection._describe_dropped_frame(b"\x00\x99") == "0x0099 (2 B)"


def test_drain_notifications_describes_frame_too_short_for_an_echo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A frame shorter than the 2-byte echo is reported verbatim, never dropped
    silently and never raising out of the drain path."""
    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    conn._notification_queue.put_nowait(b"\xff")

    with caplog.at_level(logging.WARNING, logger="opendisplay.transport.connection"):
        assert conn.drain_notifications() == 1

    assert "malformed ff (1 B)" in caplog.text


def test_link_drop_callback_flushes_queued_frames() -> None:
    """An unexpected drop never calls disconnect(), so the stack-signalled path
    must flush on its own or leftovers ride into the next connect()."""
    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    conn._notification_queue.put_nowait(b"\x00\x40\x00\x01")

    conn._on_disconnect(None)  # type: ignore[arg-type]  # client arg is unused

    assert conn._notification_queue.empty()


def test_link_drop_callback_still_notifies_owner_after_flushing() -> None:
    """Flushing must not displace the owner notification that clears session state."""
    notified: list[bool] = []
    conn = BLEConnection("AA:BB:CC:DD:EE:FF", disconnected_callback=lambda: notified.append(True))
    conn._notification_queue.put_nowait(b"\x00\x50\x00")

    conn._on_disconnect(None)  # type: ignore[arg-type]

    assert conn._notification_queue.empty()
    assert notified == [True]


@pytest.mark.asyncio
async def test_disconnect_flushes_queue_when_link_already_down() -> None:
    """disconnect() skips its teardown branch when the client is already gone, but
    frames queued before the drop must still not survive into a reconnect."""
    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    conn._notification_queue.put_nowait(b"\x00\x40\x00\x01")

    await conn.disconnect()

    assert conn._notification_queue.empty()


@pytest.mark.asyncio
async def test_clear_cache_and_drop_flushes_queue_between_connect_attempts() -> None:
    """A failed attempt that reached notification setup must not leave a frame for
    the retry to read as its first response."""

    class _FakeClient:
        is_connected = False

        async def disconnect(self) -> None:
            return None

    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    conn._client = _FakeClient()  # type: ignore[assignment]
    conn._notification_queue.put_nowait(b"\x00\x40\x00\x01")

    await conn._clear_cache_and_drop()

    assert conn._notification_queue.empty()


@pytest.mark.asyncio
async def test_read_response_recovers_item_delivered_during_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If wait_for cancels queue.get() after an item was handed over, the item
    is recovered synchronously instead of being lost."""
    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    conn._notification_queue.put_nowait(b"late-response")

    def fake_wait_for(coro: object, timeout: float) -> object:
        coro.close()  # type: ignore[attr-defined]  # avoid unawaited-coroutine warning
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    assert await conn.read_response(timeout=0.01) == b"late-response"


@pytest.mark.asyncio
async def test_read_response_times_out_when_queue_empty() -> None:
    conn = BLEConnection("AA:BB:CC:DD:EE:FF")
    with pytest.raises(BLETimeoutError):
        await conn.read_response(timeout=0.01)
