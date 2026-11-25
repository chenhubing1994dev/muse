"""Sandbox file operations with scoped approval grants and hash-chained audit logs."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any


class SandboxFileError(RuntimeError):
    """Base error for sandbox file operations."""


class SandboxPathError(SandboxFileError, ValueError):
    """A path is invalid, traversing, linked, or outside the sandbox."""


class ApprovalRequiredError(SandboxFileError, PermissionError):
    """A mutating action was attempted without a valid scoped grant."""


class VerificationError(SandboxFileError):
    """A post-operation observation did not match the expected state."""


@dataclass(frozen=True)
class ApprovalGrant:
    """Opaque, single-use capability issued by a trusted in-process authority."""
    authority_id: str
    grant_id: str
    action: str
    target: str
    expires_at: float
    signature: str


class ApprovalAuthority:
    """Capability issuer; keep this object inside the trusted approval/UI boundary.

    This is an in-process trust boundary, not user authentication against arbitrary
    code running in the same process. The application must not expose this issuer
    to untrusted plugins or agents.
    """
    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)
        self.authority_id = uuid.uuid4().hex

    def issue(self, action: str, target: str, *, ttl_seconds: float = 120.0) -> ApprovalGrant:
        if action not in {"create_folder", "write_file", "delete_file"}:
            raise ValueError("unsupported approval action")
        if not isinstance(target, str) or not target or len(target) > 240:
            raise ValueError("approval target must be bounded non-empty text")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or not 1 <= ttl_seconds <= 600:
            raise ValueError("ttl_seconds must be between 1 and 600")
        import time
        grant_id = secrets.token_hex(16)
        expires_at = time.time() + float(ttl_seconds)
        body = self._body(self.authority_id, grant_id, action, target, expires_at)
        signature = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return ApprovalGrant(self.authority_id, grant_id, action, target, expires_at, signature)

    @staticmethod
    def _body(authority_id: str, grant_id: str, action: str, target: str, expires_at: float) -> bytes:
        return json.dumps([authority_id, grant_id, action, target, expires_at], separators=(",", ":")).encode()

    def verify(self, grant: ApprovalGrant, action: str, target: str) -> bool:
        import time
        if not isinstance(grant, ApprovalGrant) or grant.authority_id != self.authority_id:
            return False
        if grant.action != action or grant.target != target or grant.expires_at <= time.time():
            return False
        expected = hmac.new(self._secret, self._body(grant.authority_id, grant.grant_id,
                            grant.action, grant.target, grant.expires_at), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, grant.signature)


class SandboxFileManager:
    """Operate only below a dedicated root; mutations consume scoped grants."""
    _GENESIS = "0" * 64

    def __init__(self, root: str | Path, *, approval_authority: ApprovalAuthority,
                 max_bytes: int = 2 * 1024 * 1024) -> None:
        if not isinstance(approval_authority, ApprovalAuthority):
            raise TypeError("approval_authority is required")
        supplied = Path(root).expanduser()
        if supplied.exists() and supplied.is_symlink():
            raise SandboxPathError("sandbox root must not be a symlink")
        supplied.mkdir(parents=True, exist_ok=True)
        resolved = supplied.resolve(strict=True)
        if not resolved.is_dir():
            raise SandboxPathError("sandbox root must be a directory")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 64 * 1024 * 1024:
            raise ValueError("max_bytes must be an integer between 1 and 64 MiB")
        self.root = resolved
        self.max_bytes = max_bytes
        self.approval_authority = approval_authority
        self.log_path = self.root / ".muse-actions.jsonl"
        self._used_grants: set[str] = set()
        self._lock = RLock()

    @staticmethod
    def _parts(relative_path: str) -> tuple[str, ...]:
        if (not isinstance(relative_path, str) or not relative_path or len(relative_path) > 240
                or "\x00" in relative_path or "\\" in relative_path):
            raise SandboxPathError("path must be bounded canonical POSIX relative text")
        pure = PurePosixPath(relative_path)
        parts = pure.parts
        if (pure.is_absolute() or pure.as_posix() != relative_path or not parts
                or any(part in {"", ".", ".."} for part in parts) or ":" in parts[0]):
            raise SandboxPathError("absolute, traversing, or non-canonical paths are forbidden")
        if parts[0] == ".muse-actions.jsonl":
            raise SandboxPathError("audit log is reserved")
        return parts

    def _target(self, relative_path: str, *, allow_missing: bool = True) -> Path:
        parts = self._parts(relative_path)
        candidate = self.root
        for index, part in enumerate(parts):
            candidate = candidate / part
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                if allow_missing:
                    continue
                raise SandboxPathError("target does not exist")
            if stat.S_ISLNK(info.st_mode):
                raise SandboxPathError("symlinks are forbidden in sandbox paths")
            if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise SandboxPathError("parent component is not a directory")
        try:
            candidate.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise SandboxPathError("target escapes sandbox root") from exc
        return candidate

    def verify_audit_log(self) -> dict[str, Any]:
        """Verify event sequence, previous hashes, and canonical event hashes."""
        if not self.log_path.exists():
            return {"valid": True, "events": 0, "head": self._GENESIS}
        previous = self._GENESIS
        count = 0
        try:
            with self.log_path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get("prev_hash") != previous:
                        raise VerificationError(f"audit chain previous hash mismatch at line {line_number}")
                    supplied = event.pop("event_hash", None)
                    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    actual = hashlib.sha256((previous + canonical).encode("utf-8")).hexdigest()
                    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, actual):
                        raise VerificationError(f"audit chain hash mismatch at line {line_number}")
                    previous = supplied
                    count += 1
            return {"valid": True, "events": count, "head": previous}
        except Exception as exc:
            return {"valid": False, "events": count, "head": previous,
                    "error": f"{type(exc).__name__}: {exc}"}

    def _record(self, action: str, target: str, approved: bool, success: bool,
                evidence: dict[str, Any] | None = None, error: str | None = None,
                parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            chain = self.verify_audit_log()
            if not chain["valid"]:
                raise VerificationError("audit log integrity check failed: " + chain["error"])
            result = {"action": action, "target": target, "approved": approved, "success": success,
                      "parameters": parameters or {}, "evidence": evidence or {}, "error": error}
            event = {"timestamp": datetime.now(timezone.utc).isoformat(), **result,
                     "prev_hash": chain["head"]}
            canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            event["event_hash"] = hashlib.sha256((chain["head"] + canonical).encode("utf-8")).hexdigest()
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return result

    def _reject(self, action: str, target: str, approved: bool, error: Exception,
                parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._record(action, target, approved, False, error=f"{type(error).__name__}: {error}",
                            parameters=parameters)

    def _consume(self, grant: ApprovalGrant | None, action: str, target: str) -> bool:
        if not self.approval_authority.verify(grant, action, target):
            raise ApprovalRequiredError("missing, invalid, expired, or out-of-scope approval grant")
        assert isinstance(grant, ApprovalGrant)
        if grant.grant_id in self._used_grants:
            raise ApprovalRequiredError("approval grant has already been consumed")
        self._used_grants.add(grant.grant_id)
        return True

    def create_folder(self, relative_path: str, *, approval: ApprovalGrant | None = None) -> dict[str, Any]:
        with self._lock:
            try:
                self._consume(approval, "create_folder", relative_path)
                target = self._target(relative_path)
                target.mkdir(parents=True, exist_ok=False)
                observed = self._target(relative_path, allow_missing=False)
                if not observed.is_dir():
                    raise VerificationError("folder missing after creation")
                return self._record("create_folder", relative_path, True, True,
                                    {"exists_after_readback": True})
            except Exception as exc:
                return self._reject("create_folder", relative_path, approval is not None, exc)

    def write_file(self, relative_path: str, content: str, *, approval: ApprovalGrant | None = None) -> dict[str, Any]:
        with self._lock:
            target: Path | None = None
            encoded = b""
            try:
                self._consume(approval, "write_file", relative_path)
                if not isinstance(content, str):
                    raise TypeError("content must be text")
                encoded = content.encode("utf-8")
                if len(encoded) > self.max_bytes:
                    raise ValueError("content exceeds configured size limit")
                target = self._target(relative_path)
                if target.exists():
                    raise FileExistsError("refusing to overwrite an existing file")
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".muse-tmp-" + uuid.uuid4().hex)
                try:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                    descriptor = os.open(temporary, flags, 0o600)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if target.exists():
                        raise FileExistsError("target appeared during write")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                observed = self.read_file(relative_path)
                digest = hashlib.sha256(encoded).hexdigest()
                if (not observed["success"] or observed["evidence"].get("content") != content
                        or observed["evidence"].get("sha256") != digest):
                    raise VerificationError("read-back content or SHA256 differs from written content")
                return self._record("write_file", relative_path, True, True,
                                    {"readback_match": True, "bytes": len(encoded), "sha256": digest},
                                    parameters={"bytes": len(encoded), "content_sha256": digest})
            except Exception as exc:
                rollback_error = None
                if target is not None:
                    try:
                        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(encoded).hexdigest():
                            target.unlink()
                    except Exception as rollback_exc:
                        rollback_error = f"{type(rollback_exc).__name__}: {rollback_exc}"
                message = f"{type(exc).__name__}: {exc}"
                if rollback_error:
                    message += "; rollback failed: " + rollback_error
                return self._reject("write_file", relative_path, approval is not None, VerificationError(message),
                                    {"bytes": len(encoded), "content_sha256": hashlib.sha256(encoded).hexdigest()})

    def read_file(self, relative_path: str) -> dict[str, Any]:
        try:
            target = self._target(relative_path, allow_missing=False)
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > self.max_bytes:
                raise SandboxFileError("target must be a bounded regular file")
            content = target.read_bytes()
            if len(content) > self.max_bytes:
                raise SandboxFileError("file exceeds configured size limit")
            decoded = content.decode("utf-8")
            return self._record("read_file", relative_path, False, True,
                                {"content": decoded, "bytes": len(content),
                                 "sha256": hashlib.sha256(content).hexdigest()})
        except Exception as exc:
            return self._reject("read_file", relative_path, False, exc)

    def sha256(self, relative_path: str) -> dict[str, Any]:
        result = self.read_file(relative_path)
        if not result["success"]:
            return result
        return self._record("sha256", relative_path, False, True,
                            {"sha256": result["evidence"]["sha256"], "bytes": result["evidence"]["bytes"]})

    def compare_content(self, relative_path: str, expected: str) -> dict[str, Any]:
        result = self.read_file(relative_path)
        if not result["success"]:
            return self._record("compare_content", relative_path, False, False, error=result["error"])
        actual = result["evidence"]["content"]
        if not isinstance(expected, str):
            return self._reject("compare_content", relative_path, False, TypeError("expected must be text"))
        matches = actual == expected
        return self._record("compare_content", relative_path, False, matches,
                            {"matches": matches, "actual_sha256": result["evidence"]["sha256"],
                             "expected_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest()},
                            None if matches else "content mismatch")

    def delete_file(self, relative_path: str, *, approval: ApprovalGrant | None = None,
                    second_approval: ApprovalGrant | None = None) -> dict[str, Any]:
        with self._lock:
            try:
                self._consume(approval, "delete_file", relative_path)
                self._consume(second_approval, "delete_file", relative_path)
                if approval is None or second_approval is None or approval.grant_id == second_approval.grant_id:
                    raise ApprovalRequiredError("delete requires two distinct approval grants")
                target = self._target(relative_path, allow_missing=False)
                if not target.is_file():
                    raise SandboxFileError("delete target must be a regular file")
                target.unlink()
                if self._target(relative_path).exists():
                    raise VerificationError("target still exists after deletion")
                return self._record("delete_file", relative_path, True, True,
                                    {"exists_after_readback": False},
                                    parameters={"approval_count": 2})
            except Exception as exc:
                return self._reject("delete_file", relative_path,
                                    approval is not None and second_approval is not None, exc,
                                    {"approval_count": int(approval is not None) + int(second_approval is not None)})
