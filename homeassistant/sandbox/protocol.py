"""The channel between core and a sandbox.

Every message is one frame: a 4 byte big-endian length followed by a JSON
object. There are three kinds of messages:

- ``{"id": 1, "method": "...", "params": {...}}`` is a call that expects a result.
- ``{"id": 1, "result": ...}`` or ``{"id": 1, "error": {...}}`` answers a call.
- ``{"method": "...", "params": {...}}`` is a notification without an answer.

Both sides can call and notify. Only JSON goes over the channel, so a sandbox
can never hand core an object, only data that core then validates.
"""

import asyncio
from collections.abc import Awaitable, Callable
import datetime as dt
import logging
import socket
import struct
from typing import Any

import orjson

from homeassistant.helpers.json import json_encoder_default

from .const import MAX_FRAME_SIZE

_LOGGER = logging.getLogger(__name__)

_HEADER = struct.Struct(">I")

type Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class SandboxError(Exception):
    """An error reported by, or about, a sandbox."""

    def __init__(self, message: str, error_type: str = "error") -> None:
        """Initialize the error."""
        super().__init__(message)
        self.error_type = error_type


class ProtocolError(SandboxError):
    """The other side broke the protocol."""


def _default(obj: Any) -> Any:
    if isinstance(obj, dt.timedelta):
        return obj.total_seconds()
    return json_encoder_default(obj)


def encode(message: dict[str, Any]) -> bytes:
    """Encode a message as a frame."""
    payload = orjson.dumps(message, default=_default, option=orjson.OPT_NON_STR_KEYS)
    if len(payload) > MAX_FRAME_SIZE:
        raise ProtocolError(f"Message of {len(payload)} bytes is too large")
    return _HEADER.pack(len(payload)) + payload


def check_frame_size(size: int) -> None:
    """Refuse a frame that is larger than allowed."""
    if size > MAX_FRAME_SIZE:
        raise ProtocolError(f"Frame of {size} bytes is too large")


def decode(payload: bytes) -> dict[str, Any]:
    """Decode the payload of a frame."""
    message = orjson.loads(payload)
    if not isinstance(message, dict):
        raise ProtocolError("A message must be a JSON object")
    return message


def read_frame_blocking(sock: socket.socket) -> dict[str, Any]:
    """Read one frame from a blocking socket.

    Used by the sandbox for the first message, before the event loop runs.
    """

    def _read(size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ProtocolError("Connection closed")
            data += chunk
        return bytes(data)

    (size,) = _HEADER.unpack(_read(_HEADER.size))
    check_frame_size(size)
    return decode(_read(size))


class Channel:
    """One end of the framed JSON channel."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handlers: dict[str, Handler],
        *,
        name: str,
        on_notification: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize the channel.

        ``handlers`` answer calls from the other side. Notifications go to
        ``on_notification``. Anything else is refused.
        """
        self._reader = reader
        self._writer = writer
        self._handlers = handlers
        self._on_notification = on_notification
        self._name = name
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.closed = asyncio.Event()

    def send(self, message: dict[str, Any]) -> None:
        """Send a message."""
        if self.closed.is_set():
            raise SandboxError(f"Channel {self._name} is closed", "closed")
        self._writer.write(encode(message))

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification."""
        self.send({"method": method, "params": params})

    async def async_call(
        self, method: str, params: dict[str, Any], timeout: float | None = None
    ) -> Any:
        """Call a method on the other side and return its result."""
        self._next_id += 1
        call_id = self._next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[call_id] = future
        try:
            self.send({"id": call_id, "method": method, "params": params})
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self._pending.pop(call_id, None)

    def answer(self, call_id: int, result: Any) -> None:
        """Answer a call that was read outside of the channel."""
        self.send({"id": call_id, "result": result})

    async def async_run(self) -> None:
        """Read messages until the other side closes the channel."""
        try:
            while True:
                header = await self._reader.readexactly(_HEADER.size)
                (size,) = _HEADER.unpack(header)
                check_frame_size(size)
                self._dispatch(decode(await self._reader.readexactly(size)))
        except asyncio.IncompleteReadError, ConnectionError:
            pass
        except (ProtocolError, orjson.JSONDecodeError) as err:
            _LOGGER.error("Closing channel %s: %s", self._name, err)
        finally:
            self.close()

    def close(self) -> None:
        """Close the channel and fail all pending calls."""
        if self.closed.is_set():
            return
        self.closed.set()
        self._writer.close()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(
                    SandboxError(f"Channel {self._name} closed", "closed")
                )
        self._pending.clear()
        for task in self._tasks:
            task.cancel()

    def _dispatch(self, message: dict[str, Any]) -> None:
        call_id = message.get("id")
        if call_id is not None and not isinstance(call_id, int):
            raise ProtocolError("Invalid message id")
        if "method" not in message:
            if (future := self._pending.get(call_id)) is None or future.done():  # type: ignore[arg-type]
                return
            if (error := message.get("error")) is not None:
                if not isinstance(error, dict):
                    raise ProtocolError("Invalid error")
                future.set_exception(
                    SandboxError(str(error.get("message")), str(error.get("type")))
                )
            else:
                future.set_result(message.get("result"))
            return

        method = message["method"]
        params = message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            raise ProtocolError("Invalid call")

        if call_id is None:
            if self._on_notification is not None:
                self._on_notification(method, params)
            return

        task = asyncio.create_task(self._async_handle_call(call_id, method, params))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _async_handle_call(
        self, call_id: int, method: str, params: dict[str, Any]
    ) -> None:
        response: dict[str, Any]
        if (handler := self._handlers.get(method)) is None:
            response = {
                "id": call_id,
                "error": {"type": "unknown_method", "message": method[:100]},
            }
        else:
            try:
                result = await handler(params)
            except SandboxError as err:
                response = {
                    "id": call_id,
                    "error": {"type": err.error_type, "message": str(err)},
                }
            except Exception as err:  # noqa: BLE001
                # The message crosses a trust boundary, keep it short.
                response = {
                    "id": call_id,
                    "error": {"type": type(err).__name__, "message": str(err)[:1000]},
                }
            else:
                response = {"id": call_id, "result": result}
        if self.closed.is_set():
            return
        try:
            self.send(response)
        except ProtocolError as err:
            self.send(
                {"id": call_id, "error": {"type": "too_large", "message": str(err)}}
            )
