"""Deterministic, local-first harness snapshots and candidate validation.

This module is intentionally pure/read-only: it never executes candidate code,
changes production configuration, or grants tool permissions.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_OUTCOMES = {"helped", "hurt", "no_effect", "confounded"}
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 256


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("path must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must stay within the workspace")
    if path.parts[0].lower() in {".git", ".venv", "venv", "__pycache__", "node_modules"}:
        raise ValueError("path is excluded from harness snapshots")
    if any(part.lower() in {".env", "secrets", "credentials"} for part in path.parts):
        raise ValueError("sensitive paths cannot enter harness snapshots")
    return path.as_posix()


@dataclass(frozen=True)
class HarnessFile:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class HarnessSnapshot:
    snapshot_id: str
    cycle_id: str
    ticket_id: str
    domain: str
    files: tuple[HarnessFile, ...]
    manifest_sha256: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HarnessCandidate:
    candidate_id: str
    baseline_snapshot_id: str
    changes: dict[str, str]
    diff_sha256: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProbeRun:
    run_id: str
    candidate_id: str
    environment_fingerprint: str
    input_fingerprint: str
    tests_run: int
    failures: int
    errors: int
    duration_ms: int
    side_effects: tuple[str, ...] = ()
    isolated: bool = False
    def __post_init__(self) -> None:
        for key in ("environment_fingerprint", "input_fingerprint"):
            if not _SHA256.fullmatch(getattr(self, key)):
                raise ValueError(f"{key} must be a lowercase SHA-256")
        for key in ("tests_run", "failures", "errors", "duration_ms"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
        if not self.run_id or not self.candidate_id:
            raise ValueError("run_id and candidate_id must not be empty")


@dataclass(frozen=True)
class CompositionDecision:
    outcome: str
    adopted: bool
    reason: str
    baseline_run_id: str
    candidate_run_id: str
    evidence_sha256: str


@dataclass(frozen=True)
class ExperienceLesson:
    domain: str
    outcome: str
    lesson: str
    evidence_sha256: str
    scope: str
    counterexamples: tuple[str, ...] = ()
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.outcome not in _ALLOWED_OUTCOMES:
            raise ValueError("unsupported experience outcome")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not _SHA256.fullmatch(self.evidence_sha256):
            raise ValueError("evidence_sha256 must be a lowercase SHA-256")


def create_snapshot(
    workspace: str | Path, paths: Iterable[str], *,
    cycle_id: str, ticket_id: str, domain: str,
) -> HarnessSnapshot:
    """Hash explicitly allowlisted files without following symlinks."""
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace must be a directory")
    if not all(isinstance(v, str) and v.strip() for v in (cycle_id, ticket_id, domain)):
        raise ValueError("cycle_id, ticket_id and domain are required")
    normalized = sorted({_safe_relative_path(p) for p in paths})
    if not normalized or len(normalized) > MAX_FILES:
        raise ValueError(f"snapshot must contain 1..{MAX_FILES} allowlisted files")
    files: list[HarnessFile] = []
    for relative in normalized:
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        current = root
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"symlink is not allowed: {relative}")
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents:
            raise ValueError(f"path escapes workspace: {relative}")
        if not resolved.is_file():
            raise ValueError(f"not a regular file: {relative}")
        size = resolved.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes: {relative}")
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        files.append(HarnessFile(relative, digest, size))
    manifest = [asdict(item) for item in files]
    manifest_hash = _json_hash(manifest)
    snapshot_id = "snap_" + _json_hash({
        "cycle_id": cycle_id, "ticket_id": ticket_id, "domain": domain,
        "manifest_sha256": manifest_hash,
    })[:32]
    return HarnessSnapshot(snapshot_id, cycle_id, ticket_id, domain,
                           tuple(files), manifest_hash)

def create_candidate(
    snapshot: HarnessSnapshot, changes: dict[str, str],
) -> HarnessCandidate:
    """Create a candidate diff restricted to files in the baseline snapshot."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("candidate changes must be a non-empty object")
    if len(changes) > MAX_FILES:
        raise ValueError(f"candidate may change at most {MAX_FILES} files")
    baseline_paths = {item.path for item in snapshot.files}
    normalized: dict[str, str] = {}
    for raw_path, content in changes.items():
        path = _safe_relative_path(raw_path)
        if path not in baseline_paths:
            raise ValueError(f"candidate path is not in baseline allowlist: {path}")
        if not isinstance(content, str):
            raise ValueError("candidate content must be text")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"candidate file exceeds {MAX_FILE_BYTES} bytes: {path}")
        normalized[path] = content
    diff_hash = _json_hash(normalized)
    candidate_id = "cand_" + _json_hash({
        "baseline": snapshot.snapshot_id, "diff_sha256": diff_hash,
    })[:32]
    return HarnessCandidate(candidate_id, snapshot.snapshot_id,
                             dict(sorted(normalized.items())), diff_hash)


