"""Ed25519-backed, one-time video reviewer attestation verification."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any


def load_video_reviewer_public_keys_from_environment() -> dict[str, dict[str, Any]]:
    """Load explicitly pinned production Reviewer public keys; absent config means no trust."""
    path_value = os.environ.get("MUSE_VIDEO_REVIEWER_TRUST_STORE")
    digest_value = os.environ.get("MUSE_VIDEO_REVIEWER_TRUST_STORE_SHA256")
    if not path_value and not digest_value:
        return {}
    if not path_value or not digest_value:
        raise ValueError("Reviewer trust store path and SHA-256 pin must be configured together")
    if re.fullmatch(r"[0-9a-f]{64}", digest_value) is None:
        raise ValueError("Reviewer trust store SHA-256 pin is malformed")
    path = Path(path_value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Reviewer trust store must be an absolute regular non-symlink file")
    if path.stat().st_size > 64 * 1024:
        raise ValueError("Reviewer trust store exceeds 64 KiB")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest_value:
        raise ValueError("Reviewer trust store does not match its pinned SHA-256")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Reviewer trust store is not valid UTF-8 JSON") from exc
    if (not isinstance(document, dict) or set(document) != {"schema", "keys"}
            or document.get("schema") != "muse-video-reviewer-trust-store-v1"
            or not isinstance(document.get("keys"), list) or not 1 <= len(document["keys"]) <= 32):
        raise ValueError("Reviewer trust store schema or key count is invalid")
    trusted: dict[str, dict[str, Any]] = {}
    fingerprints: set[str] = set()
    required = {"key_id", "reviewer_id", "public_key_hex", "fingerprint_sha256", "status"}
    for record in document["keys"]:
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("Reviewer trust store key record schema is invalid")
        key_id, reviewer_id = record.get("key_id"), record.get("reviewer_id")
        public_hex, fingerprint = record.get("public_key_hex"), record.get("fingerprint_sha256")
        if (not isinstance(key_id, str) or not 1 <= len(key_id) <= 128
                or not isinstance(reviewer_id, str) or not 1 <= len(reviewer_id) <= 120
                or record.get("status") != "trusted"
                or not isinstance(public_hex, str) or re.fullmatch(r"[0-9a-f]{64}", public_hex) is None
                or not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None):
            raise ValueError("Reviewer trust store contains an invalid or untrusted key")
        public_key = bytes.fromhex(public_hex)
        actual_fingerprint = hashlib.sha256(public_key).hexdigest()
        if fingerprint != actual_fingerprint or key_id in trusted or fingerprint in fingerprints:
            raise ValueError("Reviewer public key fingerprint mismatch or duplicate registration")
        trusted[key_id] = {
            "reviewer_id": reviewer_id,
            "public_key": public_key,
            "fingerprint_sha256": fingerprint,
        }
        fingerprints.add(fingerprint)
    return trusted


def verify_video_reviewer_attestation_signature(
    attestation: Any, trusted_public_keys: Any, expected_binding: dict[str, Any], *, now: int | None = None,
) -> str:
    """Verify a Reviewer signature without consuming its one-time nonce."""
    from muse.unified_runtime import RuntimeBlocked

    required = {
        "schema", "key_id", "reviewer_id", "nonce", "issued_at", "expires_at",
        "review_record_sha256", "binding", "signature",
    }
    if not isinstance(attestation, dict) or set(attestation) != required:
        raise RuntimeBlocked("video_pipeline requires a complete signed Reviewer attestation")
    if attestation.get("schema") != "muse-video-reviewer-attestation-v1" or attestation.get("binding") != expected_binding:
        raise RuntimeBlocked("video_pipeline Reviewer signature is not bound to this exact approved artifact")
    key_id, reviewer_id = attestation.get("key_id"), attestation.get("reviewer_id")
    nonce, issued_at, expires_at = attestation.get("nonce"), attestation.get("issued_at"), attestation.get("expires_at")
    record_sha, signature_hex = attestation.get("review_record_sha256"), attestation.get("signature")
    current_time = int(time.time()) if now is None else now
    if (not isinstance(key_id, str) or not key_id.strip() or len(key_id) > 128
            or not isinstance(reviewer_id, str) or not reviewer_id.strip() or len(reviewer_id) > 120
            or not isinstance(nonce, str) or re.fullmatch(r"[A-Za-z0-9_-]{22,128}", nonce) is None
            or type(issued_at) is not int or type(expires_at) is not int
            or abs(current_time - issued_at) > 300 or expires_at < current_time - 300
            or expires_at > current_time + 300 or expires_at <= issued_at or expires_at - issued_at > 300
            or not isinstance(record_sha, str) or re.fullmatch(r"[0-9a-f]{64}", record_sha) is None
            or not isinstance(signature_hex, str) or re.fullmatch(r"[0-9a-f]{128}", signature_hex) is None):
        raise RuntimeBlocked("video_pipeline Reviewer attestation metadata is malformed, expired or unbound")
    if not isinstance(trusted_public_keys, dict):
        raise RuntimeBlocked("video_pipeline trusted Reviewer public-key registry is unavailable")
    key_record = trusted_public_keys.get(key_id)
    if not isinstance(key_record, dict) or key_record.get("reviewer_id") != reviewer_id:
        raise RuntimeBlocked("video_pipeline signing key is not trusted for the declared Reviewer principal")
    public_key = key_record.get("public_key")
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise RuntimeBlocked("video_pipeline has no valid trusted public key for the signed Reviewer")
    fingerprint = key_record.get("fingerprint_sha256")
    if fingerprint is not None and fingerprint != hashlib.sha256(public_key).hexdigest():
        raise RuntimeBlocked("video_pipeline trusted Reviewer public-key fingerprint mismatch")
    signed_payload = {
        key: attestation[key] for key in (
            "schema", "key_id", "reviewer_id", "nonce", "issued_at", "expires_at",
            "review_record_sha256", "binding",
        )
    }
    signed_bytes = json.dumps(
        signed_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_key).verify(bytes.fromhex(signature_hex), signed_bytes)
    except ImportError as exc:
        raise RuntimeBlocked("video_pipeline Ed25519 verification dependency is unavailable") from exc
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise RuntimeBlocked("video_pipeline Reviewer signature verification failed") from exc
    attestation_bytes = json.dumps(
        attestation, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(attestation_bytes).hexdigest()


def verify_and_consume_video_reviewer_attestation(
    runtime: Any, *, task_id: str, evidence: dict[str, Any], expected: dict[str, Any],
    reviewer_id: str, review_payload: dict[str, Any], relative_path: str,
    manifest_bytes: int, manifest_sha256: str,
) -> str:
    """Verify a trusted reviewer signature and atomically consume its nonce.

    The runtime's public-key map must be provisioned by a trusted composition
    root. No key means fail-closed. Generic TaskQueue actor labels are never
    treated as authenticated reviewer identities.
    """
    from muse.unified_runtime import RuntimeBlocked

    attestation = evidence.get("reviewer_attestation")
    required = {"schema", "key_id", "reviewer_id", "nonce", "issued_at", "expires_at", "review_record_sha256", "binding", "signature"}
    if not isinstance(attestation, dict) or set(attestation) != required:
        raise RuntimeBlocked("video_pipeline requires a signed reviewer attestation")
    if review_payload.get("reviewer_attestation") != attestation:
        raise RuntimeBlocked("video_pipeline review event attestation does not match completion evidence")
    if (review_payload.get("reviewer_id") != reviewer_id or review_payload.get("decision") != "approved"
            or review_payload.get("review_record_sha256") != attestation.get("review_record_sha256")):
        raise RuntimeBlocked("video_pipeline review event is not an approval by the signed Reviewer")
    if reviewer_id == expected.get("worker_id") or attestation.get("reviewer_id") != reviewer_id:
        raise RuntimeBlocked("video_pipeline Reviewer must be distinct and match the signed principal")

    expected_binding = {
        **expected,
        "manifest_sha256": manifest_sha256,
        "reviewer_id": reviewer_id,
        "decision": "approved",
        "manifest_relative_path": relative_path,
        "manifest_bytes": manifest_bytes,
    }
    if attestation.get("schema") != "muse-video-reviewer-attestation-v1" or attestation.get("binding") != expected_binding:
        raise RuntimeBlocked("video_pipeline reviewer attestation is not bound to this approved artifact and Ticket")

    attestation_sha256 = verify_video_reviewer_attestation_signature(
        attestation, getattr(runtime, "video_reviewer_public_keys", {}), expected_binding,
    )
    nonce = attestation["nonce"]
    nonce_sha256 = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    connection = sqlite3.connect(runtime.database, timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS muse_video_reviewer_attestation_nonces (
                nonce_sha256 TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                attestation_sha256 TEXT NOT NULL,
                consumed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO muse_video_reviewer_attestation_nonces
                   (nonce_sha256, task_id, attestation_sha256) VALUES (?, ?, ?)""",
                (nonce_sha256, task_id, attestation_sha256),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise RuntimeBlocked("video_pipeline reviewer attestation nonce has already been consumed") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise RuntimeBlocked("video_pipeline reviewer attestation replay guard is unavailable") from exc
    finally:
        connection.close()
    return attestation_sha256
