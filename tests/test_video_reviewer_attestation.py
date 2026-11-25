"""Security regression tests for authenticated video reviewer attestations."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from muse.task_queue import TaskQueue
from muse.video_reviewer_attestation import load_video_reviewer_public_keys_from_environment
from muse.unified_runtime import MuseRuntime
from tests.test_unified_runtime import UnifiedRuntimeTests


class VideoReviewerAttestationTests(unittest.TestCase):
    def setUp(self):
        self.base = UnifiedRuntimeTests()
        self.base.setUp()

    def tearDown(self):
        self.base.tearDown()

    def test_valid_trusted_ed25519_attestation_allows_completion(self):
        execution = self.base._enqueue_video_pipeline_execution_ticket()

        class SignedReviewerWorker:
            def run_approved_ticket(inner_self, **kwargs):
                return self.base._video_pipeline_success_result(kwargs)
            def finalize_approved_ticket(inner_self, **kwargs):
                return {"status": "succeeded"}

        result = self.base.runtime.dispatch_video_pipeline_once(
            task_id=execution["id"], approved=True, video_worker=SignedReviewerWorker(),
        )
        self.assertEqual(result["status"], "succeeded")
        with TaskQueue(self.base.db) as queue:
            self.assertEqual(queue.get(execution["id"])["status"], "succeeded")

    def test_untrusted_or_unconfigured_reviewer_key_fails_closed(self):
        execution = self.base._enqueue_video_pipeline_execution_ticket()
        self.base.runtime.video_reviewer_public_keys = {}

        class SignedReviewerWorker:
            def run_approved_ticket(inner_self, **kwargs):
                return self.base._video_pipeline_success_result(kwargs)
            def finalize_approved_ticket(inner_self, **kwargs):
                return {"status": "succeeded"}

        result = self.base.runtime.dispatch_video_pipeline_once(
            task_id=execution["id"], approved=True, video_worker=SignedReviewerWorker(),
        )
        self.assertEqual(result["status"], "completion_evidence_rejected")
        with TaskQueue(self.base.db) as queue:
            self.assertEqual(queue.get(execution["id"])["status"], "failed")
            self.assertFalse(any(
                event["event_type"] == "video_pipeline.completed"
                for event in queue.events(execution["id"])
            ))

    def test_forged_reviewer_actor_without_valid_signature_fails_closed(self):
        execution = self.base._enqueue_video_pipeline_execution_ticket()

        class ForgingWorker:
            def run_approved_ticket(inner_self, **kwargs):
                result = self.base._video_pipeline_success_result(kwargs)
                evidence = result["completion_evidence"]
                evidence["reviewer_attestation"]["signature"] = "0" * 128
                with TaskQueue(self.base.db) as queue:
                    review = next(
                        event for event in queue.events(execution["id"])
                        if event["event_type"] == "video_pipeline.review_approved"
                    )
                    payload = dict(review["payload"])
                    payload["reviewer_attestation"] = dict(evidence["reviewer_attestation"])
                    queue._connection.execute(
                        "UPDATE task_events SET payload_json=? WHERE event_id=?",
                        (json.dumps(payload, ensure_ascii=False), review["event_id"]),
                    )
                    queue._connection.commit()
                return result

        result = self.base.runtime.dispatch_video_pipeline_once(
            task_id=execution["id"], approved=True, video_worker=ForgingWorker(),
        )
        self.assertEqual(result["status"], "completion_evidence_rejected")
        with TaskQueue(self.base.db) as queue:
            self.assertEqual(queue.get(execution["id"])["status"], "failed")
            self.assertFalse(any(
                event["event_type"] == "video_pipeline.completed"
                for event in queue.events(execution["id"])
            ))

    def test_reviewer_nonce_cannot_be_replayed_for_another_ticket(self):
        nonce = secrets.token_urlsafe(24)
        first = self.base._enqueue_video_pipeline_execution_ticket()
        second = self.base._enqueue_video_pipeline_execution_ticket()

        class ReusingNonceWorker:
            def run_approved_ticket(inner_self, **kwargs):
                return self.base._video_pipeline_success_result(kwargs, nonce=nonce)
            def finalize_approved_ticket(inner_self, **kwargs):
                return {"status": "succeeded"}

        first_result = self.base.runtime.dispatch_video_pipeline_once(
            task_id=first["id"], approved=True, video_worker=ReusingNonceWorker(),
        )
        second_result = self.base.runtime.dispatch_video_pipeline_once(
            task_id=second["id"], approved=True, video_worker=ReusingNonceWorker(),
        )
        self.assertEqual(first_result["status"], "succeeded")
        self.assertEqual(second_result["status"], "completion_evidence_rejected")
        with TaskQueue(self.base.db) as queue:
            self.assertEqual(queue.get(first["id"])["status"], "succeeded")
            self.assertEqual(queue.get(second["id"])["status"], "failed")


    def test_attestation_expiry_is_bounded_and_signed(self):
        for lifetime in (-1, 301):
            execution = self.base._enqueue_video_pipeline_execution_ticket()

            class ExpiringReviewerWorker:
                def run_approved_ticket(inner_self, **kwargs):
                    return self.base._video_pipeline_success_result(
                        kwargs, expires_in_seconds=lifetime,
                    )

            result = self.base.runtime.dispatch_video_pipeline_once(
                task_id=execution["id"], approved=True, video_worker=ExpiringReviewerWorker(),
            )
            self.assertEqual(result["status"], "completion_evidence_rejected")
            with TaskQueue(self.base.db) as queue:
                self.assertEqual(queue.get(execution["id"])["status"], "failed")
                self.assertFalse(any(
                    event["event_type"] == "video_pipeline.completed"
                    for event in queue.events(execution["id"])
                ))

    def test_production_trust_store_requires_pinned_digest_and_valid_fingerprint(self):
        private_key = self.base.video_reviewer_private_key
        from cryptography.hazmat.primitives import serialization
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        fingerprint = hashlib.sha256(public_key).hexdigest()
        document = {
            "schema": "muse-video-reviewer-trust-store-v1",
            "keys": [{
                "key_id": "production-reviewer-1",
                "reviewer_id": "independent-reviewer",
                "public_key_hex": public_key.hex(),
                "fingerprint_sha256": fingerprint,
                "status": "trusted",
            }],
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reviewer-trust.json"
            path.write_bytes(raw)
            env = {
                "MUSE_VIDEO_REVIEWER_TRUST_STORE": str(path.resolve()),
                "MUSE_VIDEO_REVIEWER_TRUST_STORE_SHA256": hashlib.sha256(raw).hexdigest(),
            }
            with patch.dict(os.environ, env, clear=True):
                trusted = load_video_reviewer_public_keys_from_environment()
                self.assertEqual(trusted["production-reviewer-1"]["reviewer_id"], "independent-reviewer")
                self.assertEqual(trusted["production-reviewer-1"]["public_key"], public_key)
                runtime = MuseRuntime(
                    Path(temporary) / "tasks" / "muse.sqlite3", workspace=temporary,
                    ruflo=self.base.runtime.ruflo, desktop=self.base.runtime.desktop,
                    video_reviewer_public_keys=None,
                )
                self.assertIn("production-reviewer-1", runtime.video_reviewer_public_keys)
            with patch.dict(os.environ, {**env, "MUSE_VIDEO_REVIEWER_TRUST_STORE_SHA256": "0" * 64}, clear=True):
                with self.assertRaisesRegex(ValueError, "pinned SHA-256"):
                    load_video_reviewer_public_keys_from_environment()

    def test_missing_production_trust_store_loads_no_trusted_keys(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_video_reviewer_public_keys_from_environment(), {})

if __name__ == "__main__":
    unittest.main()
