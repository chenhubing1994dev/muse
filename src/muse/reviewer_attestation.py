"""Cryptographic independent-review attestations for Muse Commander tickets.

A Reviewer approval is only accepted when it is an Ed25519 signature, made by a
key registered in a SHA-256-pinned trust store, over a canonical binding of
``ticket_id + artifact_sha256 + test_evidence_sha256``. Actor strings alone never
count as review. The proposer can never review their own change, signatures
expire quickly, and every nonce is consumed exactly once in SQLite so replays
are rejected.

Backends: ``cryptography`` (preferred when installed) or a pure-Python RFC 8032
implementation (stdlib only; verified against the RFC 8032 test vectors). The
pure backend is not constant-time and is intended for verification and for
clearly labelled ``ephemeral_test`` keys; production key ceremonies should run
where ``cryptography`` is available (``scripts/provision_reviewer_keys.py``
reports which backend it used).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from muse.error_journal import record_suppressed_error

ATTESTATION_SCHEMA = "muse-reviewer-attestation-v1"
TRUST_STORE_SCHEMA = "muse-reviewer-trust-store-v1"
PRIVATE_KEY_SCHEMA = "muse-reviewer-private-key-v1"
KEY_PURPOSES = frozenset({"production", "ephemeral_test"})
KEY_STATUSES = frozenset({"trusted", "revoked"})
VERDICTS = frozenset({"approve", "reject"})
MAX_TTL_SECONDS = 900
CLOCK_SKEW_SECONDS = 120
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX128 = re.compile(r"[0-9a-f]{128}")
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{22,128}")


class ReviewAttestationError(ValueError):
    """Raised whenever a Reviewer attestation must fail closed."""


# --------------------------------------------------------------------------
# Ed25519 — pure-Python RFC 8032 reference arithmetic (stdlib only)
# --------------------------------------------------------------------------
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)
_IDENTITY = (0, 1, 1, 0)


def _point_add(p: tuple, q: tuple) -> tuple:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _point_mul(scalar: int, point: tuple) -> tuple:
    result = _IDENTITY
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _point_equal(p: tuple, q: tuple) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _compress(point: tuple) -> bytes:
    zinv = pow(point[2], _P - 2, _P)
    x, y = point[0] * zinv % _P, point[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes) -> tuple | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _expand_seed(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def pure_public_key(seed: bytes) -> bytes:
    _require_seed(seed)
    scalar, _ = _expand_seed(seed)
    return _compress(_point_mul(scalar, _G))


def pure_sign(seed: bytes, message: bytes) -> bytes:
    _require_seed(seed)
    scalar, prefix = _expand_seed(seed)
    public = _compress(_point_mul(scalar, _G))
    r = _sha512_int(prefix + message) % _L
    big_r = _compress(_point_mul(r, _G))
    h = _sha512_int(big_r + public + message) % _L
    s = (r + h * scalar) % _L
    return big_r + int.to_bytes(s, 32, "little")


def pure_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a_point = _decompress(public_key)
    r_point = _decompress(signature[:32])
    if a_point is None or r_point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = _sha512_int(signature[:32] + public_key + message) % _L
    return _point_equal(_point_mul(s, _G), _point_add(r_point, _point_mul(h, a_point)))


def _require_seed(seed: bytes) -> None:
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise ReviewAttestationError("Ed25519 private seed must be exactly 32 bytes")


def _cryptography_available() -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: F401
    except Exception as _muse_suppressed:
        record_suppressed_error("reviewer_attestation.py:172", _muse_suppressed)
        return False
    return True


def active_backend() -> str:
    """Return ``"cryptography"`` when available, else ``"pure_python_rfc8032"``."""
    forced = os.environ.get("MUSE_ED25519_BACKEND", "").strip()
    if forced == "pure":
        return "pure_python_rfc8032"
    return "cryptography" if _cryptography_available() else "pure_python_rfc8032"


def public_key_from_seed(seed: bytes, *, backend: str | None = None) -> bytes:
    _require_seed(seed)
    if (backend or active_backend()) == "cryptography":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.from_private_bytes(bytes(seed))
        return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pure_public_key(bytes(seed))


def sign_bytes(seed: bytes, message: bytes, *, backend: str | None = None) -> bytes:
    _require_seed(seed)
    if (backend or active_backend()) == "cryptography":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(bytes(seed)).sign(message)
    return pure_sign(bytes(seed), message)


def verify_bytes(public_key: bytes, message: bytes, signature: bytes, *, backend: str | None = None) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    if (backend or active_backend()) == "cryptography":
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        except (InvalidSignature, ValueError):
            return False
        return True
    return pure_verify(public_key, message, signature)


def generate_seed() -> bytes:
    return secrets.token_bytes(32)


# --------------------------------------------------------------------------
# Canonical binding and attestation documents
# --------------------------------------------------------------------------
def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_ident(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENT.fullmatch(value) is None:
        raise ReviewAttestationError(f"{name} must be a safe identifier (1-128 chars)")
    return value


def _check_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ReviewAttestationError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def build_review_binding(*, ticket_id: str, artifact_sha256: str, test_evidence_sha256: str,
                         proposer_id: str) -> dict[str, str]:
    """The exact facts a Reviewer signs: which ticket, which bytes, which test evidence, whose proposal."""
    return {
        "ticket_id": _check_ident(ticket_id, "ticket_id"),
        "artifact_sha256": _check_sha(artifact_sha256, "artifact_sha256"),
        "test_evidence_sha256": _check_sha(test_evidence_sha256, "test_evidence_sha256"),
        "proposer_id": _check_ident(proposer_id, "proposer_id"),
    }


def _signed_message(document: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in document.items() if key != "signature"}
    return ATTESTATION_SCHEMA.encode("ascii") + b"\n" + canonical_json(unsigned)


def create_attestation(*, seed: bytes, key_id: str, reviewer_id: str, binding: dict[str, str],
                       verdict: str = "approve", findings: list[str] | None = None,
                       issued_at: int | None = None, ttl_seconds: int = 600,
                       nonce: str | None = None) -> dict[str, Any]:
    """Create a detached-signature attestation document (Reviewer side)."""
    _check_ident(key_id, "key_id")
    _check_ident(reviewer_id, "reviewer_id")
    if verdict not in VERDICTS:
        raise ReviewAttestationError("verdict must be approve or reject")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise ReviewAttestationError(f"ttl_seconds must be 1..{MAX_TTL_SECONDS}")
    normalized = build_review_binding(**binding)
    if reviewer_id == normalized["proposer_id"] or key_id == normalized["proposer_id"]:
        raise ReviewAttestationError("a proposer can never sign a review of their own proposal")
    clean_findings = [str(item)[:500] for item in (findings or [])][:50]
    now = int(time.time()) if issued_at is None else int(issued_at)
    document: dict[str, Any] = {
        "schema": ATTESTATION_SCHEMA,
        "key_id": key_id,
        "reviewer_id": reviewer_id,
        "binding": normalized,
        "verdict": verdict,
        "findings_sha256": sha256_hex(canonical_json(clean_findings)),
        "nonce": nonce or secrets.token_urlsafe(24),
        "issued_at": now,
        "expires_at": now + ttl_seconds,
    }
    document["signature"] = sign_bytes(seed, _signed_message(document)).hex()
    return document


# --------------------------------------------------------------------------
# Trust store (Commander-protected, SHA-256 pinned)
# --------------------------------------------------------------------------
def build_trust_record(*, key_id: str, reviewer_id: str, public_key: bytes, purpose: str,
                       status: str = "trusted") -> dict[str, str]:
    if purpose not in KEY_PURPOSES or status not in KEY_STATUSES or len(public_key) != 32:
        raise ReviewAttestationError("invalid trust record purpose/status/public key")
    return {
        "key_id": _check_ident(key_id, "key_id"),
        "reviewer_id": _check_ident(reviewer_id, "reviewer_id"),
        "public_key_hex": public_key.hex(),
        "fingerprint_sha256": sha256_hex(public_key),
        "purpose": purpose,
        "status": status,
    }


def render_trust_store(records: list[dict[str, str]]) -> bytes:
    document = {"schema": TRUST_STORE_SCHEMA, "keys": records}
    parse_trust_store(canonical_json(document))  # validate before anything is written
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def parse_trust_store(raw: bytes) -> dict[str, dict[str, Any]]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewAttestationError("Reviewer trust store is not valid UTF-8 JSON") from exc
    if (not isinstance(document, dict) or set(document) != {"schema", "keys"}
            or document.get("schema") != TRUST_STORE_SCHEMA
            or not isinstance(document.get("keys"), list) or not 1 <= len(document["keys"]) <= 64):
        raise ReviewAttestationError("Reviewer trust store schema or key count is invalid")
    required = {"key_id", "reviewer_id", "public_key_hex", "fingerprint_sha256", "purpose", "status"}
    registry: dict[str, dict[str, Any]] = {}
    fingerprints: set[str] = set()
    for record in document["keys"]:
        if not isinstance(record, dict) or set(record) != required:
            raise ReviewAttestationError("Reviewer trust record schema is invalid")
        key_id = _check_ident(record["key_id"], "key_id")
        reviewer_id = _check_ident(record["reviewer_id"], "reviewer_id")
        public_hex, fingerprint = record["public_key_hex"], record["fingerprint_sha256"]
        if (not isinstance(public_hex, str) or _HEX64.fullmatch(public_hex) is None
                or record["purpose"] not in KEY_PURPOSES or record["status"] not in KEY_STATUSES):
            raise ReviewAttestationError("Reviewer trust record has invalid key material or labels")
        public_key = bytes.fromhex(public_hex)
        if fingerprint != sha256_hex(public_key) or key_id in registry or fingerprint in fingerprints:
            raise ReviewAttestationError("Reviewer key fingerprint mismatch or duplicate registration")
        if _decompress(public_key) is None:
            raise ReviewAttestationError("Reviewer public key is not a valid Ed25519 point")
        registry[key_id] = {
            "reviewer_id": reviewer_id, "public_key": public_key, "fingerprint_sha256": fingerprint,
            "purpose": record["purpose"], "status": record["status"],
        }
        fingerprints.add(fingerprint)
    return registry


def load_trust_store(path: str | Path, pinned_sha256: str) -> dict[str, dict[str, Any]]:
    """Load the trust store only when its bytes match the out-of-band SHA-256 pin."""
    _check_sha(pinned_sha256, "trust store SHA-256 pin")
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
        raise ReviewAttestationError("Reviewer trust store must be an absolute regular non-symlink file")
    if candidate.stat().st_size > 128 * 1024:
        raise ReviewAttestationError("Reviewer trust store exceeds 128 KiB")
    raw = candidate.read_bytes()
    if sha256_hex(raw) != pinned_sha256:
        raise ReviewAttestationError("Reviewer trust store does not match its pinned SHA-256")
    return parse_trust_store(raw)


def load_trust_store_from_environment() -> dict[str, dict[str, Any]]:
    """``MUSE_REVIEWER_TRUST_STORE`` + ``MUSE_REVIEWER_TRUST_STORE_SHA256``; absent config = no trust."""
    path_value = os.environ.get("MUSE_REVIEWER_TRUST_STORE", "").strip()
    digest_value = os.environ.get("MUSE_REVIEWER_TRUST_STORE_SHA256", "").strip()
    if not path_value and not digest_value:
        return {}
    if not path_value or not digest_value:
        raise ReviewAttestationError("trust store path and SHA-256 pin must be configured together")
    return load_trust_store(path_value, digest_value)


# --------------------------------------------------------------------------
# Verifier with one-time nonce consumption
# --------------------------------------------------------------------------
class ReviewAttestationVerifier:
    """Fail-closed verifier. ``require_production`` rejects ``ephemeral_test`` keys."""

    def __init__(self, trusted_keys: dict[str, dict[str, Any]], nonce_database: str | Path, *,
                 require_production: bool = True, clock=time.time):
        if not isinstance(trusted_keys, dict):
            raise ReviewAttestationError("trusted Reviewer key registry is unavailable")
        self.trusted_keys = trusted_keys
        self.require_production = bool(require_production)
        self.clock = clock
        self.path = Path(nonce_database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS reviewer_attestation_receipts(
                nonce TEXT PRIMARY KEY,
                key_id TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                attestation_sha256 TEXT NOT NULL UNIQUE,
                verdict TEXT NOT NULL,
                consumed_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reviewer_receipts_ticket
                ON reviewer_attestation_receipts(ticket_id, consumed_at);
            CREATE TRIGGER IF NOT EXISTS reviewer_receipts_no_update
                BEFORE UPDATE ON reviewer_attestation_receipts
                BEGIN SELECT RAISE(ABORT,'reviewer receipts are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS reviewer_receipts_no_delete
                BEFORE DELETE ON reviewer_attestation_receipts
                BEGIN SELECT RAISE(ABORT,'reviewer receipts are append-only'); END;
        """)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def check(self, attestation: Any, expected_binding: dict[str, str]) -> dict[str, Any]:
        """Validate everything except nonce consumption; returns the verified facts."""
        required = {"schema", "key_id", "reviewer_id", "binding", "verdict", "findings_sha256",
                    "nonce", "issued_at", "expires_at", "signature"}
        if not isinstance(attestation, dict) or set(attestation) != required:
            raise ReviewAttestationError("Reviewer attestation is incomplete or has unexpected fields")
        if attestation["schema"] != ATTESTATION_SCHEMA:
            raise ReviewAttestationError("unsupported Reviewer attestation schema")
        if not isinstance(expected_binding, dict) or set(expected_binding) != {
                "ticket_id", "artifact_sha256", "test_evidence_sha256", "proposer_id"}:
            raise ReviewAttestationError("expected binding must contain exactly ticket/artifact/test-evidence/proposer")
        expected = build_review_binding(**expected_binding)
        if attestation["binding"] != expected:
            raise ReviewAttestationError("Reviewer signature is not bound to this exact ticket/artifact/test evidence")
        key_id = _check_ident(attestation["key_id"], "key_id")
        reviewer_id = _check_ident(attestation["reviewer_id"], "reviewer_id")
        if reviewer_id == expected["proposer_id"] or key_id == expected["proposer_id"]:
            raise ReviewAttestationError("self-review rejected: proposer identity equals Reviewer identity")
        if attestation["verdict"] not in VERDICTS:
            raise ReviewAttestationError("Reviewer verdict is invalid")
        _check_sha(attestation["findings_sha256"], "findings_sha256")
        nonce = attestation["nonce"]
        if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
            raise ReviewAttestationError("Reviewer nonce is malformed")
        issued_at, expires_at = attestation["issued_at"], attestation["expires_at"]
        if type(issued_at) is not int or type(expires_at) is not int:
            raise ReviewAttestationError("Reviewer timestamps must be integers")
        now = int(self.clock())
        if (issued_at > now + CLOCK_SKEW_SECONDS or expires_at <= issued_at
                or expires_at - issued_at > MAX_TTL_SECONDS or now > expires_at + CLOCK_SKEW_SECONDS):
            raise ReviewAttestationError("Reviewer attestation is expired, future-dated or over-long")
        signature_hex = attestation["signature"]
        if not isinstance(signature_hex, str) or _HEX128.fullmatch(signature_hex) is None:
            raise ReviewAttestationError("Reviewer signature encoding is invalid")
        record = self.trusted_keys.get(key_id)
        if not isinstance(record, dict) or record.get("reviewer_id") != reviewer_id:
            raise ReviewAttestationError("signing key is not trusted for the declared Reviewer")
        if record.get("status") != "trusted":
            raise ReviewAttestationError("signing key has been revoked")
        if self.require_production and record.get("purpose") != "production":
            raise ReviewAttestationError("ephemeral test keys are never accepted as production Reviewer keys")
        if not verify_bytes(record["public_key"], _signed_message(attestation), bytes.fromhex(signature_hex)):
            raise ReviewAttestationError("Reviewer Ed25519 signature verification failed")
        return {
            "key_id": key_id, "reviewer_id": reviewer_id, "ticket_id": expected["ticket_id"],
            "verdict": attestation["verdict"], "nonce": nonce,
            "key_purpose": record.get("purpose"),
            "attestation_sha256": sha256_hex(canonical_json(attestation)),
        }

    def verify_and_consume(self, attestation: Any, expected_binding: dict[str, str], *,
                           require_approval: bool = True) -> dict[str, Any]:
        facts = self.check(attestation, expected_binding)
        if require_approval and facts["verdict"] != "approve":
            raise ReviewAttestationError("Reviewer rejected this artifact")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                "INSERT INTO reviewer_attestation_receipts"
                "(nonce,key_id,reviewer_id,ticket_id,attestation_sha256,verdict,consumed_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (facts["nonce"], facts["key_id"], facts["reviewer_id"], facts["ticket_id"],
                 facts["attestation_sha256"], facts["verdict"], int(self.clock())),
            )
            self.connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self.connection.execute("ROLLBACK")
            raise ReviewAttestationError("Reviewer attestation replay rejected (nonce already consumed)") from exc
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        facts["receipt"] = "consumed"
        return facts

    def receipts(self, ticket_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT nonce,key_id,reviewer_id,ticket_id,attestation_sha256,verdict,consumed_at"
            " FROM reviewer_attestation_receipts WHERE ticket_id=? ORDER BY consumed_at, nonce",
            (ticket_id,),
        ).fetchall()
        names = ("nonce", "key_id", "reviewer_id", "ticket_id", "attestation_sha256", "verdict", "consumed_at")
        return [dict(zip(names, row)) for row in rows]


