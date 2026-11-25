"""Trunk B: cryptographic independent-review gate wired into Worker -> TaskQueue completion (Omega B392)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.reviewer_attestation import (
    ReviewAttestationError,
    ReviewAttestationVerifier,
    build_trust_record,
    canonical_json,
    create_attestation,
    parse_trust_store,
    public_key_from_seed,
    render_trust_store,
    sha256_hex,
)
from muse.signed_review import SignedReviewError, export_review_bundle, main as signed_review_main, submit_attestation
from muse.task_queue import TaskQueue
from muse.worker import run_once

REVIEWER_SEED = bytes(range(40, 72))      # labelled test seeds only (gate G9)
SANDBOX_SEED = bytes(range(80, 112))
SELF_SEED = bytes(range(120, 152))
TEST_LOG = "Ran 12 tests in 0.40s\n\nOK\n"


class SignedReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "tasks.sqlite3"
        self.receipts = self.root / "receipts.sqlite3"
        self.calls = 0
        records = [
            build_trust_record(key_id="rk-prod", reviewer_id="creator-reviewer",
                               public_key=public_key_from_seed(REVIEWER_SEED), purpose="production"),
            build_trust_record(key_id="rk-sandbox", reviewer_id="sandbox-reviewer",
                               public_key=public_key_from_seed(SANDBOX_SEED), purpose="ephemeral_test"),
            build_trust_record(key_id="rk-self", reviewer_id="muse-worker",
                               public_key=public_key_from_seed(SELF_SEED), purpose="production"),
        ]
        self.store_path = self.root / "trust.json"
        self.store_path.write_bytes(render_trust_store(records))
        self.store_sha = hashlib.sha256(self.store_path.read_bytes()).hexdigest()
        self.trusted = parse_trust_store(self.store_path.read_bytes())

    def tearDown(self):
        self.tmp.cleanup()

    def handler(self, payload):
        self.calls += 1
        return {"deliverable": payload.get("topic"), "words": 42}

    def _enqueue(self, *, evidence=TEST_LOG, kind="signed_build"):
        payload = {"kind": kind, "topic": "star map"}
        if evidence is not None:
            payload["_test_evidence"] = evidence
        with TaskQueue(self.db) as queue:
            return queue.enqueue("build", payload)["id"]

    def _run(self, task_id, kinds=frozenset({"signed_build"})):
        return run_once(self.db, {"signed_build": self.handler, "plain_build": self.handler},
                        approval_required_kinds=frozenset(), signed_review_required_kinds=kinds, task_id=task_id)

    def _binding(self, task_id):
        with TaskQueue(self.db) as queue:
            return queue.get(task_id)["payload"]["_signed_review"]["binding"]

    def _attest(self, binding, *, seed=REVIEWER_SEED, key_id="rk-prod", reviewer_id="creator-reviewer", verdict="approve"):
        return create_attestation(seed=seed, key_id=key_id, reviewer_id=reviewer_id, binding=binding, verdict=verdict)

    def _complete(self, task_id, attestation, *, require_production=True, receipts=None):
        with TaskQueue(self.db) as queue, ReviewAttestationVerifier(
                self.trusted, receipts or self.receipts, require_production=require_production) as verifier:
            return queue.complete_signed_review(task_id, attestation=attestation, verifier=verifier, actor="operator")

    def _status(self, task_id):
        with TaskQueue(self.db) as queue:
            return queue.get(task_id)["status"]

    def _event_types(self, task_id):
        with TaskQueue(self.db) as queue:
            return [event["event_type"] for event in queue.events(task_id, limit=100)]

    def test_worker_parks_result_instead_of_succeeding_and_binds_exact_hashes(self):
        task_id = self._enqueue()
        parked = self._run(task_id)
        self.assertEqual(parked["status"], "waiting_approval")
        review = parked["payload"]["_signed_review"]
        self.assertEqual(review["status"], "pending")
        result = parked["payload"]["_result"]
        self.assertEqual(review["binding"], {
            "ticket_id": task_id, "artifact_sha256": sha256_hex(canonical_json(result)),
            "test_evidence_sha256": sha256_hex(TEST_LOG.encode("utf-8")), "proposer_id": "muse-worker",
        })
        self.assertIsNone(parked["lease_owner"])
        self.assertIn("review.signature_requested", self._event_types(task_id))
        with TaskQueue(self.db) as queue:
            self.assertEqual([task["id"] for task in queue.pending_signed_reviews()], [task_id])

    def test_valid_production_attestation_completes_ticket_with_receipt_readback(self):
        task_id = self._enqueue()
        self._run(task_id)
        task = self._complete(task_id, self._attest(self._binding(task_id)))
        self.assertEqual(task["status"], "succeeded")
        review = task["payload"]["_signed_review"]
        self.assertEqual((review["status"], review["key_id"], review["key_purpose"]), ("attested", "rk-prod", "production"))
        self.assertIn("review.attested", self._event_types(task_id))
        with ReviewAttestationVerifier(self.trusted, self.receipts) as verifier:
            receipts = verifier.receipts(task_id)
        self.assertEqual([item["attestation_sha256"] for item in receipts], [review["attestation_sha256"]])
        self.assertEqual(self.calls, 1)

    def test_signed_reject_verdict_fails_ticket_with_classified_review_failure(self):
        task_id = self._enqueue()
        self._run(task_id)
        task = self._complete(task_id, self._attest(self._binding(task_id), verdict="reject"))
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["payload"]["_signed_review"]["status"], "rejected")
        with TaskQueue(self.db) as queue:
            failures = [event["payload"] for event in queue.events(task_id, limit=100)
                        if event["event_type"] == "failure.classified"]
        self.assertEqual(failures[-1]["error_code"], "review.rejected")

    def test_reapproval_cannot_bypass_signature_or_rerun_handler(self):
        task_id = self._enqueue()
        self._run(task_id)
        with TaskQueue(self.db) as queue:
            with self.assertRaisesRegex(PermissionError, "signed independent Reviewer"):
                queue.approve(task_id, approved_by="creator")
        self.assertEqual(self._status(task_id), "waiting_approval")
        self.assertIsNone(self._run(task_id))  # nothing claimable
        self.assertEqual(self.calls, 1)

    def test_proposer_cannot_review_own_ticket_even_with_a_trusted_key(self):
        task_id = self._enqueue()
        self._run(task_id)
        binding = self._binding(task_id)
        with self.assertRaises(ReviewAttestationError):
            self._attest(binding, seed=SELF_SEED, key_id="rk-self", reviewer_id="muse-worker")
        self.assertEqual(self._status(task_id), "waiting_approval")

    def test_attestation_for_other_artifact_ticket_or_tampered_result_is_rejected(self):
        first, second = self._enqueue(), self._enqueue()
        self._run(first)
        self._run(second)
        signed_for_first = self._attest(self._binding(first))
        with self.assertRaisesRegex(ReviewAttestationError, "not bound"):
            self._complete(second, signed_for_first)
        forged = dict(self._binding(first), artifact_sha256=sha256_hex(b"different bytes"))
        with self.assertRaisesRegex(ReviewAttestationError, "not bound"):
            self._complete(first, self._attest(forged))
        self.assertEqual((self._status(first), self._status(second)), ("waiting_approval", "waiting_approval"))
        # tampering with the stored result after parking is detected at export time
        with sqlite3.connect(self.db) as connection:
            row = connection.execute("SELECT payload_json FROM tasks WHERE id=?", (first,)).fetchone()
            payload = json.loads(row[0])
            payload["_result"]["words"] = 9000
            connection.execute("UPDATE tasks SET payload_json=? WHERE id=?", (json.dumps(payload), first))
        with TaskQueue(self.db) as queue, self.assertRaisesRegex(SignedReviewError, "tampering"):
            export_review_bundle(queue, first, self.root / "bundle")

    def test_replay_and_double_completion_are_rejected(self):
        task_id = self._enqueue()
        self._run(task_id)
        attestation = self._attest(self._binding(task_id))
        self._complete(task_id, attestation)
        with self.assertRaisesRegex(ValueError, "not waiting for a signed review"):
            self._complete(task_id, attestation)
        self.assertEqual(self._status(task_id), "succeeded")

    def test_ephemeral_test_key_is_refused_in_production_and_allowed_only_in_sandbox_mode(self):
        task_id = self._enqueue()
        self._run(task_id)
        sandbox_attestation = self._attest(self._binding(task_id), seed=SANDBOX_SEED, key_id="rk-sandbox",
                                           reviewer_id="sandbox-reviewer")
        with self.assertRaisesRegex(ReviewAttestationError, "ephemeral test keys"):
            self._complete(task_id, sandbox_attestation)
        self.assertEqual(self._status(task_id), "waiting_approval")
        task = self._complete(task_id, sandbox_attestation, require_production=False,
                              receipts=self.root / "sandbox-receipts.sqlite3")
        self.assertEqual(task["payload"]["_signed_review"]["key_purpose"], "ephemeral_test")

    def test_missing_test_evidence_fails_closed_before_parking(self):
        task_id = self._enqueue(evidence=None)
        finished = self._run(task_id)
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["payload"]["_result"]["error_code"], "review.test_evidence_missing")
        self.assertNotIn("_signed_review", finished["payload"])

    def test_parking_is_lease_fenced(self):
        task_id = self._enqueue()
        with TaskQueue(self.db) as queue:
            queue.claim_next(worker_id="worker-a", lease_seconds=60, task_id=task_id)
            result = {"x": 1}
            binding = {"ticket_id": task_id, "artifact_sha256": sha256_hex(canonical_json(result)),
                       "test_evidence_sha256": sha256_hex(b"log"), "proposer_id": "muse-worker"}
            self.assertIsNone(queue.finish_pending_signed_review(task_id, worker_id="zombie", result=result, binding=binding))
            with self.assertRaisesRegex(ValueError, "does not match"):
                queue.finish_pending_signed_review(task_id, worker_id="worker-a", result={"x": 2}, binding=binding)
            queue._connection.execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task_id,))
            queue._connection.commit()
            self.assertIsNone(queue.finish_pending_signed_review(task_id, worker_id="worker-a", result=result, binding=binding))
            self.assertEqual(queue.get(task_id)["status"], "running")

    def test_completion_requires_real_verifier_and_separate_receipts_database(self):
        task_id = self._enqueue()
        self._run(task_id)

        class AlwaysApprove:
            path = "elsewhere.sqlite3"

            def check(self, *_args, **_kwargs):
                return {}

            def verify_and_consume(self, *_args, **_kwargs):
                return {"verdict": "approve", "key_id": "x", "reviewer_id": "x", "key_purpose": "production",
                        "attestation_sha256": "0" * 64}

        attestation = self._attest(self._binding(task_id))
        with TaskQueue(self.db) as queue:
            with self.assertRaisesRegex(PermissionError, "ReviewAttestationVerifier"):
                queue.complete_signed_review(task_id, attestation=attestation, verifier=AlwaysApprove(), actor="op")
        with self.assertRaisesRegex(ValueError, "separate"):
            self._complete(task_id, attestation, receipts=self.db)
        self.assertEqual(self._status(task_id), "waiting_approval")

    def test_cancel_discards_parked_ticket(self):
        task_id = self._enqueue()
        self._run(task_id)
        with TaskQueue(self.db) as queue:
            self.assertEqual(queue.cancel(task_id, cancelled_by="creator", reason="not needed")["status"], "cancelled")
            self.assertEqual(queue.pending_signed_reviews(), [])

    def test_kinds_without_signed_review_still_succeed_directly(self):
        task_id = self._enqueue(kind="plain_build")
        finished = self._run(task_id)
        self.assertEqual(finished["status"], "succeeded")
        self.assertNotIn("_signed_review", finished["payload"])

    def test_cli_export_sign_submit_round_trip(self):
        task_id = self._enqueue()
        self._run(task_id)
        out_dir = self.root / "bundle"
        with TaskQueue(self.db) as queue:
            bundle = export_review_bundle(queue, task_id, out_dir)
        artifact = Path(bundle["files"]["artifact.json"]).read_bytes()
        evidence = Path(bundle["files"]["test_evidence.txt"]).read_bytes()
        binding = bundle["binding"]
        self.assertEqual(sha256_hex(artifact), binding["artifact_sha256"])
        self.assertEqual(sha256_hex(evidence), binding["test_evidence_sha256"])
        with TaskQueue(self.db) as queue, self.assertRaises(FileExistsError):
            export_review_bundle(queue, task_id, out_dir)  # never overwrites what the Reviewer inspected
        attestation_path = out_dir / "attestation.json"
        attestation_path.write_text(json.dumps(self._attest(binding)), encoding="utf-8")
        code = signed_review_main(["--db", str(self.db), "submit", "--task-id", task_id,
                                   "--attestation", str(attestation_path), "--receipts-db", str(self.receipts),
                                   "--trust-store", str(self.store_path), "--trust-store-sha256", "0" * 64])
        self.assertEqual(code, 2)  # wrong pin fails closed
        self.assertEqual(self._status(task_id), "waiting_approval")
        code = signed_review_main(["--db", str(self.db), "submit", "--task-id", task_id,
                                   "--attestation", str(attestation_path), "--receipts-db", str(self.receipts),
                                   "--trust-store", str(self.store_path), "--trust-store-sha256", self.store_sha])
        self.assertEqual(code, 0)
        self.assertEqual(self._status(task_id), "succeeded")

    def test_submit_without_trust_store_fails_closed(self):
        task_id = self._enqueue()
        self._run(task_id)
        path = self.root / "a.json"
        path.write_text(json.dumps(self._attest(self._binding(task_id))), encoding="utf-8")
        with TaskQueue(self.db) as queue, self.assertRaisesRegex(SignedReviewError, "trust store"):
            submit_attestation(queue, task_id, path, trusted_keys={}, receipts_db=self.receipts, actor="op")
        self.assertEqual(self._status(task_id), "waiting_approval")


if __name__ == "__main__":
    unittest.main()
