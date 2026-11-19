"""Fail-closed verification for local workflow deliverables.

Only regular files beneath an explicitly configured artifact root are accepted.
Evidence records contain a relative path, byte count and SHA-256; URLs and arbitrary
strings are deliberately outside this verifier's trust boundary.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_RELATIVE_PATH_CHARS = 159
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactEvidenceError(ValueError):
    """A local artifact cannot be safely and reproducibly verified."""


class LocalArtifactEvidenceVerifier:
    """Verify bounded regular files without following symlinks or leaving root."""

    def __init__(self, root: str | Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
        supplied = Path(root).expanduser()
        try:
            if supplied.is_symlink():
                raise ArtifactEvidenceError("artifact root must not be a symlink")
            resolved = supplied.resolve(strict=True)
            if not resolved.is_dir():
                raise ArtifactEvidenceError("artifact root must be an existing directory")
        except OSError as exc:
            raise ArtifactEvidenceError("artifact root is unavailable") from exc
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_ARTIFACT_BYTES:
            raise ValueError("max_bytes must be an integer between 1 byte and 2 GiB")
        self.root = resolved
        self.max_bytes = max_bytes

    @staticmethod
    def _relative_parts(relative_path: str) -> tuple[str, ...]:
        if (not isinstance(relative_path, str) or not relative_path
                or len(relative_path) > _MAX_RELATIVE_PATH_CHARS
                or "\x00" in relative_path or "\\" in relative_path):
            raise ArtifactEvidenceError("relative_path must be bounded canonical POSIX relative text")
        pure = PurePosixPath(relative_path)
        parts = pure.parts
        if (pure.is_absolute() or not parts or pure.as_posix() != relative_path
                or any(part in {"", ".", ".."} for part in parts)
                or ":" in parts[0]):
            raise ArtifactEvidenceError("relative_path must not be absolute, traversing or non-canonical")
        return parts

    def verify(
        self, relative_path: str, expected_sha256: str, *, expected_bytes: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
            raise ArtifactEvidenceError("expected_sha256 must be a lowercase SHA-256 digest")
        if expected_bytes is not None and (
            isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 1
        ):
            raise ArtifactEvidenceError("expected_bytes must be a positive integer when supplied")

        parts = self._relative_parts(relative_path)
        candidate = self.root
        try:
            for index, part in enumerate(parts):
                candidate = candidate / part
                info = candidate.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ArtifactEvidenceError("artifact path must not contain symlinks")
                if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                    raise ArtifactEvidenceError("artifact parent components must be directories")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.root)
            before = candidate.lstat()
        except ArtifactEvidenceError:
            raise
        except (OSError, ValueError) as exc:
            raise ArtifactEvidenceError("artifact path is missing or escapes the configured root") from exc

        if not stat.S_ISREG(before.st_mode):
            raise ArtifactEvidenceError("artifact must be a regular file")
        if before.st_size < 1 or before.st_size > self.max_bytes:
            raise ArtifactEvidenceError("artifact size is outside the configured bounds")
        if expected_bytes is not None and before.st_size != expected_bytes:
            raise ArtifactEvidenceError("artifact byte count does not match the expected value")

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(candidate, flags)
        except OSError as exc:
            raise ArtifactEvidenceError("artifact could not be opened without following links") from exc

        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ArtifactEvidenceError("artifact changed between path validation and open")
                total = 0
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ArtifactEvidenceError("artifact exceeded the configured size while hashing")
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
        except ArtifactEvidenceError:
            raise
        except OSError as exc:
            raise ArtifactEvidenceError("artifact could not be read completely") from exc

        try:
            current_path = self.root
            for part in parts:
                current_path = current_path / part
                if stat.S_ISLNK(current_path.lstat().st_mode):
                    raise ArtifactEvidenceError("artifact path changed to a symlink during hashing")
            current_resolved = candidate.resolve(strict=True)
            current_resolved.relative_to(self.root)
            current = candidate.lstat()
        except ArtifactEvidenceError:
            raise
        except (OSError, ValueError) as exc:
            raise ArtifactEvidenceError("artifact path changed or escaped during hashing") from exc
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        identity_current = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        if identity_before != identity_after or identity_after != identity_current or total != before.st_size:
            raise ArtifactEvidenceError("artifact changed while its digest was being computed")

        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ArtifactEvidenceError("artifact SHA-256 does not match the expected digest")
        evidence_ref = f"artifact:{relative_path}#sha256={actual_sha256}"
        if len(evidence_ref) > 240:
            raise ArtifactEvidenceError("artifact evidence reference exceeds the workflow limit")
        return {
            "schema_version": 1,
            "status": "verified",
            "relative_path": relative_path,
            "bytes": total,
            "sha256": actual_sha256,
            "evidence_ref": evidence_ref,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verifier": "muse.local_artifact_sha256.v1",
        }