def compare_probes(
    baseline: ProbeRun, candidate: ProbeRun,
) -> CompositionDecision:
    """Conservative deterministic gate; uncertain evidence never adopts."""
    evidence = {
        "baseline": asdict(baseline),
        "candidate": asdict(candidate),
    }
    evidence_hash = _json_hash(evidence)

    def decision(outcome: str, adopted: bool, reason: str) -> CompositionDecision:
        return CompositionDecision(outcome, adopted, reason,
                                   baseline.run_id, candidate.run_id, evidence_hash)

    if not baseline.isolated or not candidate.isolated:
        return decision("confounded", False, "both probe runs must be isolated")
    if baseline.environment_fingerprint != candidate.environment_fingerprint:
        return decision("confounded", False, "environment fingerprints differ")
    if baseline.input_fingerprint != candidate.input_fingerprint:
        return decision("confounded", False, "input fingerprints differ")
    if baseline.tests_run < 1 or candidate.tests_run < baseline.tests_run:
        return decision("confounded", False, "candidate did not execute a comparable test set")
    if candidate.failures or candidate.errors:
        return decision("hurt", False, "candidate has test failures or errors")
    if candidate.side_effects:
        return decision("confounded", False, "candidate reported side effects")
    if (candidate.failures, candidate.errors) < (baseline.failures, baseline.errors):
        return decision("helped", True, "candidate reduced failures/errors with comparable evidence")
    if candidate.duration_ms < baseline.duration_ms:
        return decision("helped", True, "candidate reduced duration with comparable passing tests")
    if (candidate.failures, candidate.errors, candidate.duration_ms) == (
        baseline.failures, baseline.errors, baseline.duration_ms
    ):
        return decision("no_effect", False, "no measured improvement")
    return decision("hurt", False, "candidate did not improve the measured outcome")


@dataclass(frozen=True)
class ProbeWorkspace:
    """Harness-owned filesystem copy; not an OS process sandbox."""
    workspace_path: str
    marker_path: str
    snapshot_id: str
    candidate_id: str
    manifest_sha256: str
    candidate_manifest_sha256: str


