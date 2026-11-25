"""Independent, OS-account-isolated Ed25519 signer for human-reviewed video artifacts.

Run this module as a dedicated Reviewer service account, never inside the Worker or
Runtime process. The service reads the append-only human visual-review ledger,
re-verifies the artifact and approval bindings, then appends one signed review event.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from muse.task_queue import TaskQueue


class ReviewerSignerError(RuntimeError):
    """The independent Reviewer signer cannot establish its trust boundary."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _safe_identifier(value: Any, *, maximum: int = 128) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= maximum and re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is not None


class VideoReviewerSigner:
    """Dedicated-account signer; private key material never enters Worker/Runtime."""

    def __init__(
        self, workspace: str | Path, *, environ: Mapping[str, str] | None = None,
        identity_check: Callable[[Path, Mapping[str, str]], None] | None = None,
    ) -> None:
        supplied = Path(workspace).expanduser()
        if supplied.is_symlink():
            raise ReviewerSignerError("Reviewer workspace cannot be a symlink")
        self.workspace = supplied.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ReviewerSignerError("Reviewer workspace must be an existing directory")
        self.environ = dict(os.environ if environ is None else environ)
        self.key_path = Path(self._required_env("MUSE_VIDEO_REVIEWER_PRIVATE_KEY_FILE"))
        if not self.key_path.is_absolute() or self.key_path.is_symlink() or not self.key_path.is_file():
            raise ReviewerSignerError("Reviewer private key must be an absolute regular non-symlink file")
        if self.key_path.stat().st_size > 16 * 1024:
            raise ReviewerSignerError("Reviewer private key file exceeds 16 KiB")
        if identity_check is None:
            self._assert_dedicated_service_identity(self.key_path, self.environ)
        else:
            identity_check(self.key_path, self.environ)
        raw = self.key_path.read_bytes()
        expected_file_hash = self._required_env("MUSE_VIDEO_REVIEWER_PRIVATE_KEY_SHA256")
        if re.fullmatch(r"[0-9a-f]{64}", expected_file_hash) is None:
            raise ReviewerSignerError("Reviewer private-key SHA-256 pin is malformed")
        if hashlib.sha256(raw).hexdigest() != expected_file_hash:
            raise ReviewerSignerError("Reviewer private-key file does not match its configured SHA-256 pin")
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            private_key = serialization.load_pem_private_key(raw, password=None)
        except (ImportError, ValueError, TypeError) as exc:
            raise ReviewerSignerError("Reviewer private key must be a valid unencrypted Ed25519 PEM key") from exc
        if not isinstance(private_key, Ed25519PrivateKey):
            raise ReviewerSignerError("Reviewer signer accepts Ed25519 private keys only")
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.key_fingerprint = hashlib.sha256(public_key).hexdigest()
        if self.key_fingerprint != self._required_env("MUSE_VIDEO_REVIEWER_PUBLIC_KEY_SHA256"):
            raise ReviewerSignerError("Reviewer private key does not match the explicitly pinned public-key fingerprint")
        self.private_key = private_key
        self.public_key = public_key
        self.key_id = self._required_env("MUSE_VIDEO_REVIEWER_KEY_ID")
        self.reviewer_id = self._required_env("MUSE_VIDEO_REVIEWER_ID")
        if not _safe_identifier(self.key_id) or not _safe_identifier(self.reviewer_id, maximum=120):
            raise ReviewerSignerError("Reviewer key_id or reviewer_id is malformed")

    def _required_env(self, name: str) -> str:
        value = self.environ.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ReviewerSignerError(f"required Reviewer signer setting is missing: {name}")
        return value.strip()

    @staticmethod
    def _assert_dedicated_service_identity(key_path: Path, environ: Mapping[str, str]) -> None:
        if os.name == "nt":
            expected_sid = environ.get("MUSE_VIDEO_REVIEWER_SIGNER_SERVICE_SID", "").strip()
            worker_sid = environ.get("MUSE_VIDEO_WORKER_SERVICE_SID", "").strip()
            if (not expected_sid.startswith("S-1-") or not worker_sid.startswith("S-1-")
                    or expected_sid == worker_sid
                    or expected_sid in {"S-1-5-18", "S-1-5-32-544"}
                    or worker_sid in {"S-1-5-18", "S-1-5-32-544"}):
                raise ReviewerSignerError("distinct Reviewer and Worker Windows service SIDs must be configured")
            identity = subprocess.run(
                ["whoami.exe", "/user", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            match = re.search(r"S-1-(?:[0-9]+-)+[0-9]+", identity.stdout or "")
            if identity.returncode != 0 or match is None or match.group(0) != expected_sid:
                raise ReviewerSignerError("signer process is not running under the configured Reviewer service SID")
            script = (
                "$acl=Get-Acl -LiteralPath $env:MUSE_REVIEWER_KEY_PATH; "
                "$acl.Access | ForEach-Object { "
                "$sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; "
                "[pscustomobject]@{sid=$sid; inherited=$_.IsInherited; access=$_.AccessControlType.ToString()} } | ConvertTo-Json -Compress"
            )
            env = dict(os.environ)
            env["MUSE_REVIEWER_KEY_PATH"] = str(key_path)
            acl = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=15, check=False, env=env,
            )
            if acl.returncode != 0:
                raise ReviewerSignerError("could not verify Reviewer private-key Windows ACL")
            try:
                entries = json.loads(acl.stdout)
            except json.JSONDecodeError as exc:
                raise ReviewerSignerError("Reviewer private-key Windows ACL response is invalid") from exc
            if isinstance(entries, dict):
                entries = [entries]
            allowed = {expected_sid, "S-1-5-18", "S-1-5-32-544"}
            if (not isinstance(entries, list) or not entries
                    or any(not isinstance(item, dict) or item.get("sid") not in allowed
                           or item.get("inherited") is not False or item.get("access") != "Allow"
                           for item in entries)
                    or not any(item.get("sid") == expected_sid for item in entries)):
                raise ReviewerSignerError("Reviewer private-key ACL must be explicit and limited to Reviewer, SYSTEM and Administrators")
            return
        if os.name == "posix":
            try:
                reviewer_uid = int(environ["MUSE_VIDEO_REVIEWER_SIGNER_SERVICE_UID"])
                worker_uid = int(environ["MUSE_VIDEO_WORKER_SERVICE_UID"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ReviewerSignerError("distinct Reviewer and Worker service UIDs must be configured") from exc
            if reviewer_uid == worker_uid or reviewer_uid == 0 or worker_uid == 0 or os.geteuid() != reviewer_uid:
                raise ReviewerSignerError("signer process is not running under its dedicated Reviewer service UID")
            info = key_path.stat()
            if (info.st_uid != reviewer_uid or stat.S_IMODE(info.st_mode) & 0o077
                    or not stat.S_IMODE(info.st_mode) & 0o400):
                raise ReviewerSignerError("Reviewer private key must be Reviewer-owned and inaccessible to group/other users")
            return
        raise ReviewerSignerError("unsupported operating system for isolated Reviewer signing")

    def register_public_key(self, output_path: str | Path | None = None) -> dict[str, str]:
        """Write an explicit public-key registration file and return its runtime pin."""
        configured = output_path or self.environ.get("MUSE_VIDEO_REVIEWER_TRUST_STORE_OUTPUT")
        if not isinstance(configured, str) or not configured.strip():
            raise ReviewerSignerError("MUSE_VIDEO_REVIEWER_TRUST_STORE_OUTPUT is required for key registration")
        path = Path(configured)
        if not path.is_absolute() or path.is_symlink() or path.exists():
            raise ReviewerSignerError("Reviewer trust-store output must be an absolute new non-symlink path")
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema": "muse-video-reviewer-trust-store-v1",
            "keys": [{
                "key_id": self.key_id,
                "reviewer_id": self.reviewer_id,
                "public_key_hex": self.public_key.hex(),
                "fingerprint_sha256": self.key_fingerprint,
                "status": "trusted",
            }],
        }
        raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.is_symlink() or path.exists():
                raise ReviewerSignerError("Reviewer trust-store destination changed during registration")
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "trust_store_path": str(path),
            "trust_store_sha256": hashlib.sha256(raw).hexdigest(),
            "key_id": self.key_id,
            "reviewer_id": self.reviewer_id,
            "public_key_fingerprint_sha256": self.key_fingerprint,
        }

    def sign_pending_ticket(self, execution_task_id: str) -> dict[str, Any]:
        """Sign one exact artifact only after its bound human visual-review record passes."""
        if not _safe_identifier(execution_task_id):
            raise ReviewerSignerError("execution_task_id is malformed")
        task_database = self.workspace / "data" / "tasks" / "muse.sqlite3"
        if task_database.is_symlink() or not task_database.is_file():
            raise ReviewerSignerError("TaskQueue database is unavailable or unsafe")
        from muse.artifact_evidence import LocalArtifactEvidenceVerifier
        from muse.unified_runtime import MuseRuntime
        from muse.video_human_review import VisualReviewLedger, verify_visual_review_evidence

        with TaskQueue(task_database) as queue:
            ticket = queue.get(execution_task_id)
            if not isinstance(ticket, dict) or ticket.get("status") != "running":
                raise ReviewerSignerError("Reviewer accepts only a currently running execution Ticket")
            try:
                ticket_expiry = datetime.fromisoformat(str(ticket.get("lease_expires_at", "")).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ReviewerSignerError("execution Ticket lease expiry is malformed") from exc
            if ticket_expiry.tzinfo is None or ticket_expiry <= datetime.now(timezone.utc):
                raise ReviewerSignerError("execution Ticket lease expired before Reviewer signing")
            payload = ticket.get("payload")
            if not isinstance(payload, dict) or payload.get("kind") != "video_pipeline":
                raise ReviewerSignerError("Reviewer accepts only video_pipeline execution Tickets")
            context = MuseRuntime._validate_video_pipeline_execution_ticket(
                queue, {**ticket, "status": "queued"},
            )
            plan = context["commander_plan"]
            if plan.source != "model":
                raise ReviewerSignerError("safe_fallback Commander plans cannot receive Reviewer signatures")
            worker_id = ticket.get("lease_owner")
            if not _safe_identifier(worker_id, maximum=64) or worker_id == self.reviewer_id:
                raise ReviewerSignerError("Reviewer must be distinct from the exact leased Worker")
            events = queue.events(execution_task_id, limit=500)
            artifacts = [event for event in events if event.get("event_type") == "video_pipeline.artifacts_verified"]
            reviews = [event for event in events if event.get("event_type") == "video_pipeline.review_approved"]
            if len(artifacts) != 1 or reviews:
                raise ReviewerSignerError("signing requires exactly one verified artifact event and no prior review event")
            artifact_event = artifacts[0]
            artifact = artifact_event.get("payload")
            required = {
                "execution_task_id", "attempt_id", "initiative_id", "version_id", "commander_plan_id",
                "dag_sha256", "approval_task_id", "approval_event_id", "approved_by",
                "worker_id", "manifest_sha256", "manifest_relative_path", "manifest_bytes",
                "mp4_sha256", "package_sha256",
            }
            if (artifact_event.get("actor") != worker_id or not isinstance(artifact, dict)
                    or set(artifact) != required or artifact.get("execution_task_id") != execution_task_id
                    or artifact.get("worker_id") != worker_id):
                raise ReviewerSignerError("artifact event is not the exact verified artifact from the leased Worker")
            for field in (
                "attempt_id", "initiative_id", "commander_plan_id", "dag_sha256",
                "approval_task_id", "approval_event_id", "approved_by",
            ):
                if artifact.get(field) != payload.get(field):
                    raise ReviewerSignerError(f"artifact event {field} differs from the persisted execution Ticket")
            if (artifact.get("commander_plan_id") != plan.plan_id
                    or artifact.get("dag_sha256") != payload.get("dag_sha256")):
                raise ReviewerSignerError("artifact event Commander DAG binding does not match the approved plan")
            bound = [event for event in queue.workflow_events(execution_task_id)
                     if event.get("event_type") == "workflow.bound"]
            commander_bound = [event for event in events
                               if event.get("event_type") == "commander.video_pipeline_bound"]
            step_ids = [step.step_id for step in plan.steps]
            if (len(bound) != 1 or bound[0].get("actor") != "commander"
                    or bound[0].get("payload", {}).get("workflow_id") != plan.plan_id
                    or bound[0].get("payload", {}).get("commander_plan_id") != plan.plan_id
                    or bound[0].get("payload", {}).get("plan_source") != "model"
                    or bound[0].get("payload", {}).get("step_ids") != step_ids
                    or bound[0].get("payload", {}).get("parallel_waves") != [list(w) for w in plan.parallel_waves]
                    or len(commander_bound) != 1 or commander_bound[0].get("actor") != "commander"):
                raise ReviewerSignerError("execution Ticket has no unique exact Commander workflow binding")
            commander_payload = commander_bound[0].get("payload")
            expected_commander = {
                "plan_id": plan.plan_id, "plan_source": "model", "step_ids": step_ids,
                "approval_task_id": payload["approval_task_id"],
                "approval_event_id": payload["approval_event_id"],
                "attempt_id": payload["attempt_id"], "initiative_id": payload["initiative_id"],
            }
            if commander_payload != expected_commander:
                raise ReviewerSignerError("Commander workflow binding event differs from the persisted approval and DAG")

            relative_path = artifact.get("manifest_relative_path")
            manifest_sha256 = artifact.get("manifest_sha256")
            manifest_bytes = artifact.get("manifest_bytes")
            if (not isinstance(relative_path, str) or not relative_path.endswith("/manifest.json")
                    or not isinstance(manifest_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
                    or isinstance(manifest_bytes, bool) or not isinstance(manifest_bytes, int) or manifest_bytes < 1):
                raise ReviewerSignerError("artifact manifest path, digest or byte count is malformed")
            verifier = LocalArtifactEvidenceVerifier(self.workspace)
            verified_manifest = verifier.verify(relative_path, manifest_sha256, expected_bytes=manifest_bytes)
            product_dir = (self.workspace / Path(relative_path)).parent
            video_relative = (product_dir / "video.mp4").relative_to(self.workspace).as_posix()
            package_relative = (product_dir / "product.zip").relative_to(self.workspace).as_posix()
            verifier.verify(video_relative, artifact["mp4_sha256"])
            verifier.verify(package_relative, artifact["package_sha256"])
            initiative_id, version_id = payload["initiative_id"], artifact.get("version_id")
            if not isinstance(version_id, str) or product_dir.name != version_id or product_dir.parent.name != initiative_id:
                raise ReviewerSignerError("artifact manifest directory is not bound to the approved Initiative and version")
            video_path = product_dir / "video.mp4"
            visual_evidence = verify_visual_review_evidence(
                product_dir, initiative_id, version_id, video_path,
            )
            if (visual_evidence.get("video_sha256") != artifact.get("mp4_sha256")
                    or not isinstance(visual_evidence.get("release_manifest_sha256"), str)):
                raise ReviewerSignerError("independent visual evidence does not match the Worker artifact digest")
            ledger_path = self.workspace / "data" / "reviews" / "visual-content-reviews.sqlite3"
            if ledger_path.is_symlink() or not ledger_path.is_file():
                raise ReviewerSignerError("append-only human visual-review ledger is unavailable")
            with VisualReviewLedger(ledger_path, workspace_root=self.workspace) as ledger:
                records = ledger.list_reviews(initiative_id, version_id, limit=100)
            if not records:
                raise ReviewerSignerError("no human visual-review record exists for this artifact")
            review = records[-1]
            reviewer = review.get("reviewer")
            subject = review.get("subject")
            checklist = review.get("checklist")
            if (review.get("decision") != "approved" or not isinstance(reviewer, dict)
                    or reviewer.get("label") != self.reviewer_id
                    or not isinstance(reviewer.get("role"), str) or not reviewer["role"].strip()
                    or not isinstance(subject, dict)
                    or subject.get("initiative_id") != initiative_id
                    or subject.get("version_id") != version_id
                    or subject.get("video_sha256") != visual_evidence.get("video_sha256")
                    or subject.get("artifact_sha256") != visual_evidence.get("artifact_sha256")
                    or subject.get("release_manifest_sha256") != visual_evidence.get("release_manifest_sha256")
                    or not isinstance(checklist, dict) or set(checklist) != {
                        "subject_framing", "shot_continuity", "visual_anomalies",
                        "subtitle_visibility", "full_video_playback",
                    } or any(value is not True for value in checklist.values())
                    or review.get("findings") not in ([], None)
                    or not isinstance(review.get("rationale"), str)
                    or not 20 <= len(review["rationale"].strip()) <= 2000):
                raise ReviewerSignerError("latest human visual review is not a complete approval of this exact artifact")
            reviewed_at = datetime.fromisoformat(str(review.get("reviewed_at", "")).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            artifact_created = datetime.fromisoformat(str(artifact_event.get("created_at", "")).replace("Z", "+00:00"))
            if (reviewed_at.tzinfo is None or artifact_created.tzinfo is None
                    or reviewed_at < now - timedelta(hours=24) or reviewed_at > now + timedelta(minutes=5)
                    or reviewed_at < artifact_created):
                raise ReviewerSignerError("human visual-review record is stale, future-dated or predates artifact verification")
            if not isinstance(review.get("review_sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", review["review_sha256"]) is None:
                raise ReviewerSignerError("human visual-review ledger digest is malformed")
            approval_ticket = queue.get(payload["approval_task_id"])
            decisions = [event for event in queue.events(payload["approval_task_id"], limit=500)
                         if event.get("event_type") == "human.decision"]
            latest = decisions[-1] if decisions else None
            approval_payload = approval_ticket.get("payload") if isinstance(approval_ticket, dict) else None
            if (approval_ticket is None or approval_ticket.get("status") != "queued"
                    or not isinstance(approval_payload, dict) or approval_payload.get("_approved") is not True
                    or not isinstance(latest, dict) or latest.get("event_id") != payload["approval_event_id"]
                    or latest.get("actor") != payload["approved_by"]
                    or not isinstance(latest.get("payload"), dict)
                    or latest["payload"].get("decision") != "approved"):
                raise ReviewerSignerError("human approval was revoked or changed before Reviewer signing")
            expected_binding = {
                "execution_task_id": execution_task_id,
                "attempt_id": payload["attempt_id"],
                "initiative_id": initiative_id,
                "commander_plan_id": plan.plan_id,
                "dag_sha256": payload["dag_sha256"],
                "approval_task_id": payload["approval_task_id"],
                "approval_event_id": payload["approval_event_id"],
                "approved_by": payload["approved_by"],
                "worker_id": worker_id,
                "manifest_sha256": manifest_sha256,
                "reviewer_id": self.reviewer_id,
                "decision": "approved",
                "manifest_relative_path": relative_path,
                "manifest_bytes": manifest_bytes,
            }
            issued_at = int(time.time())
            attestation_payload = {
                "schema": "muse-video-reviewer-attestation-v1",
                "key_id": self.key_id,
                "reviewer_id": self.reviewer_id,
                "nonce": secrets.token_urlsafe(32),
                "issued_at": issued_at,
                "expires_at": issued_at + 300,
                "review_record_sha256": review["review_sha256"],
                "binding": expected_binding,
            }
            attestation = {
                **attestation_payload,
                "signature": self.private_key.sign(_canonical_json(attestation_payload)).hex(),
            }
            review_payload = {
                **artifact,
                "reviewer_id": self.reviewer_id,
                "decision": "approved",
                "reviewer_attestation": attestation,
                "review_record_id": review["record_id"],
                "review_record_sha256": review["review_sha256"],
                "reviewer_identity_assurance": "dedicated_os_account_signer",
                "manifest_evidence": verified_manifest,
            }
            queue._connection.execute("BEGIN IMMEDIATE")
            try:
                current = queue.get(execution_task_id)
                current_reviews = [event for event in queue.events(execution_task_id, limit=500)
                                   if event.get("event_type") == "video_pipeline.review_approved"]
                current_approval_events = [event for event in queue.events(payload["approval_task_id"], limit=500)
                                           if event.get("event_type") == "human.decision"]
                if (not isinstance(current, dict) or current.get("status") != "running"
                        or current.get("lease_owner") != worker_id or current_reviews
                        or not current_approval_events
                        or current_approval_events[-1].get("event_id") != payload["approval_event_id"]):
                    raise ReviewerSignerError("Ticket lease, review uniqueness or approval changed during signing")
                queue._record_event(
                    execution_task_id, "video_pipeline.review_approved", self.reviewer_id, review_payload,
                )
                queue._connection.commit()
            except Exception:
                queue._connection.rollback()
                raise
            review_event = queue.events(execution_task_id, limit=500)[-1]
            return {
                "status": "review_signed",
                "execution_task_id": execution_task_id,
                "review_event_id": review_event["event_id"],
                "review_record_id": review["record_id"],
                "review_record_sha256": review["review_sha256"],
                "reviewer_id": self.reviewer_id,
                "key_id": self.key_id,
                "attestation_sha256": hashlib.sha256(_canonical_json(attestation)).hexdigest(),
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the isolated Muse video Reviewer signer")
    parser.add_argument("--workspace", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("register-public-key", help="export the explicitly pinned Reviewer public-key trust store")
    sign = subparsers.add_parser("sign", help="verify the human visual-review ledger and sign one execution Ticket")
    sign.add_argument("--execution-task-id", required=True)
    args = parser.parse_args(argv)
    try:
        signer = VideoReviewerSigner(args.workspace)
        if args.command == "register-public-key":
            result = signer.register_public_key()
        else:
            result = signer.sign_pending_ticket(args.execution_task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "status": "blocked", "error_type": type(exc).__name__, "message": str(exc)[:500],
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
