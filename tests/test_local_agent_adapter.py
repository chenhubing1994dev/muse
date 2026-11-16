from __future__ import annotations

import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from muse.local_agent_adapter import LocalAgentError, LocalHTTPAgentAdapter


class LocalHTTPAgentAdapterTests(unittest.TestCase):
    def test_invokes_fixed_loopback_endpoint_and_returns_matching_agent_output(self) -> None:
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                observed.append((self.path, json.loads(body)))
                payload = json.dumps({"agent_id": "researcher", "output": "verified result"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *_args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = LocalHTTPAgentAdapter("researcher", f"http://127.0.0.1:{server.server_port}/api/v1/invoke")
            self.assertEqual(adapter("Collect two sources"), "verified result")
            self.assertEqual(observed, [("/api/v1/invoke", {"agent_id": "researcher", "prompt": "Collect two sources"})])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_non_loopback_and_non_allowlisted_paths(self) -> None:
        for url in ("http://example.com:80/api/v1/invoke", "http://127.0.0.1:1234/anything", "https://127.0.0.1:1234/api/v1/invoke", "http://user@127.0.0.1:1234/api/v1/invoke"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                LocalHTTPAgentAdapter("researcher", url)

    def test_rejects_oversized_prompt_before_network(self) -> None:
        adapter = LocalHTTPAgentAdapter("researcher", "http://127.0.0.1:1234/api/v1/invoke")
        with patch("muse.local_agent_adapter.build_opener") as opener:
            with self.assertRaises(ValueError):
                adapter("x" * (32 * 1024 + 1))
            opener.assert_not_called()

    def test_rejects_oversized_response(self) -> None:
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self, limit): return b"x" * limit
        class Opener:
            def open(self, *_args, **_kwargs): return Response()
        with patch("muse.local_agent_adapter.build_opener", return_value=Opener()):
            adapter = LocalHTTPAgentAdapter("researcher", "http://127.0.0.1:1234/api/v1/invoke")
            with self.assertRaisesRegex(LocalAgentError, "64 KiB"):
                adapter("hello")

    def test_rejects_response_for_another_agent(self) -> None:
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self, _limit): return b'{"agent_id":"spoof","output":"no"}'
        class Opener:
            def open(self, *_args, **_kwargs): return Response()
        with patch("muse.local_agent_adapter.build_opener", return_value=Opener()):
            adapter = LocalHTTPAgentAdapter("researcher", "http://127.0.0.1:1234/api/v1/invoke")
            with self.assertRaises(LocalAgentError):
                adapter("hello")

    def test_loopback_calls_ignore_environment_http_proxy(self) -> None:
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                observed.append(self.path)
                body = json.dumps({"agent_id": "researcher", "output": "direct"}).encode()
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
            with patch("urllib.request.getproxies", return_value={"http": "http://127.0.0.1:1"}), patch("urllib.request.proxy_bypass", return_value=False):
                adapter = LocalHTTPAgentAdapter("researcher", f"http://127.0.0.1:{server.server_port}/api/v1/invoke")
                self.assertEqual(adapter("hello"), "direct")
            self.assertEqual(observed, ["/api/v1/invoke"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_closes_http_error_response_when_rejecting_redirect(self) -> None:
        from urllib.error import HTTPError
        body = io.BytesIO(b"redirect")
        class Opener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 302, "Found", {}, body)
        with patch("muse.local_agent_adapter.build_opener", return_value=Opener()):
            adapter = LocalHTTPAgentAdapter("researcher", "http://127.0.0.1:1234/api/v1/invoke")
            with self.assertRaises(LocalAgentError):
                adapter("hello")
        self.assertTrue(body.closed)

    def test_does_not_follow_redirects(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "/api/v1/invoke")
                self.end_headers()
            def log_message(self, *_args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = LocalHTTPAgentAdapter("researcher", f"http://127.0.0.1:{server.server_port}/api/v1/invoke")
            with self.assertRaises(LocalAgentError):
                adapter("hello")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
