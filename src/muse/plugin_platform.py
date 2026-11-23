"""Declarative plugin registry for Muse's Commander-owned capability ecosystem.

Plugin manifests are data, never executable code. Only trusted adapters injected
by the application may submit work, and every submission is handed to Commander
with an explicit human-approval requirement.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PLUGIN_ID = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+(?:-[a-z0-9]+)*)*\Z")
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z")
CAPABILITY_ID = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
ALLOWED_RISKS = frozenset({"read_only", "research", "code_change", "external_action"})
ALLOWED_PERMISSIONS = frozenset({
    "research.read", "knowledge.read", "knowledge.write", "commander.enqueue",
    "filesystem.project_read", "filesystem.project_write", "network.public_research",
    "gpu.request", "video.render",
})
MAX_MANIFEST_BYTES = 64 * 1024
MAX_CAPABILITIES = 64
MAX_PLUGIN_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_DEPENDENCIES = 32
# Risk labels are ceilings, not descriptive hints: a lower-risk capability may
# never request permissions reserved for a higher-risk class.
RISK_PERMISSION_CEILINGS = {
    "read_only": frozenset({"research.read", "knowledge.read", "commander.enqueue"}),
    "research": frozenset({"research.read", "knowledge.read", "knowledge.write", "network.public_research", "commander.enqueue"}),
    "code_change": frozenset({"research.read", "knowledge.read", "knowledge.write", "network.public_research", "filesystem.project_read", "filesystem.project_write", "commander.enqueue"}),
    "external_action": ALLOWED_PERMISSIONS,
}
# These are proposed upper bounds for human review only. No plugin runtime is
# active, so the values must never be represented as OS-enforced quotas.
RISK_RESOURCE_BUDGETS = {
    "read_only": {"cpu_seconds": 5, "wall_time_seconds": 15, "memory_mb": 128, "max_output_bytes": 262144, "max_network_requests": 0, "max_filesystem_write_bytes": 0, "gpu_allowed": False},
    "research": {"cpu_seconds": 60, "wall_time_seconds": 120, "memory_mb": 512, "max_output_bytes": 2097152, "max_network_requests": 16, "max_filesystem_write_bytes": 0, "gpu_allowed": False},
    "code_change": {"cpu_seconds": 120, "wall_time_seconds": 300, "memory_mb": 1024, "max_output_bytes": 4194304, "max_network_requests": 16, "max_filesystem_write_bytes": 16777216, "gpu_allowed": False},
    "external_action": {"cpu_seconds": 180, "wall_time_seconds": 600, "memory_mb": 2048, "max_output_bytes": 8388608, "max_network_requests": 32, "max_filesystem_write_bytes": 67108864, "gpu_allowed": True},
}


def proposed_resource_budget(risk: str) -> dict[str, Any]:
    """Return review-only resource ceilings; no runtime enforcement is implied."""
    if not isinstance(risk, str) or risk not in RISK_RESOURCE_BUDGETS:
        raise PluginError("unsupported plugin risk class")
    return {"limits": dict(RISK_RESOURCE_BUDGETS[risk]), "runtime_enforced": False}


class PluginError(ValueError):
    """Invalid, unavailable, or unauthorized plugin operation."""


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise PluginError(f"{field} must be non-empty text of at most {maximum} characters")
    return value.strip()


def validate_manifest(value: Any) -> dict[str, Any]:
    """Validate and canonicalize a strict, non-executable plugin manifest."""
    if not isinstance(value, dict):
        raise PluginError("plugin manifest must be a JSON object")
    allowed = {"schema_version", "id", "name", "version", "description", "publisher",
               "dependencies", "capabilities"}
    if set(value) != allowed:
        raise PluginError("plugin manifest fields do not match schema version 1")
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise PluginError("unsupported plugin manifest schema version")
    plugin_id = _text(value["id"], "id", 96)
    if not PLUGIN_ID.fullmatch(plugin_id):
        raise PluginError("plugin id must be a lowercase dotted or hyphenated identifier")
    name = _text(value["name"], "name", 120)
    version = _text(value["version"], "version", 64)
    if not SEMVER.fullmatch(version):
        raise PluginError("plugin version must use semantic versioning")
    description = _text(value["description"], "description", 2000)
    publisher = _text(value["publisher"], "publisher", 120)
    dependencies = value["dependencies"]
    if not isinstance(dependencies, dict) or len(dependencies) > MAX_DEPENDENCIES:
        raise PluginError("dependencies must be a bounded object")
    normalized_dependencies: dict[str, str] = {}
    for dependency_id, dependency_version in dependencies.items():
        if not isinstance(dependency_id, str) or not PLUGIN_ID.fullmatch(dependency_id):
            raise PluginError("dependency plugin id is invalid")
        if dependency_id == plugin_id:
            raise PluginError("plugin cannot depend on itself")
        if not isinstance(dependency_version, str) or not SEMVER.fullmatch(dependency_version):
            raise PluginError("dependency versions must be exact semantic versions")
        normalized_dependencies[dependency_id] = dependency_version
    capabilities = value["capabilities"]
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= MAX_CAPABILITIES:
        raise PluginError("capabilities must be a non-empty bounded list")
    normalized_capabilities = []
    seen: set[str] = set()
    for item in capabilities:
        if not isinstance(item, dict) or set(item) != {"id", "description", "risk", "permissions"}:
            raise PluginError("capability fields do not match schema version 1")
        capability_id = _text(item["id"], "capability id", 96)
        if not CAPABILITY_ID.fullmatch(capability_id) or capability_id in seen:
            raise PluginError("capability id is invalid or duplicated")
        seen.add(capability_id)
        risk = item["risk"]
        if not isinstance(risk, str) or risk not in ALLOWED_RISKS:
            raise PluginError("capability risk class is unsupported")
        permissions = item["permissions"]
        if not isinstance(permissions, list) or len(permissions) > len(ALLOWED_PERMISSIONS):
            raise PluginError("capability permissions must be a bounded list")
        if any(not isinstance(permission, str) or permission not in ALLOWED_PERMISSIONS
               for permission in permissions):
            raise PluginError("capability requests an unknown or forbidden permission")
        if len(set(permissions)) != len(permissions):
            raise PluginError("capability permissions must be unique")
        excessive_permissions = set(permissions) - RISK_PERMISSION_CEILINGS[risk]
        if excessive_permissions:
            raise PluginError("capability permissions exceed the declared risk class")
        if "commander.enqueue" not in permissions:
            raise PluginError("every plugin capability must be routed through Commander")
        normalized_capabilities.append({
            "id": capability_id,
            "description": _text(item["description"], "capability description", 500),
            "risk": risk,
            "permissions": sorted(permissions),
        })
    normalized = {
        "schema_version": 1, "id": plugin_id, "name": name, "version": version,
        "description": description, "publisher": publisher,
        "dependencies": dict(sorted(normalized_dependencies.items())),
        "capabilities": sorted(normalized_capabilities, key=lambda item: item["id"]),
    }
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise PluginError("plugin manifest exceeds the 64 KiB limit")
    return normalized


def plugin_signature_payload(manifest: Any, artifact_sha256: str) -> bytes:
    """Return the versioned canonical bytes publishers must sign for a bundle."""
    normalized = validate_manifest(manifest)
    if not isinstance(artifact_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
        raise PluginError("artifact SHA-256 must be 64 lowercase hexadecimal characters")
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return b"MUSE-PLUGIN-BUNDLE-SIGNATURE-V1\x00" + encoded + b"\x00artifact-sha256=" + artifact_sha256.encode("ascii")


class PluginRegistry:
    """Persistent manifest and approval registry; never imports plugin code."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        if self.database.is_symlink() or (self.database.exists() and not self.database.is_file()):
            raise PluginError("plugin registry database must be a regular non-symlink file")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS plugins (
                plugin_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('discovered','approved','enabled','disabled','quarantined')),
                approved_by TEXT,
                discovered_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plugin_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_plugin_audit_created ON plugin_audit(created_at DESC);
            CREATE TABLE IF NOT EXISTS plugin_trusted_publishers (
                key_id TEXT PRIMARY KEY,
                publisher TEXT NOT NULL,
                public_key BLOB NOT NULL CHECK(length(public_key)=32),
                fingerprint_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('trusted','revoked')),
                trusted_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                revoked_at REAL
            );
            CREATE TABLE IF NOT EXISTS plugin_signatures (
                plugin_id TEXT NOT NULL,
                version TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                key_id TEXT NOT NULL REFERENCES plugin_trusted_publishers(key_id),
                artifact_sha256 TEXT NOT NULL,
                signature_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('verified','revoked')),
                verified_at REAL NOT NULL,
                PRIMARY KEY(plugin_id,version,manifest_sha256)
            );
        """)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PluginRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def trust_publisher_key(
        self, key_id: str, publisher: str, public_key: bytes, *, trusted_by: str,
    ) -> dict[str, str]:
        key_id = _text(key_id, "key_id", 96)
        publisher = _text(publisher, "publisher", 120)
        actor = _text(trusted_by, "trusted_by", 120)
        if not isinstance(public_key, bytes) or len(public_key) != 32:
            raise PluginError("Ed25519 publisher public key must contain exactly 32 raw bytes")
        fingerprint = hashlib.sha256(public_key).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT publisher,public_key,fingerprint_sha256,status FROM plugin_trusted_publishers WHERE key_id=?",
                (key_id,),
            ).fetchone()
            if existing is not None:
                if (existing["publisher"] != publisher or bytes(existing["public_key"]) != public_key
                        or existing["status"] != "trusted"):
                    raise PluginError("publisher key id is already pinned or revoked; use a new key id")
                self.connection.commit()
                return {"key_id": key_id, "publisher": publisher,
                        "fingerprint_sha256": fingerprint, "status": "trusted"}
            self.connection.execute(
                "INSERT INTO plugin_trusted_publishers(key_id,publisher,public_key,fingerprint_sha256,status,trusted_by,created_at) "
                "VALUES(?,?,?,?, 'trusted', ?, ?)",
                (key_id, publisher, public_key, fingerprint, actor, time.time()),
            )
            self._audit("*", "publisher_key.trusted", actor, fingerprint)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {"key_id": key_id, "publisher": publisher,
                "fingerprint_sha256": fingerprint, "status": "trusted"}

    def revoke_publisher_key(self, key_id: str, *, revoked_by: str) -> dict[str, str]:
        key_id = _text(key_id, "key_id", 96)
        actor = _text(revoked_by, "revoked_by", 120)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT publisher,fingerprint_sha256,status FROM plugin_trusted_publishers WHERE key_id=?",
                (key_id,),
            ).fetchone()
            if row is None:
                raise PluginError("publisher key is not trusted")
            self.connection.execute(
                "UPDATE plugin_trusted_publishers SET status='revoked',revoked_at=? WHERE key_id=?",
                (time.time(), key_id),
            )
            self.connection.execute(
                "UPDATE plugin_signatures SET status='revoked' WHERE key_id=?", (key_id,),
            )
            affected_plugins = self.connection.execute(
                "SELECT DISTINCT plugin_id FROM plugin_signatures WHERE key_id=?", (key_id,),
            ).fetchall()
            self.connection.execute(
                "UPDATE plugins SET status='disabled',updated_at=? WHERE plugin_id IN "
                "(SELECT DISTINCT plugin_id FROM plugin_signatures WHERE key_id=?) AND status='enabled'",
                (time.time(), key_id),
            )
            for affected in affected_plugins:
                plugin_row = self.connection.execute(
                    "SELECT manifest_sha256 FROM plugins WHERE plugin_id=?", (affected["plugin_id"],),
                ).fetchone()
                if plugin_row is not None:
                    self._audit(affected["plugin_id"], "plugin.disabled_publisher_key_revoked", actor,
                                plugin_row["manifest_sha256"])
            self._audit("*", "publisher_key.revoked", actor, row["fingerprint_sha256"])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {"key_id": key_id, "publisher": row["publisher"],
                "fingerprint_sha256": row["fingerprint_sha256"], "status": "revoked"}

    def get_signature(self, plugin_id: str) -> dict[str, Any]:
        plugin = self.connection.execute(
            "SELECT version,manifest_sha256 FROM plugins WHERE plugin_id=?", (plugin_id,),
        ).fetchone()
        if plugin is None:
            raise PluginError("plugin not found")
        row = self.connection.execute(
            "SELECT key_id,artifact_sha256,signature_sha256,status,verified_at "
            "FROM plugin_signatures WHERE plugin_id=? AND version=? AND manifest_sha256=?",
            (plugin_id, plugin["version"], plugin["manifest_sha256"]),
        ).fetchone()
        if row is None:
            return {"plugin_id": plugin_id, "status": "unsigned"}
        return {"plugin_id": plugin_id, **dict(row)}

    def discover_signed(
        self, manifest: Any, artifact_path: str | Path, *, key_id: str, signature: bytes,
    ) -> dict[str, Any]:
        """Verify publisher identity and bundle bytes before registering a candidate.

        This is supply-chain verification only. It never extracts or executes the
        artifact, enables a plugin, or creates a Commander task.
        """
        normalized = validate_manifest(manifest)
        key_id = _text(key_id, "key_id", 96)
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise PluginError("Ed25519 signature must contain exactly 64 bytes")
        artifact = Path(artifact_path)
        if artifact.is_symlink() or not artifact.is_file():
            raise PluginError("plugin artifact must be an existing regular non-symlink file")
        try:
            with artifact.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_PLUGIN_ARTIFACT_BYTES:
                    raise PluginError("plugin artifact must be between 1 byte and 256 MiB")
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise PluginError("plugin artifact changed while its digest was being calculated")
        except OSError as exc:
            raise PluginError("plugin artifact could not be read") from exc
        artifact_sha256 = digest.hexdigest()
        key = self.connection.execute(
            "SELECT publisher,public_key,fingerprint_sha256,status FROM plugin_trusted_publishers WHERE key_id=?",
            (key_id,),
        ).fetchone()
        if key is None or key["status"] != "trusted":
            raise PluginError("publisher signing key is not trusted")
        if key["publisher"] != normalized["publisher"]:
            raise PluginError("plugin publisher does not match the trusted signing key")
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError as exc:
            raise PluginError("install the plugin-security extra to verify Ed25519 signatures") from exc
        try:
            Ed25519PublicKey.from_public_bytes(bytes(key["public_key"])).verify(
                signature, plugin_signature_payload(normalized, artifact_sha256),
            )
        except (InvalidSignature, ValueError) as exc:
            raise PluginError("plugin bundle signature verification failed") from exc

        record = self.discover(normalized)
        encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        manifest_sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        signature_sha256 = hashlib.sha256(signature).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.connection.execute(
                "SELECT version,manifest_sha256 FROM plugins WHERE plugin_id=?", (normalized["id"],),
            ).fetchone()
            current_key = self.connection.execute(
                "SELECT fingerprint_sha256,status FROM plugin_trusted_publishers WHERE key_id=?", (key_id,),
            ).fetchone()
            if (current is None or current["version"] != normalized["version"]
                    or current["manifest_sha256"] != manifest_sha256):
                raise PluginError("plugin manifest changed during signature verification")
            if (current_key is None or current_key["status"] != "trusted"
                    or current_key["fingerprint_sha256"] != key["fingerprint_sha256"]):
                raise PluginError("publisher signing key was revoked during signature verification")
            existing_signature = self.connection.execute(
                "SELECT artifact_sha256 FROM plugin_signatures WHERE plugin_id=? AND version=? AND manifest_sha256=?",
                (normalized["id"], normalized["version"], manifest_sha256),
            ).fetchone()
            if existing_signature is not None and existing_signature["artifact_sha256"] != artifact_sha256:
                raise PluginError("plugin artifact changed without a plugin version or manifest change")
            self.connection.execute(
                """INSERT INTO plugin_signatures(plugin_id,version,manifest_sha256,key_id,artifact_sha256,
                   signature_sha256,status,verified_at) VALUES(?,?,?,?,?,?,'verified',?)
                   ON CONFLICT(plugin_id,version,manifest_sha256) DO UPDATE SET
                   key_id=excluded.key_id,artifact_sha256=excluded.artifact_sha256,
                   signature_sha256=excluded.signature_sha256,status='verified',verified_at=excluded.verified_at""",
                (normalized["id"], normalized["version"], manifest_sha256, key_id,
                 artifact_sha256, signature_sha256, time.time()),
            )
            self._audit(normalized["id"], "plugin.signature_verified", key_id, manifest_sha256)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        record["signature"] = self.get_signature(normalized["id"])
        record["execution_enabled"] = False
        return record

    def _audit(self, plugin_id: str, event: str, actor: str, digest: str) -> None:
        self.connection.execute(
            "INSERT INTO plugin_audit(plugin_id,event_type,actor,manifest_sha256,created_at) VALUES(?,?,?,?,?)",
            (plugin_id, event, actor, digest, time.time()),
        )

    def discover(self, manifest: Any) -> dict[str, Any]:
        normalized = validate_manifest(manifest)
        encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = time.time()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT * FROM plugins WHERE plugin_id=?", (normalized["id"],)
            ).fetchone()
            if existing and existing["manifest_sha256"] == digest:
                self.connection.commit()
                return self._public(existing)
            self.connection.execute(
                """INSERT INTO plugins(plugin_id,version,manifest_json,manifest_sha256,status,
                   approved_by,discovered_at,updated_at) VALUES(?,?,?,?,?,NULL,?,?)
                   ON CONFLICT(plugin_id) DO UPDATE SET version=excluded.version,
                   manifest_json=excluded.manifest_json,manifest_sha256=excluded.manifest_sha256,
                   status='discovered',approved_by=NULL,updated_at=excluded.updated_at""",
                (normalized["id"], normalized["version"], encoded, digest, "discovered", now, now),
            )
            self._audit(normalized["id"], "plugin.discovered" if existing is None else "plugin.manifest_changed",
                        "system", digest)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(normalized["id"])

    def approve(self, plugin_id: str, *, approved_by: str) -> dict[str, Any]:
        actor = _text(approved_by, "approved_by", 120)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM plugins WHERE plugin_id=?", (plugin_id,)).fetchone()
            if row is None:
                raise PluginError("plugin has not been discovered")
            if row["status"] == "quarantined":
                raise PluginError("quarantined plugins require a separate recovery review")
            self.connection.execute(
                "UPDATE plugins SET status='approved',approved_by=?,updated_at=? WHERE plugin_id=?",
                (actor, time.time(), plugin_id),
            )
            self._audit(plugin_id, "plugin.approved", actor, row["manifest_sha256"])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(plugin_id)

    def enable(self, plugin_id: str, *, enabled_by: str) -> dict[str, Any]:
        actor = _text(enabled_by, "enabled_by", 120)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM plugins WHERE plugin_id=?", (plugin_id,)).fetchone()
            if row is None or row["status"] not in {"approved", "disabled"} or not row["approved_by"]:
                raise PluginError("plugin requires explicit approval before enabling")
            manifest = json.loads(row["manifest_json"])
            for dependency_id, expected_version in manifest["dependencies"].items():
                dependency = self.connection.execute(
                    "SELECT version,status FROM plugins WHERE plugin_id=?", (dependency_id,)
                ).fetchone()
                if (dependency is None or dependency["version"] != expected_version
                        or dependency["status"] != "enabled"):
                    raise PluginError(f"plugin dependency is not enabled at the pinned version: {dependency_id}")
            self.connection.execute(
                "UPDATE plugins SET status='enabled',updated_at=? WHERE plugin_id=?",
                (time.time(), plugin_id),
            )
            self._audit(plugin_id, "plugin.enabled", actor, row["manifest_sha256"])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(plugin_id)

    def disable(self, plugin_id: str, *, disabled_by: str) -> dict[str, Any]:
        actor = _text(disabled_by, "disabled_by", 120)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM plugins WHERE plugin_id=?", (plugin_id,)).fetchone()
            if row is None:
                raise PluginError("plugin not found")
            dependents = self.connection.execute(
                "SELECT plugin_id,manifest_json FROM plugins WHERE status='enabled' AND plugin_id<>?",
                (plugin_id,),
            ).fetchall()
            for dependent in dependents:
                if plugin_id in json.loads(dependent["manifest_json"])["dependencies"]:
                    raise PluginError(f"cannot disable plugin while enabled dependent exists: {dependent['plugin_id']}")
            self.connection.execute(
                "UPDATE plugins SET status='disabled',updated_at=? WHERE plugin_id=?",
                (time.time(), plugin_id),
            )
            self._audit(plugin_id, "plugin.disabled", actor, row["manifest_sha256"])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get(plugin_id)

    def get(self, plugin_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM plugins WHERE plugin_id=?", (plugin_id,)).fetchone()
        if row is None:
            raise PluginError("plugin not found")
        return self._public(row)

    def list_plugins(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise PluginError("plugin list limit must be between 1 and 500")
        rows = self.connection.execute(
            "SELECT * FROM plugins ORDER BY updated_at DESC,plugin_id LIMIT ?", (limit,)
        ).fetchall()
        return [self._public(row) for row in rows]

    def count_plugins(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM plugins").fetchone()
        return int(row["count"])

    def list_audit_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise PluginError("plugin audit limit must be between 1 and 500")
        rows = self.connection.execute(
            """SELECT event_id,plugin_id,event_type,actor,manifest_sha256,created_at
               FROM plugin_audit ORDER BY event_id DESC LIMIT ?""", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def commander_request(
        self, plugin_id: str, capability_id: str, payload: Any, *,
        enqueue: Callable[..., Any], requested_by: str,
    ) -> dict[str, Any]:
        """Submit a declared capability to an injected Commander queue adapter.

        This method never invokes plugin code. Commander receives an explicit
        approval-required envelope and remains the only execution authority.
        The registry write lock is held through enqueue so a concurrent disable
        or manifest replacement cannot race capability admission.
        """
        if not callable(enqueue):
            raise TypeError("trusted Commander enqueue adapter is required")
        actor = _text(requested_by, "requested_by", 120)
        try:
            encoded_payload = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise PluginError("capability payload must be JSON serializable") from exc
        if len(encoded_payload.encode("utf-8")) > 64 * 1024:
            raise PluginError("capability payload exceeds 64 KiB")
        payload_digest = hashlib.sha256(encoded_payload.encode("utf-8")).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM plugins WHERE plugin_id=?", (plugin_id,)).fetchone()
            if row is None:
                raise PluginError("plugin must be enabled before requesting a capability")
            signature = self.get_signature(plugin_id)
            if signature.get("status") == "revoked":
                raise PluginError("publisher signing key has been revoked")
            if signature.get("status") != "verified":
                raise PluginError("plugin bundle must have a verified publisher signature before requesting Commander approval")
            if row["status"] != "enabled":
                raise PluginError("plugin must be enabled before requesting a capability")
            manifest = json.loads(row["manifest_json"])
            capability = next((item for item in manifest["capabilities"] if item["id"] == capability_id), None)
            if capability is None:
                raise PluginError("capability is not declared by this plugin")
            request = {
                "plugin_id": plugin_id,
                "plugin_version": row["version"],
                "plugin_manifest_sha256": row["manifest_sha256"],
                "publisher_signature": {
                    "status": signature["status"],
                    "key_id": signature["key_id"],
                    "artifact_sha256": signature["artifact_sha256"],
                    "signature_sha256": signature["signature_sha256"],
                    "verified_at": signature["verified_at"],
                },
                "capability_id": capability_id,
                "action_class": capability["risk"],
                "required_permissions": capability["permissions"],
                "requested_by": actor,
                "approval_required": True,
                "proposed_resource_budget": proposed_resource_budget(capability["risk"]),
                "execution_enabled": False,
                "payload": json.loads(encoded_payload),
            }
            request_identity = hashlib.sha256(
                f"{plugin_id}\0{row['manifest_sha256']}\0{capability_id}\0{payload_digest}".encode("utf-8")
            ).hexdigest()
            idempotency_key = f"plugin:{request_identity}"
            result = enqueue(request, idempotency_key=idempotency_key)
            try:
                encoded_result = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise PluginError("Commander adapter returned a non-serializable result") from exc
            if len(encoded_result.encode("utf-8")) > 64 * 1024:
                raise PluginError("Commander adapter result exceeds 64 KiB")
            self.connection.commit()
            return {"status": "queued_for_commander_approval", "request": request,
                    "idempotency_key": idempotency_key, "commander_result": result}
        except Exception:
            self.connection.rollback()
            raise

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        plugin_id = row["plugin_id"]
        return {
            "plugin_id": plugin_id, "version": row["version"],
            "manifest": json.loads(row["manifest_json"]), "manifest_sha256": row["manifest_sha256"],
            "status": row["status"], "approved_by": row["approved_by"],
            "discovered_at": row["discovered_at"], "updated_at": row["updated_at"],
            "signature": self.get_signature(plugin_id),
            "execution_enabled": False,
        }


def sync_plugin_capabilities(plugin_registry: PluginRegistry, capability_registry: Any) -> dict[str, int]:
    """Project declarative plugin manifests into Muse's shared capability catalog.

    This is metadata synchronization only: plugin capabilities remain
    ``needs_approval`` even when the plugin is enabled, because no runtime
    execution adapter is implicitly granted by discovery or enablement.
    """
    from muse.capabilities import CapabilityRegistry

    if not isinstance(plugin_registry, PluginRegistry):
        raise TypeError("plugin_registry must be a trusted PluginRegistry")
    if not isinstance(capability_registry, CapabilityRegistry):
        raise TypeError("capability_registry must be a trusted CapabilityRegistry")

    active_ids: set[str] = set()
    registered = 0
    updated = 0
    disabled = 0
    plugin_count_before = plugin_registry.count_plugins()
    plugins = plugin_registry.list_plugins(limit=500)
    plugin_count_after = plugin_registry.count_plugins()
    complete_snapshot = plugin_count_before == plugin_count_after == len(plugins)
    for plugin in plugins:
        manifest = plugin["manifest"]
        plugin_id = plugin["plugin_id"]
        plugin_state = plugin["status"]
        signature_state = plugin.get("signature", {}).get("status")
        for capability in manifest["capabilities"]:
            source_capability_id = capability["id"]
            catalog_id = f"plugin.{plugin_id}.{source_capability_id}"
            active_ids.add(catalog_id)
            existing_entry = capability_registry.get(catalog_id)
            existed = existing_entry is not None
            if existed:
                existing_metadata = existing_entry.get("metadata", {})
                if (existing_metadata.get("source") != "muse_plugin_registry"
                        or existing_metadata.get("plugin_id") != plugin_id
                        or existing_metadata.get("source_capability_id") != source_capability_id):
                    raise PluginError(f"plugin capability catalog id collides with an existing capability: {catalog_id}")
            # Enabled only means the manifest passed its management gates. A
            # trusted, reviewed runtime adapter is a separate prerequisite.

            catalog_status = (
                "disabled"
                if plugin_state in {"disabled", "quarantined"} or signature_state == "revoked"
                else "needs_approval"
            )
            capability_registry.register(
                capability_id=catalog_id,
                domain="plugin",
                name=f"{manifest['name']} / {source_capability_id}",
                description=capability["description"],
                status=catalog_status,
                executable=None,
                capabilities=[f"plugin.{plugin_id}.{source_capability_id}"],
                requirements=capability["permissions"],
                trust="declarative_manifest_untrusted",
                license="unknown",
                version=plugin["version"],
                metadata={
                    "source": "muse_plugin_registry",
                    "plugin_id": plugin_id,
                    "plugin_version": plugin["version"],
                    "plugin_status": plugin_state,
                    "signature_status": signature_state,
                    "plugin_manifest_sha256": plugin["manifest_sha256"],
                    "source_capability_id": source_capability_id,
                    "risk": capability["risk"],
                    "permissions": capability["permissions"],
                    "execution_enabled": False,
                    "runtime_adapter_registered": False,
                },
            )
            if catalog_status == "disabled":
                disabled += 1
            elif existed:
                updated += 1
            else:
                registered += 1

    # A manifest update may remove a previously declared capability. Retain its
    # audit trail but make the stale catalog entry unschedulable.
    if complete_snapshot:
        for existing in capability_registry.list_all(domain="plugin"):
            metadata = existing.get("metadata", {})
            if (metadata.get("source") == "muse_plugin_registry"
                    and existing["capability_id"] not in active_ids
                    and existing["status"] != "disabled"):
                metadata = {**metadata, "execution_enabled": False, "catalog_state": "removed_from_manifest"}
                capability_registry.register(
                    capability_id=existing["capability_id"], domain="plugin",
                    name=existing["name"], description=existing["description"],
                    status="disabled", executable=None, capabilities=existing["capabilities"],
                    requirements=existing["requirements"], trust=existing["trust"],
                    license=existing["license"], version=existing.get("version"), metadata=metadata,
                )
                disabled += 1
    return {"registered": registered, "updated": updated, "disabled": disabled,
            "total": len(active_ids)}


def _plugin_capability_database_paths(
    plugin_database: str | Path, capability_database: str | Path,
) -> tuple[Path, Path]:
    plugin_path = Path(plugin_database).expanduser()
    capability_path = Path(capability_database).expanduser()
    for label, path in (("plugin registry", plugin_path), ("capability catalog", capability_path)):
        if path.is_symlink() or not path.is_file():
            raise PluginError(f"{label} must be an existing regular non-symlink database file")
    try:
        plugin_resolved = plugin_path.resolve(strict=True)
        capability_resolved = capability_path.resolve(strict=True)
    except OSError as exc:
        raise PluginError("plugin registry and capability catalog paths must resolve") from exc
    if os.path.normcase(str(plugin_resolved)) == os.path.normcase(str(capability_resolved)):
        raise PluginError("plugin registry and capability catalog must be separate database files")
    return plugin_resolved, capability_resolved


def sync_plugin_capability_databases(
    plugin_database: str | Path, capability_database: str | Path,
) -> dict[str, int]:
    """Reconcile the separate plugin and capability SQLite stores explicitly."""
    from muse.capabilities import CapabilityRegistry

    plugin_path, capability_path = _plugin_capability_database_paths(plugin_database, capability_database)
    try:
        with PluginRegistry(plugin_path) as plugin_registry, CapabilityRegistry(capability_path) as capability_registry:
            return sync_plugin_capabilities(plugin_registry, capability_registry)
    except PluginError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise PluginError("plugin capability catalog synchronization failed") from exc


def revoke_publisher_key_and_sync(
    plugin_database: str | Path, capability_database: str | Path, key_id: str, *, revoked_by: str,
) -> dict[str, Any]:
    """Revoke a publisher, then reconcile the separate capability catalog.

    The two SQLite files cannot share one atomic transaction. If projection fails,
    the key remains revoked and this error explicitly directs the operator to the
    idempotent sync-capabilities repair command.
    """
    from muse.capabilities import CapabilityRegistry

    plugin_path, capability_path = _plugin_capability_database_paths(plugin_database, capability_database)
    try:
        with PluginRegistry(plugin_path) as plugin_registry, CapabilityRegistry(capability_path) as capability_registry:
            revocation = plugin_registry.revoke_publisher_key(key_id, revoked_by=revoked_by)
            try:
                synchronization = sync_plugin_capabilities(plugin_registry, capability_registry)
            except Exception as exc:
                raise PluginError(
                    "publisher key is revoked, but capability catalog synchronization failed; "
                    "rerun muse-plugins sync-capabilities with both database paths"
                ) from exc
    except PluginError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise PluginError("publisher revocation or capability catalog synchronization failed") from exc
    return {
        "revocation": revocation,
        "capability_sync": synchronization,
        "atomic_across_databases": False,
    }


def commander_task_queue_adapter(task_database: str | Path) -> Callable[..., dict[str, Any]]:
    """Build a trusted adapter that persists plugin requests as Commander approval tickets.

    No plugin code is executed by this adapter. A future runtime adapter must be
    explicitly registered with Commander before an approved plugin ticket can run.
    """
    database = Path(task_database)
    if database.is_symlink() or (database.exists() and not database.is_file()):
        raise PluginError("Commander task database must be a regular non-symlink file")

    def enqueue(request: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        from muse.task_queue import TaskQueue

        risk = request["action_class"]
        workload_class = {
            "read_only": "general",
            "research": "research",
            "code_change": "coding",
            "external_action": "operations",
        }[risk]
        title = f"Plugin request: {request['plugin_id']}/{request['capability_id']}"
        payload = {
            "kind": "plugin_capability_request",
            "plugin_capability_request": request,
            "execution_adapter_required": True,
            "execution_enabled": False,
        }
        with TaskQueue(database) as queue:
            task = queue.enqueue(
                title, payload, priority=50, idempotency_key=idempotency_key,
                require_approval=True, workload_class=workload_class, recovery_policy="manual",
            )
        return {"task_id": task["id"], "task_status": task["status"], "task_kind": "plugin_capability_request"}

    return enqueue


def validate_approved_plugin_ticket(
    plugin_database: str | Path, task_database: str | Path, task_id: str,
    *, worker_id: str | None = None,
) -> dict[str, Any]:
    """Revalidate a human-approved plugin ticket before a future trusted Worker sees it.

    This is an admission/audit check only. It never executes code and always
    returns execution_enabled=False because no sandbox runtime is registered.
    """
    from muse.task_queue import TaskQueue

    plugin_path = Path(plugin_database).expanduser()
    task_path = Path(task_database).expanduser()
    if (plugin_path.is_symlink() or task_path.is_symlink()
            or not plugin_path.is_file() or not task_path.is_file()):
        raise PluginError("plugin and Commander databases must be existing regular non-symlink files")
    try:
        if os.path.normcase(str(plugin_path.resolve(strict=True))) == os.path.normcase(str(task_path.resolve(strict=True))):
            raise PluginError("plugin registry and Commander task database must be separate files")
    except OSError as exc:
        raise PluginError("plugin and Commander database paths must resolve") from exc

    with PluginRegistry(plugin_path) as registry, TaskQueue(task_path) as queue:
        task = queue.get(task_id)
        if task is None:
            raise PluginError("Commander plugin ticket does not exist")
        if task.get("status") == "waiting_approval":
            raise PluginError("plugin ticket has not been explicitly approved")
        if task.get("status") not in {"queued", "running"}:
            raise PluginError("Commander plugin ticket is not in an approved dispatch state")
        if task.get("status") == "running":
            if (not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 120
                    or task.get("lease_owner") != worker_id.strip()):
                raise PluginError("running plugin ticket requires the current Worker lease owner")
            lease_value = task.get("lease_expires_at")
            try:
                lease_expiry = datetime.fromisoformat(lease_value.replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError):
                raise PluginError("plugin Worker lease expiry is invalid") from None
            if lease_expiry.tzinfo is None or lease_expiry.utcoffset() is None:
                raise PluginError("plugin Worker lease expiry is invalid")
            if lease_expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                raise PluginError("plugin Worker lease has expired")
            attempts = task.get("attempts")
            if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
                raise PluginError("plugin Worker attempt metadata is invalid")
            lease_events = [
                event for event in queue.events(task_id, limit=500)
                if event.get("event_type") in {"assignment.claimed", "assignment.lease_heartbeat"}
            ]
            if not lease_events:
                raise PluginError("running plugin ticket is missing its matching Worker claim audit event")
            latest_lease_event = lease_events[-1]
            expected_lease_payload = {
                "attempt": attempts,
                "lease_epoch": attempts,
                "lease_id": f"{task_id}:{attempts}",
                "heartbeat_at": task.get("heartbeat_at"),
                "lease_expires_at": lease_value,
            }
            if (
                latest_lease_event.get("actor") != worker_id.strip()
                or latest_lease_event.get("payload") != expected_lease_payload
            ):
                raise PluginError("plugin Worker lease expiry does not match the latest lease audit")
        envelope = task.get("payload")
        if not isinstance(envelope, dict) or envelope.get("kind") != "plugin_capability_request":
            raise PluginError("Commander ticket is not a plugin capability request")
        if envelope.get("execution_enabled") is not False or envelope.get("execution_adapter_required") is not True:
            raise PluginError("plugin ticket execution boundary is invalid")
        if envelope.get("_approved") is not True:
            raise PluginError("plugin ticket has not been explicitly approved")
        approval = envelope.get("_approval")
        if (not isinstance(approval, dict) or not isinstance(approval.get("approved_by"), str)
                or not approval["approved_by"].strip()):
            raise PluginError("plugin ticket approval metadata is invalid")
        events = queue.events(task_id, limit=500)
        matching_approval_events = [
            event for event in events
            if event.get("event_type") == "human.decision"
            and event.get("actor") == approval["approved_by"]
            and event.get("payload") == {"decision": "approved"}
        ]
        if not matching_approval_events:
            raise PluginError("plugin ticket is missing its matching human approval audit event")
        if not any(
            TaskQueue.approval_timestamp_matches_event(approval.get("approved_at"), event.get("created_at"))
            for event in matching_approval_events
        ):
            raise PluginError("plugin ticket approval timestamp does not match its audit event")

        request = envelope.get("plugin_capability_request")
        if not isinstance(request, dict) or request.get("approval_required") is not True:
            raise PluginError("plugin capability request envelope is invalid")
        plugin_id = request.get("plugin_id")
        if not isinstance(plugin_id, str) or not plugin_id:
            raise PluginError("plugin capability request identity is invalid")
        current = registry.get(plugin_id)
        if current.get("status") != "enabled":
            raise PluginError("plugin is no longer enabled; approved ticket is stale")
        if (current.get("version") != request.get("plugin_version")
                or current.get("manifest_sha256") != request.get("plugin_manifest_sha256")):
            raise PluginError("plugin version or manifest changed after approval; ticket is stale")
        current_signature = current.get("signature") or {}
        signed = request.get("publisher_signature")
        if (not isinstance(signed, dict) or current_signature.get("status") != "verified"
                or any(current_signature.get(key) != signed.get(key)
                       for key in ("key_id", "artifact_sha256", "signature_sha256"))):
            raise PluginError("publisher signature is no longer verified or no longer matches the approved ticket")
        manifest_capabilities = current["manifest"]["capabilities"]
        capability = next((item for item in manifest_capabilities if item["id"] == request.get("capability_id")), None)
        if (capability is None or capability.get("risk") != request.get("action_class")
                or capability.get("permissions") != request.get("required_permissions")):
            raise PluginError("plugin capability declaration changed after approval; ticket is stale")
        return {
            "status": "approved_for_worker_review",
            "task_id": task_id,
            "plugin_id": plugin_id,
            "plugin_version": current["version"],
            "capability_id": request["capability_id"],
            "publisher_signature": dict(signed),
            "approval": dict(approval),
            "execution_enabled": False,
            "runtime_adapter_registered": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and inspect Muse declarative plugin manifests")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("manifest", type=Path)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--database", required=True, type=Path)
    list_parser.add_argument("--limit", type=int, default=100)
    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("manifest", type=Path)
    discover_parser.add_argument("--database", required=True, type=Path)
    trust_parser = subparsers.add_parser("trust-key")
    trust_parser.add_argument("key_id")
    trust_parser.add_argument("--publisher", required=True)
    trust_parser.add_argument("--public-key-file", required=True, type=Path)
    trust_parser.add_argument("--database", required=True, type=Path)
    trust_parser.add_argument("--trusted-by", required=True)
    revoke_parser = subparsers.add_parser("revoke-key")
    revoke_parser.add_argument("key_id")
    revoke_parser.add_argument("--database", required=True, type=Path)
    revoke_parser.add_argument("--capability-db", required=True, type=Path)
    revoke_parser.add_argument("--revoked-by", required=True)
    sync_parser = subparsers.add_parser("sync-capabilities")
    sync_parser.add_argument("--database", required=True, type=Path)
    sync_parser.add_argument("--capability-db", required=True, type=Path)
    signed_parser = subparsers.add_parser("discover-signed")
    signed_parser.add_argument("manifest", type=Path)
    signed_parser.add_argument("artifact", type=Path)
    signed_parser.add_argument("--key-id", required=True)
    signed_parser.add_argument("--signature-file", required=True, type=Path)
    signed_parser.add_argument("--database", required=True, type=Path)
    for command, actor_option in (("approve", "--approved-by"), ("enable", "--enabled-by"), ("disable", "--disabled-by")):
        action_parser = subparsers.add_parser(command)
        action_parser.add_argument("plugin_id")
        action_parser.add_argument("--database", required=True, type=Path)
        action_parser.add_argument(actor_option, required=True)
    request_parser = subparsers.add_parser("request")
    request_parser.add_argument("plugin_id")
    request_parser.add_argument("capability_id")
    request_parser.add_argument("payload", type=Path)
    request_parser.add_argument("--database", required=True, type=Path)
    request_parser.add_argument("--task-db", required=True, type=Path)
    request_parser.add_argument("--requested-by", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            raw = args.manifest.read_bytes()
            if len(raw) > MAX_MANIFEST_BYTES:
                raise PluginError("plugin manifest exceeds the 64 KiB limit")
            result = validate_manifest(json.loads(raw.decode("utf-8")))
        elif args.command == "discover":
            raw = args.manifest.read_bytes()
            if len(raw) > MAX_MANIFEST_BYTES:
                raise PluginError("plugin manifest exceeds the 64 KiB limit")
            with PluginRegistry(args.database) as registry:
                result = registry.discover(json.loads(raw.decode("utf-8")))
        elif args.command == "trust-key":
            public_key = args.public_key_file.read_bytes()
            with PluginRegistry(args.database) as registry:
                result = registry.trust_publisher_key(
                    args.key_id, args.publisher, public_key, trusted_by=args.trusted_by,
                )
        elif args.command == "revoke-key":
            result = revoke_publisher_key_and_sync(
                args.database, args.capability_db, args.key_id, revoked_by=args.revoked_by,
            )
        elif args.command == "sync-capabilities":
            result = {"capability_sync": sync_plugin_capability_databases(args.database, args.capability_db)}
        elif args.command == "discover-signed":
            raw = args.manifest.read_bytes()
            if len(raw) > MAX_MANIFEST_BYTES:
                raise PluginError("plugin manifest exceeds the 64 KiB limit")
            signature = args.signature_file.read_bytes()
            with PluginRegistry(args.database) as registry:
                result = registry.discover_signed(
                    json.loads(raw.decode("utf-8")), args.artifact,
                    key_id=args.key_id, signature=signature,
                )
        elif args.command == "list":
            with PluginRegistry(args.database) as registry:
                result = {"plugins": registry.list_plugins(limit=args.limit)}
        elif args.command == "approve":
            with PluginRegistry(args.database) as registry:
                result = registry.approve(args.plugin_id, approved_by=getattr(args, "approved_by"))
        elif args.command == "enable":
            with PluginRegistry(args.database) as registry:
                result = registry.enable(args.plugin_id, enabled_by=getattr(args, "enabled_by"))
        elif args.command == "disable":
            with PluginRegistry(args.database) as registry:
                result = registry.disable(args.plugin_id, disabled_by=getattr(args, "disabled_by"))
        else:
            raw = args.payload.read_bytes()
            if len(raw) > 64 * 1024:
                raise PluginError("capability payload exceeds 64 KiB")
            payload = json.loads(raw.decode("utf-8"))
            with PluginRegistry(args.database) as registry:
                result = registry.commander_request(
                    args.plugin_id, args.capability_id, payload,
                    enqueue=commander_task_queue_adapter(args.task_db),
                    requested_by=args.requested_by,
                )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, PluginError, ValueError) as exc:
        parser.exit(2, f"muse-plugins: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
