"""Host-side pairing and request authentication primitives for Muse Companion.

This module is transport-agnostic. Callers must establish verified TLS (or a
separately authenticated trusted tunnel) before invoking authenticate_request.
It never opens a listener, widens the loopback API, or grants execution rights.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import uuid
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any


PROTOCOL_VERSION = "muse-companion-v1"
PAIRING_MIN_TTL_SECONDS = 60
PAIRING_MAX_TTL_SECONDS = 600
PAIRING_DEFAULT_TTL_SECONDS = 180
PAIRING_MAX_ATTEMPTS = 5
PAIRING_REQUEST_TTL_SECONDS = 300
REQUEST_CLOCK_SKEW_MS = 60_000
NONCE_RETENTION_SECONDS = 300
MAX_SIGNED_BODY_BYTES = 65_536
DEFAULT_DEVICE_PERMISSIONS = ("events.read", "status.read")
READ_PATH_PERMISSIONS = {
    "/api/v1/companion/status": "status.read",
    "/api/v1/companion/events": "events.read",
}


class CompanionSecurityError(PermissionError):
    """Raised when a pairing or Companion authentication invariant is violated."""


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


def _required_text(value: str, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if not value or len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field} must be non-empty printable text of at most {maximum} characters")
    return value


def _epoch(value: float | int | None) -> float:
    if value is None:
        import time
        value = time.time()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("timestamp must be a finite epoch value")
    return float(value)


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_request(
    *, device_id: str, session_id: str, nonce: str, timestamp_ms: int,
    method: str, path: str, body: bytes, cursor: str = "",
) -> bytes:
    device_id = _required_text(device_id, "device_id", maximum=64)
    session_id = _required_text(session_id, "session_id", maximum=64)
    nonce = _required_text(nonce, "nonce", maximum=128)
    method = _required_text(method, "method", maximum=12).upper()
    path = _required_text(path, "path", maximum=2048)
    if not isinstance(cursor, str) or len(cursor) > 64:
        raise ValueError("cursor must be text of at most 64 characters")
    if cursor and not re.fullmatch(r"[a-f0-9-]{16,64}", cursor):
        raise ValueError("cursor is invalid")
    if not re.fullmatch(r"[a-f0-9]{32}", device_id):
        raise ValueError("device_id must be a 32-character lowercase hex identifier")
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise ValueError("session_id must be a 32-character lowercase hex identifier")
    if not re.fullmatch(r"[A-Za-z0-9_-]{22,128}", nonce):
        raise ValueError("nonce must be a high-entropy URL-safe value")
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        raise ValueError("timestamp_ms must be a non-negative integer")
    if method != "GET" or path not in READ_PATH_PERMISSIONS:
        raise CompanionSecurityError("Companion credentials are read-only")
    if not isinstance(body, bytes) or len(body) > MAX_SIGNED_BODY_BYTES:
        raise ValueError("request body must be bytes within the size limit")
    body_digest = _sha256_hex(body)
    fields = (
        PROTOCOL_VERSION, device_id, session_id, nonce, str(timestamp_ms),
        method, path, cursor, body_digest,
    )
    return "\n".join(fields).encode("utf-8")


def sign_companion_request(
    token: str, *, device_id: str, session_id: str, nonce: str,
    timestamp_ms: int, method: str, path: str, body: bytes = b"", cursor: str = "",
) -> str:
    """Create the v1 HMAC-SHA256 request signature; the token is never persisted."""
    if not isinstance(token, str) or len(token) < 40 or len(token) > 128:
        raise ValueError("device token is invalid")
    canonical = _canonical_request(
        device_id=device_id, session_id=session_id, nonce=nonce,
        timestamp_ms=timestamp_ms, method=method, path=path, body=body, cursor=cursor,
    )
    return hmac.new(token.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


class CompanionDeviceStore:
    """SQLite pairing/device registry with one-time claims and replay-resistant reads.

    Pairing codes, claim secrets and device tokens are never stored in plaintext.
    The transport adapter must prove TLS/trusted-tunnel protection before calling
    authenticate_request; this store cannot infer HTTPS from forwarded headers.
    """

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.connection = sqlite3.connect(
            self.database, timeout=10, isolation_level=None, check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS companion_pairing_challenges (
                challenge_id TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('open','requested','expired','locked'))
            );
            CREATE TABLE IF NOT EXISTS companion_pairing_requests (
                request_id TEXT PRIMARY KEY,
                challenge_id TEXT NOT NULL UNIQUE,
                device_name TEXT NOT NULL,
                device_fingerprint TEXT NOT NULL,
                claim_secret_hash TEXT NOT NULL,
                claim_attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','claimed','expired','locked')),
                approved_at REAL,
                claimed_at REAL,
                FOREIGN KEY(challenge_id) REFERENCES companion_pairing_challenges(challenge_id)
            );
            CREATE TABLE IF NOT EXISTS companion_devices (
                device_id TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                device_fingerprint TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                permissions_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                revoked_at REAL,
                revoked_by TEXT
            );
            CREATE TABLE IF NOT EXISTS companion_request_nonces (
                device_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                seen_at REAL NOT NULL,
                PRIMARY KEY(device_id, session_id, nonce),
                FOREIGN KEY(device_id) REFERENCES companion_devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS companion_audit_events (
                event_id TEXT PRIMARY KEY,
                device_id TEXT,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_companion_challenges_expiry
                ON companion_pairing_challenges(state, expires_at);
            CREATE INDEX IF NOT EXISTS idx_companion_requests_status
                ON companion_pairing_requests(status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_companion_devices_active
                ON companion_devices(revoked_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_companion_nonce_seen
                ON companion_request_nonces(seen_at);
            CREATE INDEX IF NOT EXISTS idx_companion_audit_created
                ON companion_audit_events(created_at);
            """
        )

    @_serialized
    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CompanionDeviceStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _audit(
        self, event_type: str, *, actor: str, now: float,
        device_id: str | None = None, details: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO companion_audit_events
               (event_id, device_id, event_type, actor, details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex, device_id, event_type, actor,
                json.dumps(details or {}, sort_keys=True, separators=(",", ":")), now,
            ),
        )

    @_serialized
    def create_pairing_challenge(
        self, *, now: float | None = None,
        expires_in_seconds: int = PAIRING_DEFAULT_TTL_SECONDS,
        max_attempts: int = PAIRING_MAX_ATTEMPTS,
    ) -> dict[str, Any]:
        timestamp = _epoch(now)
        if isinstance(expires_in_seconds, bool) or not isinstance(expires_in_seconds, int):
            raise ValueError("expires_in_seconds must be an integer")
        if not PAIRING_MIN_TTL_SECONDS <= expires_in_seconds <= PAIRING_MAX_TTL_SECONDS:
            raise ValueError("pairing challenge lifetime must be 60..600 seconds")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        challenge_id = uuid.uuid4().hex
        code = secrets.token_urlsafe(24)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            superseded = self.connection.execute(
                "UPDATE companion_pairing_challenges SET state='expired' WHERE state='open'"
            ).rowcount
            self.connection.execute(
                """INSERT INTO companion_pairing_challenges
                   (challenge_id, code_hash, created_at, expires_at, max_attempts, state)
                   VALUES (?, ?, ?, ?, ?, 'open')""",
                (challenge_id, _sha256_hex(code.encode("utf-8")), timestamp,
                 timestamp + expires_in_seconds, max_attempts),
            )
            if superseded:
                self._audit(
                    "pairing.challenge_superseded", actor="host", now=timestamp,
                    details={"count": superseded},
                )
            self._audit("pairing.challenge_created", actor="host", now=timestamp)
            self.connection.commit()
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise
        return {
            "challenge_id": challenge_id, "code": code,
            "expires_at": timestamp + expires_in_seconds,
        }
    @_serialized
    def request_pairing(
        self, challenge_id: str, code: str, *, device_name: str,
        device_fingerprint: str, claim_secret: str, now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = _epoch(now)
        challenge_id = _required_text(challenge_id, "challenge_id", maximum=64)
        device_name = _required_text(device_name, "device_name", maximum=80)
        device_fingerprint = _required_text(device_fingerprint, "device_fingerprint", maximum=64)
        claim_secret = _required_text(claim_secret, "claim_secret", maximum=256)
        if not re.fullmatch(r"[a-f0-9]{32}", challenge_id):
            raise ValueError("challenge_id must be a 32-character lowercase hex identifier")
        if not re.fullmatch(r"[a-f0-9]{64}", device_fingerprint):
            raise ValueError("device_fingerprint must be a SHA-256 hex fingerprint")
        if len(claim_secret) < 32:
            raise ValueError("claim_secret must contain at least 32 characters")
        if not isinstance(code, str) or not 1 <= len(code) <= 128:
            raise CompanionSecurityError("pairing challenge is invalid or unavailable")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            challenge = self.connection.execute(
                "SELECT * FROM companion_pairing_challenges WHERE challenge_id=?",
                (challenge_id,),
            ).fetchone()
            if challenge is None or challenge["state"] != "open":
                raise CompanionSecurityError("pairing challenge is invalid or unavailable")
            if challenge["expires_at"] <= timestamp:
                self.connection.execute(
                    "UPDATE companion_pairing_challenges SET state='expired' WHERE challenge_id=?",
                    (challenge_id,),
                )
                self.connection.commit()
                raise CompanionSecurityError("pairing challenge has expired")
            supplied_hash = _sha256_hex(code.encode("utf-8"))
            if not hmac.compare_digest(challenge["code_hash"], supplied_hash):
                attempts = challenge["attempts"] + 1
                state = "locked" if attempts >= challenge["max_attempts"] else "open"
                self.connection.execute(
                    "UPDATE companion_pairing_challenges SET attempts=?, state=? WHERE challenge_id=?",
                    (attempts, state, challenge_id),
                )
                self._audit("pairing.code_rejected", actor="remote", now=timestamp)
                self.connection.commit()
                raise CompanionSecurityError("pairing challenge is invalid or unavailable")

            request_id = uuid.uuid4().hex
            expires_at = min(challenge["expires_at"], timestamp + PAIRING_REQUEST_TTL_SECONDS)
            self.connection.execute(
                """INSERT INTO companion_pairing_requests
                   (request_id, challenge_id, device_name, device_fingerprint,
                    claim_secret_hash, created_at, expires_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (request_id, challenge_id, device_name, device_fingerprint,
                 _sha256_hex(claim_secret.encode("utf-8")), timestamp, expires_at),
            )
            self.connection.execute(
                "UPDATE companion_pairing_challenges SET state='requested' WHERE challenge_id=?",
                (challenge_id,),
            )
            self._audit(
                "pairing.requested", actor="device", now=timestamp,
                details={"request_id": request_id, "device_name": device_name,
                         "device_fingerprint": device_fingerprint},
            )
            self.connection.commit()
            return {"request_id": request_id, "status": "pending", "expires_at": expires_at}
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    @_serialized
    def list_pending_pairings(self, *, now: float | None = None, limit: int = 20) -> list[dict[str, Any]]:
        timestamp = _epoch(now)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        self.connection.execute(
            """UPDATE companion_pairing_requests SET status='expired'
               WHERE status IN ('pending','approved') AND expires_at<=?""",
            (timestamp,),
        )
        rows = self.connection.execute(
            """SELECT request_id, device_name, device_fingerprint, created_at,
                      expires_at, status, approved_at
               FROM companion_pairing_requests
               WHERE status IN ('pending','approved')
               ORDER BY created_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    @_serialized
    def approve_pairing(
        self, request_id: str, *, approved_by: str, now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = _epoch(now)
        request_id = _required_text(request_id, "request_id", maximum=64)
        actor = _required_text(approved_by, "approved_by", maximum=96)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM companion_pairing_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None or row["status"] != "pending" or row["expires_at"] <= timestamp:
                raise CompanionSecurityError("pairing request is unavailable for approval")
            self.connection.execute(
                "UPDATE companion_pairing_requests SET status='approved', approved_at=? WHERE request_id=?",
                (timestamp, request_id),
            )
            self._audit(
                "pairing.approved", actor=actor, now=timestamp,
                details={"request_id": request_id, "device_name": row["device_name"],
                         "device_fingerprint": row["device_fingerprint"]},
            )
            self.connection.commit()
            return {
                "request_id": request_id, "status": "approved",
                "device_name": row["device_name"],
                "device_fingerprint": row["device_fingerprint"],
            }
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    @_serialized
    def deny_pairing(
        self, request_id: str, *, denied_by: str, now: float | None = None,
    ) -> bool:
        timestamp = _epoch(now)
        request_id = _required_text(request_id, "request_id", maximum=64)
        actor = _required_text(denied_by, "denied_by", maximum=96)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT status FROM companion_pairing_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                self.connection.rollback()
                return False
            self.connection.execute(
                "UPDATE companion_pairing_requests SET status='denied', claim_secret_hash='' WHERE request_id=?",
                (request_id,),
            )
            self._audit("pairing.denied", actor=actor, now=timestamp,
                        details={"request_id": request_id})
            self.connection.commit()
            return True
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    @_serialized
    def claim_pairing(
        self, request_id: str, claim_secret: str, *, now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = _epoch(now)
        request_id = _required_text(request_id, "request_id", maximum=64)
        claim_secret = _required_text(claim_secret, "claim_secret", maximum=256)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM companion_pairing_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None or row["status"] != "approved" or row["expires_at"] <= timestamp:
                raise CompanionSecurityError("pairing claim is unavailable")
            supplied_hash = _sha256_hex(claim_secret.encode("utf-8"))
            if not hmac.compare_digest(row["claim_secret_hash"], supplied_hash):
                attempts = row["claim_attempts"] + 1
                status = "locked" if attempts >= PAIRING_MAX_ATTEMPTS else "approved"
                self.connection.execute(
                    "UPDATE companion_pairing_requests SET claim_attempts=?, status=? WHERE request_id=?",
                    (attempts, status, request_id),
                )
                self._audit("pairing.claim_rejected", actor="remote", now=timestamp,
                            details={"request_id": request_id})
                self.connection.commit()
                raise CompanionSecurityError("pairing claim is unavailable")

            device_id = uuid.uuid4().hex
            token = secrets.token_urlsafe(32)
            permissions = list(DEFAULT_DEVICE_PERMISSIONS)
            self.connection.execute(
                """INSERT INTO companion_devices
                   (device_id, device_name, device_fingerprint, token_hash,
                    permissions_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (device_id, row["device_name"], row["device_fingerprint"],
                 _sha256_hex(token.encode("utf-8")),
                 json.dumps(permissions, separators=(",", ":")), timestamp),
            )
            self.connection.execute(
                """UPDATE companion_pairing_requests
                   SET status='claimed', claimed_at=?, claim_secret_hash='' WHERE request_id=?""",
                (timestamp, request_id),
            )
            self._audit(
                "device.claimed", actor="device", now=timestamp, device_id=device_id,
                details={"device_name": row["device_name"], "permissions": permissions},
            )
            self.connection.commit()
            return {"device_id": device_id, "token": token, "permissions": permissions}
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    @_serialized
    def get_device(self, device_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT device_id, device_name, device_fingerprint, permissions_json,
                      created_at, revoked_at, revoked_by
               FROM companion_devices WHERE device_id=?""",
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["permissions"] = json.loads(result.pop("permissions_json"))
        result["status"] = "revoked" if result["revoked_at"] is not None else "active"
        return result

    @_serialized
    def list_devices(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute(
            """SELECT device_id, device_name, device_fingerprint, permissions_json,
                      created_at, revoked_at, revoked_by
               FROM companion_devices ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["permissions"] = json.loads(item.pop("permissions_json"))
            item["status"] = "revoked" if item["revoked_at"] is not None else "active"
            result.append(item)
        return result

    @_serialized
    def revoke_device(
        self, device_id: str, *, revoked_by: str, now: float | None = None,
    ) -> bool:
        timestamp = _epoch(now)
        device_id = _required_text(device_id, "device_id", maximum=64)
        actor = _required_text(revoked_by, "revoked_by", maximum=96)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """UPDATE companion_devices SET revoked_at=?, revoked_by=?
                   WHERE device_id=? AND revoked_at IS NULL""",
                (timestamp, actor, device_id),
            )
            if cursor.rowcount:
                self._audit("device.revoked", actor=actor, now=timestamp, device_id=device_id)
            self.connection.commit()
            return cursor.rowcount == 1
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    @_serialized
    def authenticate_request(
        self, device_id: str, token: str, *, session_id: str, nonce: str,
        timestamp_ms: int, method: str, path: str, body: bytes,
        signature: str, transport_verified: bool, cursor: str = "", now: float | None = None,
    ) -> dict[str, Any]:
        """Verify a signed read request and atomically consume its nonce.

        transport_verified must be set only by an adapter that verified TLS
        peer/hostname or a configured authenticated tunnel. Forwarded headers
        alone are not transport proof.
        """
        timestamp = _epoch(now)
        if transport_verified is not True:
            raise CompanionSecurityError("verified TLS or trusted tunnel is required")
        if not isinstance(token, str) or not 40 <= len(token) <= 128:
            raise CompanionSecurityError("device authentication failed")
        if not isinstance(signature, str) or not re.fullmatch(r"[a-f0-9]{64}", signature):
            raise CompanionSecurityError("device authentication failed")
        canonical = _canonical_request(
            device_id=device_id, session_id=session_id, nonce=nonce,
            timestamp_ms=timestamp_ms, method=method, path=path, body=body, cursor=cursor,
        )
        current_ms = int(timestamp * 1000)
        if abs(current_ms - timestamp_ms) > REQUEST_CLOCK_SKEW_MS:
            raise CompanionSecurityError("device request timestamp is outside the allowed window")
        expected_signature = hmac.new(
            token.encode("utf-8"), canonical, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise CompanionSecurityError("device authentication failed")
        expected_token_hash = _sha256_hex(token.encode("utf-8"))

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """SELECT device_id, device_name, device_fingerprint, token_hash,
                          permissions_json, revoked_at
                   FROM companion_devices WHERE device_id=?""",
                (device_id,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None or not hmac.compare_digest(
                row["token_hash"], expected_token_hash,
            ):
                raise CompanionSecurityError("device authentication failed")
            permission = READ_PATH_PERMISSIONS[path]
            permissions = json.loads(row["permissions_json"])
            if permission not in permissions:
                raise CompanionSecurityError("device permission denied")
            self.connection.execute(
                "DELETE FROM companion_request_nonces WHERE seen_at<?",
                (timestamp - NONCE_RETENTION_SECONDS,),
            )
            try:
                self.connection.execute(
                    """INSERT INTO companion_request_nonces
                       (device_id, session_id, nonce, seen_at) VALUES (?, ?, ?, ?)""",
                    (device_id, session_id, nonce, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                raise CompanionSecurityError("device request nonce has already been used") from exc
            self.connection.commit()
            return {
                "device_id": row["device_id"], "device_name": row["device_name"],
                "device_fingerprint": row["device_fingerprint"],
                "permissions": permissions,
            }
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    @_serialized
    def list_audit_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self.connection.execute(
            """SELECT event_id, device_id, event_type, actor, details_json, created_at
               FROM companion_audit_events ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        events = []
        for row in reversed(rows):
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            events.append(item)
        return events
