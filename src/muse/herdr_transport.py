"""Existing-Core-only, read-only Herdr Core event subscription transport.

This module never launches `hc` or starts a Core. The Windows connector opens
only an already-existing local named pipe; callers own the subscription's
lifetime and must create a fresh subscription after disconnect/restart.
"""
from __future__ import annotations

import ctypes
import json
import os
import time
import tomllib
import unicodedata

from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping

from .herdr_events import HerdrEventResult, HerdrEventStream

MAX_LINE_BYTES = 1024 * 1024
_PIPE_PREFIX = "\\\\.\\pipe\\"
_INVALID_SESSION_CHARS = set('/\\:*?"<>|')


def _env_value(environ: Mapping[str, str], name: str) -> str | None:
    for key, value in environ.items():
        if key.upper() == name:
            return value
    return None


def _session_name(raw: str | None) -> str:
    value = (raw or "").strip() or "default"
    if value in {".", ".."} or any(unicodedata.category(char) == "Cc" or char in _INVALID_SESSION_CHARS for char in value):
        raise ValueError("HC_SESSION contains characters not accepted by Herdr Core")
    return value


def _validate_local_pipe(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint or "\x00" in endpoint:
        raise ValueError("Herdr endpoint must be a non-empty local named-pipe path")
    if not endpoint.startswith(_PIPE_PREFIX) or endpoint.startswith("\\\\?\\"):
        raise ValueError("only local Windows named-pipe endpoints are supported")
    if len(endpoint) > 256:
        raise ValueError("Herdr named-pipe endpoint exceeds the Core path limit")
    return endpoint


def resolve_windows_pipe_endpoint(
    environ: Mapping[str, str] | None = None,
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> str:
    """Resolve HC_SOCKET > config.toml socket > Core's Windows default pipe."""
    env = os.environ if environ is None else environ
    explicit = _env_value(env, "HC_SOCKET")
    if explicit:
        return _validate_local_pipe(explicit)

    if config_path is None:
        xdg_config = _env_value(env, "XDG_CONFIG_HOME")
        appdata = _env_value(env, "APPDATA")
        profile = _env_value(env, "USERPROFILE") or "C:\\\\"
        config_home = Path(xdg_config) if xdg_config else (
            Path(appdata) if appdata else Path(profile) / "AppData" / "Roaming"
        )
        config_path = config_home / "herdr-core" / "config.toml"

    config_file = Path(config_path)
    try:
        config_text = config_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        config_text = ""
    except OSError as exc:
        raise ValueError(f"cannot read Herdr Core config: {type(exc).__name__}") from exc

    if config_text:
        try:
            config = tomllib.loads(config_text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError("Herdr Core config.toml is invalid; refusing endpoint guess") from exc
        configured = config.get("socket")
        if configured is not None:
            if not isinstance(configured, str):
                raise ValueError("Herdr Core config socket must be a string")
            if configured:
                return _validate_local_pipe(configured)

    session = _session_name(_env_value(env, "HC_SESSION"))
    username = _env_value(env, "USERNAME") or ""
    safe_user = "".join(char for char in username if char.isascii() and char.isalnum())
    return _validate_local_pipe(f"{_PIPE_PREFIX}herdr-core-{safe_user}-{session}.sock")


class _WindowsPipeStream:
    """Unbuffered named-pipe stream with cancellable, bounded line reads.

    PeekNamedPipe ensures the subsequent os.read never asks for more bytes than
    are already available, so a partial JSON line cannot pin the supervisor in
    a blocking read while it waits for the rest of the line.
    """

    def __init__(self, stream: BinaryIO) -> None:
        from ctypes import wintypes
        import msvcrt

        self._stream = stream
        self._fd = stream.fileno()
        self._handle = wintypes.HANDLE(msvcrt.get_osfhandle(self._fd))
        self._buffer = bytearray()
        self._closed = False
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._peek = self._kernel32.PeekNamedPipe
        self._peek.argtypes = (
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        )
        self._peek.restype = wintypes.BOOL

    def write(self, data: bytes) -> int:
        return self._stream.write(data)

    def flush(self) -> None:
        self._stream.flush()

    def readline(self, size: int = -1) -> bytes:
        return self.readline_cancellable(size)

    def readline_cancellable(
        self, size: int = -1, *, stop_event: Any = None,
        timeout_secs: float | None = None, poll_interval: float = 0.1,
    ) -> bytes:
        if size <= 0:
            size = MAX_LINE_BYTES + 1
        if not 0 < poll_interval <= 1:
            raise ValueError("poll_interval must be between 0 and 1 second")
        deadline = None if timeout_secs is None else time.monotonic() + timeout_secs
        from ctypes import wintypes

        while True:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Herdr Core event read cancelled")
            if self._closed:
                raise ConnectionError("Herdr Core pipe is closed")

            newline = self._buffer.find(b"\n")
            if newline >= 0:
                end = newline + 1
                if end <= size:
                    result = bytes(self._buffer[:end])
                    del self._buffer[:end]
                    return result
            if len(self._buffer) >= size:
                result = bytes(self._buffer[:size])
                del self._buffer[:size]
                return result

            available = wintypes.DWORD(0)
            if not self._peek(self._handle, None, 0, None, ctypes.byref(available), None):
                error = ctypes.get_last_error()
                if self._closed:
                    raise ConnectionError("Herdr Core pipe closed during read")
                raise OSError(error, "PeekNamedPipe failed while reading Herdr Core events")
            if available.value:
                amount = min(available.value, size - len(self._buffer))
                chunk = os.read(self._fd, amount)
                if not chunk:
                    raise ConnectionError("Herdr Core closed the event subscription")
                self._buffer.extend(chunk)
                continue

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Herdr Core event read timed out")
            wait_for = poll_interval if remaining is None else min(poll_interval, remaining)
            if stop_event is not None:
                if stop_event.wait(wait_for):
                    raise InterruptedError("Herdr Core event read cancelled")
            else:
                time.sleep(wait_for)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._stream.close()



def open_existing_windows_pipe(endpoint: str) -> _WindowsPipeStream:
    """Open one existing local pipe with identification-only server QoS.

    No WaitNamedPipe retry is used: a missing/busy endpoint fails immediately.
    This deliberately has no path that can spawn or autostart the Core.
    """
    endpoint = _validate_local_pipe(endpoint)
    if os.name != "nt":
        raise OSError("Herdr Core named-pipe transport is available only on Windows")

    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    # Match Herdr's Windows client: GENERIC_READ|WRITE, OPEN_EXISTING,
    # SECURITY_SQOS_PRESENT|SECURITY_IDENTIFICATION. No FILE_FLAG_OVERLAPPED.
    handle = create_file(
        endpoint,
        0x80000000 | 0x40000000,
        0,
        None,
        3,
        0x00100000 | 0x00010000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        raise OSError(error, f"cannot connect to existing Herdr Core pipe: {endpoint}")
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_BINARY | os.O_RDWR)
        stream = os.fdopen(descriptor, "r+b", buffering=0)
    except Exception:
        close_handle(handle)
        raise
    try:
        return _WindowsPipeStream(stream)
    except Exception:
        stream.close()
        raise


def _read_json_line(
    stream: Any, *, max_line_bytes: int, stop_event: Any = None,
    timeout_secs: float | None = None, poll_interval: float = 0.1,
) -> dict[str, Any]:
    cancellable_reader = getattr(stream, "readline_cancellable", None)
    if stop_event is not None and not callable(cancellable_reader):
        raise NotImplementedError("this Herdr stream does not support cancellable reads")
    if timeout_secs is not None and not callable(cancellable_reader):
        raise NotImplementedError("this Herdr stream does not support bounded reads")
    if timeout_secs is not None and callable(cancellable_reader):
        raw = cancellable_reader(
            max_line_bytes + 1, stop_event=stop_event,
            timeout_secs=timeout_secs, poll_interval=poll_interval,
        )
    elif callable(cancellable_reader):
        raw = cancellable_reader(
            max_line_bytes + 1, stop_event=stop_event,
            poll_interval=poll_interval,
        )
    else:
        raw = stream.readline(max_line_bytes + 1)
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("Herdr Core stream must return bytes")
    if not raw:
        raise ConnectionError("Herdr Core closed the event subscription")
    if len(raw) > max_line_bytes or not raw.endswith(b"\n"):
        raise ValueError("Herdr Core JSON-RPC line is oversized or incomplete")
    try:
        message = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Herdr Core returned invalid UTF-8 JSON-RPC") from exc
    if not isinstance(message, dict):
        raise ValueError("Herdr Core JSON-RPC message must be an object")
    return message

def _write_request(stream: Any, request: bytes) -> None:
    view = memoryview(request)
    while view:
        written = stream.write(view)
        if not isinstance(written, int) or written <= 0:
            raise ConnectionError("Herdr Core subscription request could not be written")
        view = view[written:]
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


class HerdrCoreEventSubscription:
    """One existing Core connection subscribed only to status/event notifications.

    Native Windows pipe reads poll only bytes already available and can be
    cancelled with the caller's stop event. Call `next_event` from Muse's
    existing supervised background worker, not from the UI thread. There is no
    reconnect loop: after EOF/error, close and create a new subscription to get
    a fresh sequence and authoritative snapshot.
    """

    def __init__(
        self,
        stream: Any,
        reducer: HerdrEventStream,
        *,
        max_line_bytes: int,
        core_version: str | None = None,
        schema_methods: tuple[str, ...] = (),
    ):
        self._stream = stream
        self.reducer = reducer
        self.max_line_bytes = max_line_bytes
        self.core_version = core_version
        self.schema_methods = schema_methods
        self._closed = False

    @classmethod
    def connect(
        cls,
        endpoint: str,
        *,
        opener: Callable[[str], Any] = open_existing_windows_pipe,
        max_line_bytes: int = MAX_LINE_BYTES,
        stop_event: Any = None,
        timeout_secs: float = 10.0,
        poll_interval: float = 0.1,
        expected_version: str | None = None,
        expected_openrpc_version: str | None = None,
    ) -> "HerdrCoreEventSubscription":
        endpoint = _validate_local_pipe(endpoint)
        if not callable(opener):
            raise ValueError("pipe opener must be callable")
        if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int) or not 256 <= max_line_bytes <= MAX_LINE_BYTES:
            raise ValueError("max_line_bytes must be between 256 and 1048576")
        if isinstance(timeout_secs, bool) or not isinstance(timeout_secs, (int, float)) or not 0.1 <= timeout_secs <= 60:
            raise ValueError("timeout_secs must be between 0.1 and 60")
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)) or not 0 < poll_interval <= 1:
            raise ValueError("poll_interval must be between 0 and 1 second")
        if stop_event is not None and (
            not callable(getattr(stop_event, "is_set", None))
            or not callable(getattr(stop_event, "wait", None))
        ):
            raise ValueError("stop_event must expose is_set() and wait()")
        if expected_version is not None and (
            not isinstance(expected_version, str) or not expected_version.strip() or len(expected_version) > 64
        ):
            raise ValueError("expected_version must be a non-empty version string")
        if expected_openrpc_version is not None and (
            not isinstance(expected_openrpc_version, str)
            or not expected_openrpc_version.strip()
            or len(expected_openrpc_version) > 32
        ):
            raise ValueError("expected_openrpc_version must be a non-empty version string")
        if expected_openrpc_version is not None and expected_version is None:
            raise ValueError("expected_openrpc_version requires expected_version")
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Herdr Core connection cancelled before opening the pipe")

        stream = opener(endpoint)  # exactly one attempt; opener must never autostart
        try:
            deadline = time.monotonic() + float(timeout_secs)
            core_version = None
            schema_methods: tuple[str, ...] = ()
            request_id = 1
            if expected_version is not None:
                discovery = {
                    "jsonrpc": "2.0", "id": request_id,
                    "method": "rpc.discover", "params": {},
                }
                _write_request(
                    stream,
                    (json.dumps(discovery, separators=(",", ":")) + "\n").encode("utf-8"),
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Herdr Core schema discovery timed out")
                schema_reply = _read_json_line(
                    stream, max_line_bytes=max_line_bytes, stop_event=stop_event,
                    timeout_secs=remaining, poll_interval=float(poll_interval),
                )
                if schema_reply.get("jsonrpc") != "2.0" or type(schema_reply.get("id")) is not int or schema_reply["id"] != request_id:
                    raise ValueError("Herdr Core schema reply did not match request id")
                if "error" in schema_reply:
                    raise ConnectionError("Herdr Core rejected rpc.discover")
                schema = schema_reply.get("result")
                info = schema.get("info") if isinstance(schema, dict) else None
                methods = schema.get("methods") if isinstance(schema, dict) else None
                if not isinstance(info, dict) or not isinstance(methods, list):
                    raise ValueError("Herdr Core returned an invalid OpenRPC schema")
                core_version = info.get("version")
                openrpc_version = schema.get("openrpc")
                if expected_openrpc_version is not None and openrpc_version != expected_openrpc_version:
                    raise ValueError("Herdr Core OpenRPC schema version does not match the pinned compatibility version")
                if core_version != expected_version:
                    raise ValueError("Herdr Core version does not match the pinned compatibility version")
                schema_methods = tuple(
                    item["name"] for item in methods
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                )
                if "events.subscribe" not in schema_methods:
                    raise ValueError("Herdr Core schema does not advertise events.subscribe")
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Herdr Core connection cancelled before event subscription")
                request_id += 1

            request = {
                "jsonrpc": "2.0", "id": request_id,
                "method": "events.subscribe", "params": {},
            }
            _write_request(
                stream,
                (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"),
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Herdr Core event subscription timed out")
            reply = _read_json_line(
                stream, max_line_bytes=max_line_bytes, stop_event=stop_event,
                timeout_secs=remaining, poll_interval=float(poll_interval),
            )
            if reply.get("jsonrpc") != "2.0" or type(reply.get("id")) is not int or reply["id"] != request_id:
                raise ValueError("Herdr Core subscription reply did not match request id")
            if "error" in reply:
                raise ConnectionError("Herdr Core rejected events.subscribe")
            result = reply.get("result")
            if not isinstance(result, dict):
                raise ValueError("Herdr Core subscription reply has no result object")
            reducer = HerdrEventStream.from_snapshot(result)
            return cls(
                stream, reducer, max_line_bytes=max_line_bytes,
                core_version=core_version, schema_methods=schema_methods,
            )
        except Exception:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
            raise


    def next_event(
        self, *, stop_event: Any = None, timeout_secs: float | None = None,
        poll_interval: float = 0.1,
    ) -> HerdrEventResult:
        if self._closed:
            raise ConnectionError("Herdr Core subscription is closed")
        if timeout_secs is not None and (
            isinstance(timeout_secs, bool) or not isinstance(timeout_secs, (int, float))
            or not 0.0 < timeout_secs <= 3600
        ):
            raise ValueError("timeout_secs must be greater than 0 and at most 3600")
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)) or not 0 < poll_interval <= 1:
            raise ValueError("poll_interval must be between 0 and 1 second")
        if stop_event is not None and (
            not callable(getattr(stop_event, "is_set", None))
            or not callable(getattr(stop_event, "wait", None))
        ):
            raise ValueError("stop_event must expose is_set() and wait()")
        message = _read_json_line(
            self._stream, max_line_bytes=self.max_line_bytes,
            stop_event=stop_event, timeout_secs=timeout_secs,
            poll_interval=float(poll_interval),
        )
        if message.get("jsonrpc") != "2.0" or message.get("method") not in {"event", "heartbeat", "missed"}:
            raise ValueError("unexpected Herdr Core event notification")
        return self.reducer.apply(message)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            close = getattr(self._stream, "close", None)
            if callable(close):
                close()
