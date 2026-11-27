"""Publish approved Muse memories as ordinary, Obsidian-friendly Markdown notes.

SQLite remains the authoritative store in this first adapter version. Existing Vault
notes are never overwritten or deleted; this is deliberately a safe, one-way mirror.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from muse.memory import MemoryStore


INDEX_NAME = "Muse Memory Index.md"


def _project_slug(project: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", project.casefold()).strip("-")
    return slug[:64] or "general"


def _note_location(memory: dict[str, Any]) -> tuple[str, str]:
    project = str(memory.get("project", "general"))
    if project.casefold() in {"profile", "personal", "general"}:
        folder = "01-Profile"
    elif project.casefold() in {"knowledge", "research"}:
        folder = "03-Knowledge"
    elif project.casefold() in {"decision", "decisions"}:
        folder = "04-Decisions"
    else:
        folder = f"02-Projects/{_project_slug(project)}"
    relative = f"{folder}/memory-{int(memory['id']):06d}.md"
    return relative, f"[[{relative[:-3]}]]"


def _render_note(memory: dict[str, Any]) -> str:
    lines = [
        "---",
        f"muse_id: {int(memory['id'])}",
        f"project: {json.dumps(str(memory['project']), ensure_ascii=False)}",
        f"source: {json.dumps(str(memory['source']), ensure_ascii=False)}",
        f"confidence: {float(memory['confidence']):.6g}",
        f"kind: {str(memory.get('kind', 'fact'))}",
        f"track: {str(memory.get('track', 'user'))}",
        f"revision: {int(memory['revision'])}",
        f"created_at: {json.dumps(str(memory['created_at']))}",
        f"updated_at: {json.dumps(str(memory['updated_at']))}",
        "tags:",
        "  - muse/memory",
        f"  - muse/project/{_project_slug(str(memory['project']))}",
        "---",
        "",
        f"# Memory {int(memory['id'])}",
        "",
        str(memory["content"]).strip(),
        "",
        f"> Source: {memory['source']}",
        f"> Confidence: {float(memory['confidence']):.2f} · Revision: {int(memory['revision'])}",
        "",
    ]
    return "\n".join(lines)


RESEARCH_WIKI_INDEX = "Muse Research Wiki Index.md"


def _research_note_path(node: dict[str, Any]) -> str:
    node_id = str(node.get("id", ""))
    node_type = str(node.get("node_type", ""))
    if not re.fullmatch(r"[a-f0-9]{32}", node_id) or not re.fullmatch(r"[a-z_]{1,32}", node_type):
        raise ValueError("research node identity is invalid")
    return f"05-Research-Wiki/{node_type}/{node_id}.md"


def _render_research_note(node: dict[str, Any], edges: list[dict[str, Any]],
                          nodes_by_id: dict[str, dict[str, Any]]) -> str:
    node_id = str(node["id"])
    links = []
    for edge in edges:
        if edge["source_id"] == node_id:
            other_id, direction = edge["target_id"], "outgoing"
        elif edge["target_id"] == node_id:
            other_id, direction = edge["source_id"], "incoming"
        else:
            continue
        other = nodes_by_id.get(other_id)
        if other is None:
            continue
        other_path = _research_note_path(other)[:-3]
        links.append((edge["edge_type"], direction, other_path, other["title"]))
    body_lines = [
        "# " + str(node["title"]).replace("\n", " ").strip(),
        "",
        "## Summary",
        "",
        str(node.get("summary", "")).strip() or "（暂无摘要）",
        "",
        "## Graph links",
        "",
    ]
    if links:
        for relation, direction, target, title in sorted(links):
            body_lines.append(f"- {relation} ({direction}): [[{target}|{title}]]")
    else:
        body_lines.append("- 暂无已导出的图谱关系。")
    body_lines.extend(["", "## Provenance", ""])
    provenance = node.get("provenance", [])
    if provenance:
        body_lines.extend("- " + str(item) for item in provenance[:32])
    else:
        body_lines.append("- 暂无来源引用。")
    external_ref = node.get("external_ref")
    if external_ref:
        body_lines.extend(["", "## External reference", "", str(external_ref)])
    body_lines.extend([
        "", "## Epistemic boundary", "",
        "此笔记是 SQLite ResearchKnowledgeStore 的只读 Markdown 镜像；SQLite 仍是权威源。",
        "导出不会把 proposed、hypothesis、planned 或 unreviewed 状态升级为事实。",
        "",
    ])
    body = "\n".join(body_lines).rstrip("\n")
    body_sha256 = __import__("hashlib").sha256(body.encode("utf-8")).hexdigest()
    metadata = [
        "---",
        f"muse_research_node_id: {node_id}",
        f"node_type: {node['node_type']}",
        f"status: {node['status']}",
        f"created_at: {json.dumps(str(node['created_at']))}",
        f"updated_at: {json.dumps(str(node['updated_at']))}",
        f"body_sha256: {body_sha256}",
        "tags:",
        "  - muse/research-wiki",
        f"  - muse/research-wiki/{node['node_type']}",
        "---",
        "",
    ]
    return "\n".join(metadata) + body


def audit_research_wiki(knowledge: Any, vault_path: str | Path) -> dict[str, Any]:
    """Read-only audit of the Research Wiki mirror against its SQLite authority."""
    import hashlib

    from muse.research_knowledge import ResearchKnowledgeStore

    if not isinstance(knowledge, ResearchKnowledgeStore):
        raise TypeError("knowledge must be a ResearchKnowledgeStore")
    root = Path(vault_path).expanduser()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError("research wiki audit requires an existing Obsidian Vault directory")
    try:
        resolved_root = root.resolve()
        if resolved_root == knowledge.path.resolve():
            raise ValueError("vault_path cannot point to the ResearchKnowledgeStore database file")
    except OSError as exc:
        raise ValueError("vault_path cannot be resolved") from exc

    active_nodes = knowledge.list_nodes(limit=500)
    nodes_by_id = {node["id"]: node for node in active_nodes}
    wiki_root = root / "05-Research-Wiki"
    counts = {key: 0 for key in (
        "clean", "external_edit", "stale_source", "orphaned", "malformed", "legacy_unverifiable"
    )}
    items: list[dict[str, Any]] = []
    unmanaged = 0
    if wiki_root.exists() and wiki_root.is_dir():
        for note_path in sorted(wiki_root.rglob("*.md")):
            try:
                resolved_note = note_path.resolve()
                if not resolved_note.is_relative_to(resolved_root) or not note_path.is_file():
                    counts["malformed"] += 1
                    items.append({"path": note_path.relative_to(root).as_posix(), "status": "malformed",
                                  "reason": "path_outside_vault_or_not_regular_file"})
                    continue
                content = note_path.read_text(encoding="utf-8-sig")  # B391-P2-13: a BOM is not an edit
            except (OSError, UnicodeError, ValueError):
                counts["malformed"] += 1
                items.append({"path": note_path.relative_to(root).as_posix(), "status": "malformed",
                              "reason": "unreadable_or_invalid_utf8"})
                continue
            relative = note_path.relative_to(root).as_posix()
            lines = content.splitlines()
            if not lines or lines[0].strip() != "---" or "---" not in lines[1:]:
                unmanaged += 1
                continue
            closing = lines.index("---", 1)
            frontmatter = "\n".join(lines[1:closing])

            def field(name: str) -> str | None:
                match = re.search(rf"(?m)^{re.escape(name)}:\s*(.*?)\s*$", frontmatter)
                return match.group(1).strip('"') if match else None

            node_id = field("muse_research_node_id")
            if node_id is None:
                unmanaged += 1
                continue
            node_type = field("node_type")
            status_value = field("status")
            updated_at = field("updated_at")
            expected_hash = field("body_sha256")
            body = "\n".join(lines[closing + 1:]).lstrip("\n")
            actual_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if not re.fullmatch(r"[a-f0-9]{32}", node_id) or not node_type or not status_value or not updated_at:
                result_status, reason = "malformed", "invalid_or_incomplete_muse_frontmatter"
            else:
                node = nodes_by_id.get(node_id) or knowledge.get_node(node_id)
                if node is None:
                    result_status, reason = "orphaned", "source_node_missing_or_tombstoned"
                else:
                    nodes_by_id[node_id] = node
                    expected_path = _research_note_path(node)
                    if relative != expected_path or node_type != node["node_type"]:
                        result_status, reason = "malformed", "node_identity_or_path_mismatch"
                    elif updated_at != str(node["updated_at"]):
                        result_status, reason = "stale_source", "sqlite_source_updated_after_export"
                    elif status_value != str(node["status"]):
                        result_status, reason = "external_edit", "frontmatter_status_differs_from_sqlite"
                    elif expected_hash is None:
                        result_status, reason = "legacy_unverifiable", "no_export_body_hash_recorded"
                    elif not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
                        result_status, reason = "malformed", "invalid_body_sha256_metadata"
                    elif actual_hash != expected_hash:
                        result_status, reason = "external_edit", "markdown_body_hash_mismatch"
                    else:
                        result_status, reason = "clean", "hash_and_source_metadata_match"

            counts[result_status] += 1
            items.append({
                "node_id": node_id, "path": relative, "status": result_status, "reason": reason,
                "source_updated_at": nodes_by_id[node_id]["updated_at"] if node_id in nodes_by_id else None,
                "body_sha256": actual_hash,
            })
    return {
        "vault": str(resolved_root), "source_of_truth": "sqlite_research_knowledge",
        "read_only": True, "scanned_directory": "05-Research-Wiki", **counts,
        "unmanaged_notes_ignored": unmanaged, "active_nodes_visible": len(active_nodes),
        "node_scan_truncated": len(active_nodes) == 500, "items": items,
        "writes": 0, "deletes": 0, "automatic_repairs": 0,
    }


def export_research_wiki(
    knowledge: Any, vault_path: str | Path, *, node_ids: list[str],
) -> dict[str, Any]:
    """Export only explicitly selected research nodes as create-only Obsidian notes.

    This deliberately requires an explicit node-id allowlist. It exports summaries,
    """
    from muse.research_knowledge import ResearchKnowledgeStore

    if not isinstance(knowledge, ResearchKnowledgeStore):
        raise TypeError("knowledge must be a ResearchKnowledgeStore")
    if not isinstance(node_ids, list) or not 1 <= len(node_ids) <= 200:
        raise ValueError("node_ids must contain between 1 and 200 explicit IDs")
    selected_ids = list(dict.fromkeys(node_ids))
    if len(selected_ids) != len(node_ids):
        raise ValueError("node_ids must not contain duplicates")
    if any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{32}", item) for item in selected_ids):
        raise ValueError("node_ids must be canonical research node IDs")
    root = Path(vault_path).expanduser()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError("research wiki export requires an existing Obsidian Vault directory")
    try:
        if root.resolve() == knowledge.path.resolve():
            raise ValueError("vault_path cannot point to the ResearchKnowledgeStore database file")
    except OSError as exc:
        raise ValueError("vault_path cannot be resolved") from exc

    nodes = []
    for node_id in selected_ids:
        node = knowledge.get_node(node_id)
        if node is None:
            raise ValueError("every selected research node must exist and be active")
        nodes.append(node)
    nodes_by_id = {node["id"]: node for node in nodes}
    edges = [
        edge for edge in knowledge.list_edges(limit=500)
        if edge["source_id"] in nodes_by_id and edge["target_id"] in nodes_by_id
    ]
    created: list[str] = []
    skipped: list[str] = []
    links: list[str] = []
    for node in nodes:
        relative = _research_note_path(node)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        links.append(f"- [[{relative[:-3]}|{node['node_type']}: {node['title']}]]")
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(_render_research_note(node, edges, nodes_by_id))
                handle.flush()
                os.fsync(handle.fileno())
            created.append(relative)
        except FileExistsError:
            skipped.append(relative)

    index_path = root / RESEARCH_WIKI_INDEX
    index_created = False
    try:
        with index_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join([
                "# Muse Research Wiki Index", "",
                "SQLite 是权威知识库；本索引仅镜像明确选择导出的节点。", "",
                *sorted(links), "",
            ]))
            handle.flush()
            os.fsync(handle.fileno())
        index_created = True
    except FileExistsError:
        pass
    return {
        "vault": str(root.resolve()), "source_of_truth": "sqlite_research_knowledge",
        "sync_direction": "sqlite_to_obsidian_create_only", "explicit_selection_required": True,
        "selected": len(nodes), "created": len(created), "skipped_existing": len(skipped),
        "edge_count": len(edges), "index_created": index_created,
        "created_paths": created, "skipped_paths": skipped,
        "existing_notes_overwritten": 0, "notes_deleted": 0, "source_bodies_exported": 0,
    }


def audit_vault(memory: MemoryStore, vault_path: str | Path) -> dict[str, Any]:
    """Compare Muse-managed notes with SQLite without modifying either store.

    Changed notes are reported as conflicts; this function never imports, rewrites,
    or deletes content. Notes without Muse metadata are counted but left untouched.
    """
    import hashlib

    root = Path(vault_path).expanduser()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Obsidian Vault directory does not exist: {root}")
    try:
        if root.resolve() == memory.database.resolve():
            raise ValueError("vault_path cannot point to the SQLite database file")
    except OSError as exc:
        raise ValueError("vault_path cannot be resolved") from exc

    records = memory.list_memories(limit=200)
    known_ids = {int(record["id"]) for record in records}
    items: list[dict[str, Any]] = []
    expected_paths: set[str] = set()
    counts = {"unchanged": 0, "legacy_metadata": 0, "modified": 0, "missing": 0, "conflicts": 0,
              "orphaned_managed": 0}

    for record in records:
        relative, _link = _note_location(record)
        expected_paths.add(relative.replace("\\", "/"))
        note_path = root / relative
        actual_hash = None
        if not note_path.is_file():
            status = "missing"
        else:
            content = note_path.read_text(encoding="utf-8-sig")  # B391-P2-13: a BOM is not an edit
            actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            lines = content.splitlines()
            if not lines or lines[0].strip() != "---" or "---" not in lines[1:]:
                status = "malformed_managed_note"
            else:
                closing = lines.index("---", 1)
                frontmatter = "\n".join(lines[1:closing])
                id_match = re.search(r"(?m)^muse_id:\s*(\d+)\s*$", frontmatter)
                revision_match = re.search(r"(?m)^revision:\s*(\d+)\s*$", frontmatter)
                if not id_match or int(id_match.group(1)) != int(record["id"]):
                    status = "metadata_conflict"
                elif not revision_match:
                    status = "malformed_managed_note"
                elif int(revision_match.group(1)) != int(record["revision"]):
                    status = "stale_source_revision"
                elif content == _render_note(record):
                    status = "unchanged"
                elif content == _render_note(record).replace(
                    f"kind: {record.get('kind', 'fact')}\n", ""
                ).replace(f"track: {record.get('track', 'user')}\n", ""):
                    status = "legacy_metadata"
                else:
                    status = "external_edit_detected"
        if status == "unchanged":
            counts["unchanged"] += 1
        elif status == "legacy_metadata":
            counts["legacy_metadata"] += 1
        elif status == "missing":
            counts["missing"] += 1
        else:
            counts["modified"] += 1
            counts["conflicts"] += 1
        items.append({"memory_id": int(record["id"]), "path": relative, "status": status,
                      "source_revision": int(record["revision"]), "sha256": actual_hash})

    unmanaged = 0
    for note_path in root.rglob("*.md"):
        relative = note_path.relative_to(root).as_posix()
        if relative in expected_paths or relative == INDEX_NAME:
            continue
        try:
            content = note_path.read_text(encoding="utf-8-sig")  # B391-P2-13: a BOM is not an edit
        except (OSError, UnicodeError):
            unmanaged += 1
            continue
        match = re.search(r"(?m)^muse_id:\s*(\d+)\s*$", content)
        if match:
            memory_id = int(match.group(1))
            if memory_id not in known_ids:
                counts["orphaned_managed"] += 1
                items.append({"memory_id": memory_id, "path": relative,
                              "status": "orphaned_managed_note", "source_revision": None,
                              "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()})
        else:
            unmanaged += 1

    return {"vault": str(root.resolve()), "source_of_truth": "sqlite",
            "read_only": True, **counts, "unmanaged_notes_ignored": unmanaged,
            "items": items}


def export_approved_memories(
    memory: MemoryStore, vault_path: str | Path, *, limit: int = 200
) -> dict[str, Any]:
    """Create missing Markdown notes; never overwrite or delete user-authored Vault files."""
    root = Path(vault_path).expanduser()
    if root.exists() and not root.is_dir():
        raise ValueError("vault_path must be a directory")
    try:
        if root.resolve() == memory.database.resolve():
            raise ValueError("vault_path cannot point to the SQLite database file")
    except OSError as exc:
        raise ValueError("vault_path cannot be resolved") from exc
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")

    root.mkdir(parents=True, exist_ok=True)
    records = memory.list_memories(limit=limit)
    created: list[str] = []
    skipped: list[str] = []
    links: list[str] = []
    for record in records:
        relative, link = _note_location(record)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(_render_note(record))
                handle.flush()
                os.fsync(handle.fileno())
            created.append(relative)
        except FileExistsError:
            skipped.append(relative)
        links.append(f"- {link} — {str(record['project'])} · Memory {int(record['id'])}")

    index_path = root / INDEX_NAME
    existing = index_path.read_text(encoding="utf-8-sig") if index_path.exists() else "# Muse Memory Index\n\n"
    if not existing.startswith("# Muse Memory Index"):
        existing = "# Muse Memory Index\n\n" + existing
        index_path.write_text(existing, encoding="utf-8")
    if not index_path.exists():
        index_path.write_text(existing, encoding="utf-8")
    missing_lines = [line for line in links if line.split(" — ", 1)[0] not in existing]
    if missing_lines:
        with index_path.open("a", encoding="utf-8", newline="\n") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("\n".join(missing_lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    elif not index_path.exists():
        index_path.write_text("# Muse Memory Index\n", encoding="utf-8")

    return {
        "vault": str(root.resolve()),
        "total_approved_memories_seen": len(records),
        "created": len(created),
        "skipped_existing": len(skipped),
        "created_paths": created,
        "skipped_paths": skipped,
        "index": INDEX_NAME,
        "index_entries_added": len(missing_lines),
        "source_of_truth": "sqlite",
        "sync_direction": "sqlite_to_obsidian",
    }