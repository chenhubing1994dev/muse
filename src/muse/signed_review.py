"""Operator CLI for the cryptographic independent-review gate on Commander tickets.

Flow (Trunk B):  approved ticket -> Worker runs handler -> result parked in
``waiting_approval`` with ``_signed_review.status = pending`` (it can NOT reach
``succeeded`` from the Worker) -> Reviewer inspects the exported bytes and signs
them with an Ed25519 key held outside Muse -> ``submit`` verifies the signature
against the pinned trust store and resolves the ticket (approve -> succeeded,
reject -> failed). Re-approving a parked ticket is refused by ``TaskQueue.approve``;
``muse-task cancel`` remains available to discard it.

  muse-signed-review list   --db data/tasks/muse.sqlite3
  muse-signed-review export --db ... --task-id T --out-dir review-T
  muse-reviewer-attest sign --ticket-id T --artifact review-T/artifact.json \\
      --test-evidence review-T/test_evidence.txt --proposer-id muse-worker \\
      --private-key E:/MuseKeys/rk.json --out review-T/attestation.json
  muse-signed-review submit --db ... --task-id T --attestation review-T/attestation.json \\
      --receipts-db data/reviews/attestation-receipts.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from muse.reviewer_attestation import (
    ReviewAttestationError,
    ReviewAttestationVerifier,
    canonical_json,
    load_trust_store,
    load_trust_store_from_environment,
    sha256_hex,
)
from muse.task_queue import TaskQueue

ARTIFACT_FILE = "artifact.json"
EVIDENCE_FILE = "test_evidence.txt"
BINDING_FILE = "binding.json"


class SignedReviewError(RuntimeError):
    """Operator-facing failure of the signed review workflow."""


def review_summary(task: dict[str, Any]) -> dict[str, Any]:
    review = task["payload"].get("_signed_review") or {}
    return {
        "task_id": task["id"], "title": task["title"], "kind": task["payload"].get("kind"),
        "status": task["status"], "review_status": review.get("status"),
        "binding": review.get("binding"), "requested_at": review.get("requested_at"),
    }


def export_review_bundle(queue: TaskQueue, task_id: str, out_dir: str | Path) -> dict[str, Any]:
    """Write the exact bytes the Reviewer must sign; refuses to overwrite and re-checks every hash."""
    task = queue.get(task_id)
    if task is None:
        raise SignedReviewError("unknown task id")
    review = task["payload"].get("_signed_review")
    if task["status"] != "waiting_approval" or not isinstance(review, dict) or review.get("status") != "pending":
        raise SignedReviewError("task has no pending signed review")
    binding = review["binding"]
    artifact_bytes = canonical_json(task["payload"].get("_result"))
    evidence = task["payload"].get("_test_evidence")
    if not isinstance(evidence, str):
        raise SignedReviewError("task test evidence is missing")
    evidence_bytes = evidence.encode("utf-8")
    if sha256_hex(artifact_bytes) != binding["artifact_sha256"]:
        raise SignedReviewError("stored result no longer matches the requested artifact SHA-256 (possible tampering)")
    if sha256_hex(evidence_bytes) != binding["test_evidence_sha256"]:
        raise SignedReviewError("stored test evidence no longer matches the requested SHA-256 (possible tampering)")
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, data in ((ARTIFACT_FILE, artifact_bytes), (EVIDENCE_FILE, evidence_bytes),
                       (BINDING_FILE, json.dumps(binding, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")):
        path = folder / name
        with path.open("xb") as handle:
            handle.write(data)
        written[name] = str(path)
    return {"task_id": task_id, "binding": binding, "files": written,
            "sign_command": (f"muse-reviewer-attest sign --ticket-id {binding['ticket_id']} "
                             f"--artifact \"{written[ARTIFACT_FILE]}\" --test-evidence \"{written[EVIDENCE_FILE]}\" "
                             f"--proposer-id {binding['proposer_id']} --private-key <离线私钥.json> "
                             f"--out \"{folder / 'attestation.json'}\"")}


def submit_attestation(queue: TaskQueue, task_id: str, attestation_path: str | Path, *, trusted_keys: dict,
                       receipts_db: str | Path, actor: str, allow_test_keys: bool = False) -> dict[str, Any]:
    raw = Path(attestation_path).read_bytes()
    if len(raw) > 64 * 1024:
        raise SignedReviewError("attestation file exceeds 64 KiB")
    try:
        attestation = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SignedReviewError("attestation file is not valid UTF-8 JSON") from exc
    if not trusted_keys:
        raise SignedReviewError("no Reviewer trust store configured")
    with ReviewAttestationVerifier(trusted_keys, receipts_db, require_production=not allow_test_keys) as verifier:
        task = queue.complete_signed_review(task_id, attestation=attestation, verifier=verifier, actor=actor)
    return review_summary(task) | {"signed_review": task["payload"].get("_signed_review")}


def _trusted_keys(args) -> dict:
    if args.trust_store or args.trust_store_sha256:
        if not (args.trust_store and args.trust_store_sha256):
            raise SignedReviewError("--trust-store and --trust-store-sha256 must be given together")
        return load_trust_store(args.trust_store, args.trust_store_sha256)
    return load_trust_store_from_environment()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse-signed-review", description="Ed25519 independent review of Commander tickets")
    parser.add_argument("--db", default="data/tasks/muse.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="tickets waiting for a signed Reviewer attestation")
    listing.add_argument("--limit", type=int, default=100)
    export = sub.add_parser("export", help="write artifact/test-evidence bytes for the Reviewer to inspect and sign")
    export.add_argument("--task-id", required=True)
    export.add_argument("--out-dir", required=True)
    submit = sub.add_parser("submit", help="verify an attestation and resolve the ticket")
    submit.add_argument("--task-id", required=True)
    submit.add_argument("--attestation", required=True)
    submit.add_argument("--receipts-db", default="data/reviews/attestation-receipts.sqlite3")
    submit.add_argument("--actor", default="commander-operator")
    submit.add_argument("--trust-store", help="absolute trust store path (default: MUSE_REVIEWER_TRUST_STORE)")
    submit.add_argument("--trust-store-sha256", help="pinned SHA-256 (default: MUSE_REVIEWER_TRUST_STORE_SHA256)")
    submit.add_argument("--allow-test-keys", action="store_true", help="sandbox drills only: accept ephemeral_test keys")
    args = parser.parse_args(argv)
    try:
        with TaskQueue(args.db) as queue:
            if args.command == "list":
                output: Any = [review_summary(task) for task in queue.pending_signed_reviews(limit=args.limit)]
            elif args.command == "export":
                output = export_review_bundle(queue, args.task_id, args.out_dir)
            else:
                output = submit_attestation(queue, args.task_id, args.attestation, trusted_keys=_trusted_keys(args),
                                            receipts_db=args.receipts_db, actor=args.actor,
                                            allow_test_keys=args.allow_test_keys)
    except (SignedReviewError, ReviewAttestationError, PermissionError, ValueError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
