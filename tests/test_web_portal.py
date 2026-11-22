from __future__ import annotations

import http.client
import json
import ssl
import struct
import tempfile
import zlib
import threading
from unittest.mock import patch
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from muse.web_portal import (
    PortalError, PortalSessionStore, UPSTREAM_READ_ROUTES, WebPortalServer,
    safe_static_file, validate_public_origin,
)
from muse.plugin_platform import PluginRegistry
from muse.capabilities import CapabilityRegistry
from muse.task_queue import TaskQueue


class WebPortalPolicyTests(unittest.TestCase):
    def test_public_origin_requires_exact_https_non_loopback_origin(self) -> None:
        self.assertEqual(validate_public_origin("https://muse.example.com/"), "https://muse.example.com")
        self.assertEqual(validate_public_origin("https://MUSE.example.com:443/"), "https://muse.example.com")
        self.assertEqual(validate_public_origin("https://muse.example.com:8443"), "https://muse.example.com:8443")
        for value in ("http://muse.example.com", "https://localhost", "https://127.0.0.1",
                      "https://192.168.1.20", "https://muse.local", "https://muse.example.com:99999",
                      "https://user:pass@muse.example.com", "https://muse.example.com/path"):
            with self.subTest(value=value), self.assertRaises(PortalError):
                validate_public_origin(value)

    def test_upstream_routes_are_get_only_allowlisted_data(self) -> None:
        self.assertEqual(set(UPSTREAM_READ_ROUTES), {"overview", "agents", "inspirations", "knowledge", "system"})
        self.assertNotIn("tasks", UPSTREAM_READ_ROUTES)
        self.assertNotIn("daemon-control", UPSTREAM_READ_ROUTES)

        self.assertIn("/api/v1/portal/tasks", Path(__file__).resolve().parents[1].joinpath("src/muse/web_portal.py").read_text(encoding="utf-8"))

    def test_inspiration_write_routes_are_narrow_and_browser_ui_is_wired(self) -> None:
        from muse import web_portal
        self.assertEqual(web_portal.UPSTREAM_WRITE_ROUTES, {
            "inspirations": "/api/v1/inspirations",
            "inspiration-evolve": "/api/v1/inspirations/evolve",
        })
        root = Path(__file__).resolve().parents[1]
        html = (root / "web-portal" / "index.html").read_text(encoding="utf-8")
        app = (root / "web-portal" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="inspiration-create-form"', html)
        self.assertIn("/api/v1/portal/inspirations", app)
        self.assertIn("/api/v1/portal/inspirations/evolve", app)
        self.assertNotIn("enqueue_video", app)
        self.assertIn('api("/api/v1/portal/tasks").catch', app)
        self.assertIn("任务账本暂不可用", app)
        self.assertIn('<option value="false" selected>', html)
        self.assertIn('<option value="true">允许本条灵感进行外部联网检索</option>', html)
        self.assertIn("易经、紫微、奇门、大六壬只用于生成灵感假设", html)

    def test_portal_data_paths_resolve_real_muse_stores_without_creating_empty_databases(self) -> None:
        from muse.web_portal import resolve_portal_data_paths

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "Muse"
            data = workspace / "data"
            task_db = data / "tasks" / "muse.sqlite3"
            task_db.parent.mkdir(parents=True)
            task_db.touch()
            existing_files = (
                data / "agents" / "muse.sqlite3",
                data / "improvements" / "muse.sqlite3",
                data / "research" / "knowledge.sqlite3",
                data / "gpu-resources.sqlite3",
            )
            for path in existing_files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            (data / "research" / "reports").mkdir(parents=True)
            (data / "products").mkdir(parents=True)

            paths = resolve_portal_data_paths(task_db)
            self.assertEqual(paths["workspace_root"], workspace.resolve())
            self.assertEqual(paths["agent_db"], (data / "agents" / "muse.sqlite3").resolve())
            self.assertEqual(paths["improvement_db"], (data / "improvements" / "muse.sqlite3").resolve())
            self.assertEqual(paths["knowledge_db"], (data / "research" / "knowledge.sqlite3").resolve())
            self.assertEqual(paths["report_dir"], (data / "research" / "reports").resolve())
            self.assertEqual(paths["products_dir"], (data / "products").resolve())
            self.assertEqual(paths["gpu_resource_db"], (data / "gpu-resources.sqlite3").resolve())
            self.assertEqual(paths["memory_db"], (data / "memory" / "muse.sqlite3").resolve())
            self.assertEqual(paths["plugin_db"], (data / "plugins" / "registry.sqlite3").resolve())
            self.assertFalse(paths["memory_db"].exists())
            self.assertFalse(paths["plugin_db"].exists())

            empty_workspace = Path(directory) / "EmptyMuse"
            empty_task_db = empty_workspace / "data" / "tasks" / "muse.sqlite3"
            empty_task_db.parent.mkdir(parents=True)
            empty_task_db.touch()
            empty_paths = resolve_portal_data_paths(empty_task_db)
            self.assertIsNone(empty_paths["knowledge_db"])
            self.assertIsNone(empty_paths["agent_db"])
            self.assertFalse((empty_workspace / "data" / "research" / "knowledge.sqlite3").exists())
            with self.assertRaisesRegex(ValueError, "existing Muse TaskQueue"):
                resolve_portal_data_paths(empty_workspace / "data" / "tasks" / "missing.sqlite3")





    def test_pwa_icons_include_ios_touch_png_and_installable_sizes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "web-portal" / "manifest.webmanifest").read_text(encoding="utf-8"))
        png_icons = {item["sizes"] for item in manifest["icons"] if item["type"] == "image/png"}
        self.assertTrue({"192x192", "512x512"}.issubset(png_icons))
        for filename, size in (("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)):
            data = (root / "web-portal" / filename).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))
            offset = 8
            compressed = bytearray()
            while offset < len(data):
                chunk_length = struct.unpack(">I", data[offset:offset + 4])[0]
                chunk_type = data[offset + 4:offset + 8]
                chunk_data = data[offset + 8:offset + 8 + chunk_length]
                chunk_crc = struct.unpack(">I", data[offset + 8 + chunk_length:offset + 12 + chunk_length])[0]
                self.assertEqual(chunk_crc, zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF)
                if chunk_type == b"IDAT":
                    compressed.extend(chunk_data)
                offset += chunk_length + 12
                if chunk_type == b"IEND":
                    break
            self.assertEqual(offset, len(data))
            self.assertEqual(len(zlib.decompress(compressed)), size * (size * 4 + 1))
        html = (root / "web-portal" / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"', html)
        service_worker = (root / "web-portal" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("/icon-192.png", service_worker)
        self.assertIn("/icon-512.png", service_worker)

    def test_static_path_resolution_rejects_traversal_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            (root / "index.html").write_text("ok", encoding="utf-8")
            outside = Path(tmp) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            self.assertEqual(safe_static_file(root, "/").read_text(encoding="utf-8"), "ok")
            self.assertIsNone(safe_static_file(root, "/../secret.txt"))
            self.assertIsNone(safe_static_file(root, "/%2e%2e/secret.txt"))
            (root / "linked.txt").symlink_to(outside)
            self.assertIsNone(safe_static_file(root, "/linked.txt"))

    def test_portal_password_minimum_is_measured_in_utf8_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            short_store = None
            try:
                with self.assertRaises(PortalError):
                    short_store = PortalSessionStore(Path(tmp) / "too-short.sqlite3", "a" * 31)
            finally:
                if short_store is not None:
                    short_store.close()
            store = PortalSessionStore(Path(tmp) / "unicode-length.sqlite3", "密" * 11)
            store.close()

    def test_session_cookie_secret_is_hashed_and_csrf_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            password = "a" * 32
            store = PortalSessionStore(Path(tmp) / "sessions.sqlite3", password)
            try:
                self.assertTrue(store.verify_password(password))
                self.assertFalse(store.verify_password("wrong-password"))
                self.assertFalse(store.verify_password(chr(0xD800)))
                token, expiry = store.create()
                self.assertGreater(expiry, 0)
                self.assertTrue(store.validate(token))
                csrf1 = store.rotate_csrf(token)
                self.assertIsNotNone(csrf1)
                self.assertTrue(store.verify_csrf(token, csrf1))
                csrf2 = store.rotate_csrf(token)
                self.assertNotEqual(csrf1, csrf2)
                self.assertFalse(store.verify_csrf(token, csrf1))
                self.assertTrue(store.verify_csrf(token, csrf2))
                store.revoke(token)
                self.assertFalse(store.validate(token))
            finally:
                store.close()

    def test_restart_invalidates_previous_browser_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "sessions.sqlite3"
            first = PortalSessionStore(database, "r" * 32)
            token, _ = first.create()
            self.assertTrue(first.validate(token))
            first.close()
            restarted = PortalSessionStore(database, "r" * 32)
            try:
                self.assertFalse(restarted.validate(token))
            finally:
                restarted.close()

    def test_admin_password_hash_is_persisted_and_wrong_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "sessions.sqlite3"
            password = "persistent-admin-secret-" + "x" * 32
            first = PortalSessionStore(database, password)
            try:
                tables = {row[0] for row in first.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                self.assertIn("web_admin_credentials", tables)
                credential = first.connection.execute(
                    "SELECT salt,password_hash FROM web_admin_credentials WHERE credential_id=1"
                ).fetchone()
                self.assertEqual(len(credential["salt"]), 16)
                self.assertEqual(len(credential["password_hash"]), 32)
                self.assertNotIn(password.encode("utf-8"), database.read_bytes())
                self.assertTrue(first.verify_password(password))
            finally:
                first.close()

            restarted = PortalSessionStore(database, password)
            try:
                self.assertTrue(restarted.verify_password(password))
            finally:
                restarted.close()

            with self.assertRaisesRegex(PortalError, "does not match stored administrator credential"):
                PortalSessionStore(database, "different-admin-secret-" + "y" * 32)

    def test_admin_password_rotation_cli_prompts_and_updates_persisted_hash(self) -> None:
        from muse.web_portal_admin import rotate_admin_password

        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "sessions.sqlite3"
            current = "current-admin-secret-" + "c" * 32
            replacement = "replacement-admin-secret-" + "d" * 32
            initial = PortalSessionStore(database, current)
            initial.close()
            with patch("muse.web_portal_admin.getpass.getpass", side_effect=[current, replacement, replacement]):
                rotate_admin_password(database)
            restarted = PortalSessionStore(database, replacement)
            try:
                self.assertTrue(restarted.verify_password(replacement))
            finally:
                restarted.close()

    def test_admin_password_rotation_requires_current_secret_and_revokes_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "sessions.sqlite3"
            current = "current-admin-secret-" + "a" * 32
            replacement = "replacement-admin-secret-" + "b" * 32
            server_store = PortalSessionStore(database, current)
            admin_store = None
            try:
                token, _ = server_store.create()
                admin_store = PortalSessionStore(database, current)
                with self.assertRaisesRegex(PortalError, "current administrator password is incorrect"):
                    admin_store.rotate_password("wrong-admin-secret-" + "z" * 32, replacement)
                self.assertTrue(admin_store.verify_password(current))
                admin_store.rotate_password(current, replacement)
                self.assertFalse(server_store.verify_password(current))
                self.assertTrue(server_store.verify_password(replacement))
                self.assertFalse(server_store.validate(token))
            finally:
                if admin_store is not None:
                    admin_store.close()
                server_store.close()

            restarted = PortalSessionStore(database, replacement)
            try:
                self.assertTrue(restarted.verify_password(replacement))
            finally:
                restarted.close()

    def test_login_throttle_and_audit_survive_restart_and_success_resets_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "sessions.sqlite3"
            first = PortalSessionStore(database, "p" * 32)
            try:
                for _ in range(5):
                    first.record_login_event("203.0.113.8", "login_failure")
                self.assertFalse(first.login_allowed("203.0.113.8"))
                first.record_login_event("203.0.113.8", "login_rate_limited")
            finally:
                first.close()
            restarted = PortalSessionStore(database, "p" * 32)
            try:
                self.assertFalse(restarted.login_allowed("203.0.113.8"))
                restarted.record_login_event("203.0.113.8", "login_success")
                self.assertTrue(restarted.login_allowed("203.0.113.8"))
                outcomes = [row["outcome"] for row in restarted.connection.execute(
                    "SELECT outcome FROM web_login_events WHERE client_address=? ORDER BY id",
                    ("203.0.113.8",),
                ).fetchall()]
                self.assertEqual(outcomes, ["login_failure"] * 5 + ["login_rate_limited", "login_success"])
            finally:
                restarted.close()


    def test_password_must_be_long_and_database_stores_only_session_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PortalError):
                PortalSessionStore(Path(tmp) / "bad.sqlite3", "short")
            database = Path(tmp) / "sessions.sqlite3"
            store = PortalSessionStore(database, "a" * 32)
            token, _ = store.create()
            rows = store.connection.execute("SELECT session_sha256,csrf_sha256 FROM web_sessions").fetchone()
            self.assertNotEqual(rows["session_sha256"], token)
            self.assertEqual(len(rows["session_sha256"]), 64)
            store.close()




class WebPortalHttpsIntegrationTests(unittest.TestCase):
    def test_login_session_read_only_proxy_and_logout_over_tls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
            certificate = (
                x509.CertificateBuilder()
                .subject_name(subject).issuer_name(subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=2))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
                .sign(key, hashes.SHA256())
            )
            cert_path = root / "server.pem"
            key_path = root / "server-key.pem"
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))

            upstream_state = {"authorization": None, "paths": [], "post_payloads": []}
            class UpstreamHandler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:
                    upstream_state["authorization"] = self.headers.get("Authorization")
                    upstream_state["paths"].append(self.path)
                    body = json.dumps({"api_version": 1, "queue": {"counts": {"queued": 3}}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                def do_POST(self) -> None:
                    upstream_state["authorization"] = self.headers.get("Authorization")
                    upstream_state["paths"].append(self.path)
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    upstream_state["post_payloads"].append(payload)
                    if self.path == "/api/v1/inspirations":
                        status = 201
                        result = {"api_version": 1, "inspiration": {
                            "id": "d" * 32, "title": payload.get("title", "Test inspiration"),
                            "status": "not_researched", "allow_external_research": payload.get("allow_external_research", False),
                        }}
                    elif self.path == "/api/v1/inspirations/evolve":
                        status = 200
                        result = {"api_version": 1, "evolution": {
                            "status": "completed", "inspiration_id": payload.get("inspiration_id"),
                            "external_network_called": False, "approval_required": True,
                            "automatic_code_changes": False, "blueprint_path": "data/prometheus/blueprints/test.json",
                        }}
                    else:
                        status = 404
                        result = {"api_version": 1, "error": "not found"}
                    body = json.dumps(result).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                def log_message(self, *_args) -> None:
                    return

            upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
            upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
            upstream_thread.start()
            static_dir = Path(__file__).resolve().parents[1] / "web-portal"
            with TaskQueue(root / "tasks.sqlite3") as queue:
                private_task = queue.enqueue(
                    "Private research task", {"kind": "research", "private_detail": "must-not-leak"},
                    require_approval=True, workload_class="research",
                )
            portal = WebPortalServer(
                ("127.0.0.1", 0), public_origin="https://muse.example.com",
                certfile=cert_path, keyfile=key_path, session_db=root / "sessions.sqlite3",
                password="i" * 32,
                upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}",
                upstream_token="internal-status-bearer-token-not-for-browser",
                plugin_db=root / "plugins.sqlite3", task_db=root / "tasks.sqlite3",
                capability_db=root / "capabilities.sqlite3", static_dir=static_dir,
            )
            portal_thread = threading.Thread(target=portal.serve_forever, daemon=True)
            portal_thread.start()
            context = ssl.create_default_context(cafile=str(cert_path))
            connection = http.client.HTTPSConnection("localhost", portal.server_address[1], context=context, timeout=5)
            headers = {"Host": "muse.example.com", "Origin": "https://muse.example.com"}

            try:
                connection.request("GET", "/", headers={"Host": "muse.example.com"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                homepage = response.read()
                self.assertIn(b"plugin-discover-form", homepage)
                self.assertIn(b"task-list", homepage)
                connection.request("GET", "/healthz", headers={"Host": "muse.example.com"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["check"], "liveness")
                connection.putrequest("GET", "/healthz", skip_host=True)
                connection.putheader("Host", "muse.example.com")
                connection.putheader("Host", "attacker.example.com")
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                connection.putrequest("GET", "/healthz", skip_host=True)
                connection.putheader("Host", "muse.example.com")
                connection.putheader("Origin", "https://muse.example.com")
                connection.putheader("Origin", "https://attacker.example.com")
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                connection.request("GET", "/api/v1/portal/tasks", headers=headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
                connection.request("GET", "/api/v1/portal/session", headers=headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()

                login_headers = {**headers, "Content-Type": "application/json"}
                connection.request("POST", "/api/v1/portal/login", body=json.dumps({
                    "password": "i" * 32,
                }), headers=login_headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                login = json.loads(response.read())
                cookie = response.getheader("Set-Cookie").split(";", 1)[0]
                self.assertIn("HttpOnly", response.getheader("Set-Cookie"))
                self.assertIn("SameSite=Strict", response.getheader("Set-Cookie"))
                self.assertNotIn("internal-status-bearer-token", json.dumps(login))

                connection.request("GET", "/api/v1/portal/session", headers={**headers, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                csrf = json.loads(response.read())["csrf_token"]

                connection.request("GET", "/api/v1/portal/overview", headers={**headers, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["queue"]["counts"]["queued"], 3)
                connection.request("GET", "/api/v1/portal/tasks", headers={**headers, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                task_ledger = json.loads(response.read())
                self.assertFalse(task_ledger["payloads_exposed"])
                self.assertEqual(task_ledger["tasks"][0]["id"], private_task["id"])
                self.assertEqual(task_ledger["tasks"][0]["status"], "waiting_approval")
                self.assertNotIn("payload", task_ledger["tasks"][0])
                self.assertNotIn("must-not-leak", json.dumps(task_ledger))
                connection.request("GET", "/api/v1/portal/extra/tasks", headers={**headers, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                self.assertEqual(upstream_state["authorization"], "Bearer internal-status-bearer-token-not-for-browser")
                connection.request("GET", "/api/v1/portal/knowledge", headers={**headers, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                self.assertIn("/api/v1/knowledge/graph?limit=80", upstream_state["paths"])

                inspiration_id = "d" * 32
                inspiration_payload = {
                    "title": "Muse 跨平台灵感闭环",
                    "content": "用本地知识与反证机制把灵感变成可验证研究。",
                    "allow_external_research": False,
                    "source": "user_manual",
                    "tags": ["muse", "cross-platform"],
                    "research_domains": ["agent architecture"],
                }
                inspiration_path = "/api/v1/portal/inspirations"
                connection.request("POST", inspiration_path, body=json.dumps(inspiration_payload), headers={
                    **login_headers, "Cookie": cookie,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.request("POST", inspiration_path, body=json.dumps(inspiration_payload), headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                created = json.loads(response.read())
                self.assertEqual(created["inspiration"]["id"], inspiration_id)
                self.assertFalse(created["inspiration"]["allow_external_research"])
                self.assertEqual(upstream_state["paths"][-1], "/api/v1/inspirations")
                self.assertEqual(upstream_state["post_payloads"][-1], inspiration_payload)
                self.assertEqual(upstream_state["authorization"], "Bearer internal-status-bearer-token-not-for-browser")

                evolve_path = "/api/v1/portal/inspirations/evolve"
                connection.request("POST", evolve_path, body=json.dumps({
                    "inspiration_id": inspiration_id, "enqueue_video": True,
                }), headers={**login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf})
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                self.assertEqual(upstream_state["paths"][-1], "/api/v1/inspirations")

                connection.request("POST", evolve_path, body=json.dumps({"inspiration_id": inspiration_id}), headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                evolution = json.loads(response.read())["evolution"]
                self.assertEqual(evolution["status"], "completed")
                self.assertFalse(evolution["external_network_called"])
                self.assertTrue(evolution["approval_required"])
                self.assertFalse(evolution["automatic_code_changes"])
                self.assertEqual(upstream_state["paths"][-1], "/api/v1/inspirations/evolve")
                self.assertEqual(upstream_state["post_payloads"][-1], {"inspiration_id": inspiration_id})


                manifest = {
                    "schema_version": 1,
                    "id": "research.cross-check",
                    "name": "Research Cross-check",
                    "version": "1.0.0",
                    "description": "Compare research claims with counter-evidence.",
                    "publisher": "local-review",
                    "dependencies": {},
                    "capabilities": [{
                        "id": "research",
                        "description": "Submit a research cross-check request through Commander.",
                        "risk": "research",
                        "permissions": ["commander.enqueue", "research.read"],
                    }],
                }
                discovery_path = "/api/v1/portal/plugins/discover"
                discovery_body = json.dumps({"manifest": manifest})
                connection.request("POST", discovery_path, body=discovery_body, headers=login_headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()

                connection.request("POST", discovery_path, body=discovery_body, headers={
                    **login_headers, "Cookie": cookie,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()

                connection.request("POST", discovery_path, body=discovery_body, headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                discovery = json.loads(response.read())
                self.assertEqual(discovery["plugin"]["status"], "discovered")
                self.assertTrue(discovery["review_required"])
                self.assertFalse(discovery["execution_enabled"])

                connection.request("POST", discovery_path, body=discovery_body, headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                    "Origin": "https://attacker.example.com",
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()

                connection.request("GET", "/api/v1/portal/plugins", headers={**headers, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                plugins = json.loads(response.read())["plugins"]
                self.assertEqual(plugins[0]["plugin_id"], "research.cross-check")
                self.assertEqual(plugins[0]["status"], "discovered")
                with CapabilityRegistry(root / "capabilities.sqlite3") as catalog:
                    candidate = catalog.get("plugin.research.cross-check.research")
                    self.assertEqual(candidate["status"], "needs_approval")
                    self.assertIsNone(candidate["executable"])
                    self.assertFalse(candidate["metadata"]["execution_enabled"])

                request_path = "/api/v1/portal/plugins/research.cross-check/capabilities/research/request"
                request_body = json.dumps({"payload": {"topic": "Muse research graph"}})
                connection.request("POST", request_path, body=request_body, headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 409)
                response.read()

                with PluginRegistry(root / "plugins.sqlite3") as registry:
                    registry.approve("research.cross-check", approved_by="local-admin-test")
                    registry.enable("research.cross-check", enabled_by="local-admin-test")
                connection.request("GET", "/api/v1/portal/plugins", headers={**headers, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                with CapabilityRegistry(root / "capabilities.sqlite3") as catalog:
                    self.assertEqual(catalog.get("plugin.research.cross-check.research")["status"], "needs_approval")

                connection.request("POST", request_path, body=request_body, headers={
                    **login_headers, "Cookie": cookie,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.request("POST", request_path, body=request_body, headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 202)
                queued = json.loads(response.read())
                self.assertEqual(queued["status"], "queued_for_commander_approval")

                connection.request("POST", request_path, body=json.dumps({"payload": []}), headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()

                task_id = queued["commander_result"]["task_id"]
                with TaskQueue(root / "tasks.sqlite3") as queue:
                    task = queue.get(task_id)
                self.assertEqual(task["status"], "waiting_approval")
                self.assertFalse(task["payload"]["execution_enabled"])
                self.assertTrue(task["payload"]["execution_adapter_required"])

                connection.request("POST", "/api/v1/portal/plugins/research.cross-check/enable",
                    body=json.dumps({"confirmation": "ENABLE-PLUGIN"}), headers={
                        **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                    })
                response = connection.getresponse()
                self.assertEqual(response.status, 405)
                response.read()

                invalid_manifest = {"manifest": {**manifest, "entrypoint": "module:run"}}
                connection.request("POST", discovery_path, body=json.dumps(invalid_manifest), headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()

                oversized = {"manifest": {**manifest, "description": "x" * (72 * 1024)}}
                connection.request("POST", discovery_path, body=json.dumps(oversized), headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()


                duplicate_body = '{"manifest":' + json.dumps(manifest) + ',"manifest":' + json.dumps(manifest) + '}'
                connection.request("POST", discovery_path, body=duplicate_body, headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()

                connection.request("POST", "/api/v1/portal/overview", body="{}", headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 405)
                response.read()

                connection.request("POST", "/api/v1/portal/logout", body="{}", headers={
                    **login_headers, "Cookie": cookie, "X-Muse-CSRF": csrf,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()

                connection.request("GET", "/api/v1/portal/session", headers={**headers, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
            finally:
                connection.close()
                portal.shutdown()
                portal.server_close()
                portal.close_portal()
                upstream.shutdown()
                upstream.server_close()
if __name__ == "__main__":
    unittest.main()
