"""Test-only Ed25519 reviewer attestation fixtures for video pipeline runtime tests."""
import hashlib
import json
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from muse.task_queue import TaskQueue


def make_test_reviewer_key():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return private_key, public_key


def build_video_pipeline_success_result(
    test_case, kwargs, *, reviewer_id="independent-reviewer", decision="approved",
    nonce=None, expires_in_seconds=300,
):
    binding = {
        "execution_task_id": kwargs["execution_task_id"],
        "attempt_id": "attempt-test",
        "initiative_id": "initiative-test",
        "commander_plan_id": kwargs["commander_plan"].plan_id,
        "dag_sha256": kwargs["dag_sha256"],
        "approval_task_id": kwargs["approval_task_id"],
        "approval_event_id": kwargs["approval_event_id"],
        "approved_by": kwargs["approved_by"],
        "worker_id": kwargs["worker_id"],
    }
    manifest_dir = test_case.tmp.name
    from pathlib import Path
    manifest_dir = Path(manifest_dir) / "video-artifacts"
    manifest_dir.mkdir(exist_ok=True)
    manifest_path = manifest_dir / "manifest.json"
    manifest_bytes = json.dumps(
        {"schema": "muse-video-manifest-v1", **binding}, sort_keys=True,
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    relative_path = "video-artifacts/manifest.json"
    artifact_payload = {
        **binding,
        "manifest_sha256": manifest_sha256,
        "manifest_relative_path": relative_path,
        "manifest_bytes": len(manifest_bytes),
    }
    reviewer_binding = {
        **binding,
        "manifest_sha256": manifest_sha256,
        "reviewer_id": reviewer_id,
        "decision": decision,
        "manifest_relative_path": relative_path,
        "manifest_bytes": len(manifest_bytes),
    }
    issued_at = int(time.time())
    attestation_payload = {
        "schema": "muse-video-reviewer-attestation-v1",
        "key_id": "test-reviewer-key",
        "reviewer_id": reviewer_id,
        "nonce": nonce or secrets.token_urlsafe(32),
        "issued_at": issued_at,
        "expires_at": issued_at + expires_in_seconds,
        "review_record_sha256": hashlib.sha256(b"test-review-record").hexdigest(),
        "binding": reviewer_binding,
    }
    signed_bytes = json.dumps(
        attestation_payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    reviewer_attestation = {
        **attestation_payload,
        "signature": test_case.video_reviewer_private_key.sign(signed_bytes).hex(),
    }
    review_payload = {
        **artifact_payload,
        "reviewer_id": reviewer_id,
        "decision": decision,
        "review_record_sha256": attestation_payload["review_record_sha256"],
        "reviewer_attestation": reviewer_attestation,
    }
    with TaskQueue(test_case.db) as queue:
        queue.record_event(
            kwargs["execution_task_id"], "video_pipeline.artifacts_verified",
            actor=kwargs["worker_id"], payload=artifact_payload,
        )
        review_event = queue.record_event(
            kwargs["execution_task_id"], "video_pipeline.review_approved",
            actor=reviewer_id, payload=review_payload,
        )
    completion_evidence = {
        **binding,
        "manifest_sha256": manifest_sha256,
        "reviewer_id": reviewer_id,
        "review_event_id": review_event["event_id"],
        "reviewer_attestation": reviewer_attestation,
    }
    return {"status": "succeeded", "completion_evidence": completion_evidence}