# --------------------------------------------------------------------------
# Private key files (written by scripts/provision_reviewer_keys.py)
# --------------------------------------------------------------------------
def read_private_key_file(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if len(raw) > 4096:
        raise ReviewAttestationError("private key file is unexpectedly large")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewAttestationError("private key file is not valid JSON") from exc
    required = {"schema", "key_id", "reviewer_id", "purpose", "seed_hex", "public_key_hex"}
    if not isinstance(document, dict) or set(document) != required or document["schema"] != PRIVATE_KEY_SCHEMA:
        raise ReviewAttestationError("private key file schema is invalid")
    if document["purpose"] not in KEY_PURPOSES or not isinstance(document["seed_hex"], str) \
            or _HEX64.fullmatch(document["seed_hex"]) is None:
        raise ReviewAttestationError("private key file content is invalid")
    seed = bytes.fromhex(document["seed_hex"])
    if public_key_from_seed(seed).hex() != document["public_key_hex"]:
        raise ReviewAttestationError("private key file public key does not match its seed")
    return {**document, "seed": seed}


# --------------------------------------------------------------------------
# CLI: muse-reviewer-attest
# --------------------------------------------------------------------------
def _load_json_file(path: str) -> Any:
    raw = Path(path).read_bytes()
    if len(raw) > 256 * 1024:
        raise ReviewAttestationError(f"{path} exceeds 256 KiB")
    return json.loads(raw.decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse-reviewer-attest",
                                     description="Sign or verify Muse independent Reviewer attestations (Ed25519).")
    sub = parser.add_subparsers(dest="command", required=True)
    binding = argparse.ArgumentParser(add_help=False)
    binding.add_argument("--ticket-id", required=True)
    binding.add_argument("--artifact", help="artifact file to hash (or use --artifact-sha256)")
    binding.add_argument("--artifact-sha256")
    binding.add_argument("--test-evidence", help="test log/report file to hash (or --test-evidence-sha256)")
    binding.add_argument("--test-evidence-sha256")
    binding.add_argument("--proposer-id", required=True)
    sign = sub.add_parser("sign", parents=[binding], help="Reviewer signs a binding with a private key file")
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--verdict", choices=sorted(VERDICTS), default="approve")
    sign.add_argument("--finding", action="append", default=[])
    sign.add_argument("--ttl", type=int, default=600)
    sign.add_argument("--out", required=True)
    verify = sub.add_parser("verify", parents=[binding], help="verify and consume an attestation")
    verify.add_argument("--attestation", required=True)
    verify.add_argument("--receipts-db", required=True)
    verify.add_argument("--allow-test-keys", action="store_true",
                        help="accept ephemeral_test keys (sandbox drills only; never production)")
    verify.add_argument("--check-only", action="store_true", help="verify without consuming the nonce")
    sub.add_parser("backend", help="print the active Ed25519 backend")
    args = parser.parse_args(argv)
    try:
        if args.command == "backend":
            print(json.dumps({"backend": active_backend()}))
            return 0
        artifact_sha = args.artifact_sha256 or (sha256_file(args.artifact) if args.artifact else None)
        evidence_sha = args.test_evidence_sha256 or (sha256_file(args.test_evidence) if args.test_evidence else None)
        expected = build_review_binding(ticket_id=args.ticket_id, artifact_sha256=artifact_sha or "",
                                        test_evidence_sha256=evidence_sha or "", proposer_id=args.proposer_id)
        if args.command == "sign":
            key = read_private_key_file(args.private_key)
            document = create_attestation(seed=key["seed"], key_id=key["key_id"], reviewer_id=key["reviewer_id"],
                                          binding=expected, verdict=args.verdict, findings=args.finding,
                                          ttl_seconds=args.ttl)
            out = Path(args.out)
            if out.exists():
                raise ReviewAttestationError("refusing to overwrite an existing attestation file")
            out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": "signed", "out": str(out), "key_id": key["key_id"],
                              "key_purpose": key["purpose"], "backend": active_backend()}, ensure_ascii=False))
            return 0
        trusted = load_trust_store_from_environment()
        if not trusted:
            raise ReviewAttestationError("no Reviewer trust store configured (MUSE_REVIEWER_TRUST_STORE + _SHA256)")
        with ReviewAttestationVerifier(trusted, args.receipts_db, require_production=not args.allow_test_keys) as verifier:
            attestation = _load_json_file(args.attestation)
            facts = verifier.check(attestation, expected) if args.check_only \
                else verifier.verify_and_consume(attestation, expected)
        print(json.dumps({"status": "verified", **facts}, ensure_ascii=False))
        return 0
    except (ReviewAttestationError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
