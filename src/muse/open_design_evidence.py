"""Persist bounded Open Design artifact metadata into Muse's existing Research Wiki.

No generated source bodies or binary bytes are copied into the knowledge graph.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote
from typing import Any

from muse.initiative_engine import InitiativeStore
from muse.research_knowledge import ResearchKnowledgeStore


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INITIATIVE_ID = re.compile(r"[0-9a-f]{32}\Z")


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise ValueError("artifact path must be non-empty bounded text")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError("artifact path must be a safe project-relative path")
    return normalized


def persist_open_design_artifact_evidence(
    *,

    knowledge_database: str | Path,
    initiative_database: str | Path,
    task_id: str,
    run_id: str,
    evidence: dict[str, Any],
    initiative_id: str | None = None,
) -> dict[str, Any]:
    """Persist one immutable metadata snapshot, idempotently, in Muse's stores."""
    if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ValueError("task_id must be a 32-character lowercase UUID hex string")
    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 256:
        raise ValueError("run_id must be non-empty bounded text")
    if initiative_id is not None and (
        not isinstance(initiative_id, str) or not _INITIATIVE_ID.fullmatch(initiative_id)
    ):
        raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
    if not isinstance(evidence, dict):
        raise ValueError("artifact evidence must be an object")

    project_id = evidence.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 512:
        raise ValueError("artifact evidence must include a bounded project_id")
    files = evidence.get("files")
    if not isinstance(files, list) or len(files) > 200:
        raise ValueError("artifact evidence files must be a list of at most 200 entries")
    complete = evidence.get("complete")
    truncated = evidence.get("truncated")
    skipped = evidence.get("skipped_file_count")
    if not isinstance(complete, bool) or not isinstance(truncated, bool):
        raise ValueError("artifact evidence completeness flags must be booleans")
    if isinstance(skipped, bool) or not isinstance(skipped, int) or skipped < 0:
        raise ValueError("artifact evidence skipped_file_count must be a non-negative integer")
    declared_file_count = evidence.get("file_count")
    if (
        isinstance(declared_file_count, bool)
        or not isinstance(declared_file_count, int)
        or declared_file_count != len(files)
    ):
        raise ValueError("artifact evidence file_count does not match its file list")
    expected_complete = not truncated and skipped == 0 and bool(files)
    if complete is not expected_complete:
        raise ValueError("artifact evidence complete flag is inconsistent with truncation, skipped files, or file count")

    normalized_files = []
    seen_paths = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("artifact evidence contains a non-object file record")
        path = _safe_relative_path(item.get("path"))
        if path in seen_paths:
            raise ValueError("artifact evidence contains duplicate file paths")
        seen_paths.add(path)
        mime = item.get("mime")
        binary = item.get("binary")
        byte_count = item.get("bytes")
        digest = item.get("sha256")
        if not isinstance(mime, str) or not mime.strip() or len(mime) > 200:
            raise ValueError("artifact MIME type must be non-empty bounded text")
        if not isinstance(binary, bool):
            raise ValueError("artifact binary flag must be boolean")
        if byte_count is not None and (
            isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0
        ):
            raise ValueError("artifact byte count must be a non-negative integer or null")
        if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ValueError("artifact SHA-256 must be a lowercase digest or null")
        if not binary and digest is None:
            raise ValueError("text artifacts require a verified SHA-256 digest")
        normalized_files.append({
            "path": path, "mime": mime.strip(), "binary": binary,
            "bytes": byte_count, "sha256": digest,
        })

    snapshot = {
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "complete": complete,
        "truncated": truncated,
        "skipped_file_count": skipped,
        "file_count": len(normalized_files),
        "files": normalized_files,
    }
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    snapshot_digest = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    encoded_project = quote(project_id, safe="")
    encoded_run = quote(run_id, safe="")
    bundle_ref = f"open-design://projects/{encoded_project}/runs/{encoded_run}/snapshots/{snapshot_digest}"

    if initiative_id is not None:
        with InitiativeStore(initiative_database) as initiatives:
            if initiatives.get_by_id(initiative_id) is None:
                raise ValueError("initiative_id does not identify an existing Creator Initiative")

    with ResearchKnowledgeStore(knowledge_database) as knowledge:
        bundle_node = knowledge.upsert_node(
            "source",
            f"Open Design output bundle {project_id}/{run_id}",
            summary=f"{len(normalized_files)} generated files; complete={complete}; metadata-only evidence",
            external_ref=bundle_ref,
            metadata={
                "kind": "generated_artifact_bundle",
                "project_id": project_id,
                "run_id": run_id,
                "task_id": task_id,
                "snapshot_sha256": snapshot_digest,
                "complete": complete,
                "truncated": truncated,
                "skipped_file_count": skipped,
                "file_count": len(normalized_files),
                "content_copied": False,
            },
            provenance=[f"muse-task:{task_id}", f"open-design-run:{run_id}"],
        )
        file_node_ids = []
        for item in normalized_files:
            path_digest = hashlib.sha256(item["path"].encode("utf-8")).hexdigest()
            file_ref = f"{bundle_ref}/files/{path_digest}"
            node = knowledge.upsert_node(
                "source",
                f"Open Design generated file: {item['path']}",
                summary="Generated artifact metadata only; file body is not copied into Research Wiki.",
                external_ref=file_ref,
                metadata={
                    "kind": "generated_artifact_file",
                    "project_id": project_id,
                    "run_id": run_id,
                    "task_id": task_id,
                    **item,
                    "content_copied": False,
                },
                provenance=[f"muse-task:{task_id}", f"open-design-run:{run_id}"],
            )
            knowledge.add_edge(
                bundle_node["id"], node["id"], "relates",
                metadata={"relationship": "contains", "path": item["path"], "sha256": item["sha256"]},
            )
            file_node_ids.append(node["id"])

    initiative_link = None
    if initiative_id is not None:
        with InitiativeStore(initiative_database) as initiatives:
            initiative_link = initiatives.record_knowledge_link(
                initiative_id, bundle_node["id"], node_type="source", relationship="related",
            )

    return {
        "status": "persisted",
        "snapshot_sha256": snapshot_digest,
        "bundle_node_id": bundle_node["id"],
        "file_node_ids": file_node_ids,
        "file_count": len(file_node_ids),
        "complete": complete,
        "initiative_link": initiative_link,
    }
