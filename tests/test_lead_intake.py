from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from muse.lead_intake import (
    LeadRateLimiter,
    LeadStore,
    LeadValidationError,
    create_request_handler,
    export_leads_csv,
    validate_lead_payload,
)


def valid_payload(submission_id="123e4567-e89b-12d3-a456-426614174000"):
    return {
        "submission_id": submission_id,
        "service": "AI 短视频制作",
        "brief": "制作三条竖屏产品介绍短片。",
        "budget": "$300–$1,000",
        "timeline": "两周内",
        "contact": "studio@example.test",
        "consent": True,
        "website": "",
    }


class LeadPayloadTests(unittest.TestCase):
    def test_explicit_consent_and_exact_schema_are_required(self):
        payload = valid_payload()
        payload["consent"] = False
        with self.assertRaises(LeadValidationError):
            validate_lead_payload(payload)
        payload = valid_payload()
        payload["unexpected"] = "ignore"
        with self.assertRaises(LeadValidationError):
            validate_lead_payload(payload)

    def test_honeypot_and_unknown_choices_are_rejected(self):
        payload = valid_payload()
        payload["website"] = "spam"
        with self.assertRaises(LeadValidationError):
            validate_lead_payload(payload)
        payload = valid_payload()
        payload["service"] = "Unknown service"
        with self.assertRaises(LeadValidationError):
            validate_lead_payload(payload)

    def test_control_characters_and_invalid_submission_id_are_rejected(self):
        payload = valid_payload()
        payload["brief"] = "hello\x00world"
        with self.assertRaises(LeadValidationError):
            validate_lead_payload(payload)
        payload = valid_payload()
        payload["submission_id"] = "not-a-uuid"
        with self.assertRaises(LeadValidationError):
            validate_lead_payload(payload)

    def test_sqlite_store_is_idempotent_and_returns_only_a_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LeadStore(Path(temp) / "leads.sqlite3")
            first = store.submit(valid_payload())
            second = store.submit(valid_payload())
            self.assertFalse(first["duplicate"])
            self.assertTrue(second["duplicate"])
            self.assertEqual(first["lead_reference"], second["lead_reference"])
            self.assertEqual(store.count(), 1)
            self.assertRegex(first["lead_reference"], r"^MUSE-\d{8}-[A-F0-9]{8}$")
            self.assertNotIn("contact", first)
            self.assertNotIn("brief", first)

    def test_retention_prunes_expired_rows_on_next_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LeadStore(Path(temp) / "leads.sqlite3", retention_days=1)
            store.submit(valid_payload())
            connection = store._connect()
            try:
                connection.execute(
                    "UPDATE leads SET retention_until = '2000-01-01T00:00:00+00:00'"
                )
                connection.commit()
            finally:
                connection.close()
            payload = valid_payload("123e4567-e89b-12d3-a456-426614174001")
            store.submit(payload)
            self.assertEqual(store.count(), 1)


    def test_lead_management_keeps_list_redacted_but_show_explicitly_includes_contact(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LeadStore(Path(temp) / "leads.sqlite3")
            submitted = store.submit(valid_payload())
            listing = store.list_leads()
            self.assertEqual(len(listing), 1)
            self.assertNotIn("contact", listing[0])
            self.assertNotIn("brief", listing[0])
            detail = store.get_lead(submitted["lead_reference"])
            self.assertEqual(detail["contact"], "studio@example.test")
            self.assertIn("brief", detail)

    def test_csv_export_is_new_file_and_neutralizes_spreadsheet_formulas(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = LeadStore(root / "leads.sqlite3")
            payload = valid_payload()
            payload["brief"] = '=HYPERLINK("https://example.test","open")'
            store.submit(payload)
            target = export_leads_csv(store.export_rows(), root / "exports" / "leads.csv")
            content = target.read_text(encoding="utf-8-sig")
            self.assertIn("'=HYPERLINK", content)
            with self.assertRaises(FileExistsError):
                export_leads_csv(store.export_rows(), target)


class LeadHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        static_root = root / "static"
        static_root.mkdir()
        (static_root / "showcase.html").write_text("<h1>Muse</h1>", encoding="utf-8")
        self.store = LeadStore(root / "leads.sqlite3")
        self.limiter = LeadRateLimiter(max_requests=5, window_seconds=60)
        handler = create_request_handler(self.store, static_root, rate_limiter=self.limiter)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def post(self, payload, *, origin=None, content_type="application/json"):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection.request(
            "POST", "/api/leads", body=body,
            headers={
                "Origin": self.origin if origin is None else origin,
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        data = response.read()
        result = json.loads(data.decode("utf-8")) if data else {}
        connection.close()
        return response.status, result

    def test_same_origin_submission_is_saved_and_only_returns_reference(self):
        status, result = self.post(valid_payload())
        self.assertEqual(status, 201)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["retention_days"], 90)
        self.assertEqual(self.store.count(), 1)
        self.assertNotIn("contact", result)
        self.assertNotIn("brief", result)

    def test_duplicate_retry_returns_the_same_reference(self):
        first_status, first = self.post(valid_payload())
        second_status, second = self.post(valid_payload())
        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 200)
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["lead_reference"], second["lead_reference"])
        self.assertEqual(self.store.count(), 1)

    def test_cross_origin_and_invalid_content_type_are_blocked(self):
        status, result = self.post(valid_payload(), origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertEqual(result["status"], "origin_rejected")
        status, result = self.post(valid_payload(), content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(result["status"], "json_required")
        self.assertEqual(self.store.count(), 0)

    def test_oversized_and_malformed_payloads_are_blocked(self):
        status, result = self.post(b"x" * 8193)
        self.assertEqual(status, 413)
        self.assertEqual(result["status"], "request_too_large")
        status, result = self.post(b"{broken")
        self.assertEqual(status, 400)
        self.assertEqual(result["status"], "invalid_submission")
        self.assertEqual(self.store.count(), 0)

    def test_rate_limiter_blocks_excess_requests(self):
        self.limiter.max_requests = 1
        first = self.post(valid_payload())
        second = self.post(valid_payload("123e4567-e89b-12d3-a456-426614174002"))
        self.assertEqual(first[0], 201)
        self.assertEqual(second[0], 429)
        self.assertEqual(self.store.count(), 1)


if __name__ == "__main__":
    unittest.main()
