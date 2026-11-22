from __future__ import annotations

import http.client
import json
import ssl
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from muse.companion_security import CompanionDeviceStore, sign_companion_request
from muse.companion_transport import CompanionTLSHTTPServer, build_companion_server_context
from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:  # pragma: no cover - optional test fixture dependency
    x509 = None


@unittest.skipUnless(x509 is not None, "cryptography is needed only for ephemeral TLS test certificates")
class CompanionTLSTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.certfile = self.root / "server-cert.pem"
        self.keyfile = self.root / "server-key.pem"
        self._create_certificate()
        self.database = self.root / "companion.sqlite3"
        self.task_db = self.root / "tasks.sqlite3"
        self.progress_db = self.root / "progress.sqlite3"
        self.store = CompanionDeviceStore(self.database)
        with TaskQueue(self.task_db):
            pass
        with ProgressEventStore(self.progress_db):
            pass
        self.server = CompanionTLSHTTPServer(
            ("127.0.0.1", 0), database=self.database,
            task_db=self.task_db, progress_db=self.progress_db,
            certfile=self.certfile, keyfile=self.keyfile,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"https://localhost:{self.server.server_address[1]}"
        self.client_context = ssl.create_default_context(cafile=str(self.certfile))
        self.challenge = self.store.create_pairing_challenge()
        self.claim_secret = "C" * 48

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.store.close()
        self.temp.cleanup()

    def _create_certificate(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256())
        )
        self.keyfile.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        self.certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        body = json.dumps(payload).encode()
        request = Request(
            self.base + path, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, context=self.client_context, timeout=5) as response:
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
            return response.status, json.loads(response.read())

    def _pair_and_claim(self) -> tuple[str, str]:
        status, response = self._post("/api/v1/companion/pairing/request", {
            "challenge_id": self.challenge["challenge_id"],
            "code": self.challenge["code"],
            "device_name": "Test Companion",
            "device_fingerprint": "a" * 64,
            "claim_secret": self.claim_secret,
        })
        self.assertEqual(status, 202)
        request_id = response["pairing"]["request_id"]
        self.store.approve_pairing(request_id, approved_by="host-test")
        status, response = self._post("/api/v1/companion/pairing/claim", {
            "request_id": request_id, "claim_secret": self.claim_secret,
        })
        self.assertEqual(status, 200)
        return response["device"]["device_id"], response["device"]["token"]

    def _signed_headers(self, device_id: str, token: str, path: str, *, cursor: str = "", nonce: str = "n" * 32) -> dict:
        import time
        session_id = "a" * 32
        timestamp_ms = int(time.time() * 1000)
        signature = sign_companion_request(
            token, device_id=device_id, session_id=session_id, nonce=nonce,
            timestamp_ms=timestamp_ms, method="GET", path=path, body=b"", cursor=cursor,
        )
        headers = {
            "X-Muse-Device-ID": device_id,
            "X-Muse-Device-Token": token,
            "X-Muse-Session-ID": session_id,
            "X-Muse-Nonce": nonce,
            "X-Muse-Timestamp": str(timestamp_ms),
            "X-Muse-Signature": signature,
        }
        if cursor:
            headers["Last-Event-ID"] = cursor
        return headers

    def test_tls_context_requires_certificate_and_enforces_tls12_minimum(self) -> None:
        context = build_companion_server_context(self.certfile, self.keyfile)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.maximum_version, ssl.TLSVersion.TLSv1_3)
        self.assertTrue(context.options & ssl.OP_NO_COMPRESSION)

    def test_client_must_validate_server_certificate_and_hostname(self) -> None:
        untrusted_context = ssl.create_default_context()
        with self.assertRaises(URLError):
            urlopen(self.base + "/api/v1/companion/status", context=untrusted_context, timeout=5)
        wrong_host = f"https://127.0.0.1:{self.server.server_address[1]}/api/v1/companion/status"
        with self.assertRaises(URLError):
            urlopen(wrong_host, context=self.client_context, timeout=5)

    def test_server_refuses_missing_database_paths_instead_of_creating_empty_databases(self) -> None:
        missing_task_db = self.root / "missing-tasks.sqlite3"
        with self.assertRaisesRegex(ValueError, "Commander task database must already exist"):
            CompanionTLSHTTPServer(
                ("127.0.0.1", 0), database=self.database,
                task_db=missing_task_db, progress_db=self.progress_db,
                certfile=self.certfile, keyfile=self.keyfile,
            )
        self.assertFalse(missing_task_db.exists())

    def test_missing_or_invalid_certificate_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            build_companion_server_context(self.root / "missing.pem", self.keyfile)
        invalid = self.root / "invalid.pem"
        invalid.write_text("not a certificate", encoding="utf-8")
        with self.assertRaises(ValueError):
            build_companion_server_context(invalid, self.keyfile)

    def test_pairing_and_one_time_claim_work_only_through_verified_tls(self) -> None:
        status, response = self._post("/api/v1/companion/pairing/request", {
            "challenge_id": self.challenge["challenge_id"],
            "code": self.challenge["code"],
            "device_name": "Test Companion",
            "device_fingerprint": "b" * 64,
            "claim_secret": self.claim_secret,
        })
        self.assertEqual(status, 202)
        request_id = response["pairing"]["request_id"]
        self.store.approve_pairing(request_id, approved_by="host-test")
        status, claim = self._post("/api/v1/companion/pairing/claim", {
            "request_id": request_id, "claim_secret": self.claim_secret,
        })
        self.assertEqual(status, 200)
        self.assertEqual(claim["device"]["permissions"], ["events.read", "status.read"])
        self.assertEqual(self.store.get_device(claim["device"]["device_id"])["status"], "active")

    def test_signed_status_is_readonly_redacted_and_replay_resistant(self) -> None:
        device_id, token = self._pair_and_claim()
        headers = self._signed_headers(device_id, token, "/api/v1/companion/status")
        request = Request(self.base + "/api/v1/companion/status", headers=headers)
        with urlopen(request, context=self.client_context, timeout=5) as response:
            payload = json.loads(response.read())
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertEqual(payload["host"], {"status": "online", "read_only": True})
        self.assertEqual(payload["device"]["device_id"], device_id)
        with self.assertRaises(HTTPError) as rejected:
            urlopen(Request(self.base + "/api/v1/companion/status", headers=headers), context=self.client_context, timeout=5)
        self.assertEqual(rejected.exception.code, 401)
        rejected.exception.close()

    def test_events_are_redacted_cursor_bound_and_replay_resistant(self) -> None:
        from muse.progress import ProgressEventStore
        with ProgressEventStore(self.progress_db) as progress:
            progress.record("system", "private-subject", "started", summary="private summary")
            progress.record("task", "another-private-subject", "completed", summary="also private")
        device_id, token = self._pair_and_claim()
        headers = self._signed_headers(device_id, token, "/api/v1/companion/events")
        with urlopen(Request(self.base + "/api/v1/companion/events", headers=headers), context=self.client_context, timeout=5) as response:
            payload = json.loads(response.read())
        self.assertEqual(len(payload["events"]), 2)
        encoded = json.dumps(payload)
        self.assertNotIn("private-subject", encoded)
        self.assertNotIn("private summary", encoded)
        cursor = payload["events"][0]["id"]
        cursor_headers = self._signed_headers(
            device_id, token, "/api/v1/companion/events", cursor=cursor, nonce="c" * 32,
        )
        request = Request(self.base + "/api/v1/companion/events", headers=cursor_headers)
        with urlopen(request, context=self.client_context, timeout=5) as response:
            next_payload = json.loads(response.read())
        self.assertEqual([event["id"] for event in next_payload["events"]], [payload["events"][1]["id"]])
        with self.assertRaises(HTTPError) as rejected:
            urlopen(request, context=self.client_context, timeout=5)
        self.assertEqual(rejected.exception.code, 401)
        rejected.exception.close()

    def test_tauri_cors_preflight_is_exact_origin_and_route_scoped(self) -> None:
        allowed = Request(
            self.base + "/api/v1/companion/pairing/request",
            method="OPTIONS",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        with urlopen(allowed, context=self.client_context, timeout=5) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "http://tauri.localhost")
            self.assertEqual(response.headers.get("Access-Control-Allow-Methods"), "POST")
            self.assertIsNone(response.headers.get("Access-Control-Allow-Credentials"))

        signed_read = Request(
            self.base + "/api/v1/companion/events",
            method="OPTIONS",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": (
                    "x-muse-device-id,x-muse-device-token,x-muse-session-id,"
                    "x-muse-nonce,x-muse-timestamp,x-muse-signature,last-event-id"
                ),
            },
        )
        with urlopen(signed_read, context=self.client_context, timeout=5) as response:
            self.assertEqual(response.status, 204)
            allowed_headers = response.headers.get("Access-Control-Allow-Headers", "")
            self.assertIn("x-muse-device-token", allowed_headers)
            self.assertIn("last-event-id", allowed_headers)

        rejected = Request(
            self.base + "/api/v1/companion/pairing/request",
            method="OPTIONS",
            headers={
                "Origin": "https://untrusted.invalid",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        with self.assertRaises(HTTPError) as denied:
            urlopen(rejected, context=self.client_context, timeout=5)
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()

    def test_plain_http_unknown_methods_and_query_strings_are_rejected(self) -> None:
        host, port = self.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=2)
        try:
            with self.assertRaises((ConnectionError, OSError, http.client.HTTPException)):
                connection.request("GET", "/api/v1/companion/status")
                connection.getresponse()
        finally:
            connection.close()
        with self.assertRaises(HTTPError) as rejected:
            urlopen(Request(self.base + "/api/v1/companion/status?token=leak", method="PUT"), context=self.client_context, timeout=5)
        self.assertEqual(rejected.exception.code, 405)
        rejected.exception.close()
        with self.assertRaises(HTTPError) as rejected:
            urlopen(Request(self.base + "/api/v1/companion/status?anything=1"), context=self.client_context, timeout=5)
        self.assertEqual(rejected.exception.code, 400)
        rejected.exception.close()


if __name__ == "__main__":
    unittest.main()
