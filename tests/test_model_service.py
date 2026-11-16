from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from muse.model_service import probe_configured_model_service, probe_model_service


class ModelServiceProbeTests(unittest.TestCase):
    def test_reports_expected_local_model_healthy(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"data": [{"id": "test-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = probe_model_service(f"http://127.0.0.1:{server.server_port}/v1", "test-model")
            self.assertEqual(result["status"], "healthy")
            self.assertTrue(result["model_available"])
            self.assertEqual(result["model_count"], 1)
            self.assertNotIn(str(server.server_port), str(result))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_non_loopback_or_credentialed_endpoint_without_network(self) -> None:
        for url in ("https://127.0.0.1/v1", "http://example.com/v1",
                    "http://user:secret@127.0.0.1/v1", "http://127.0.0.1/v1?token=x",
                    "http://127.0.0.1/api"):
            with self.subTest(url=url):
                result = probe_model_service(url, "test-model")
                self.assertEqual(result["status"], "endpoint_rejected")
                self.assertNotIn("secret", str(result))

    def test_reports_unavailable_endpoint_without_leaking_url(self) -> None:
        result = probe_model_service("http://127.0.0.1:1/v1", "private-model", timeout=0.1)
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("private-model", str(result))
        self.assertNotIn("127.0.0.1", str(result))

    def test_configured_workspace_uses_muse_model_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "muse.toml").write_text(
                '[models]\nprovider = "chatanywhere"\nbase_url = "https://example.invalid/v1"\nmodel = "private"\n',
                encoding="utf-8",
            )
            result = probe_configured_model_service(str(root))
        self.assertEqual(result["status"], "not_managed")
        self.assertEqual(result["provider"], "chatanywhere")
        self.assertNotIn("private", str(result))


if __name__ == "__main__":
    unittest.main()
