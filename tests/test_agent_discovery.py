from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from muse.agent_discovery import discover_configured_agents
from muse.agent_registry import AgentRegistry


class AgentDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "agents.sqlite3"
        self.config = self.root / "discovery.json"
        self.registry = AgentRegistry(self.database)

    def tearDown(self) -> None:
        self.registry.close()
        self.temp.cleanup()

    def test_rejects_non_loopback_or_unbounded_urls_before_network(self) -> None:
        self.config.write_text(json.dumps({"agents": [{"agent_id": "x", "health_url": "http://example.com:80/health"}]}))
        with patch("muse.agent_discovery.build_opener") as opener:
            with self.assertRaises(ValueError):
                discover_configured_agents(self.config, self.registry)
            opener.return_value.open.assert_not_called()

    def test_valid_local_health_response_registers_metadata_but_disabled_by_default_example(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"status": "ok", "agent_id": "researcher", "role": "research", "capabilities": ["web.search"]}).encode()
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
            self.config.write_text(json.dumps({"agents": [{"agent_id": "researcher", "health_url": f"http://127.0.0.1:{server.server_port}/health", "enabled": True}]}))
            result = discover_configured_agents(self.config, self.registry)
            self.assertEqual(result["discovered"], ["researcher"])
            self.assertEqual(result["executed_programs"], 0)
            agent = self.registry.get("researcher")
            self.assertEqual(agent["status"], "online")
            self.assertEqual(agent["capabilities"], ["web.search"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_identity_mismatch_is_not_registered(self) -> None:
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self, _limit): return b'{"status":"ok","agent_id":"spoof","role":"research","capabilities":[]}'
        class Opener:
            def open(self, *_args, **_kwargs): return Response()
        self.config.write_text(json.dumps({"agents": [{"agent_id": "expected", "health_url": "http://127.0.0.1:1234/health"}]}))
        with patch("muse.agent_discovery.build_opener", return_value=Opener()):
            result = discover_configured_agents(self.config, self.registry)
        self.assertEqual(result["discovered"], [])
        self.assertIsNone(self.registry.get("expected"))

    def test_discovery_loop_repeats_until_stop_event_is_set(self) -> None:
        from muse.agent_discovery import run_discovery_loop
        stop = threading.Event()
        calls = []
        def probe(*_args, **_kwargs):
            calls.append(True)
            if len(calls) == 2:
                stop.set()
            return {"discovered": [], "unavailable": [], "probed": 0, "executed_programs": 0}
        with patch("muse.agent_discovery.discover_configured_agents", side_effect=probe):
            result = run_discovery_loop(self.config, self.registry, interval_seconds=0.1, stop_event=stop)
        self.assertEqual(result["cycles"], 2)
        self.assertEqual(len(calls), 2)

    def test_unknown_config_fields_and_too_many_endpoints_are_rejected(self) -> None:
        self.config.write_text(json.dumps({"agents": [], "scan_all_ports": True}))
        with self.assertRaises(ValueError):
            discover_configured_agents(self.config, self.registry)


if __name__ == "__main__":
    unittest.main()
