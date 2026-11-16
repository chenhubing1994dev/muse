"""Prepare human-reviewable code-change Tickets from approved improvement proposals."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

_MAX_INDEX_FILES = 256
_MAX_CONTEXT_FILE_BYTES = 6 * 1024
_MAX_CONTEXT_BYTES = 12 * 1024
_MAX_RESPONSE_BYTES = 48 * 1024
_MAX_PATCH_TEXT_BYTES = 16 * 1024
# response size cap is intentionally defined above


def _parse_object(raw: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip() or len(raw.encode("utf-8")) > _MAX_RESPONSE_BYTES:
        raise ValueError(f"{label} must be non-empty bounded JSON text")
    text = raw.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be one complete JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _top_level_import_counts(source: str, *, filename: str) -> dict[str, int]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return {}
    counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.asname or alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [alias.asname or alias.name for alias in node.names if alias.name != "*"]
        else:
            continue
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    return counts


class ImprovementPatchPlanner:
    """Turn one already-approved proposal into a patch preview and approval Ticket.

    The model can only select from a bounded Python-file index and return exact
    text replacements. It cannot write files, choose commands, or approve work.
    """

    def __init__(
        self, workspace: str | Path, *, improvement_store: Any,
        task_queue: Any, builder: Builder, builder_model_id: str,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if not callable(builder):
            raise TypeError("builder must be a trusted local-model callable")
        if not isinstance(builder_model_id, str) or not builder_model_id.strip() or len(builder_model_id) > 160:
            raise ValueError("builder_model_id must be non-empty text of at most 160 characters")
        self.improvement_store = improvement_store
        self.task_queue = task_queue
        self.builder = builder
        self.builder_model_id = builder_model_id.strip()

    def _read_snapshot(self, relative: str) -> bytes:
        rel = Path(relative)
        if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
            raise ValueError("source path must be workspace-relative without traversal")
        cursor = self.workspace
        for part in rel.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("source path contains a symlink")
        resolved = cursor.resolve(strict=True)
        if self.workspace not in resolved.parents or not resolved.is_file():
            raise ValueError("source path must resolve to a regular file inside the workspace")
        return resolved.read_bytes()

    def _file_index(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for relative_root in ("src/muse", "tests"):
            directory = self.workspace / relative_root
            if not directory.is_dir() or directory.is_symlink():
                continue
            for path in sorted(directory.rglob("*.py")):
                if len(candidates) >= _MAX_INDEX_FILES:
                    break
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    relative = path.relative_to(self.workspace).as_posix()
                    raw = self._read_snapshot(relative)
                    if len(raw) > _MAX_CONTEXT_FILE_BYTES:
                        continue
                    candidates.append({"path": relative, "size_bytes": len(raw)})
                except OSError:
                    continue
        return candidates

    def prepare_ticket(self, proposal_id: str) -> dict[str, Any]:
        if not isinstance(proposal_id, str) or not proposal_id.strip() or len(proposal_id) > 128:
            raise ValueError("proposal_id is invalid")
        proposal = self.improvement_store.get(proposal_id)
        if proposal is None:
            raise ValueError("improvement proposal does not exist")
        if proposal.get("status") != "approved":
            raise PermissionError("only an explicitly approved improvement proposal can generate a code-change Ticket")
        index = self._file_index()
        if not index:
            raise ValueError("workspace contains no eligible Python source or test files")
        selection_prompt = json.dumps({
            "task": "Select the smallest set of relevant existing Python files for the approved improvement. Return JSON only.",
            "proposal": {key: proposal.get(key) for key in ("title", "problem", "hypothesis")},
            "file_index": index,
            "constraints": {"paths": "choose only exact paths from file_index", "max_paths": 2,
                            "max_file_size_bytes": _MAX_CONTEXT_FILE_BYTES,
                            "no_commands": True, "no_file_writes": True},
        }, ensure_ascii=False, allow_nan=False)
        selection = _parse_object(self.builder(selection_prompt), label="builder file selection")
        if set(selection) != {"paths"} or not isinstance(selection["paths"], list) or not 1 <= len(selection["paths"]) <= 2:
            raise ValueError("builder selection must contain only 1-2 paths")
        indexed = {item["path"]: item["size_bytes"] for item in index}
        paths = selection["paths"]
        if any(not isinstance(path, str) or path not in indexed for path in paths) or len(set(paths)) != len(paths):
            raise ValueError("builder selected a path outside the bounded workspace file index")
        sources: dict[str, tuple[bytes, str]] = {}
        context_bytes = 0
        for relative in paths:
            target = self.workspace / relative
            if target.is_symlink():
                raise ValueError("selected source path became a symlink")
            original = self._read_snapshot(relative)
            if len(original) > _MAX_CONTEXT_FILE_BYTES:
                raise ValueError("selected source file is too large for bounded patch context")
            context_bytes += len(original)
            if context_bytes > _MAX_CONTEXT_BYTES:
                raise ValueError("selected source context exceeds the 12 KiB total budget")
            try:
                source_text = original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("selected source file is not UTF-8") from exc
            sources[relative] = (original, source_text)
        patch_prompt = json.dumps({
            "task": "Propose the smallest safe source patch for this approved improvement. Return JSON only; do not execute or write files.",
            "proposal": {key: proposal.get(key) for key in ("title", "problem", "hypothesis")},
            "source_files": [{"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "content": content}
                             for path, (raw, content) in sources.items()],
            "constraints": {"max_patches": 2, "old_text": "exact non-empty substring occurring exactly once in the named file",
                            "new_text": "replacement text; do not include commands or instructions",
                            "scope": "only the supplied source_files", "preserve_public_interfaces": True,
                            "tests": "do not edit tests unless required by the proposal; no shell commands"},
            "required_response": {"summary": "short patch summary", "patches": [
                {"path": "one supplied path", "old_text": "exact original substring", "new_text": "replacement"}]},
        }, ensure_ascii=False, allow_nan=False)
        response = _parse_object(self.builder(patch_prompt), label="builder patch proposal")
        if set(response) != {"summary", "patches"}:
            raise ValueError("builder patch response must contain only summary and patches")
        summary, raw_patches = response["summary"], response["patches"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 400:
            raise ValueError("builder patch summary is invalid")
        if not isinstance(raw_patches, list) or not 1 <= len(raw_patches) <= 2:
            raise ValueError("builder must return between 1 and 2 patches")
        patches = []
        for patch in raw_patches:
            if not isinstance(patch, dict) or set(patch) != {"path", "old_text", "new_text"}:
                raise ValueError("each builder patch must contain only path, old_text and new_text")
            relative, old_text, new_text = patch["path"], patch["old_text"], patch["new_text"]
            if not isinstance(relative, str) or relative not in sources or not isinstance(old_text, str) or not old_text or len(old_text) > _MAX_PATCH_TEXT_BYTES:
                raise ValueError("patch path or old_text is invalid")
            if not isinstance(new_text, str) or len(new_text) > _MAX_PATCH_TEXT_BYTES or new_text == old_text:
                raise ValueError("patch new_text is invalid or unchanged")
            original, source_text = sources[relative]
            if source_text.count(old_text) != 1:
                raise ValueError("patch old_text must match exactly once in the selected source snapshot")
            candidate_text = source_text.replace(old_text, new_text, 1)
            try:
                ast.parse(candidate_text, filename=relative)
            except SyntaxError as exc:
                raise ValueError(f"patch would make {relative} syntactically invalid") from exc
            original_imports = _top_level_import_counts(source_text, filename=relative)
            candidate_imports = _top_level_import_counts(candidate_text, filename=relative)
            if any(count > 1 and count > original_imports.get(name, 0)
                   for name, count in candidate_imports.items()):
                raise ValueError("patch introduces a duplicate top-level import")
            patches.append({"path": relative, "expected_sha256": hashlib.sha256(original).hexdigest(),
                            "old_text": old_text, "new_text": new_text})
        if len({patch["path"] for patch in patches}) != len(patches):
            raise ValueError("only one patch per file is allowed")
        for relative, (original, _text) in sources.items():
            if self._read_snapshot(relative) != original:
                raise RuntimeError("source changed while the local builder was preparing the patch; no Ticket was created")
        stable_payload = {"proposal_id": proposal_id, "patches": patches, "summary": summary,
                          "builder_model_id": self.builder_model_id}
        fingerprint = hashlib.sha256(json.dumps(stable_payload, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode("utf-8")).hexdigest()
        payload = {"kind": "approved_code_change", "proposal_id": proposal_id,
                   "patches": patches, "patch_summary": summary, "builder_model_id": self.builder_model_id,
                   "proposal_updated_at": proposal.get("updated_at")}
        task = self.task_queue.enqueue(
            f"Self-improvement patch: {proposal.get('title', 'approved proposal')[:100]}", payload,
            priority=proposal.get("priority", 50), workload_class="coding", recovery_policy="manual",
            idempotency_key=f"evolution-patch:{proposal_id}:{fingerprint[:32]}", require_approval=True,
        )
        return {"ticket_id": task["id"], "status": task["status"], "approval_required": True,
                "proposal_id": proposal_id, "summary": summary,
                "patch_preview": [{"path": item["path"], "old_text": item["old_text"],
                                   "new_text": item["new_text"], "expected_sha256": item["expected_sha256"]}
                                  for item in patches],
                "builder_model_id": self.builder_model_id}
