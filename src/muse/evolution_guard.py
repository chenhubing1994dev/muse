"""B394 controlled self-evolution guard: protected surfaces, signed patch gate, versioned rollback.

Everything here runs *before* :class:`muse.evolution_executor.ApprovedCodeChangeExecutor`
writes a single source byte:

1. :func:`enforce_protected_surfaces` — hard-coded, non-configurable deny list. Patches that
   touch the charter, red lines, approval gates, the trusted key store, audit logs, circuit
   breakers or resource limits are refused, and so are patches whose *new* text introduces
   gate-disabling identifiers that were absent from the approved snapshot.
2. :class:`PatchSignatureGate` — an independent Ed25519 signature over the canonical patch
   manifest (ticket, proposal, every file hash, old/new text hashes, nonce, issue time). Keys
   come from the pinned reviewer trust store; the proposer/implementer can never sign its own
   patch; nonces are single-use in an append-only receipt table; signatures expire.
3. :class:`EvolutionVersionLedger` — every committed patch set becomes a numbered version that
   stores the original bytes, so any version can be rolled back atomically (all files or none,
   never over a concurrent edit).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from muse.reviewer_attestation import (
    ReviewAttestationError,
    canonical_json,
    load_trust_store_from_environment,
    sign_bytes,
    verify_bytes,
)

PATCH_SIGNATURE_DOMAIN = b"MUSE-EVOLUTION-PATCH-V1\x00"
PATCH_MANIFEST_SCHEMA = "muse.evolution.patch_manifest.v1"
DEFAULT_SIGNATURE_TTL_SECONDS = 24 * 3600
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{32,64}\Z")

# --------------------------------------------------------------------------------------
# Protected surfaces (hard-coded on purpose; there is no configuration hook to relax them)
# --------------------------------------------------------------------------------------
PROTECTED_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("charter", ("docs/agent_build_charter.md", "agents.md", "docs/*charter*", "*constitution*")),
    ("red_lines", ("*red_line*", "*redline*", "*red-line*", "config/*policy*")),
    ("approval_gates", (
        "src/muse/review_gate.py", "src/muse/signed_review.py", "src/muse/review_desk.py",
        "src/muse/task_queue.py", "src/muse/worker.py", "src/muse/evolution_executor.py",
        "src/muse/evolution_guard.py", "src/muse/evolution_journal.py", "src/muse/improvement.py",
        "src/muse/video_reviewer_attestation.py", "src/muse/video_reviewer_signer.py",
        "src/muse/contextual_inquiry_security.py", "src/muse/companion_security.py",
    )),
    ("trust_store", ("src/muse/reviewer_attestation.py", "*trust-store*", "*trust_store*",
                     "*reviewer-keys*", "*reviewerkeys*", "*.pem", "*.key")),
    ("audit_log", ("src/muse/progress.py", "src/muse/usage_ledger.py", "src/muse/revenue_ledger.py", "*audit*")),
    ("circuit_breaker", ("src/muse/autonomy_safety.py", "src/muse/watchdog.py",
                         "src/muse/autonomous_runtime.py", "src/muse/daemon_control.py", "*kill_switch*")),
    ("resource_limits", ("src/muse/resource_governor.py", "src/muse/process_limits.py",
                         "src/muse/gpu_resources.py", "src/muse/provider_policy.py")),
    ("plugin_isolation", ("src/muse/plugin_platform.py", "src/muse/plugin_capability_broker.py")),
    ("secrets", (".env", ".env.*", "config/muse.toml", ".git/*", "*.sqlite3", "*.sqlite", "*.db")),
)
# Identifiers that a patch may never *introduce* into any file (it may keep pre-existing ones).
GATE_DISABLING_TOKENS: tuple[str, ...] = (
    "PROTECTED_SURFACES", "GATE_DISABLING_TOKENS", "PatchSignatureGate", "enforce_protected_surfaces",
    "kill_switch", "KillSwitch", "SafetyController", "approval_required_kinds",
    "signed_review_required_kinds", "require_production", "MUSE_REVIEWER_TRUST_STORE",
    "complete_signed_review", "_approved", "ALLOW_UNSIGNED", "os.system(", "subprocess.", "eval(", "exec(",
    "__import__(", "importlib.", "ctypes", "shutil.rmtree",
)


class ProtectedSurfaceViolation(PermissionError):
    """A patch attempted to modify a protected governance surface."""


class PatchSignatureError(PermissionError):
    """The patch manifest signature is missing, invalid, replayed, expired or self-issued."""


def _normalise_relative(path: str) -> str:
    if not isinstance(path, str) or not path.strip() or len(path) > 240 or "\x00" in path:
        raise ProtectedSurfaceViolation("patch path is invalid")
    text = path.replace("\\", "/").strip()
    pure = PurePosixPath(text)
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or any(p in {"", ".", ".."} for p in text.split("/")):
        raise ProtectedSurfaceViolation("patch path must be workspace-relative without traversal")
    return pure.as_posix().casefold()


def protected_surface_for(path: str) -> str | None:
    """Return the protected-surface label matching ``path`` (case-insensitive), else ``None``."""
    relative = _normalise_relative(path)
    name = relative.rsplit("/", 1)[-1]
    for label, patterns in PROTECTED_SURFACES:
        for pattern in patterns:
            if fnmatch.fnmatchcase(relative, pattern) or ("/" not in pattern and fnmatch.fnmatchcase(name, pattern)):
                return label
    return None


def enforce_protected_surfaces(patches: Iterable[dict[str, Any]]) -> list[str]:
    """Raise :class:`ProtectedSurfaceViolation` for any protected target or gate-disabling insertion."""
    checked: list[str] = []
    for patch in patches:
        if not isinstance(patch, dict):
            raise ProtectedSurfaceViolation("each patch must be an object")
        relative = _normalise_relative(patch.get("path", ""))
        label = protected_surface_for(relative)
        if label is not None:
            raise ProtectedSurfaceViolation(f"patch targets protected surface '{label}': {relative}")
        old_text = patch.get("old_text") if isinstance(patch.get("old_text"), str) else ""
        new_text = patch.get("new_text") if isinstance(patch.get("new_text"), str) else ""
        for token in GATE_DISABLING_TOKENS:
            pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(token))
            if len(pattern.findall(new_text)) > len(pattern.findall(old_text)):
                raise ProtectedSurfaceViolation(f"patch introduces governance-sensitive token '{token}' in {relative}")
        checked.append(relative)
    return checked


# --------------------------------------------------------------------------------------
# Canonical manifest + Ed25519 signature gate
# --------------------------------------------------------------------------------------
def _sha_text(value: Any) -> str:
    return hashlib.sha256((value if isinstance(value, str) else "").encode("utf-8")).hexdigest()


def build_patch_manifest(*, ticket_id: str, proposal_id: str, patches: list[dict[str, Any]],
                         nonce: str, issued_at: int) -> dict[str, Any]:
    if not isinstance(patches, list) or not 1 <= len(patches) <= 8:
        raise PatchSignatureError("manifest requires 1-8 patches")
    if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
        raise PatchSignatureError("nonce must be 32-64 lowercase hex characters")
    if isinstance(issued_at, bool) or not isinstance(issued_at, int) or issued_at <= 0:
        raise PatchSignatureError("issued_at must be a positive integer timestamp")
    for name, value in (("ticket_id", ticket_id), ("proposal_id", proposal_id)):
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise PatchSignatureError(f"{name} is required")
    entries = []
    for patch in patches:
        if not isinstance(patch, dict):
            raise PatchSignatureError("patch must be an object")
        expected = patch.get("expected_sha256")
        if not isinstance(expected, str) or _HEX64.fullmatch(expected) is None:
            raise PatchSignatureError("patch expected_sha256 is invalid")
        entries.append({
            "path": _normalise_relative(patch.get("path", "")),
            "expected_sha256": expected,
            "old_text_sha256": _sha_text(patch.get("old_text")),
            "new_text_sha256": _sha_text(patch.get("new_text")),
        })
    return {"schema": PATCH_MANIFEST_SCHEMA, "ticket_id": ticket_id, "proposal_id": proposal_id,
            "patches": entries, "nonce": nonce, "issued_at": issued_at}


def manifest_message(manifest: dict[str, Any]) -> bytes:
    return PATCH_SIGNATURE_DOMAIN + canonical_json(manifest)


def sign_patch_manifest(seed: bytes, *, key_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Produce the ``_patch_signature`` block (used by the offline reviewer tool and tests)."""
    signature = sign_bytes(seed, manifest_message(manifest))
    return {"key_id": key_id, "nonce": manifest["nonce"], "issued_at": manifest["issued_at"],
            "signature_hex": signature.hex()}


