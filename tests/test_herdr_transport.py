from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from muse.herdr_transport import HerdrCoreEventSubscription, _WindowsPipeStream, resolve_windows_pipe_endpoint


class MemoryDuplex:
    def __init__(self, incoming: bytes):
        self.reader = io.BytesIO(incoming)
        self.writes = bytearray()
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.extend(data)
        return len(data)

    def flush(self) -> None:
        pass

    def readline(self, size: int = -1) -> bytes:
        return self.reader.readline(size)

    def readline_cancellable(self, size: int = -1, *, stop_event=None, timeout_secs=None, poll_interval=0.01) -> bytes:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("read cancelled")
        return self.readline(size)

    def close(self) -> None:
        self.closed = True


class HerdrTransportTests(unittest.TestCase):
    def test_explicit_hc_socket_wins_and_must_be_local_named_pipe(self):
        endpoint = r"\\.\pipe\herdr-core-custom.sock"
        self.assertEqual(resolve_windows_pipe_endpoint({"HC_SOCKET": endpoint}), endpoint)
        with self.assertRaises(ValueError):
            resolve_windows_pipe_endpoint({"HC_SOCKET": r"\\remote\pipe\herdr.sock"})
        with self.assertRaises(ValueError):
            resolve_windows_pipe_endpoint({"HC_SOCKET": r"C:\temp\herdr.sock"})

    def test_config_socket_is_used_before_default_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "herdr-core" / "config.toml"
            config.parent.mkdir()
            config.write_text('socket = ' + json.dumps(r"\\.\pipe\herdr-core-config.sock") + "\n", encoding="utf-8")
            endpoint = resolve_windows_pipe_endpoint({"XDG_CONFIG_HOME": directory, "USERNAME": "Muse User"})
        self.assertEqual(endpoint, r"\\.\pipe\herdr-core-config.sock")

    def test_default_endpoint_matches_core_username_and_session_rules(self):
        endpoint = resolve_windows_pipe_endpoint({"USERNAME": "Muse User!", "HC_SESSION": " dev-01 "})
        self.assertEqual(endpoint, r"\\.\pipe\herdr-core-MuseUser-dev-01.sock")
        with self.assertRaises(ValueError):
            resolve_windows_pipe_endpoint({"USERNAME": "Muse", "HC_SESSION": "bad/session"})

    def test_session_rejects_all_unicode_control_characters(self):
        with self.assertRaises(ValueError):
            resolve_windows_pipe_endpoint({"USERNAME": "Muse", "HC_SESSION": "bad\x85name"})

    def test_malformed_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / "herdr-core"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text("socket = [", encoding="utf-8")
            with self.assertRaises(ValueError):
                resolve_windows_pipe_endpoint({"XDG_CONFIG_HOME": directory})

    def test_subscription_sends_only_events_subscribe_and_reduces_notifications(self):
        snapshot = {"jsonrpc": "2.0", "id": 1, "result": {"seq": 4, "terminals": [
            {"id": "t1", "status": "idle", "reported": True},
        ]}}
        event = {"jsonrpc": "2.0", "method": "event", "params": {
            "seq": 5, "type": "status", "terminal": "t1", "data": {"status": "blocked", "reported": True},
        }}
        missed = {"jsonrpc": "2.0", "method": "missed", "params": {
            "seq": 8, "terminals": [{"id": "t2", "status": "working", "reported": True}],
        }}
        incoming = (json.dumps(snapshot) + "\n" + json.dumps(event) + "\n" + json.dumps(missed) + "\n").encode()
        stream = MemoryDuplex(incoming)
        opened: list[str] = []

        def opener(endpoint: str):
            opened.append(endpoint)
            return stream

        endpoint = r"\\.\pipe\herdr-core-user-default.sock"
        subscription = HerdrCoreEventSubscription.connect(endpoint, opener=opener)
        self.assertEqual(subscription.reducer.sequence, 4)
        self.assertEqual(subscription.reducer.statuses, {"t1": "idle"})
        first = subscription.next_event()
        self.assertTrue(first.accepted)
        self.assertEqual(subscription.reducer.statuses, {"t1": "blocked"})
        second = subscription.next_event()
        self.assertTrue(second.snapshot_replaced)
        self.assertEqual(subscription.reducer.statuses, {"t2": "working"})
        request = json.loads(stream.writes.decode("utf-8").strip())
        self.assertEqual(request, {"jsonrpc": "2.0", "id": 1, "method": "events.subscribe", "params": {}})
        self.assertEqual(opened, [endpoint])
        subscription.close()

    def test_openrpc_pin_requires_core_version_pin(self):
        with self.assertRaisesRegex(ValueError, "requires expected_version"):
            HerdrCoreEventSubscription.connect(
                r"\\.\pipe\herdr-core-user-default.sock",
                opener=lambda _endpoint: MemoryDuplex(b""),
                expected_openrpc_version="1.3.2",
            )

    def test_pinned_connection_discovers_version_then_subscribes_on_same_pipe(self):
        schema = {
            "jsonrpc": "2.0", "id": 1, "result": {
                "info": {"title": "herdr-core", "version": "0.1.21"},
                "openrpc": "1.3.2",
                "methods": [{"name": "rpc.discover"}, {"name": "events.subscribe"}],
            },
        }
        snapshot = {"jsonrpc": "2.0", "id": 2, "result": {"seq": 9, "terminals": []}}
        stream = MemoryDuplex(
            (json.dumps(schema) + "\n" + json.dumps(snapshot) + "\n").encode("utf-8")
        )
        endpoint = r"\\.\pipe\herdr-core-user-default.sock"
        subscription = HerdrCoreEventSubscription.connect(
            endpoint, opener=lambda _endpoint: stream, expected_version="0.1.21",
            expected_openrpc_version="1.3.2",
        )
        requests = [json.loads(line) for line in stream.writes.decode("utf-8").splitlines()]
        self.assertEqual([request["method"] for request in requests], ["rpc.discover", "events.subscribe"])
        self.assertEqual([request["id"] for request in requests], [1, 2])
        self.assertEqual(subscription.core_version, "0.1.21")
        self.assertEqual(set(subscription.schema_methods), {"rpc.discover", "events.subscribe"})
        self.assertEqual(subscription.reducer.sequence, 9)
        subscription.close()

    def test_pinned_connection_rejects_wrong_openrpc_schema_version(self):
        schema = {
            "jsonrpc": "2.0", "id": 1, "result": {
                "openrpc": "1.2.6", "info": {"version": "0.1.21"},
                "methods": [{"name": "events.subscribe"}],
            },
        }
        stream = MemoryDuplex((json.dumps(schema) + "\n").encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "OpenRPC schema version"):
            HerdrCoreEventSubscription.connect(
                r"\\.\pipe\herdr-core-user-default.sock",
                opener=lambda _endpoint: stream, expected_version="0.1.21",
                expected_openrpc_version="1.3.2",
            )
        self.assertTrue(stream.closed)

    def test_pinned_connection_rejects_wrong_core_version_and_closes_pipe(self):
        schema = {
            "jsonrpc": "2.0", "id": 1, "result": {
                "info": {"version": "0.1.20"},
                "methods": [{"name": "events.subscribe"}],
            },
        }
        stream = MemoryDuplex((json.dumps(schema) + "\n").encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "version"):
            HerdrCoreEventSubscription.connect(
                r"\\.\pipe\herdr-core-user-default.sock",
                opener=lambda _endpoint: stream, expected_version="0.1.21",
            )
        self.assertTrue(stream.closed)
        requests = [json.loads(line) for line in stream.writes.decode("utf-8").splitlines()]
        self.assertEqual([request["method"] for request in requests], ["rpc.discover"])

    def test_pinned_connection_rejects_schema_without_event_subscription(self):
        schema = {
            "jsonrpc": "2.0", "id": 1, "result": {
                "info": {"version": "0.1.21"},
                "methods": [{"name": "terminal.list"}],
            },
        }
        stream = MemoryDuplex((json.dumps(schema) + "\n").encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "events.subscribe"):
            HerdrCoreEventSubscription.connect(
                r"\\.\pipe\herdr-core-user-default.sock",
                opener=lambda _endpoint: stream, expected_version="0.1.21",
            )
        self.assertTrue(stream.closed)

    def test_invalid_subscribe_reply_closes_stream_and_fails(self):
        stream = MemoryDuplex(b'{"jsonrpc":"2.0","id":2,"result":{"seq":0,"terminals":[]}}\n')
        with self.assertRaises(ValueError):
            HerdrCoreEventSubscription.connect(r"\\.\pipe\herdr-core-user-default.sock", opener=lambda _endpoint: stream)
        self.assertTrue(stream.closed)

    def test_pre_cancelled_connect_does_not_open_the_pipe(self):
        import threading
        stop_event = threading.Event()
        stop_event.set()
        opened = []
        with self.assertRaises(InterruptedError):
            HerdrCoreEventSubscription.connect(
                r"\\.\pipe\herdr-core-user-default.sock",
                opener=lambda endpoint: opened.append(endpoint), stop_event=stop_event,
            )
        self.assertEqual(opened, [])

    def test_connect_handshake_can_be_cancelled_and_closes_stream(self):
        import threading
        stream = CancellableMemoryDuplex(b"")
        stop_event = threading.Event()
        outcome = []

        def connect():
            try:
                HerdrCoreEventSubscription.connect(
                    r"\\.\pipe\herdr-core-user-default.sock",
                    opener=lambda _endpoint: stream, stop_event=stop_event, poll_interval=0.005,
                )
            except BaseException as exc:
                outcome.append(exc)

        connector = threading.Thread(target=connect)
        connector.start()
        self.assertTrue(stream.read_started.wait(0.5))
        stop_event.set()
        connector.join(0.5)
        self.assertFalse(connector.is_alive(), "cancelled subscribe handshake must return promptly")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], InterruptedError)
        self.assertTrue(stream.closed)

    def test_connect_handshake_timeout_is_bounded_and_closes_stream(self):
        stream = CancellableMemoryDuplex(b"")
        with self.assertRaises(TimeoutError):
            HerdrCoreEventSubscription.connect(
                r"\\.\pipe\herdr-core-user-default.sock",
                opener=lambda _endpoint: stream, timeout_secs=0.1, poll_interval=0.005,
            )
        self.assertTrue(stream.closed)

    def test_next_event_can_be_cancelled_without_waiting_for_pipe_data(self):
        import threading
        snapshot = {"jsonrpc": "2.0", "id": 1, "result": {"seq": 4, "terminals": []}}
        stream = CancellableMemoryDuplex((json.dumps(snapshot) + "\n").encode())
        subscription = HerdrCoreEventSubscription.connect(
            r"\\.\pipe\herdr-core-user-default.sock", opener=lambda _endpoint: stream,
        )
        stop_event = threading.Event()
        stream.read_started.clear()
        outcome = []

        def read_next():
            try:
                subscription.next_event(stop_event=stop_event, poll_interval=0.005)
            except BaseException as exc:
                outcome.append(exc)

        reader = threading.Thread(target=read_next)
        reader.start()
        self.assertTrue(stream.read_started.wait(0.5))
        stop_event.set()
        reader.join(0.5)
        self.assertFalse(reader.is_alive(), "cancelled event read must return promptly")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], InterruptedError)
        subscription.close()

    def test_close_wakes_a_pending_cancellable_read(self):
        import threading
        snapshot = {"jsonrpc": "2.0", "id": 1, "result": {"seq": 4, "terminals": []}}
        stream = CancellableMemoryDuplex((json.dumps(snapshot) + "\n").encode())
        subscription = HerdrCoreEventSubscription.connect(
            r"\\.\pipe\herdr-core-user-default.sock", opener=lambda _endpoint: stream,
        )
        stream.read_started.clear()
        outcome = []

        def read_next():
            try:
                subscription.next_event(poll_interval=0.005)
            except BaseException as exc:
                outcome.append(exc)

        reader = threading.Thread(target=read_next)
        reader.start()
        self.assertTrue(stream.read_started.wait(0.5))
        subscription.close()
        reader.join(0.5)
        self.assertFalse(reader.is_alive(), "closing the subscription must wake a pending read")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], ConnectionError)

    def test_next_event_read_timeout_is_bounded(self):
        snapshot = {"jsonrpc": "2.0", "id": 1, "result": {"seq": 4, "terminals": []}}
        stream = CancellableMemoryDuplex((json.dumps(snapshot) + "\n").encode())
        subscription = HerdrCoreEventSubscription.connect(
            r"\\.\pipe\herdr-core-user-default.sock", opener=lambda _endpoint: stream,
        )
        with self.assertRaises(TimeoutError):
            subscription.next_event(timeout_secs=0.02, poll_interval=0.005)
        subscription.close()

    def test_windows_pipe_reader_reads_only_peeked_bytes(self):
        import ctypes
        from ctypes import wintypes
        from unittest.mock import patch

        reader = _WindowsPipeStream.__new__(_WindowsPipeStream)
        reader._stream = MemoryDuplex(b"")
        reader._fd = 77
        reader._handle = object()
        reader._buffer = bytearray()
        reader._closed = False
        pending = bytearray(b'{"jsonrpc":"2.0","method":"heartbeat","params":{"seq":4}}\n')

        def peek(_handle, _buffer, _size, _read, available_ptr, _remaining):
            ctypes.cast(available_ptr, ctypes.POINTER(wintypes.DWORD)).contents.value = len(pending)
            return 1

        def read(_fd, count):
            chunk = bytes(pending[:count])
            del pending[:count]
            return chunk

        reader._peek = peek
        with patch("muse.herdr_transport.os.read", side_effect=read):
            line = reader.readline_cancellable(1024, timeout_secs=0.1, poll_interval=0.005)
        self.assertTrue(line.endswith(b"\n"))
        self.assertIn(b"heartbeat", line)

    def test_windows_pipe_reader_can_cancel_while_pipe_has_no_bytes(self):
        import ctypes
        from ctypes import wintypes

        class StopAfterPoll:
            def is_set(self):
                return False

            def wait(self, _timeout):
                return True

        reader = _WindowsPipeStream.__new__(_WindowsPipeStream)
        reader._stream = MemoryDuplex(b"")
        reader._fd = 77
        reader._handle = object()
        reader._buffer = bytearray()
        reader._closed = False

        def peek(_handle, _buffer, _size, _read, available_ptr, _remaining):
            ctypes.cast(available_ptr, ctypes.POINTER(wintypes.DWORD)).contents.value = 0
            return 1

        reader._peek = peek
        with self.assertRaises(InterruptedError):
            reader.readline_cancellable(1024, stop_event=StopAfterPoll(), poll_interval=0.005)

    def test_reconnect_uses_fresh_snapshot_even_when_core_sequence_resets(self):
        snapshots = [
            {"jsonrpc": "2.0", "id": 1, "result": {"seq": 90, "terminals": [{"id": "old", "status": "working", "reported": True}]}},
            {"jsonrpc": "2.0", "id": 1, "result": {"seq": 1, "terminals": [{"id": "new", "status": "idle", "reported": True}]}},
        ]
        subscriptions = []
        for snapshot in snapshots:
            stream = MemoryDuplex((json.dumps(snapshot) + "\n").encode())
            subscriptions.append(HerdrCoreEventSubscription.connect(
                r"\\.\pipe\herdr-core-user-default.sock", opener=lambda _endpoint, stream=stream: stream,
            ))
        self.assertEqual(subscriptions[0].reducer.sequence, 90)
        self.assertEqual(subscriptions[1].reducer.sequence, 1)
        self.assertEqual(subscriptions[1].reducer.statuses, {"new": "idle"})
        for subscription in subscriptions:
            subscription.close()

    def test_absent_core_is_not_started_or_retried(self):
        calls: list[str] = []

        def opener(endpoint: str):
            calls.append(endpoint)
            raise FileNotFoundError("pipe not found")

        endpoint = r"\\.\pipe\herdr-core-user-default.sock"
        with self.assertRaises(FileNotFoundError):
            HerdrCoreEventSubscription.connect(endpoint, opener=opener)
        self.assertEqual(calls, [endpoint])


class CancellableMemoryDuplex(MemoryDuplex):
    def __init__(self, incoming: bytes):
        super().__init__(incoming)
        import threading
        self.read_started = threading.Event()

    def readline_cancellable(self, size: int = -1, *, stop_event=None, timeout_secs=None, poll_interval=0.01) -> bytes:
        import time
        self.read_started.set()
        deadline = None if timeout_secs is None else time.monotonic() + timeout_secs
        while True:
            if self.closed:
                raise ConnectionError("stream closed")
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("read cancelled")
            raw = self.reader.readline(size)
            if raw:
                return raw
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("read timed out")
            if stop_event is not None:
                stop_event.wait(poll_interval)
            else:
                time.sleep(poll_interval)


if __name__ == "__main__":
    unittest.main()