def _tree_manifest(root: Path, snapshot: HarnessSnapshot | tuple[HarnessFile, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    files = snapshot.files if isinstance(snapshot, HarnessSnapshot) else snapshot
    for item in files:
        path = root.joinpath(*PurePosixPath(item.path).parts)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"probe copy is missing or unsafe: {item.path}")
        data = path.read_bytes()
        result.append({"path": item.path, "sha256": hashlib.sha256(data).hexdigest(),
                       "size_bytes": len(data)})
    return result


def create_probe_workspace(    source_workspace: str | Path, temp_root: str | Path,
    snapshot: HarnessSnapshot, candidate: HarnessCandidate | None = None,
) -> ProbeWorkspace:
    """Create a fresh allowlisted copy. This does not sandbox processes or code."""
    import shutil
    import tempfile

    source = Path(source_workspace).resolve(strict=True)
    temp_path = Path(temp_root)
    temp_path.mkdir(parents=True, exist_ok=True)
    temp = temp_path.resolve(strict=True)
    if not source.is_dir() or not temp.is_dir():
        raise ValueError("source and temp roots must be directories")
    if source == temp or source in temp.parents or temp in source.parents:
        raise ValueError("temp root and source workspace must be disjoint")
    if candidate is not None and candidate.baseline_snapshot_id != snapshot.snapshot_id:
        raise ValueError("candidate does not belong to supplied baseline snapshot")

    for item in snapshot.files:
        relative = _safe_relative_path(item.path)
        current = source
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"source symlink is not allowed: {relative}")
        resolved = current.resolve(strict=True)
        if source not in resolved.parents or not resolved.is_file():
            raise ValueError(f"source path is missing or unsafe: {relative}")
        raw = resolved.read_bytes()
        if len(raw) != item.size_bytes or hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError(f"source drift since snapshot: {relative}")

    owned = Path(tempfile.mkdtemp(prefix="muse-h2-probe-", dir=str(temp)))
    marker = owned / ".muse-harness-owned.json"
    try:
        for item in snapshot.files:
            relative = _safe_relative_path(item.path)
            src = source.joinpath(*PurePosixPath(relative).parts)
            dst = owned.joinpath(*PurePosixPath(relative).parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            with dst.open("xb") as stream:
                stream.write(src.read_bytes())
        baseline_manifest = _tree_manifest(owned, snapshot)
        if _json_hash(baseline_manifest) != snapshot.manifest_sha256:
            raise ValueError("copied baseline manifest does not match snapshot")
        if candidate is not None:
            allowed = {item.path for item in snapshot.files}
            for relative, content in candidate.changes.items():
                safe = _safe_relative_path(relative)
                if safe not in allowed:
                    raise ValueError(f"candidate path is not in baseline allowlist: {safe}")
                target = owned.joinpath(*PurePosixPath(safe).parts)
                if target.is_symlink() or not target.is_file():
                    raise ValueError(f"candidate target is missing or unsafe: {safe}")
                target.write_text(content, encoding="utf-8", newline="")
        candidate_manifest = _tree_manifest(owned, snapshot)
        payload = {
            "format": 1, "snapshot_id": snapshot.snapshot_id,
            "candidate_id": candidate.candidate_id if candidate else "baseline",
            "manifest_sha256": snapshot.manifest_sha256,
            "candidate_manifest_sha256": _json_hash(candidate_manifest),
            "files": candidate_manifest,
        }
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
        return ProbeWorkspace(str(owned), str(marker), snapshot.snapshot_id,
                              candidate.candidate_id if candidate else "baseline",
                              snapshot.manifest_sha256, payload["candidate_manifest_sha256"])
    except Exception:
        shutil.rmtree(owned, ignore_errors=True)
        raise

def cleanup_probe_workspace(probe: ProbeWorkspace, temp_root: str | Path) -> bool:
    """Remove only a marked direct child created by create_probe_workspace."""
    import shutil

    root = Path(temp_root).resolve(strict=True)
    owned = Path(probe.workspace_path)
    if owned.is_symlink():
        raise ValueError("refusing to clean a symlink")
    resolved = owned.resolve(strict=True)
    if resolved.parent != root or not resolved.name.startswith("muse-h2-probe-"):
        raise ValueError("probe workspace is outside the designated temp root")
    marker = resolved / ".muse-harness-owned.json"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("ownership marker missing; refusing cleanup")
    data = json.loads(marker.read_text(encoding="utf-8"))
    if (data.get("snapshot_id") != probe.snapshot_id or
            data.get("candidate_id") != probe.candidate_id or
            data.get("manifest_sha256") != probe.manifest_sha256 or
            data.get("candidate_manifest_sha256") != probe.candidate_manifest_sha256):
        raise ValueError("ownership marker mismatch; refusing cleanup")
    shutil.rmtree(resolved)
    return not resolved.exists()



@dataclass(frozen=True)
class StaticProbeResult:
    """Non-executing validation evidence; never an isolated ProbeRun."""
    candidate_id: str
    workspace_manifest_sha256: str
    checks: tuple[dict[str, Any], ...]
    passed: bool
    evidence_sha256: str


def run_static_probes(probe: ProbeWorkspace) -> StaticProbeResult:
    """Run fixed, non-executing parsers against an intact Harness-owned copy.

    Supported checks: Python syntax via ast.parse and JSON syntax via json.loads.
    No imports, subprocesses, shell commands, test discovery, or candidate execution.
    """
    import ast

    root = Path(probe.workspace_path).resolve(strict=True)
    marker = Path(probe.marker_path)
    if marker.is_symlink() or not marker.is_file() or marker.parent.resolve() != root:
        raise ValueError("probe ownership marker is missing or unsafe")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if (payload.get("snapshot_id") != probe.snapshot_id or
            payload.get("candidate_id") != probe.candidate_id or
            payload.get("manifest_sha256") != probe.manifest_sha256 or
            payload.get("candidate_manifest_sha256") != probe.candidate_manifest_sha256):
        raise ValueError("probe ownership marker mismatch")

    allowed = {item.path for item in _snapshot_files_from_probe_manifest(probe, payload)}
    discovered: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink found inside probe workspace")
        if path.is_file() and path != marker:
            discovered.add(path.relative_to(root).as_posix())
    if discovered != allowed:
        raise ValueError("probe workspace inventory differs from snapshot allowlist")
    current_manifest = _json_hash(_tree_manifest(root, _snapshot_files_from_probe_manifest(probe, payload)))
    if current_manifest != probe.candidate_manifest_sha256:
        raise ValueError("probe workspace content changed after creation")

    checks: list[dict[str, Any]] = []
    for relative in sorted(allowed):
        path = root.joinpath(*PurePosixPath(relative).parts)
        suffix = path.suffix.lower()
        if suffix not in {".py", ".json"}:
            continue
        try:
            source = path.read_bytes().decode("utf-8")
            if suffix == ".py":
                ast.parse(source, filename=relative, mode="exec")
                check = {"path": relative, "validator": "python-ast-parse", "passed": True}
            else:
                json.loads(source)
                check = {"path": relative, "validator": "json-parse", "passed": True}
        except (SyntaxError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            check = {"path": relative, "validator": "python-ast-parse" if suffix == ".py" else "json-parse",
                     "passed": False, "error_type": type(exc).__name__}
        checks.append(check)
    evidence = {
        "candidate_id": probe.candidate_id,
        "workspace_manifest_sha256": probe.candidate_manifest_sha256,
        "checks": checks,
        "execution": "none",
    }
    return StaticProbeResult(probe.candidate_id, probe.candidate_manifest_sha256,
                             tuple(checks), all(item["passed"] for item in checks),
                             _json_hash(evidence))


def _snapshot_files_from_probe_manifest(
    probe: ProbeWorkspace, payload: dict[str, Any],
) -> tuple[HarnessFile, ...]:
    """Rebuild the exact file allowlist from the immutable snapshot manifest."""
    # ProbeWorkspace carries only the manifest digest; recover the manifest
    # from the adjacent ownership marker, which is written by the creator.
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("probe marker does not contain a file inventory")
    parsed: list[HarnessFile] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("invalid probe marker file inventory")
        relative = _safe_relative_path(item.get("path"))
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("invalid probe marker file hash")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("invalid probe marker file size")
        parsed.append(HarnessFile(relative, digest, size))
    if len({item.path for item in parsed}) != len(parsed):
        raise ValueError("duplicate probe marker paths")
    return tuple(parsed)
def lesson_from_decision(
    decision: CompositionDecision, *, domain: str, scope: str,
    counterexamples: tuple[str, ...] = (),
) -> ExperienceLesson:
    return ExperienceLesson(
        domain=domain, outcome=decision.outcome, lesson=decision.reason,
        evidence_sha256=decision.evidence_sha256, scope=scope,
        counterexamples=counterexamples,
        confidence=1.0 if decision.outcome in {"helped", "hurt"} else 0.5,
    )