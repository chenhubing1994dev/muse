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
                body = json.dumps({"choices": [{"message": {"content": "local test reply"}}]}).encode()
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
            reply = client.chat([{"role": "user", "content": "hello"}])
            self.assertEqual(reply, "local test reply")
            self.assertEqual(server.received_messages[0]["content"], "hello")
            self.assertEqual(server.received_chat_template_kwargs, {"enable_thinking": False})
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
