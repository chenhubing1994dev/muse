"""B398: signed review desk (vault, one-click sign, API/UI contract)."""
from __future__ import annotations

import json
from contextlib import closing
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.review_desk import (
    ReviewDeskError,
    ReviewerVault,
    decide,
    detail,
    list_pending,
    open_seed,
    seal_seed,
)
from muse.reviewer_attestation import ReviewAttestationVerifier
from muse.task_queue import TaskQueue
from muse.worker import run_once

PASS = "correct horse battery"
REPO = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ReviewDeskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "Muse"
        self.workspace.mkdir()
        self.db = self.workspace / "tasks.sqlite3"
        self.receipts = self.workspace / "receipts.sqlite3"
        self.clock = Clock()
        self.vault = ReviewerVault(self.root / "keys", workspace=self.workspace, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def _parked_ticket(self):
        with TaskQueue(self.db) as queue:
            task_id = queue.enqueue("Cantonese kit", {"kind": "kit_build", "_test_evidence": "Ran 3 tests\nOK\n"})["id"]
        run_once(self.db, {"kit_build": lambda payload: {"files": 3}}, approval_required_kinds=frozenset(),
                 signed_review_required_kinds=frozenset({"kit_build"}), task_id=task_id)
        return task_id

    def _status(self, task_id):
        with TaskQueue(self.db) as queue:
            return queue.get(task_id)["status"]

    def test_seal_open_round_trip_and_tamper_detection(self):
        sealed = seal_seed(b"\x01" * 32, PASS)
        self.assertEqual(open_seed(sealed, PASS), b"\x01" * 32)
        with self.assertRaisesRegex(ReviewDeskError, "口令不正确"):
            open_seed(sealed, PASS + "x")
        tampered = dict(sealed, ciphertext="00" * 32)
        with self.assertRaises(ReviewDeskError):
            open_seed(tampered, PASS)

    def test_initialize_rules(self):
        inside = ReviewerVault(self.workspace / "keys", workspace=self.workspace)
        with self.assertRaisesRegex(ReviewDeskError, "项目目录"):
            inside.initialize(PASS, confirm_passphrase=PASS)
        with self.assertRaisesRegex(ReviewDeskError, "至少"):
            self.vault.initialize("short", confirm_passphrase="short")
        with self.assertRaisesRegex(ReviewDeskError, "不一致"):
            self.vault.initialize(PASS, confirm_passphrase=PASS + "!")
        status = self.vault.initialize(PASS, confirm_passphrase=PASS)
        self.assertTrue(status["initialized"] and status["trust_store_ok"])
        key_text = (self.root / "keys" / "reviewer-key.enc.json").read_text(encoding="utf-8")
        self.assertNotIn("seed_hex", key_text)  # never stored in clear
        with self.assertRaisesRegex(ReviewDeskError, "已存在"):
            self.vault.initialize(PASS, confirm_passphrase=PASS)

    def test_one_click_approve_completes_ticket_with_receipt(self):
        self.vault.initialize(PASS, confirm_passphrase=PASS)
        task_id = self._parked_ticket()
        pending = list_pending(self.db)
        self.assertEqual([item["task_id"] for item in pending], [task_id])
        item = detail(self.db, task_id)
        self.assertTrue(item["hashes_match"])
        self.assertIn('"files":3', item["artifact_preview"])
        result = decide(self.db, task_id, verdict="approve", passphrase=PASS, vault=self.vault,
                        receipts_db=self.receipts, note="看过了")
        self.assertEqual((result["status"], result["review_status"]), ("succeeded", "attested"))
        with ReviewAttestationVerifier(self.vault.trusted_keys(), self.receipts) as verifier:
            self.assertEqual(len(verifier.receipts(task_id)), 1)
        self.assertEqual(list_pending(self.db), [])

    def test_one_click_reject_fails_ticket(self):
        self.vault.initialize(PASS, confirm_passphrase=PASS)
        task_id = self._parked_ticket()
        result = decide(self.db, task_id, verdict="reject", passphrase=PASS, vault=self.vault, receipts_db=self.receipts)
        self.assertEqual(result["status"], "failed")

    def test_wrong_passphrase_leaves_ticket_and_locks_after_five_failures(self):
        self.vault.initialize(PASS, confirm_passphrase=PASS)
        task_id = self._parked_ticket()
        for _ in range(5):
            with self.assertRaisesRegex(ReviewDeskError, "口令不正确"):
                decide(self.db, task_id, verdict="approve", passphrase="wrong-pass-123", vault=self.vault,
                       receipts_db=self.receipts)
        with self.assertRaisesRegex(ReviewDeskError, "次数过多"):
            decide(self.db, task_id, verdict="approve", passphrase=PASS, vault=self.vault, receipts_db=self.receipts)
        self.assertEqual(self._status(task_id), "waiting_approval")
        self.clock.now += 61
        decide(self.db, task_id, verdict="approve", passphrase=PASS, vault=self.vault, receipts_db=self.receipts)
        self.assertEqual(self._status(task_id), "succeeded")

    def test_tampered_result_and_uninitialized_vault_are_refused(self):
        task_id = self._parked_ticket()
        with self.assertRaisesRegex(ReviewDeskError, "初始化"):
            decide(self.db, task_id, verdict="approve", passphrase=PASS, vault=self.vault, receipts_db=self.receipts)
        self.vault.initialize(PASS, confirm_passphrase=PASS)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            payload = json.loads(connection.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
            payload["_result"]["files"] = 999
            connection.execute("UPDATE tasks SET payload_json=? WHERE id=?", (json.dumps(payload), task_id))
        self.assertFalse(detail(self.db, task_id)["hashes_match"])
        with self.assertRaisesRegex(ReviewDeskError, "改动"):
            decide(self.db, task_id, verdict="approve", passphrase=PASS, vault=self.vault, receipts_db=self.receipts)
        self.assertEqual(self._status(task_id), "waiting_approval")

    def test_trust_store_edit_breaks_pin(self):
        self.vault.initialize(PASS, confirm_passphrase=PASS)
        trust = self.root / "keys" / "reviewer-trust-store.json"
        trust.write_text(trust.read_text(encoding="utf-8").replace("production", "production "), encoding="utf-8")
        with self.assertRaises(Exception):
            self.vault.trusted_keys()
        self.assertFalse(self.vault.status()["trust_store_ok"])

    def test_bad_verdict_and_unknown_ticket(self):
        self.vault.initialize(PASS, confirm_passphrase=PASS)
        with self.assertRaises(ReviewDeskError):
            decide(self.db, "0" * 32, verdict="approve", passphrase=PASS, vault=self.vault, receipts_db=self.receipts)
        with self.assertRaises(ReviewDeskError):
            decide(self.db, "0" * 32, verdict="maybe", passphrase=PASS, vault=self.vault, receipts_db=self.receipts)


class ReviewDeskContractTests(unittest.TestCase):
    """Static front-end/back-end closure: every id the JS touches exists and every API path is routed."""

    def test_ui_ids_api_paths_and_csp_compatibility(self):
        html = (REPO / "ui-prototype" / "review-desk.html").read_text(encoding="utf-8")
        script = (REPO / "ui-prototype" / "review-desk.js").read_text(encoding="utf-8")
        api = (REPO / "src" / "muse" / "status_api.py").read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        used = set(re.findall(r'\$\("([^"]+)"\)', script))
        self.assertFalse(used - ids, f"JS references missing element ids: {sorted(used - ids)}")
        self.assertNotIn("<script>", html)          # CSP script-src 'self'
        self.assertNotIn("style=", html)            # CSP style-src 'self'
        self.assertNotIn("innerHTML", script)       # untrusted data only via textContent
        for path in ("/api/v1/signed-reviews\"", "/api/v1/signed-reviews/[0-9a-f]{32}\"",
                     "/api/v1/signed-reviews/([0-9a-f]{32})/decide", "/api/v1/reviewer-key/initialize",
                     "/api/v1/reviewer-key/status"):
            self.assertIn(path, api)
        for fragment in ('"/signed-reviews"', '"/signed-reviews/"', '"/decide"', '"/reviewer-key/initialize"',
                         '"SIGN-REVIEW"', '"INIT-REVIEWER-KEY"'):
            self.assertIn(fragment, script)
        self.assertIn('"SIGN-REVIEW"', api)
        self.assertIn('"INIT-REVIEWER-KEY"', api)
        vite = (REPO / "ui-prototype" / "vite.config.js").read_text(encoding="utf-8")
        self.assertIn('"review-desk.html"', vite)


if __name__ == "__main__":
    unittest.main()
