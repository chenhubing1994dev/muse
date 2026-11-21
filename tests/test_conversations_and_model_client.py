from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.conversations import ConversationStore
from muse.model_client import LMStudioClient, ModelError


class ConversationStoreTests(unittest.TestCase):
    def test_checkpoint_is_persistent_and_thread_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "threads.sqlite3"
            with ConversationStore(database) as store:
                first = store.save("thread-a", [{"role": "user", "content": "hello"}])
                self.assertEqual(first["revision"], 1)
                store.save("thread-a", [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}])
                store.save("thread-b", [{"role": "user", "content": "separate"}])
            with ConversationStore(database) as reopened:
                checkpoint = reopened.load("thread-a")
                self.assertEqual(checkpoint["revision"], 2)
                self.assertEqual(len(checkpoint["messages"]), 2)
                self.assertEqual(reopened.load("thread-b")["messages"][0]["content"], "separate")

    def test_rejects_empty_thread_and_invalid_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ConversationStore(Path(directory) / "db.sqlite3") as store:
            with self.assertRaises(ValueError):
                store.save(" ", [])
            with self.assertRaises(ValueError):
                store.list_threads(0)


class ModelClientTests(unittest.TestCase):
    def test_chat_requires_messages(self) -> None:
        client = LMStudioClient()
        with self.assertRaises(ValueError):
            client.chat([])

    def test_openai_compatible_local_http_chat_round_trip(self) -> None:
        import json
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps({"data": [{"id": "test-local-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.received_messages = request["messages"]
                self.server.received_chat_template_kwargs = request.get("chat_template_kwargs")
                body = json.dumps({
                    "model": "test-local-model", "choices": [{"message": {"content": "local test reply"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19,
                              "ignored_private_field": "must-not-be-retained"},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = LMStudioClient(
                f"http://127.0.0.1:{server.server_port}/v1",
                chat_template_kwargs={"enable_thinking": False},
            )
            self.assertEqual(client.list_models()[0]["id"], "test-local-model")
            detailed = client.chat_with_usage([{"role": "user", "content": "hello"}])
            self.assertEqual(detailed.content, "local test reply")
            self.assertEqual(detailed.model, "test-local-model")
            self.assertEqual(detailed.usage, {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19})
            reply = client.chat([{"role": "user", "content": "hello"}])
            self.assertEqual(reply, "local test reply")
            self.assertEqual(server.received_messages[0]["content"], "hello")
            self.assertEqual(server.received_chat_template_kwargs, {"enable_thinking": False})
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_bypass_proxies_uses_a_direct_opener(self) -> None:
        import json
        from unittest.mock import MagicMock, patch

        with (
            patch("urllib.request.build_opener") as build_opener,
            patch("urllib.request.urlopen") as global_urlopen,
        ):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"data": [{"id": "loopback-only"}]}
            ).encode("utf-8")
            build_opener.return_value = opener
            client = LMStudioClient(
                "http://127.0.0.1:1234/v1",
                bypass_proxies=True,
            )
            self.assertEqual(client.list_models()[0]["id"], "loopback-only")
            build_opener.assert_called_once()
            self.assertEqual(build_opener.call_args.args[0].proxies, {})
            opener.open.assert_called_once()
            global_urlopen.assert_not_called()

    def test_bypass_proxies_rejects_redirect_without_forwarding_prompt(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.server.request_count += 1
                self.send_response(307)
                self.send_header("Location", "http://example.invalid/collect")
                self.end_headers()

            def log_message(self, *_: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.request_count = 0
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = LMStudioClient(
                f"http://127.0.0.1:{server.server_port}/v1",
                model="local-review-model", timeout=2, bypass_proxies=True,
            )
            with self.assertRaises(ModelError):
                client.chat([{"role": "user", "content": "private case content"}])
            self.assertEqual(server.request_count, 1)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_missing_local_endpoint_is_reported_cleanly(self) -> None:
        client = LMStudioClient("http://127.0.0.1:1/v1", timeout=0.1)
        with self.assertRaises(ModelError):
            client.list_models()

if __name__ == "__main__":
    unittest.main()
