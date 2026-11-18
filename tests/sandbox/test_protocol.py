"""Tests for the channel between core and a sandbox."""

import asyncio
from collections.abc import AsyncGenerator
import datetime as dt
import socket
from typing import Any

import pytest

from homeassistant.sandbox import protocol
from homeassistant.sandbox.const import MAX_FRAME_SIZE
from homeassistant.sandbox.protocol import Channel, ProtocolError, SandboxError


async def _channel(
    sock: socket.socket,
    handlers: dict[str, protocol.Handler],
    notifications: list[tuple[str, dict[str, Any]]],
) -> Channel:
    reader, writer = await asyncio.open_connection(sock=sock)
    return Channel(
        reader,
        writer,
        handlers,
        name="test",
        on_notification=lambda method, params: notifications.append((method, params)),
    )


@pytest.fixture
async def channels() -> AsyncGenerator[
    tuple[Channel, Channel, list[tuple[str, dict[str, Any]]]]
]:
    """Return two connected channels; the second answers calls."""
    left_sock, right_sock = socket.socketpair()
    notifications: list[tuple[str, dict[str, Any]]] = []

    async def echo(params: dict[str, Any]) -> Any:
        return params

    async def fail(params: dict[str, Any]) -> Any:
        raise SandboxError("no", "denied")

    async def crash(params: dict[str, Any]) -> Any:
        raise RuntimeError("boom")

    left = await _channel(left_sock, {}, notifications)
    right = await _channel(
        right_sock, {"echo": echo, "fail": fail, "crash": crash}, notifications
    )
    tasks = [
        asyncio.create_task(left.async_run()),
        asyncio.create_task(right.async_run()),
    ]
    yield left, right, notifications
    left.close()
    right.close()
    await asyncio.gather(*tasks)


async def test_call_and_notify(channels) -> None:
    """Calls get answers, notifications arrive."""
    left, right, notifications = channels
    when = dt.datetime(2026, 11, 18, tzinfo=dt.UTC)
    result = await left.async_call(
        "echo", {"when": when, "every": dt.timedelta(minutes=1), "ids": {1}}, 5
    )
    assert result == {"when": when.isoformat(), "every": 60.0, "ids": [1]}

    right.notify("hello", {"a": 1})
    async with asyncio.timeout(5):
        while not notifications:
            await asyncio.sleep(0)
    assert notifications == [("hello", {"a": 1})]


async def test_errors(channels) -> None:
    """Errors cross the channel as data, never as objects."""
    left, _, _ = channels
    with pytest.raises(SandboxError) as err:
        await left.async_call("fail", {}, 5)
    assert err.value.error_type == "denied"

    with pytest.raises(SandboxError) as err:
        await left.async_call("crash", {}, 5)
    assert err.value.error_type == "RuntimeError"
    assert str(err.value) == "boom"

    with pytest.raises(SandboxError) as err:
        await left.async_call("missing", {}, 5)
    assert err.value.error_type == "unknown_method"


async def test_close_fails_pending_calls(channels) -> None:
    """Closing the channel fails calls that wait for an answer."""
    left, right, _ = channels
    right._handlers["wait"] = lambda params: asyncio.sleep(10)
    call = asyncio.create_task(left.async_call("wait", {}, 5))
    await asyncio.sleep(0.01)
    left.close()
    with pytest.raises(SandboxError):
        await call


def test_frame_limits() -> None:
    """Frames that are too large are refused on both sides."""
    with pytest.raises(ProtocolError):
        protocol.encode({"data": "x" * MAX_FRAME_SIZE})
    with pytest.raises(ProtocolError):
        protocol.check_frame_size(MAX_FRAME_SIZE + 1)
    with pytest.raises(ProtocolError):
        protocol.decode(b"[1, 2]")


async def test_oversized_frame_closes_channel() -> None:
    """A peer that announces a huge frame is disconnected."""
    left_sock, right_sock = socket.socketpair()
    channel = await _channel(left_sock, {}, [])
    right_sock.sendall((MAX_FRAME_SIZE + 1).to_bytes(4, "big"))
    async with asyncio.timeout(5):
        await channel.async_run()
    assert channel.closed.is_set()
    right_sock.close()


def test_read_frame_blocking() -> None:
    """The sandbox reads its first message before the event loop runs."""
    left, right = socket.socketpair()
    left.sendall(protocol.encode({"id": 1, "method": "init", "params": {}}))
    assert protocol.read_frame_blocking(right) == {
        "id": 1,
        "method": "init",
        "params": {},
    }
    left.close()
    with pytest.raises(ProtocolError):
        protocol.read_frame_blocking(right)
    right.close()
