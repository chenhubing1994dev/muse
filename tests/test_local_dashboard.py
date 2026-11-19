from __future__ import annotations
import json
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from muse.local_dashboard import create_local_dashboard_server, main as dashboard_main
from muse.status_api import create_status_server

class LocalDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text(
            '<!doctype html><html><head><title>Muse</title>'
            '<script type="module" src="/assets/app.js"></script></head><body></body></html>',
            encoding="utf-8",
        )
        (self.dist / "assets").mkdir()
        (self.dist / "assets" / "app.js").write_text("console.log('Muse');", encoding="utf-8")
        self.api, self.token = create_status_server(
            task_db=self.root / "tasks.sqlite3",
            progress_db=self.root / "progress.sqlite3",
            capability_db=self.root / "capabilities.sqlite3",
            agent_db=self.root / "agents.sqlite3",
            improvement_db=self.root / "improvements.sqlite3",
        )
        self.dashboard = create_local_dashboard_server(self.dist, api_server=self.api, bearer=self.token)
        self.threads = [
            threading.Thread(target=self.api.serve_forever, daemon=True),
            threading.Thread(target=self.dashboard.serve_forever, daemon=True),
        ]
        for thread in self.threads:
            thread.start()
        self.base = f"http://127.0.0.1:{self.dashboard.server_address[1]}"
        self.api_base = f"http://127.0.0.1:{self.api.server_address[1]}"
        # Localhost security tests must never be routed through environment proxies.
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def tearDown(self):
        self.dashboard.shutdown()
        self.api.shutdown()
        self.dashboard.server_close()
        self.api.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.temp.cleanup()

    def test_expected_browser_disconnects_do_not_emit_server_tracebacks(self):
        import io
        from unittest.mock import patch

        for error_type in (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            error = error_type("browser closed the connection")
            with (self.subTest(error=error_type.__name__),
                  patch("muse.local_dashboard.sys.exc_info", return_value=(error_type, error, None)),
                  patch("sys.stderr", new_callable=io.StringIO) as stderr):
                self.dashboard.handle_error(None, ("127.0.0.1", 12345))
                self.assertEqual(stderr.getvalue(), "")
    def test_root_bootstraps_ephemeral_connection_with_security_headers(self):
        with self.opener.open(self.base + "/", timeout=3) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('/__muse__/config.js', html)
            self.assertIn("Content-Security-Policy", response.headers)
            self.assertNotIn("Set-Cookie", response.headers)
        request = Request(self.base + "/__muse__/config.js", headers={"Sec-Fetch-Site": "same-origin"})
        with self.opener.open(request, timeout=3) as response:
            script = response.read().decode("utf-8")
        self.assertIn(self.token, script)
        self.assertIn(self.base + "/", script)

    def test_local_bootstrap_is_loopback_only_and_rejects_cross_site(self):
        request = Request(self.base + "/__muse__/config.js", headers={"Sec-Fetch-Site": "cross-site"})
        with self.assertRaises(HTTPError) as denied:
            self.opener.open(request, timeout=3)
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()


    def test_bootstrap_token_authenticates_loopback_api(self):
        with self.opener.open(self.base + "/", timeout=3):
            pass
        request = Request(self.base + "/__muse__/config.js", headers={"Sec-Fetch-Site": "same-origin"})
        with self.opener.open(request, timeout=3) as response:
            script = response.read().decode("utf-8")
        config = json.loads(script.split("Object.freeze(", 1)[1].rsplit(");", 1)[0])
        api_request = Request(
            self.api_base + "/api/v1/health",
            headers={"Authorization": "Bearer " + config["token"], "Origin": self.base},
        )
        with self.opener.open(api_request, timeout=3) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["api_version"], 1)

    def test_self_improvement_api_is_forwarded_by_local_dashboard(self):
        request = Request(
            self.base + "/api/v1/self-improvement",
            headers={"Authorization": "Bearer " + self.token, "Origin": self.base},
        )
        with self.opener.open(request, timeout=3) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["api_version"], 1)
        self.assertTrue(payload["approval_required"])
        self.assertFalse(payload["automatic_code_changes"])

    def test_host_header_is_bound_to_loopback_port(self):
        request = Request(self.base + "/__muse__/health", headers={"Host": "evil.example"})
        with self.assertRaises(HTTPError) as denied:
            self.opener.open(request, timeout=3)
        self.assertEqual(denied.exception.code, 421)
        denied.exception.close()

    def test_browser_favicon_probe_returns_empty_success(self):
        with self.opener.open(self.base + "/favicon.ico", timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.read(), b"")

    def test_static_assets_have_security_headers(self):
        with self.opener.open(self.base + "/assets/app.js", timeout=3) as response:
            self.assertEqual(response.read(), b"console.log('Muse');")
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertIn("frame-ancestors 'none'", response.headers.get("Content-Security-Policy", ""))

    def test_large_video_product_proxy_streams_without_buffering_limit(self):
        payload = b"muse-video" * (12 * 1024 * 1024 // 10 + 1)

        class FakeHeaders(dict):
            pass

        class FakeResponse:
            status = 200
            headers = FakeHeaders({
                "Content-Type": "video/mp4",
                "Content-Length": str(len(payload)),
                "Content-Disposition": 'attachment; filename="muse-video-test.mp4"',
            })

            def __init__(self):
                self.offset = 0
                self.closed = False

            def read(self, size=-1):
                if size < 0:
                    size = len(payload) - self.offset
                chunk = payload[self.offset:self.offset + size]
                self.offset += len(chunk)
                return chunk

            def close(self):
                self.closed = True

        fake = FakeResponse()
        request = Request(
            self.base + "/api/v1/products/" + "a" * 32 + "/" + "b" * 20 + "/video",
            headers={"Sec-Fetch-Site": "same-origin"},
        )
        upstream_opener = MagicMock()
        upstream_opener.open.return_value = fake
        with patch("muse.local_dashboard.build_opener", return_value=upstream_opener):
            with self.opener.open(request, timeout=10) as response:
                received = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get("Content-Length"), str(len(payload)))
                self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertEqual(received, payload)
        self.assertTrue(fake.closed)


    def test_lan_requires_pairing_and_proxies_api_same_origin(self):
        code = "24681357"
        lan_dashboard = create_local_dashboard_server(
            self.dist, api_server=self.api, bearer=self.token,
            host="0.0.0.0", port=0, lan_mode=True, pairing_code=code,
        )
        thread = threading.Thread(target=lan_dashboard.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertTrue(lan_dashboard.lan_urls)
            # The route-selected first interface is the local virtual switch in CI,
            # which permits self-connect even before firewall setup.
            lan_base = lan_dashboard.lan_urls[0].rstrip("/")
            # This is a direct local-network integration test; do not route its private
            # interface URL through the host's configured corporate HTTP proxy.
            remote = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
            with remote.open(lan_base + "/", timeout=3) as response:
                login = response.read().decode("utf-8")
            self.assertIn("Muse 局域网设备配对", login)
            self.assertIn("http://127.0.0.1:8766/", login)
            local_base = f"http://127.0.0.1:{lan_dashboard.server_address[1]}"
            with remote.open(local_base + "/__muse__/config.js", timeout=3) as response:                local_config = json.loads(response.read().decode("utf-8").split("Object.freeze(", 1)[1].rsplit(");", 1)[0])
            self.assertEqual(local_config["pairingCode"], code)
            self.assertEqual(local_config["lanUrls"], lan_dashboard.lan_urls)

            with self.assertRaises(HTTPError) as unpaired:
                remote.open(lan_base + "/api/v1/health", timeout=3)
            self.assertEqual(unpaired.exception.code, 401)
            unpaired.exception.close()

            bad_pair = Request(
                lan_base + "/__muse__/pair", data=b"code=00000000",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as denied:
                remote.open(bad_pair, timeout=3)
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()

            pair = Request(
                lan_base + "/__muse__/pair", data=("code=" + code).encode("ascii"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with remote.open(pair, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn('/__muse__/config.js', response.read().decode("utf-8"))
            config_request = Request(lan_base + "/__muse__/config.js")
            with remote.open(config_request, timeout=3) as response:
                config = json.loads(response.read().decode("utf-8").split("Object.freeze(", 1)[1].rsplit(");", 1)[0])
            self.assertEqual(config["baseUrl"], lan_base + "/")
            self.assertEqual(config["token"], self.token)
            self.assertIsNone(config["pairingCode"])

            health = Request(
                lan_base + "/api/v1/health",
                headers={"Authorization": "Bearer " + self.token},
            )
            with remote.open(health, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["api_version"], 1)
        finally:
            lan_dashboard.shutdown()
            lan_dashboard.server_close()
            thread.join(timeout=3)
    def test_main_uses_runtime_task_db_for_progress_events(self):
        root = self.root
        (root / "pyproject.toml").write_text("[project]\nname='muse-test'\n", encoding="utf-8")
        dist = root / "ui-prototype" / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><html><head></head><body></body></html>", encoding="utf-8")
        api = MagicMock()
        api.server_address = ("127.0.0.1", 55001)
        dashboard = MagicMock()
        dashboard.server_address = ("127.0.0.1", 8767)
        dashboard.lan_urls = []
        dashboard.serve_forever.side_effect = KeyboardInterrupt
        with patch("muse.local_dashboard.create_status_server", return_value=(api, "token")) as create_api, \
             patch("muse.local_dashboard.create_local_dashboard_server", return_value=dashboard), \
             patch("muse.local_dashboard._write_state"):
            result = dashboard_main(["--root", str(root), "--port", "8767", "--state-file", str(root / "dashboard.json")])
        kwargs = create_api.call_args.kwargs
        self.assertEqual(kwargs["progress_db"], root.resolve() / "data/tasks/muse.sqlite3")
        self.assertEqual(kwargs["progress_db"], kwargs["task_db"])
        self.assertEqual(result, 0)

if __name__ == "__main__":
    unittest.main()