class PatchSignatureGate:
    """Fail-closed Ed25519 verifier for approved_code_change patch sets."""

    def __init__(self, trusted_keys: dict[str, dict[str, Any]], receipts_database: str | Path, *,
                 require_production: bool = True, forbidden_signers: Iterable[str] = (),
                 ttl_seconds: int = DEFAULT_SIGNATURE_TTL_SECONDS, clock: Callable[[], float] = time.time) -> None:
        if not isinstance(trusted_keys, dict) or not trusted_keys:
            raise PatchSignatureError("no trusted reviewer keys are configured; signed evolution is unavailable")
        self.trusted_keys = trusted_keys
        self.require_production = bool(require_production)
        self.forbidden_signers = {str(item) for item in forbidden_signers if item}
        self.ttl_seconds = int(ttl_seconds)
        self.clock = clock
        self.path = Path(receipts_database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS evolution_patch_receipts(
                nonce TEXT PRIMARY KEY, key_id TEXT NOT NULL, reviewer_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, consumed_at INTEGER NOT NULL);
            CREATE TRIGGER IF NOT EXISTS evolution_patch_receipts_no_update BEFORE UPDATE ON evolution_patch_receipts
                BEGIN SELECT RAISE(ABORT,'evolution patch receipts are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS evolution_patch_receipts_no_delete BEFORE DELETE ON evolution_patch_receipts
                BEGIN SELECT RAISE(ABORT,'evolution patch receipts are append-only'); END;
        """)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PatchSignatureGate":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def verify(self, payload: dict[str, Any], *, task_id: str, implementer_id: str = "") -> dict[str, Any]:
        block = payload.get("_patch_signature") if isinstance(payload, dict) else None
        if not isinstance(block, dict) or set(block) != {"key_id", "nonce", "issued_at", "signature_hex"}:
            raise PatchSignatureError("approved_code_change requires an independent _patch_signature block")
        key = self.trusted_keys.get(block["key_id"]) if isinstance(block.get("key_id"), str) else None
        if key is None or key.get("status") != "trusted":
            raise PatchSignatureError("patch signing key is not trusted")
        if self.require_production and key.get("purpose") != "production":
            raise PatchSignatureError("ephemeral/test keys cannot authorise source mutation")
        reviewer_id = str(key.get("reviewer_id", ""))
        approver = (payload.get("_approval") or {}).get("approved_by") if isinstance(payload.get("_approval"), dict) else None
        forbidden = self.forbidden_signers | {implementer_id} | {str(payload.get("proposer_id") or "")}
        if reviewer_id in forbidden or not reviewer_id:
            raise PatchSignatureError("the proposer/implementer cannot sign its own patch")
        issued_at = block.get("issued_at")
        now = int(self.clock())
        if isinstance(issued_at, bool) or not isinstance(issued_at, int) or issued_at > now + 300 \
                or now - issued_at > self.ttl_seconds:
            raise PatchSignatureError("patch signature is expired or issued in the future")
        manifest = build_patch_manifest(ticket_id=task_id, proposal_id=str(payload.get("proposal_id") or ""),
                                        patches=payload.get("patches"), nonce=block["nonce"], issued_at=issued_at)
        signature_hex = block.get("signature_hex")
        if not isinstance(signature_hex, str) or re.fullmatch(r"[0-9a-f]{128}", signature_hex) is None:
            raise PatchSignatureError("signature_hex must be 128 lowercase hex characters")
        if not verify_bytes(key["public_key"], manifest_message(manifest), bytes.fromhex(signature_hex)):
            raise PatchSignatureError("patch manifest signature verification failed")
        manifest_sha = hashlib.sha256(canonical_json(manifest)).hexdigest()
        try:
            self.connection.execute(
                "INSERT INTO evolution_patch_receipts(nonce,key_id,reviewer_id,ticket_id,manifest_sha256,consumed_at)"
                " VALUES(?,?,?,?,?,?)", (block["nonce"], block["key_id"], reviewer_id, task_id, manifest_sha, now))
        except sqlite3.IntegrityError as exc:
            raise PatchSignatureError("patch signature nonce was already consumed (replay)") from exc
        return {"status": "verified", "key_id": block["key_id"], "reviewer_id": reviewer_id,
                "manifest_sha256": manifest_sha, "approved_by": approver}


def gate_from_environment(receipts_database: str | Path, *, implementer_id: str) -> PatchSignatureGate:
    """Build the production gate from the pinned trust store; raises when unconfigured (fail closed)."""
    try:
        keys = load_trust_store_from_environment()
    except ReviewAttestationError as exc:
        raise PatchSignatureError(f"reviewer trust store rejected: {exc}") from exc
    return PatchSignatureGate(keys, receipts_database, forbidden_signers={implementer_id})


# --------------------------------------------------------------------------------------
# Versioned apply + atomic rollback
# --------------------------------------------------------------------------------------
def _atomic_write(path: Path, content: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".muse-evo-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class EvolutionVersionLedger:
    """Numbered history of committed self-evolution patch sets with one-click atomic rollback."""

    def __init__(self, database: str | Path, workspace: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.clock = clock
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS evolution_versions(
                version INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
                signature_manifest_sha256 TEXT NOT NULL DEFAULT '', status TEXT NOT NULL
                CHECK(status IN ('applied','rolled_back')), applied_at REAL NOT NULL, rolled_back_at REAL,
                rolled_back_by TEXT);
            CREATE TABLE IF NOT EXISTS evolution_version_files(
                version INTEGER NOT NULL REFERENCES evolution_versions(version), relative_path TEXT NOT NULL,
                original_bytes BLOB NOT NULL, original_sha256 TEXT NOT NULL, candidate_sha256 TEXT NOT NULL,
                PRIMARY KEY(version, relative_path));
        """)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "EvolutionVersionLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _resolve(self, relative: str) -> Path:
        _normalise_relative(relative)
        candidate = (self.workspace / relative)
        cursor = self.workspace
        for part in Path(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("rollback path contains a symlink")
        resolved = candidate.resolve(strict=True)
        if self.workspace not in resolved.parents:
            raise ValueError("rollback path escapes the workspace")
        return resolved

    def record(self, *, task_id: str, proposal_id: str, files: list[tuple[str, bytes, str]],
               signature_manifest_sha256: str = "") -> int:
        if not files:
            raise ValueError("a version needs at least one file")
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO evolution_versions(task_id,proposal_id,signature_manifest_sha256,status,applied_at)"
                " VALUES(?,?,?,'applied',?)", (task_id, proposal_id, signature_manifest_sha256, self.clock()))
            version = int(cursor.lastrowid)
            for relative, original, candidate_sha in files:
                self.connection.execute(
                    "INSERT INTO evolution_version_files VALUES(?,?,?,?,?)",
                    (version, relative, original, hashlib.sha256(original).hexdigest(), candidate_sha))
        return version

    def list_versions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT v.*, (SELECT COUNT(*) FROM evolution_version_files f WHERE f.version=v.version) AS files"
            " FROM evolution_versions v ORDER BY version DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
        return [dict(row) for row in rows]

    def rollback(self, version: int, *, actor: str) -> dict[str, Any]:
        """Restore every file of ``version`` or none of them; refuse if any file drifted."""
        row = self.connection.execute("SELECT * FROM evolution_versions WHERE version=?", (int(version),)).fetchone()
        if row is None:
            raise ValueError("evolution version not found")
        if row["status"] != "applied":
            raise ValueError("evolution version is already rolled back")
        newer = self.connection.execute(
            "SELECT version FROM evolution_versions WHERE version>? AND status='applied' ORDER BY version",
            (int(version),)).fetchall()
        files = self.connection.execute(
            "SELECT * FROM evolution_version_files WHERE version=? ORDER BY relative_path", (int(version),)).fetchall()
        newer_paths = set()
        for item in newer:
            newer_paths.update(r[0] for r in self.connection.execute(
                "SELECT relative_path FROM evolution_version_files WHERE version=?", (item[0],)))
        overlap = sorted(newer_paths & {f["relative_path"] for f in files})
        if overlap:
            return {"status": "blocked_newer_version", "version": int(version), "paths": overlap}
        targets = []
        for item in files:
            try:
                path = self._resolve(item["relative_path"])
                current = path.read_bytes()
            except (OSError, ValueError):
                return {"status": "blocked_missing_file", "version": int(version), "path": item["relative_path"]}
            if hashlib.sha256(current).hexdigest() != item["candidate_sha256"]:
                return {"status": "blocked_concurrent_edit", "version": int(version), "path": item["relative_path"]}
            targets.append((path, current, bytes(item["original_bytes"])))
        restored: list[tuple[Path, bytes]] = []
        try:
            for path, current, original in targets:
                _atomic_write(path, original)
                restored.append((path, current))
        except OSError:
            for path, current in reversed(restored):
                try:
                    _atomic_write(path, current)
                except OSError:
                    pass
            return {"status": "rollback_failed_reverted", "version": int(version)}
        with self.connection:
            self.connection.execute(
                "UPDATE evolution_versions SET status='rolled_back',rolled_back_at=?,rolled_back_by=? WHERE version=?",
                (self.clock(), str(actor)[:120], int(version)))
        return {"status": "rolled_back", "version": int(version), "files": len(targets)}

    def rollback_latest(self, *, actor: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT version FROM evolution_versions WHERE status='applied' ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            return {"status": "nothing_to_roll_back"}
        return self.rollback(int(row[0]), actor=actor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse-evolution", description="Self-evolution versions and protected surfaces")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--db", default="data/evolution/versions.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    rb = sub.add_parser("rollback")
    rb.add_argument("--version", type=int, required=True)
    rb.add_argument("--confirm", required=True, help="must equal ROLLBACK-<version>")
    chk = sub.add_parser("check-path")
    chk.add_argument("path")
    args = parser.parse_args(argv)
    if args.command == "check-path":
        label = protected_surface_for(args.path)
        print(json.dumps({"path": args.path, "protected": label is not None, "surface": label}, ensure_ascii=False))
        return 0
    with EvolutionVersionLedger(Path(args.workspace) / args.db, args.workspace) as ledger:
        if args.command == "list":
            print(json.dumps(ledger.list_versions(), ensure_ascii=False, indent=2))
            return 0
        if args.confirm != f"ROLLBACK-{args.version}":
            parser.error("confirmation phrase must be ROLLBACK-<version>")
        result = ledger.rollback(args.version, actor="cli")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "rolled_back" else 2


if __name__ == "__main__":
    raise SystemExit(main())
