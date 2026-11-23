"""Read-only, loopback-only status API for the Muse desktop client."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import math
import json
import csv
import io
import subprocess
import sys
import os
import tomllib
from contextlib import closing
import secrets
import sqlite3
import re
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore, Lock, Thread
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit
from muse.agent_registry import AgentRegistry
from muse.capabilities import CapabilityRegistry
from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue
from muse.improvement import ImprovementStore
from muse.capability_radar import scan_capabilities
from muse.workflow_profiles import WorkflowController
from muse.research_knowledge import ResearchKnowledgeStore
from muse.product_builder import LocalProductBuilder, ProductBuildError
from muse.media import discover_media_tools
from muse.video_engines import inspect_video_engines
from muse.cyber_cosmos import (
    build_agent_galaxy,
    build_celestial_catalog,
    build_knowledge_graph,
    build_knowledge_node_enriched,
    build_prometheus_core,
    preview_specialist_route,
    run_yijing_inquiry,
)
from datetime import datetime, timezone


class VideoProductBuilder:
    """Lazy compatibility facade: keep unrelated dashboard APIs importable if video extras are broken."""

    def __init__(self, workspace):
        self.workspace = Path(workspace)

    def _implementation(self):
        from muse.video_product_builder import VideoProductBuilder as Implementation
        return Implementation(self.workspace)

    def build_from_prompt(self, *args, **kwargs):
        return self._implementation().build_from_prompt(*args, **kwargs)

    def package_approved_subtitle_revision(self, *args, **kwargs):
        return self._implementation().package_approved_subtitle_revision(*args, **kwargs)

    def _source(self, *args, **kwargs):
        return self._implementation()._source(*args, **kwargs)

    @staticmethod
    def verify_path(*args, **kwargs):
        from muse.video_product_builder import VideoProductBuilder as Implementation
        return Implementation.verify_path(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._implementation(), name)


def _validate_json_nesting(raw: bytes, *, max_depth: int = 64) -> None:
    """Reject deeply nested JSON before schema validation can mask the depth error."""
    text = raw.decode("utf-8")
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise RecursionError("request nesting exceeds the supported limit")
        elif char in "]}":
            depth = max(0, depth - 1)


def _allowed_origin(origin: str | None) -> bool:
    if origin is None:
        return True
    if origin in {"tauri://localhost", "http://tauri.localhost"}:
        return True
    parsed = urlsplit(origin)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.username is None
        and parsed.password is None
    )

def build_mempalace_status(workspace_root: str | Path, memory_db: str | Path | None = None) -> dict[str, Any]:
    """Read-only, privacy-minimal MemPalace and projection-outbox health snapshot."""
    root = Path(workspace_root)
    config_path = root / "config" / "muse.toml"
    config: dict[str, Any] = {}
    config_state = "not_configured"
    if config_path.is_file():
        try:
            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
            section = parsed.get("mempalace", {})
            if not isinstance(section, dict):
                raise ValueError("mempalace section must be a table")
            config = section
            config_state = "configured"
        except (OSError, tomllib.TOMLDecodeError, ValueError):
            config_state = "invalid_config"

    raw_enabled = os.environ.get("MUSE_MEMPALACE_ENABLED")
    enabled = config.get("enabled", False) is True
    if raw_enabled is not None:
        normalized_enabled = raw_enabled.strip().casefold()
        if normalized_enabled not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            config_state = "invalid_config"
        else:
            enabled = normalized_enabled in {"1", "true", "yes", "on"}
    palace_present = False
    vector_index_present = False
    model = config.get("embedding_model", "embeddinggemma")
    device = config.get("embedding_device", "cpu")
    raw_palace = os.environ.get("MUSE_MEMPALACE_PALACE") or config.get("palace_path")
    if enabled and isinstance(raw_palace, str) and raw_palace.strip():
        candidate = Path(raw_palace).expanduser()
        if candidate.is_absolute() and not candidate.is_symlink():
            palace_present = candidate.is_dir()
            vector_index_present = palace_present and (candidate / "chroma.sqlite3").is_file()
    if config_state == "invalid_config":
        connection_state = "invalid_config"
    elif not enabled:
        connection_state = "disabled" if config_state == "configured" else "not_configured"
    elif palace_present:
        connection_state = "local_store_present"
    else:
        connection_state = "configured_store_missing"

    database = Path(memory_db) if memory_db is not None else root / "data" / "memory" / "muse.sqlite3"
    outbox = {
        "initialized": False, "status": "not_initialized", "total": 0,
        "pending": 0, "failed": 0, "delivered": 0,
        "oldest_pending_at": None, "latest_delivered_at": None,
        "latest_created_at": None, "content_included": False,
    }
    memory_store_state = "not_initialized"
    if database.is_file():
        try:
            uri = database.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as connection:
                connection.execute("PRAGMA query_only=ON")
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mempalace_projection_outbox'"
                ).fetchone()
                memory_store_state = "ready"
                if exists:
                    outbox["initialized"] = True
                    rows = connection.execute(
                        "SELECT status, COUNT(*) AS amount, MAX(created_at) AS latest "
                        "FROM mempalace_projection_outbox GROUP BY status"
                    ).fetchall()
                    for status, amount, latest in rows:
                        if status in {"pending", "failed", "delivered"}:
                            outbox[status] = int(amount)
                        if isinstance(latest, str) and (outbox["latest_created_at"] is None or latest > outbox["latest_created_at"]):
                            outbox["latest_created_at"] = latest
                    outbox["oldest_pending_at"] = connection.execute(
                        "SELECT MIN(created_at) FROM mempalace_projection_outbox WHERE status='pending'"
                    ).fetchone()[0]
                    outbox["latest_delivered_at"] = connection.execute(
                        "SELECT MAX(delivered_at) FROM mempalace_projection_outbox WHERE status='delivered'"
                    ).fetchone()[0]
                    outbox["total"] = outbox["pending"] + outbox["failed"] + outbox["delivered"]
                    outbox["status"] = (
                        "attention_required" if outbox["failed"] else
                        "pending" if outbox["pending"] else "healthy"
                    )
        except (OSError, sqlite3.Error, ValueError):
            memory_store_state = "unavailable"

    return {
        "api_version": 1,
        "config_state": config_state,
        "enabled": enabled,
        "connection_state": connection_state,
        "palace_directory_present": palace_present,
        "vector_index_present": vector_index_present,
        "index_freshness": "not_measured",
        "embedding_model": model if model in {"embeddinggemma", "minilm"} else "unknown",
        "embedding_device": device if device in {"cpu", "dml", "auto"} else "unknown",
        "memory_store_state": memory_store_state,
        "projection_outbox": outbox,
        "remote_process_started": False,
        "private_content_exposed": False,
    }


def build_overview(task_db: str | Path, progress_db: str | Path) -> dict[str, Any]:
    """Return bounded operational data only; never expose task payloads or chat text."""
    with TaskQueue(task_db) as queue:
        queue_snapshot = queue.snapshot(limit=50)
        workflows = []
        for task in queue_snapshot.get("active", [])[:20]:
            if queue.workflow_revision(task["id"]) == 0:
                continue
            events_for_task = queue.workflow_events(task["id"])
            bound = next((event for event in events_for_task if event["event_type"] == "workflow.bound"), None)
            if bound is None:
                continue
            stage_event = next(
                (event for event in reversed(events_for_task) if event["event_type"] == "workflow.stage_changed"),
                None,
            )
            stage_payload = stage_event["payload"] if stage_event else {}
            profile = bound["payload"].get("profile", {})
            workflows.append({
                "ticket_id": task["id"], "title": task["title"],
                "workflow_id": bound["payload"].get("workflow_id"),
                "profile_name": profile.get("name"),
                "profile_version": profile.get("schema_version"),
                "status": queue.workflow_status(task["id"]),
                "stage": stage_payload.get("stage", "plan"),
                "attempt": stage_payload.get("attempt", task.get("attempts", 0)),
                "step_id": stage_payload.get("step_id"),
                "next_action": stage_payload.get("next_action"),
            })
    with ProgressEventStore(progress_db) as progress:
        workers = progress.list_workers(limit=50)
        events = progress.list_events(limit=20)
    return {"queue": queue_snapshot, "workers": workers, "recent_events": events, "workflow_runs": workflows}

_DASHBOARD_FILE_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_DASHBOARD_FILE_CACHE_LOCK = Lock()

def _dashboard_file_inventory(workspace_root: Path, *, limit: int = 160) -> dict[str, Any]:
    """Return a bounded, allowlisted project file inventory; never read file contents."""
    cache_key = (str(workspace_root.resolve()), limit)
    with _DASHBOARD_FILE_CACHE_LOCK:
        cached = _DASHBOARD_FILE_CACHE.get(cache_key)
        if cached is not None and cached[0] > time.monotonic():
            return dict(cached[1])
    allowed_roots = ("docs", "scripts", "tests", "src/muse", "ui-prototype", "workflows")
    root_files = ("README.md", "AGENTS.md", "pyproject.toml")
    excluded_parts = {
        ".git", ".venv", "node_modules", "__pycache__", "data", "models",
        "third_party", "vault", "logs", "tmp", ".muse-backups",
    }
    excluded_suffixes = {
        ".sqlite", ".sqlite3", ".db", ".log", ".pem", ".p12", ".pfx",
        ".key", ".env", ".bak",
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_file(candidate: Path) -> None:
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return
            relative = candidate.relative_to(workspace_root).as_posix()
            parts = Path(relative).parts
            if any(part in excluded_parts or part.startswith(".") for part in parts):
                return
            lowered = candidate.name.casefold()
            if lowered.endswith(tuple(excluded_suffixes)) or any(
                marker in lowered for marker in ("secret", "credential", "password", "token")
            ):
                return
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(workspace_root)
            stat = candidate.stat()
        except (OSError, ValueError):
            return
        if relative in seen:
            return
        seen.add(relative)
        rows.append({
            "path": relative,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })

    for name in root_files:
        add_file(workspace_root / name)
    for relative_root in allowed_roots:
        directory = workspace_root / relative_root
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            candidates = directory.rglob("*")
            for candidate in candidates:
                add_file(candidate)
        except OSError:
            continue
    rows.sort(key=lambda item: item["path"].casefold())
    result = {
        "status": "available",
        "count": len(rows),
        "items": rows[:limit],
        "truncated": len(rows) > limit,
        "content_included": False,
    }
    with _DASHBOARD_FILE_CACHE_LOCK:
        _DASHBOARD_FILE_CACHE[cache_key] = (time.monotonic() + 60.0, result)
    return result


_DASHBOARD_TASK_STATUS_CACHE: tuple[float, dict[str, Any]] | None = None
_DASHBOARD_TASK_STATUS_LOCK = Lock()


def _dashboard_scheduled_tasks(*, platform_name: str | None = None, command_runner=None) -> dict[str, Any]:
    global _DASHBOARD_TASK_STATUS_CACHE
    cacheable = platform_name is None and command_runner is None
    if cacheable:
        with _DASHBOARD_TASK_STATUS_LOCK:
            cached = _DASHBOARD_TASK_STATUS_CACHE
            if cached is not None and cached[0] > time.monotonic():
                return dict(cached[1])
    result = _query_dashboard_scheduled_tasks(platform_name=platform_name, command_runner=command_runner)
    if cacheable:
        with _DASHBOARD_TASK_STATUS_LOCK:
            _DASHBOARD_TASK_STATUS_CACHE = (time.monotonic() + 30.0, result)
    return result


def _query_dashboard_scheduled_tasks(*, platform_name: str | None = None, command_runner=None) -> dict[str, Any]:
    """Read only the two documented Muse Task Scheduler entries using fixed argv."""
    platform_value = platform_name if platform_name is not None else sys.platform
    if platform_value != "win32":
        return {"status": "unsupported", "items": [], "note": "Windows Task Scheduler is unavailable on this platform."}
    runner = command_runner or subprocess.run
    names = ("MuseAutonomousRuntime", "MuseDesktopDashboard")
    items = []
    for name in names:
        try:
            completed = runner(
                ["schtasks.exe", "/Query", "/TN", name, "/FO", "CSV"],
                capture_output=True, text=True, timeout=4, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return {"status": "unavailable", "items": items, "note": "Task Scheduler query failed."}
        if completed.returncode != 0:
            items.append({"name": name, "state": "not_registered", "next_run_time": None})
            continue
        try:
            records = list(csv.reader(io.StringIO(completed.stdout or "")))
        except (csv.Error, TypeError):
            return {"status": "unavailable", "items": items, "note": "Task Scheduler returned an unreadable response."}
        record = next((
            row for row in records[1:]
            if row and str(row[0]).strip().lstrip("\\").rstrip("\\") == name
        ), None)
        if record is None:
            items.append({"name": name, "state": "not_registered", "next_run_time": None})
            continue
        raw_state = str(record[2] if len(record) > 2 else "").strip().casefold()
        state = {
            "running": "running", "ready": "ready", "disabled": "disabled",
            "正在运行": "running", "就绪": "ready", "已禁用": "disabled", "禁用": "disabled",
        }.get(raw_state, "unknown")
        items.append({
            "name": name,
            "state": state,
            "next_run_time": str(record[1] if len(record) > 1 else "").strip() or None,
        })
    return {"status": "available", "items": items, "note": "Ready/就绪 means registered and idle; Running/正在运行 means currently executing."}


def _read_runner_snapshot(workspace_root: Path) -> dict[str, Any]:
    """Read a redacted, timestamped WebCodex observation snapshot, never credentials."""
    path = workspace_root / "data" / "dashboard" / "runner_snapshot.json"

    def empty(status: str) -> dict[str, Any]:
        return {
            "status": status, "captured_at": None, "age_seconds": None,
            "runner_status": "unknown", "connected": False,
            "projects": [], "skills": [], "project_count": None,
            "skill_count": None, "invalid_skill_count": None,
            "active_job_count": None, "running_job_count": None,
            "queued_job_count": None, "concurrency_limit": None,
            "active_jobs": [], "skill_sources": [],
            "mcp_servers": [], "plugins": [],
        }

    if path.is_symlink() or not path.is_file():
        return empty("not_synced")
    try:
        path.resolve(strict=True).relative_to(workspace_root.resolve())
    except (OSError, ValueError):
        return empty("invalid")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("unsupported snapshot")
        captured = datetime.fromisoformat(str(raw["captured_at"]))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        captured = captured.astimezone(timezone.utc)
        age = max(0, int((datetime.now(timezone.utc) - captured).total_seconds()))

        def safe_count(key: str) -> int | None:
            value = raw.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        projects = [
            {"name": str(item["name"])[:80], "connected": item.get("connected") is True}
            for item in raw.get("projects", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ][:100]
        skills = [
            {"name": str(item["name"])[:100]}
            for item in raw.get("skills", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ][:100]
        mcp_servers = [str(item)[:100] for item in raw.get("mcp_servers", []) if isinstance(item, str)][:50]
        plugins = [str(item)[:100] for item in raw.get("plugins", []) if isinstance(item, str)][:50]
        invalid_count = safe_count("invalid_skill_count") or 0
        active_jobs = []
        for item in raw.get("active_jobs", []):
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            status = item.get("status")
            elapsed = item.get("elapsed_secs")
            if not isinstance(kind, str) or not isinstance(status, str):
                continue
            active_jobs.append({
                "kind": kind[:60], "status": status[:24],
                "elapsed_secs": elapsed if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0 else None,
                "project": str(item.get("project", ""))[:80],
            })
        active_jobs = active_jobs[:30]
        skill_sources = []
        for item in raw.get("skill_sources", []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            count = item.get("count")
            invalid = item.get("invalid_count", 0)
            skill_sources.append({
                "name": item["name"][:80],
                "count": count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0,
                "invalid_count": invalid if isinstance(invalid, int) and not isinstance(invalid, bool) and invalid >= 0 else 0,
            })
        runner_status = raw.get("runner_status")
        if runner_status not in {"online", "offline"}:
            runner_status = "unknown"
        return {
            "status": "stale" if age > 900 else "fresh",
            "captured_at": captured.isoformat(), "age_seconds": age,
            "runner_status": runner_status, "connected": runner_status == "online",
            "projects": projects, "skills": skills,
            "project_count": len(projects), "skill_count": len(skills),
            "invalid_skill_count": min(invalid_count, len(skills)),
            "active_job_count": safe_count("active_job_count"),
            "running_job_count": safe_count("running_job_count"),
            "queued_job_count": safe_count("queued_job_count"),
            "concurrency_limit": safe_count("concurrency_limit"),
            "active_jobs": active_jobs, "skill_sources": skill_sources,
            "mcp_servers": mcp_servers, "plugins": plugins,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return empty("invalid")



def build_system_dashboard(
    workspace_root: str | Path, capability_db: str | Path | None = None,
    *, platform_name: str | None = None, command_runner=None,
) -> dict[str, Any]:
    """Build the unified read-only system dashboard from local sources and a timestamped Runner snapshot."""
    root = Path(workspace_root).resolve()
    runner = _read_runner_snapshot(root)
    capabilities = []
    capability_state = "not_configured"
    if capability_db is not None:
        database = Path(capability_db)
        if database.is_file() and not database.is_symlink():
            try:
                capabilities = build_capabilities(database)
                capability_state = "available"
            except (OSError, sqlite3.Error, ValueError):
                capability_state = "unavailable"
    spaces = {
        "status": "not_configured",
        "count": 0,
        "items": [],
        "note": "Muse 当前没有独立 Space 注册表；本项目根目录是已确认的本地工作区。",
    }
    integrations = [
        {"id": "webcodex_runner", "label": "WebCodex Runner", "status": "snapshot_only" if runner["status"] in {"fresh", "stale"} else "not_bridged", "detail": f"{runner['runner_status']} · {runner['active_job_count'] if runner['active_job_count'] is not None else 0} 个活跃任务；本地 API 仍未建立实时桥接。"},
        {"id": "mcp", "label": "MCP 服务", "status": ("not_bridged" if runner["status"] not in {"fresh", "stale"} else "available" if runner["mcp_servers"] else "empty"), "detail": f"{len(runner['mcp_servers'])} 个已登记服务（快照）。"},
        {"id": "plugins", "label": "Runner 插件", "status": ("not_bridged" if runner["status"] not in {"fresh", "stale"} else "available" if runner["plugins"] else "empty"), "detail": f"{len(runner['plugins'])} 个已登记插件（快照）。"},
        {"id": "email", "label": "邮件", "status": "not_connected", "detail": "当前没有可验证的邮件连接器。"},
    ]
    return {
        "api_version": 1,
        "workspace": {"name": root.name, "status": "available"},
        "files": _dashboard_file_inventory(root),
        "spaces": spaces,
        "runner": runner,
        "projects": runner["projects"],
        "skills": runner["skills"],
        "local_capabilities": {"status": capability_state, "count": len(capabilities), "items": capabilities},
        "scheduled_tasks": _dashboard_scheduled_tasks(platform_name=platform_name, command_runner=command_runner),
        "integrations": integrations,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
    }



def build_capabilities(capability_db: str | Path) -> list[dict[str, Any]]:
    """Expose display-safe capability metadata, excluding executable paths and opaque metadata."""
    with CapabilityRegistry(capability_db) as registry:
        items = registry.list_all()
    fields = ("capability_id", "domain", "name", "description", "capabilities", "requirements", "trust", "license", "status", "version")
    return [{key: item.get(key) for key in fields} for item in items]


def build_agent_org_chart(agent_db: str | Path, *, limit: int = 500) -> dict[str, object]:
    """Return the bounded, display-safe reporting tree without exposing runtime secrets."""
    with AgentRegistry(agent_db) as registry:
        return registry.org_chart(limit=limit)


def build_agents(agent_db: str | Path, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return display-safe Agent state and aggregate outcome metrics."""
    with AgentRegistry(agent_db) as registry:
        agents = registry.list_agents(limit=limit)
        return [{
            "agent_id": agent["agent_id"], "role": agent["role"],
            "title": agent["title"], "reports_to": agent["reports_to"],
            "display_name": agent["display_name"], "capabilities": agent["capabilities"],
            "source": agent["source"], "enabled": agent["enabled"],
            "status": agent["status"], "last_seen_at": agent["last_seen_at"],
            "stale_after_seconds": agent["stale_after_seconds"],
            "reported_state": agent.get("reported_state"),
            "reported_at": agent.get("reported_at"),
            "reported_source": agent.get("reported_source"),
            "current_task_id": agent["current_task_id"],
            "effectiveness": registry.effectiveness(str(agent["agent_id"])),
        } for agent in agents]

def build_feedback(improvement_db: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return only allowlisted feedback fields; no free-text or identifiers."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    with ImprovementStore(improvement_db) as store:
        rows = store.list_feedback(limit=limit)
    return [
        {key: row[key] for key in ("category", "tag", "severity", "created_at")}
        for row in rows
    ]


def build_improvements(improvement_db: str | Path, *, limit: int = 25) -> list[dict[str, Any]]:
    """Return bounded proposal summaries, excluding arbitrary metadata and audit notes."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    with ImprovementStore(improvement_db) as store:
        rows = store.list_proposals()
    return [
        {
            key: row[key]
            for key in ("id", "title", "status", "priority", "created_at", "updated_at")
        }
        for row in rows[:limit]
    ]


def build_self_improvement_status(
    improvement_db: str | Path, progress_db: str | Path,
) -> dict[str, Any]:
    """Expose aggregate improvement blockers and safe cycle state, never private checkpoint IDs."""
    with ImprovementStore(improvement_db) as improvements:
        rows = improvements.connection.execute(
            "SELECT status, COUNT(*) AS count FROM improvement_proposals GROUP BY status"
        ).fetchall()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    for state in ("proposed", "researching", "ready_for_review", "approved",
                  "implemented", "rejected", "rolled_back"):
        counts.setdefault(state, 0)
    with ProgressEventStore(progress_db) as progress:
        events = progress.list_events(category="evolution", limit=200)
    latest_cycle = next((event for event in events if isinstance(event.get("subject_id"), str)
                         and re.fullmatch(r"cycle_[0-9a-f]{32}", event["subject_id"])), None)
    cycle_state = latest_cycle.get("event") if latest_cycle else "not_started"
    cycle_resumable = cycle_state in {"started", "progress"}
    review_blockers = counts["ready_for_review"]
    execution_blockers = counts["approved"]
    research_backlog = counts["proposed"] + counts["researching"]
    return {
        "status": "available", "proposal_counts": counts,
        "pending_research": research_backlog,
        "pending_human_review": review_blockers,
        "approved_waiting_execution": execution_blockers,
        "next_action": (
            "human_review_required" if review_blockers else
            "approved_ticket_required" if execution_blockers else
            "continue_research" if research_backlog else
            "discover_new_improvements"
        ),
        "cycle_state": cycle_state,
        "cycle_resumable": cycle_resumable,
        "cycle_updated_at": latest_cycle.get("created_at") if latest_cycle else None,
        "topic_checkpoint_saved": bool(
            latest_cycle and any(
                event.get("subject_id") == latest_cycle.get("subject_id")
                and isinstance(event.get("summary"), str)
                and event["summary"].startswith("topic_key:")
                for event in events
            )
        ),
        "approval_required": True,
        "automatic_code_changes": False,
    }


def build_improvement_detail(improvement_db: str | Path, proposal_id: str) -> dict[str, Any] | None:
    """Return a bounded, allowlisted proposal detail without arbitrary metadata or audit notes."""
    if not isinstance(proposal_id, str) or len(proposal_id) != 32 or any(c not in "0123456789abcdef" for c in proposal_id):
        raise ValueError("invalid proposal id")
    with ImprovementStore(improvement_db) as store:
        row = store.get(proposal_id)
    if row is None:
        return None
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), dict) else {}
    safe_evidence = {
        key: evidence[key] for key in (
            "lookback_hours", "signal_count", "failed_tasks", "recent_task_failure_events",
            "test_failures", "retry_attempts", "retried_tasks", "observed_at",
        ) if key in evidence and isinstance(evidence[key], (str, int, float, bool, type(None)))
    }
    criteria = metadata.get("acceptance_criteria", [])
    if not isinstance(criteria, list):
        criteria = []
    sources = [{
        key: source.get(key) for key in (
            "id", "title", "url", "license_status", "security_status", "created_at"
        )
    } for source in row.get("sources", [])[:30]]
    events = [{
        key: event.get(key) for key in ("from_status", "to_status", "actor", "created_at")
    } for event in row.get("events", [])[-30:]]
    return {
        key: row.get(key) for key in (
            "id", "title", "problem", "hypothesis", "status", "priority", "created_at", "updated_at"
        )
    } | {
        "confidence": metadata.get("confidence") if isinstance(metadata.get("confidence"), (int, float)) else None,
        "impact": metadata.get("impact") if isinstance(metadata.get("impact"), str) else "",
        "evidence": safe_evidence,
        "acceptance_criteria": [item[:500] for item in criteria[:20] if isinstance(item, str)],
        "rollback_plan": (metadata.get("rollback_plan")[:2000]
                          if isinstance(metadata.get("rollback_plan"), str) else ""),
        "sources": sources,
        "events": events,
        "approval_required": True,
    }


def build_knowledge_node(knowledge_db: str | Path, node_id: str) -> dict[str, Any] | None:
    """Return one safe, bounded Research Wiki node for local timeline traversal."""
    if not isinstance(node_id, str) or not re.fullmatch(r"[0-9a-f]{32}", node_id):
        raise ValueError("invalid knowledge node id")
    with ResearchKnowledgeStore(knowledge_db) as store:
        node = store.get_node(node_id)
    if node is None:
        return None
    metadata = node.get("metadata", {}) if isinstance(node.get("metadata"), dict) else {}
    metadata_fields = (
        "origin", "epistemic_status", "review_state", "decision_kind",
        "independent_review_state", "improvement_proposal_id", "source_feedback_id",
        "evolution_cycle_id", "review_id", "reviewer_id", "approved",
        "creator_initiative_id", "candidate_fingerprint",
    )
    safe_metadata = {
        key: metadata[key] for key in metadata_fields
        if key in metadata and isinstance(metadata[key], (str, int, float, bool, type(None)))
    }
    return {
        "id": node["id"], "node_type": node["node_type"], "title": node["title"],
        "summary": str(node.get("summary", ""))[:2000], "status": node["status"],
        "provenance": [str(item)[:300] for item in node.get("provenance", [])[:30]],
        "created_at": node["created_at"], "updated_at": node["updated_at"],
        "metadata": safe_metadata,
    }


def build_claim_dossier(knowledge_db: str | Path, claim_id: str, *, evidence_limit: int = 12) -> dict[str, Any] | None:
    """Expose a bounded claim dossier; retrieval metadata never becomes a verdict."""
    if not isinstance(claim_id, str) or not re.fullmatch(r"[0-9a-f]{32}", claim_id):
        raise ValueError("invalid claim id")
    if isinstance(evidence_limit, bool) or not isinstance(evidence_limit, int) or not 1 <= evidence_limit <= 100:
        raise ValueError("evidence_limit must be between 1 and 100")
    with ResearchKnowledgeStore(knowledge_db) as store:
        try:
            return store.claim_dossier(claim_id, evidence_limit=evidence_limit)
        except ValueError as exc:
            if "active claim node" in str(exc):
                return None
            raise


def build_inspirations(knowledge_db: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    with ResearchKnowledgeStore(knowledge_db) as store:
        nodes = store.list_nodes(node_type="claim", limit=500)
    rows = [node for node in nodes if node.get("metadata", {}).get("origin") == "creator_inspiration_inbox"]
    results = []
    for node in rows[:limit]:
        meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        results.append({
            "id": node["id"], "title": node["title"], "content": node["summary"],
            "created_at": node["created_at"], "updated_at": node["updated_at"],
            "status": meta.get("review_state", "not_researched"),
            "allow_external_research": meta.get("allow_external_research") is True,
            "tags": list(meta.get("tags", [])),
            "research_domains": list(meta.get("research_domains", [])),
            "source": meta.get("source_kind", "muse_local_dashboard"),
            "evolution_stage": str(meta.get("evolution_stage") or "not_evolved"),
            "blueprint_path": str(meta.get("blueprint_path") or ""),
            "research_report_path": str(meta.get("research_report_path") or ""),
            "improvement_proposal_ids": list(meta.get("improvement_proposal_ids") or [])[:12],
        })
    return results


def build_artifacts(report_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    root = Path(report_dir).resolve()
    if not root.is_dir():
        return []
    rows = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".md":
            continue
        resolved = path.resolve()
        if resolved.parent != root:
            continue
        stat = resolved.stat()
        rows.append({
            "id": resolved.name, "name": resolved.name, "kind": "research_report",
            "size_bytes": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "download_path": "/api/v1/artifacts/" + resolved.name,
        })
    return sorted(rows, key=lambda item: item["modified_at"], reverse=True)[:limit]

def _verified_product_files(products_dir: str | Path, initiative_id: str, version: str):
    if not re.fullmatch(r"[0-9a-f]{32}", initiative_id) or not re.fullmatch(r"[0-9a-f]{20}", version):
        raise ValueError("invalid product identity")
    root = Path(products_dir)
    if root.is_symlink() or root.parent.is_symlink():
        raise ValueError("product root and parent must not be symlinks")
    root = root.resolve()
    initiative_root = root / initiative_id
    product_dir = initiative_root / version
    if initiative_root.is_symlink() or product_dir.is_symlink():
        raise ValueError("product path must not contain symlinks")
    if not product_dir.is_dir() or product_dir.resolve().parent != initiative_root.resolve():
        raise FileNotFoundError("product not found")
    manifest_path = product_dir / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FileNotFoundError("product manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if manifest.get("product_kind") == "video_creator":
        VideoProductBuilder.verify_path(root, initiative_id, version)
        resolved = {}
        for name in ("README.md", "storyboard.json", "video.mp4", "product.zip", "qa-report.json"):
            path = product_dir / name
            if name == "qa-report.json" and not path.is_file():
                continue
            resolved[name] = (path, None if name in {"video.mp4", "product.zip"} else path.read_bytes())
        return product_dir, manifest, resolved
    expected = {"index.html", "README.md", "product.zip"}
    if manifest.get("initiative_id") != initiative_id or not isinstance(files, dict) or set(files) != expected:
        raise ValueError("product manifest is invalid")
    resolved_files = {}
    for name, digest in files.items():
        path = product_dir / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != product_dir.resolve():
            raise FileNotFoundError("product file missing or unsafe")
        body = path.read_bytes()
        if len(body) > 20 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("product file integrity check failed")
        resolved_files[name] = (path, body)
    try:
        LocalProductBuilder._validate_html(resolved_files["index.html"][1].decode("utf-8"))
    except (UnicodeDecodeError, ProductBuildError) as exc:
        raise ValueError("product HTML does not match the trusted offline template") from exc
    with zipfile.ZipFile(resolved_files["product.zip"][0]) as archive:
        if set(archive.namelist()) != {"index.html", "README.md"} or archive.testzip() is not None:
            raise ValueError("product package structure is invalid")
        if archive.read("index.html") != resolved_files["index.html"][1]:
            raise ValueError("product package HTML mismatch")
        if archive.read("README.md") != resolved_files["README.md"][1]:
            raise ValueError("product package README mismatch")
    return product_dir, manifest, resolved_files

def _creator_initiative_map(products_root: Path) -> dict[str, str]:
    """Resolve product-directory identities to their owning Creator Initiatives."""
    database = products_root.parent / "initiatives" / "muse.sqlite3"
    if database.is_symlink() or database.parent.is_symlink() or not database.is_file():
        return {}
    uri = "file:" + quote(database.resolve().as_posix(), safe="/:") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=3)
        try:
            rows = connection.execute(
                "SELECT initiative_id,details_json FROM initiative_events "
                "WHERE event_type='product_built' ORDER BY sequence DESC LIMIT 10000"
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return {}
    mapping: dict[str, str] = {}
    for initiative_id, encoded in rows:
        if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            continue
        try:
            details = json.loads(encoded)
        except (TypeError, json.JSONDecodeError):
            continue
        relative_path = details.get("relative_path") if isinstance(details, dict) else None
        if isinstance(relative_path, str) and relative_path.startswith("data/products/"):
            mapping.setdefault(relative_path, initiative_id)
    return mapping


def _safe_storyboard_warning_mapping(mapping: object, warning: dict[str, Any]) -> dict[str, Any] | None:
    """Return a bounded, path-safe storyboard mapping for one verified QA warning."""
    if not isinstance(mapping, dict):
        return None
    if (mapping.get("code") != warning.get("code")
            or mapping.get("start_seconds") != warning.get("start_seconds")
            or mapping.get("duration_seconds") != warning.get("duration_seconds")):
        return None
    status = mapping.get("mapping_status")
    if status not in {"mapped", "outside_storyboard", "not_time_based"}:
        return None
    matches = []
    raw_matches = mapping.get("storyboard_matches")
    if isinstance(raw_matches, list):
        for match in raw_matches[:12]:
            if not isinstance(match, dict):
                continue
            scene_id = match.get("scene_id")
            source_file = match.get("source_file")
            if (not isinstance(scene_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", scene_id)
                    or not isinstance(source_file, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,179}", source_file)
                    or source_file in {".", ".."}):
                continue
            numeric_keys = (
                "scene_start_seconds", "scene_end_seconds", "overlap_start_seconds",
                "overlap_end_seconds", "overlap_seconds",
            )
            values = [match.get(key) for key in numeric_keys]
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value < 0 for value in values):
                continue
            scene_start, scene_end, overlap_start, overlap_end, overlap_seconds = map(float, values)
            warning_start = float(warning["start_seconds"])
            warning_end = warning_start + float(warning["duration_seconds"])
            if (scene_end <= scene_start or overlap_end <= overlap_start
                    or overlap_start < scene_start - 1e-5 or overlap_end > scene_end + 1e-5
                    or overlap_start < warning_start - 1e-5 or overlap_end > warning_end + 1e-5
                    or abs((overlap_end - overlap_start) - overlap_seconds) > 1e-4):
                continue
            matches.append({
                "scene_id": scene_id, "source_file": source_file,
                "scene_start_seconds": scene_start, "scene_end_seconds": scene_end,
                "overlap_start_seconds": overlap_start, "overlap_end_seconds": overlap_end,
                "overlap_seconds": overlap_seconds,
            })
    if status == "mapped" and not matches:
        return None
    return {"mapping_status": status, "storyboard_matches": matches}


def build_products(products_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    root = Path(products_dir)
    if root.is_symlink() or not root.is_dir():
        return []
    rows = []
    creator_initiatives = _creator_initiative_map(root)
    for initiative_root in sorted(root.iterdir(), key=lambda item: item.name):
        if initiative_root.is_symlink() or not initiative_root.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", initiative_root.name):
            continue
        for product_dir in sorted(initiative_root.iterdir(), key=lambda item: item.name):
            if product_dir.is_symlink() or not product_dir.is_dir() or not re.fullmatch(r"[0-9a-f]{20}", product_dir.name):
                continue
            try:
                _, manifest, files = _verified_product_files(root, initiative_root.name, product_dir.name)
            except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, json.JSONDecodeError):
                continue
            modified = max(path.stat().st_mtime for path, _ in files.values())
            product_kind = manifest.get("product_kind", "offline_product")
            row = {
                "id": initiative_root.name + "-" + product_dir.name,
                "initiative_id": initiative_root.name,
                "creator_initiative_id": creator_initiatives.get(
                    f"data/products/{initiative_root.name}/{product_dir.name}"
                ),
                "creator_feedback_enabled": (
                    f"data/products/{initiative_root.name}/{product_dir.name}" in creator_initiatives
                ),
                "version_id": product_dir.name,
                "name": manifest.get("title") if isinstance(manifest.get("title"), str) else "Muse 本地产品 " + initiative_root.name[:8],
                "kind": product_kind,
                "status": manifest.get("status", "testing"),
                "version": "v" + str(manifest.get("builder_version", 1)),
                "size_bytes": files["product.zip"][0].stat().st_size,
                "modified_at": datetime.fromtimestamp(modified, timezone.utc).isoformat(),
                "download_path": f"/api/v1/products/{initiative_root.name}/{product_dir.name}/download",
                "preview_path": f"/api/v1/products/{initiative_root.name}/{product_dir.name}/preview",
            }
            if product_kind == "video_creator":
                row["video_path"] = f"/api/v1/products/{initiative_root.name}/{product_dir.name}/video"
                row["video_bytes"] = files["video.mp4"][0].stat().st_size
                storyboard_entry = files.get("storyboard.json")
                if storyboard_entry and isinstance(storyboard_entry[1], bytes):
                    try:
                        storyboard_data = json.loads(storyboard_entry[1].decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        storyboard_data = {}
                    raw_scenes = storyboard_data.get("scenes", []) if isinstance(storyboard_data, dict) else []
                    safe_scenes = []
                    if isinstance(raw_scenes, list):
                        for scene in raw_scenes[:100]:
                            if not isinstance(scene, dict):
                                continue
                            scene_id = scene.get("id")
                            source_asset = scene.get("source_asset")
                            if not isinstance(scene_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", scene_id):
                                continue
                            if not isinstance(source_asset, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,179}", source_asset) or source_asset in {".", ".."}:
                                continue
                            summary = {"scene_id": scene_id, "source_asset": source_asset}
                            camera_move = scene.get("camera_move")
                            if camera_move in {"push_in", "drift_left", "pull_out", "rise", "drift_right"}:
                                summary["camera_move"] = camera_move
                            for key in ("timeline_start_seconds", "timeline_end_seconds", "duration_seconds", "transition_in_seconds", "transition_out_seconds"):
                                value = scene.get(key)
                                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                                    summary[key] = value
                            if ("timeline_start_seconds" in summary and "timeline_end_seconds" in summary
                                    and summary["timeline_end_seconds"] < summary["timeline_start_seconds"]):
                                summary.pop("timeline_start_seconds", None)
                                summary.pop("timeline_end_seconds", None)
                            safe_scenes.append(summary)
                    if safe_scenes:
                        row["scene_timeline"] = safe_scenes
                metadata = manifest.get("video_metadata", {})
                if isinstance(metadata, dict):
                    motion = metadata.get("motion")
                    if isinstance(motion, dict):
                        motion_summary = {
                            key: value[:80] for key in ("profile", "renderer", "remotion_version", "composition_id", "transition", "audio_mode")
                            if isinstance((value := motion.get(key)), str) and value and len(value) <= 80
                        }
                        for key in ("transition_seconds", "scene_transition_seconds", "scene_count", "intro_seconds", "outro_seconds"):
                            value = motion.get(key)
                            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                                motion_summary[key] = value
                        if motion_summary:
                            row["motion"] = motion_summary
                    metric_keys = (
                        "duration_seconds", "fps", "frames", "width", "height",
                        "encoder", "model", "seed", "steps", "aspect_ratio",
                    )
                    row["video_metrics"] = {
                        key: value for key in metric_keys
                        if isinstance((value := metadata.get(key)), (str, int, float))
                        and not isinstance(value, bool)
                        and (not isinstance(value, float) or math.isfinite(value))
                    }
                    motion_duration = motion.get("duration_seconds") if isinstance(motion, dict) else None
                    if (isinstance(motion_duration, (int, float)) and not isinstance(motion_duration, bool)
                            and math.isfinite(motion_duration)):
                        row["video_metrics"]["duration_seconds"] = motion_duration
                        fps_value = row["video_metrics"].get("fps")
                        if isinstance(fps_value, (int, float)) and not isinstance(fps_value, bool) and math.isfinite(fps_value):
                            row["video_metrics"]["frames"] = max(0, int(round(float(fps_value) * float(motion_duration))))
                manifest_files = manifest.get("files", {})
                quality_gate = manifest.get("quality_gate")
                if isinstance(quality_gate, dict):
                    row["quality_gate"] = {
                        "status": quality_gate.get("status") if quality_gate.get("status") in {"passed", "failed", "blocked"} else "unknown",
                        "review_warning_count": quality_gate.get("review_warning_count", 0) if isinstance(quality_gate.get("review_warning_count"), int) else 0,
                        "review_warning_codes": [code for code in quality_gate.get("review_warning_codes", []) if isinstance(code, str)][:20] if isinstance(quality_gate.get("review_warning_codes"), list) else [],
                        "warning_review_required": quality_gate.get("warning_review_required") is True,
                        "ready_to_publish": quality_gate.get("ready_to_publish") is True,
                    }
                subtitle_review = manifest.get("subtitle_review")
                if isinstance(manifest.get("subtitles"), dict):
                    if isinstance(subtitle_review, dict) and subtitle_review.get("human_approved") is True:
                        review_status = "approved"
                        review_available = True
                    else:
                        try:
                            review_document = _find_product_subtitle_review(root.parent.parent, manifest)
                        except FileNotFoundError:
                            review_status = "unavailable"
                            review_available = False
                        except ValueError:
                            review_status = "ambiguous"
                            review_available = False
                        else:
                            review_available = True
                            review_status = "approved" if review_document.get("human_approved") is True else "pending_human_review"
                    row["subtitle_review"] = {
                        "status": review_status,
                        "human_approved": review_status == "approved",
                        "review_available": review_available,
                        **({
                            "approved_by": subtitle_review["approved_by"],
                            "approval_timestamp": subtitle_review["approval_timestamp"],
                            "language": subtitle_review.get("language", "yue"),
                            "muxed_subtitle_codec": subtitle_review.get("muxed_subtitle_codec", "mov_text"),
                        } if review_status == "approved" and isinstance(subtitle_review, dict)
                           and isinstance(subtitle_review.get("approved_by"), str)
                           and isinstance(subtitle_review.get("approval_timestamp"), str) else {}),
                    }
                qa_entry = files.get("qa-report.json")
                if qa_entry and qa_entry[0].is_file():
                    try:
                        qa_report = json.loads(qa_entry[0].read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        qa_report = {}
                    raw_warnings = qa_report.get("warnings", []) if isinstance(qa_report, dict) else []
                    raw_mappings = qa_report.get("storyboard_mapping", []) if isinstance(qa_report, dict) else []
                    safe_warnings = []
                    if isinstance(raw_warnings, list):
                        for warning_index, warning in enumerate(raw_warnings[:50]):
                            if not isinstance(warning, dict) or not isinstance(warning.get("code"), str):
                                continue
                            summary = {
                                "code": warning["code"],
                                "message": warning.get("message", "")[:500] if isinstance(warning.get("message"), str) else "",
                            }
                            start, duration = warning.get("start_seconds"), warning.get("duration_seconds")
                            valid_timing = (
                                isinstance(start, (int, float)) and not isinstance(start, bool)
                                and math.isfinite(start) and start >= 0
                                and isinstance(duration, (int, float)) and not isinstance(duration, bool)
                                and math.isfinite(duration) and duration > 0
                            )
                            if valid_timing:
                                summary["start_seconds"] = start
                                summary["duration_seconds"] = duration
                            threshold = warning.get("review_threshold_seconds")
                            if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) and math.isfinite(threshold):
                                summary["review_threshold_seconds"] = threshold
                            value, unit = warning.get("value"), warning.get("unit")
                            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                                summary["value"] = value
                            if isinstance(unit, str) and unit in {"LUFS", "dBFS", "dB", "seconds"}:
                                summary["unit"] = unit
                            if isinstance(raw_mappings, list) and warning_index < len(raw_mappings):
                                mapping_summary = _safe_storyboard_warning_mapping(raw_mappings[warning_index], warning)
                                if mapping_summary is not None:
                                    summary.update(mapping_summary)
                            safe_warnings.append(summary)
                    row["quality_warnings"] = safe_warnings

                if isinstance(manifest_files, dict):
                    row["video_mp4_sha256"] = manifest_files.get("video.mp4")
                    row["video_package_sha256"] = manifest_files.get("product.zip")
            rows.append(row)
    return sorted(rows, key=lambda item: item["modified_at"], reverse=True)[:limit]



def _find_product_subtitle_review(workspace: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Find the exact run-side subtitle review bound to a verified product's audio/script/SRT."""
    run_root = Path(workspace) / "data" / "prometheus" / "runs"
    if run_root.is_symlink() or not run_root.is_dir():
        raise FileNotFoundError("subtitle review run store is not configured")
    initiative_id = manifest.get("initiative_id")
    version_id = manifest.get("version_id")
    if (not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id)
            or not isinstance(version_id, str) or not re.fullmatch(r"[0-9a-f]{20}", version_id)):
        raise FileNotFoundError("product identity is missing for subtitle review provenance")
    expected_product_path = f"data/products/{initiative_id}/{version_id}"
    narration = manifest.get("narration")
    asr_fact = manifest.get("narration_asr")
    subtitle_fact = manifest.get("subtitles")
    if not isinstance(narration, dict) or not isinstance(asr_fact, dict) or not isinstance(subtitle_fact, dict):
        raise FileNotFoundError("product has no narration subtitle review source")
    expected = {
        "audio_sha256": asr_fact.get("audio_sha256"),
        "model_repo": asr_fact.get("model_repo"),
        "model_revision": asr_fact.get("model_revision"),
        "reference_script": narration.get("script"),
        "raw_asr_transcript": asr_fact.get("transcript"),
        "subtitle_sha256": subtitle_fact.get("sha256"),
    }
    if any(not isinstance(value, str) or not value for value in expected.values()):
        raise FileNotFoundError("product subtitle review binding metadata is incomplete")
    root = run_root.resolve()
    matches: list[dict[str, Any]] = []
    inspected = 0
    for path in root.glob("*/*/subtitle-review.json"):
        inspected += 1
        if inspected > 2000:
            raise ValueError("subtitle review lookup exceeded its bounded run limit")
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if resolved.parent.parent.parent != root or resolved.stat().st_size > 512 * 1024:
            continue
        generation_path = resolved.parent / "generation.json"
        if generation_path.is_symlink() or not generation_path.is_file() or generation_path.stat().st_size > 512 * 1024:
            continue
        try:
            generation = json.loads(generation_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        product_source = generation.get("product") if isinstance(generation, dict) else None
        if not isinstance(product_source, dict):
            continue
        relative_product_path = product_source.get("relative_path")
        if (product_source.get("initiative_id") != initiative_id
                or product_source.get("version_id") != version_id
                or not isinstance(relative_product_path, str)
                or relative_product_path.replace("\\", "/") != expected_product_path):
            continue
        try:
            review = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        asr = review.get("asr") if isinstance(review, dict) else None
        if not isinstance(asr, dict):
            continue
        subtitle_file = review.get("subtitle_file")
        if (not isinstance(subtitle_file, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}\.srt", subtitle_file)):
            continue
        if any((
            asr.get("audio_sha256") != expected["audio_sha256"],
            asr.get("model_repo") != expected["model_repo"],
            asr.get("model_revision") != expected["model_revision"],
            review.get("reference_script") != expected["reference_script"],
            review.get("raw_asr_transcript") != expected["raw_asr_transcript"],
        )):
            continue
        srt_path = resolved.parent / subtitle_file
        if srt_path.is_symlink() or not srt_path.is_file() or srt_path.stat().st_size > 4 * 1024 * 1024:
            continue
        if hashlib.sha256(srt_path.read_bytes()).hexdigest() != expected["subtitle_sha256"]:
            continue
        segments = review.get("segments")
        if (review.get("schema_version") != 1 or not isinstance(segments, list)
                or not 1 <= len(segments) <= 500 or review.get("subtitle_cue_count") != len(segments)):
            continue
        matches.append(review)
        if len(matches) > 1:
            raise ValueError("multiple subtitle review documents match this product; manual reconciliation is required")
    if not matches:
        raise FileNotFoundError("matching subtitle review document not found")
    return matches[0]

def build_initiative_timeline(
    initiative_db: str | Path, initiative_id: str, *, limit: int = 100,
) -> dict[str, Any] | None:
    """Read a bounded, allowlisted initiative timeline without creating or mutating its database."""
    if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
        raise ValueError("invalid initiative id")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    path = Path(initiative_db)
    if not path.is_file():
        raise FileNotFoundError("initiative store is not configured")
    uri = "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        try:
            initiative = connection.execute(
                """SELECT id,title,kind,status,created_at,updated_at
                FROM creator_initiatives WHERE id=?""",
                (initiative_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if initiative is None:
            return None
        history_complete = False
        try:
            rows = connection.execute(
                """SELECT id,event_type,from_status,to_status,actor,source,
                    details_json,created_at,sequence
                FROM (
                    SELECT * FROM initiative_events WHERE initiative_id=?
                    ORDER BY sequence DESC LIMIT ?
                ) ORDER BY sequence ASC""",
                (initiative_id, limit),
            ).fetchall()
            history_complete = connection.execute(
                """SELECT 1 FROM initiative_events
                WHERE initiative_id=? AND event_type='created' LIMIT 1""",
                (initiative_id,),
            ).fetchone() is not None
        except sqlite3.OperationalError:
            rows = []
            history_complete = False
        events = []
        event_order: dict[str, int] = {}
        for row in rows:
            try:
                details = json.loads(row["details_json"])
            except (TypeError, json.JSONDecodeError):
                details = {}
            if not isinstance(details, dict):
                details = {}
            event_type = row["event_type"]
            if event_type == "feedback_recorded":
                details = {
                    "feedback_id": details.get("feedback_id")
                    if isinstance(details.get("feedback_id"), str)
                    and re.fullmatch(r"[0-9a-f]{32}", details["feedback_id"]) else "",
                    "decision": details.get("decision")
                    if details.get("decision") in {"accept", "revise", "reject"} else "",
                    "evolution_cycle_id": details.get("evolution_cycle_id")
                    if isinstance(details.get("evolution_cycle_id"), str)
                    and re.fullmatch(r"cycle_[0-9a-f]{32}", details["evolution_cycle_id"]) else None,
                }
            elif event_type == "product_built":
                relative_path = details.get("relative_path")
                if (
                    not isinstance(relative_path, str)
                    or not relative_path.startswith("data/products/")
                    or chr(92) in relative_path
                    or ".." in relative_path.split("/")
                ):
                    relative_path = ""
                artifact = details.get("artifact_metadata")
                if not isinstance(artifact, dict):
                    artifact = {}
                artifact_allowlist = {
                    key: artifact[key] for key in (
                        "mp4_sha256", "manifest_sha256", "storyboard_sha256", "readme_sha256",
                        "source_asset_hashes", "generation_metadata", "validation",
                        "evolution_cycle_id", "product_id", "version_id",
                    ) if key in artifact
                }
                details = {
                    "relative_path": relative_path,
                    "package_sha256": details.get("package_sha256")
                    if isinstance(details.get("package_sha256"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", details["package_sha256"]) else "",
                    "validated": details.get("validated") is True,
                    "artifact_metadata": artifact_allowlist,
                }
            elif event_type == "status_changed":
                details = {"outcome_recorded": details.get("outcome_recorded") is True}
            else:
                details = {}
            events.append({
                "id": row["id"],
                "event_type": event_type,
                "from_status": row["from_status"],
                "to_status": row["to_status"],
                "actor": row["actor"],
                "source": row["source"],
                "created_at": row["created_at"],
                "details": details,
            })
            event_order[row["id"]] = row["sequence"]
        try:
            links = connection.execute(
                "SELECT id,knowledge_node_id,node_type,relationship,evolution_cycle_id,created_at "
                "FROM initiative_knowledge_links WHERE initiative_id=? ORDER BY created_at,id LIMIT ?",
                (initiative_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            links = []
        for link in links:
            events.append({
                "id": link["id"], "event_type": "knowledge_linked",
                "from_status": None, "to_status": None, "actor": "system",
                "source": "research_knowledge", "created_at": link["created_at"],
                "details": {
                    "knowledge_node_id": link["knowledge_node_id"],
                    "node_type": link["node_type"], "relationship": link["relationship"],
                    "evolution_cycle_id": link["evolution_cycle_id"],
                },
            })
            event_order[link["id"]] = 2**63 - 1
        try:
            failures = connection.execute(
                "SELECT id,failure_code,error_type,evolution_cycle_id,created_at "
                "FROM initiative_failures WHERE initiative_id=? ORDER BY created_at,id LIMIT ?",
                (initiative_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            failures = []
        for failure in failures:
            events.append({
                "id": failure["id"], "event_type": "product_failed",
                "from_status": None, "to_status": None, "actor": "system",
                "source": "video_product_job", "created_at": failure["created_at"],
                "details": {
                    "failure_code": failure["failure_code"],
                    "error_type": failure["error_type"],
                    "evolution_cycle_id": failure["evolution_cycle_id"] or None,
                },
            })
            event_order[failure["id"]] = 2**63 - 1
        events.sort(key=lambda item: (
            item["created_at"], event_order.get(item["id"], 2**63 - 1), item["id"]
        ))
        return {
            "initiative": dict(initiative),
            "events": events,
            "history_complete": history_complete,
        }
    finally:
        connection.close()


def build_gpu_resource_status(database: str | Path) -> dict[str, Any]:
    """Return a redacted GPU lease/supervisor snapshot for the local dashboard."""
    try:
        from muse.gpu_resources import GpuResourceCoordinator
        return GpuResourceCoordinator(database).status_snapshot()
    except Exception:
        return {
            "status": "unknown", "lease": None,
            "model_supervisor": {
                "status": "unknown", "action": "state_unavailable",
                "heartbeat_age_seconds": None, "restore_pending": False,
                "managed_child": False,
            },
            "comfyui_memory_release": {"status": "not_observed", "updated_age_seconds": None},
            "policy": "exclusive_gpu_lease; unknown_vram_is_not_guessed; external_processes_are_never_stopped",
        }


class BoundedStatusServer(ThreadingHTTPServer):
    """Bound request concurrency and socket lifetime for the local control plane."""
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        self._request_slots = BoundedSemaphore(8)
        super().__init__(address, handler)
        self.daemon_threads = True

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def get_request(self) -> tuple[Any, Any]:
        request, client_address = super().get_request()
        request.settimeout(5.0)
        return request, client_address


def create_status_server(
    *, task_db: str | Path, progress_db: str | Path, capability_db: str | Path,
    agent_db: str | Path | None = None, improvement_db: str | Path | None = None,
    port: int = 0, token: str | None = None,
    knowledge_db: str | Path | None = None, report_dir: str | Path | None = None,
    products_dir: str | Path | None = None, gpu_resource_db: str | Path | None = None,
    memory_db: str | Path | None = None, plugin_db: str | Path | None = None,
) -> tuple[BoundedStatusServer, str]:
    """Create a loopback API with bounded reads and narrow, authenticated feedback writes."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    bearer = token or secrets.token_urlsafe(32)
    if not isinstance(bearer, str) or len(bearer) < 32:
        raise ValueError("token must contain at least 32 characters")
    task_path, progress_path, capability_path = Path(task_db), Path(progress_db), Path(capability_db)
    resolved_task_path = task_path.resolve()
    default_gpu_db = (resolved_task_path.parent.parent / "gpu-resources.sqlite3"
                      if resolved_task_path.parent.name == "tasks"
                      else resolved_task_path.parent / "gpu-resources.sqlite3")
    gpu_resource_path = Path(gpu_resource_db) if gpu_resource_db is not None else default_gpu_db
    agent_path = Path(agent_db) if agent_db is not None else None
    improvement_path = Path(improvement_db) if improvement_db is not None else None
    knowledge_path = Path(knowledge_db) if knowledge_db is not None else None
    reports_path = Path(report_dir) if report_dir is not None else None
    products_path = Path(products_dir) if products_dir is not None else None
    initiatives_path = (
        products_path.parent / "initiatives" / "muse.sqlite3"
        if products_path is not None else None
    )
    # Control-plane helpers for the long-running local runtime and the
    # reproducible short-video pipeline. Workspace is derived from the task DB
    # (data/tasks/muse.sqlite3 -> project root).
    if products_path is not None and products_path.parent.name == "data":
        workspace_root = products_path.parent.parent.resolve()
    elif resolved_task_path.parent.name == "tasks" and resolved_task_path.parent.parent.name == "data":
        workspace_root = resolved_task_path.parent.parent.parent.resolve()
    elif resolved_task_path.parent.name == "tasks":
        workspace_root = resolved_task_path.parent.parent.resolve()
    else:
        workspace_root = resolved_task_path.parent.resolve()
    products_workspace = products_path.parent.parent if products_path is not None else workspace_root
    memory_path = Path(memory_db) if memory_db is not None else workspace_root / "data" / "memory" / "muse.sqlite3"
    companion_db_path = workspace_root / "data" / "companion" / "devices.sqlite3"
    plugin_path = Path(plugin_db) if plugin_db is not None else workspace_root / "data" / "plugins" / "registry.sqlite3"
    studio_db = workspace_root / "data" / "studio" / "render-jobs.sqlite3"
    studio_db.parent.mkdir(parents=True, exist_ok=True)
    from muse.daemon_control import MuseDaemonController, DaemonError
    from muse.pipeline_control import PipelineController, PipelineError
    daemon = MuseDaemonController(
        workspace=workspace_root,
        state_db=workspace_root / "data" / "autonomy" / "runtime.sqlite3",
        control_log=workspace_root / "data" / "autonomy" / "control-log.jsonl",
    )
    pipeline = PipelineController(workspace_root, task_db=resolved_task_path)
    video_jobs: dict[str, dict[str, Any]] = {}
    video_jobs_lock = Lock()
    video_job_slot = BoundedSemaphore(1)

    def _run_video_job(job_id: str, payload: dict[str, Any]) -> None:
        from muse.initiative_engine import InitiativeStore
        with video_jobs_lock:
            video_jobs[job_id]["status"] = "running"
        try:
            if initiatives_path is not None:
                with InitiativeStore(initiatives_path) as attempts:
                    attempts.mark_video_attempt_running(job_id)
            workspace = products_path.parent.parent if products_path is not None else Path.cwd()
            builder = VideoProductBuilder(workspace)
            if payload.get("mode", "local_assets") == "prompt_to_video":
                raise RuntimeError(
                    "AI video generation must use Commander VideoTaskWorker after persisted approval and admission"
                )
            result = builder.build_from_source(
                payload["source"], payload["title"], audio_name=payload.get("audio"),
                audio_mode=payload.get("audio_mode", "music"),
                aspect_ratio=payload.get("aspect_ratio", "16:9"), fps=payload.get("fps", 30),
                image_seconds=payload.get("image_seconds", 4.0), encoder=payload.get("encoder", "auto"),
                creator_initiative_id=payload.get("creator_initiative_id"),
                evolution_cycle_id=payload.get("evolution_cycle_id"),
                motion_profile=payload.get("motion_profile", "none"),
            )
            if result.get("recipe_sha256") != payload.get("recipe_sha256"):
                raise RuntimeError("video product recipe fingerprint does not match the submitted attempt")
            visual_review_evidence = {"status": "unavailable", "reason": "product_store_not_configured", "can_publish": False}
            if products_path is not None:
                try:
                    from muse.video_visual_evidence import create_visual_review_evidence_pack
                    inventory = discover_media_tools()
                    media_tools = {item.tool_id: item.executable for item in inventory.tools}
                    visual_review_evidence = create_visual_review_evidence_pack(
                        products_path, result["initiative_id"], result["version_id"],
                        ffmpeg_path=media_tools.get("ffmpeg"), ffprobe_path=media_tools.get("ffprobe"),
                    )
                except Exception as evidence_error:
                    visual_review_evidence = {
                        "status": "unavailable", "reason": str(evidence_error)[:240],
                        "can_publish": False, "human_review_required": True,
                    }
            if initiatives_path is not None:
                with InitiativeStore(initiatives_path) as attempts:
                    attempts.complete_video_attempt(
                        job_id, initiative_id=result["creator_initiative_id"],
                        product_id=result["initiative_id"], version_id=result["version_id"],
                        mp4_sha256=result["mp4_sha256"], package_sha256=result["package_sha256"],
                        manifest_sha256=result.get("manifest_sha256"),
                    )
            outcome = {"status": "succeeded", "visual_review_evidence": visual_review_evidence, "product": {
                "initiative_id": result["initiative_id"], "version_id": result["version_id"],
                "title": result["title"], "kind": "video_creator",
                "creator_initiative_id": result["creator_initiative_id"],
                "ledger_event_id": result["ledger_event_id"],
                "mp4_sha256": result["mp4_sha256"], "package_sha256": result["package_sha256"],
            }}
        except Exception as exc:
            initiative_id = payload.get("creator_initiative_id")
            if initiatives_path is not None:
                try:
                    with InitiativeStore(initiatives_path) as attempts:
                        attempts.fail_video_attempt(
                            job_id, failure_code="video.product_job_failed",
                            error_type=type(exc).__name__,
                        )
                        if isinstance(initiative_id, str):
                            attempts.record_product_failure(
                                initiative_id, failure_code="video.product_job_failed",
                                error_type=type(exc).__name__,
                                evolution_cycle_id=payload.get("evolution_cycle_id"),
                            )
                except Exception:
                    pass
            outcome = {"status": "failed", "error": str(exc)[:500]}
        finally:
            video_job_slot.release()
        with video_jobs_lock:
            video_jobs[job_id].update(outcome, updated_at=datetime.now(timezone.utc).isoformat())

    class StatusHandler(BaseHTTPRequestHandler):
        server_version = "MuseStatus/1"
        sys_version = ""
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            origin = self.headers.get("Origin")
            if origin and _allowed_origin(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self) -> None:
            if not self.client_address or self.client_address[0] not in {"127.0.0.1", "::1"}:
                self._send(403, {"error": "loopback clients only"})
                return
            if not _allowed_origin(self.headers.get("Origin")):
                self._send(403, {"error": "origin not allowed"})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + bearer):
                self._send(401, {"error": "unauthorized"})
                return
            parsed = urlsplit(self.path)
            try:
                if parsed.path == "/api/v1/health":
                    self._send(200, {
                        "service": "muse-status-api", "status": "ok", "api_version": 1,
                        "agent_registry_configured": agent_path is not None,
                    })
                elif parsed.path == "/api/v1/companion/pairings":
                    from muse.companion_security import CompanionDeviceStore
                    limit = int(parse_qs(parsed.query).get("limit", ["20"])[0])
                    with CompanionDeviceStore(companion_db_path) as companion:
                        self._send(200, {"api_version": 1, "pairings": companion.list_pending_pairings(limit=limit)})
                elif parsed.path == "/api/v1/companion/devices":
                    from muse.companion_security import CompanionDeviceStore
                    limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                    with CompanionDeviceStore(companion_db_path) as companion:
                        self._send(200, {"api_version": 1, "devices": companion.list_devices(limit=limit)})
                elif parsed.path == "/api/v1/companion/audit":
                    from muse.companion_security import CompanionDeviceStore
                    limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                    with CompanionDeviceStore(companion_db_path) as companion:
                        self._send(200, {"api_version": 1, "events": companion.list_audit_events(limit=limit)})
                elif parsed.path == "/api/v1/plugins":
                    from muse.plugin_platform import PluginRegistry
                    limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                    with PluginRegistry(plugin_path) as plugins:
                        self._send(200, {"api_version": 1, "plugins": plugins.list_plugins(limit=limit)})
                elif parsed.path == "/api/v1/plugins/audit":
                    from muse.plugin_platform import PluginRegistry
                    limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                    with PluginRegistry(plugin_path) as plugins:
                        self._send(200, {"api_version": 1, "events": plugins.list_audit_events(limit=limit)})
                elif parsed.path == "/api/v1/system-dashboard":
                    self._send(200, build_system_dashboard(workspace_root, capability_path))
                elif parsed.path == "/api/v1/mempalace/status":
                    self._send(200, build_mempalace_status(workspace_root, memory_path))
                elif parsed.path == "/api/v1/studio/styles":
                    from muse.creative_studio_api import list_styles
                    self._send(200, list_styles())
                elif parsed.path == "/api/v1/studio/render":
                    from muse.creative_studio_api import CreativeStudioStore
                    with CreativeStudioStore(studio_db) as studio:
                        limit = int(parse_qs(parsed.query).get("limit", ["30"])[0])
                        self._send(200, {"api_version": 1, "jobs": studio.list_jobs(limit=limit)})
                elif re.fullmatch(r"/api/v1/studio/render/[0-9a-f]{32}", parsed.path):
                    from muse.creative_studio_api import CreativeStudioStore
                    job_id = parsed.path.rsplit("/", 1)[-1]
                    with CreativeStudioStore(studio_db) as studio:
                        job = studio.get_job(job_id)
                    self._send(200 if job else 404, {"api_version": 1, "job": job} if job else {"error": "render job not found"})
                elif parsed.path == "/api/v1/gpu-resources":
                    self._send(200, {"api_version": 1, **build_gpu_resource_status(gpu_resource_path)})
                elif parsed.path == "/api/v1/overview":
                    runtime = daemon.status()
                    safe_runtime = {
                        key: runtime.get(key)
                        for key in ("state", "running", "heartbeat_age_seconds", "stale")
                    }
                    self._send(200, {
                        "api_version": 1, **build_overview(task_path, progress_path),
                        "autonomous_runtime": safe_runtime,
                    })
                elif parsed.path == "/api/v1/contextual-inquiry-reviews":
                    limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                    if not 1 <= limit <= 100:
                        raise ValueError("limit must be between 1 and 100")
                    from muse.task_queue import TaskQueue
                    with TaskQueue(task_path) as queue:
                        tasks = queue.list_tasks(kind="contextual_inquiry_review", limit=limit)
                    reviews = []
                    for task in tasks:
                        payload = task.get("payload")
                        if (
                            task.get("status") == "waiting_approval"
                            and isinstance(payload, dict)
                            and payload.get("case_context_envelope_stored") is True
                            and payload.get("case_context_attached") is True
                            and payload.get("requires_context_reentry") is False
                            and payload.get("context_status") == "validated_attached"
                            and payload.get("long_term_storage_performed") is False
                            and isinstance(payload.get("review_result_envelope"), dict)
                            and "_approved" not in payload and "_result" not in payload
                            and task.get("lease_owner") is None
                            and task.get("lease_expires_at") is None
                            and isinstance(task.get("id"), str)
                            and re.fullmatch(r"[0-9a-f]{32}", task["id"])
                        ):
                            reviews.append({
                                "task_id": task["id"],
                                "created_at": task.get("created_at"),
                                "updated_at": task.get("updated_at"),
                            })
                    self._send(200, {"api_version": 1, "count": len(reviews), "reviews": reviews})
                elif re.fullmatch(r"/api/v1/contextual-inquiry-reviews/[0-9a-f]{32}", parsed.path):
                    from muse.task_queue import TaskQueue
                    task_id = parsed.path.rsplit("/", 1)[-1]
                    try:
                        with TaskQueue(task_path) as queue:
                            review = queue.load_contextual_inquiry_review_result(task_id)
                    except KeyError:
                        self._send(404, {"error": "contextual inquiry review not found"})
                    except PermissionError:
                        self._send(409, {"error": "contextual inquiry review is not ready for human review"})
                    except Exception:
                        self._send(503, {"error": "contextual inquiry review result is unavailable"})
                    else:
                        self._send(200, {"api_version": 1, "task_id": task_id, "review": review})
                elif parsed.path == "/api/v1/events/stream":
                    # Finite SSE batches keep each connection below the 5s socket budget.
                    cursor = self.headers.get("Last-Event-ID", "")
                    if cursor and not re.fullmatch(r"[a-f0-9-]{16,64}", cursor):
                        raise ValueError("invalid event cursor")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    origin = self.headers.get("Origin")
                    if origin and _allowed_origin(origin):
                        self.send_header("Access-Control-Allow-Origin", origin)
                        self.send_header("Vary", "Origin")
                    self.end_headers()
                    self.close_connection = True
                    deadline = time.monotonic() + 3.5
                    last_id = cursor
                    while time.monotonic() < deadline:
                        with ProgressEventStore(progress_path) as store:
                            batch = list(reversed(store.list_events(limit=100)))
                        if last_id and not any(event["id"] == last_id for event in batch):
                            self.wfile.write(b"event: muse-reset\ndata: {}\n\n")
                            self.wfile.flush()
                            return
                        fresh = []
                        passed_cursor = not last_id
                        for event in batch:
                            if event["id"] == last_id:
                                passed_cursor = True
                                continue
                            if passed_cursor:
                                fresh.append(event)
                        for event in fresh:
                            # Public stream excludes subject identifiers and free-text summaries.
                            payload = {key: event[key] for key in ("category", "event", "created_at")}
                            frame = (f"id: {event['id']}\nevent: muse-progress\ndata: "
                                     + json.dumps(payload, separators=(",", ":")) + "\n\n")
                            self.wfile.write(frame.encode("utf-8"))
                            self.wfile.flush()
                            last_id = event["id"]
                        if not fresh:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                        time.sleep(0.25)
                    return
                elif parsed.path == "/api/v1/events":
                    limit = int(parse_qs(parsed.query).get("limit", ["30"])[0])
                    if not 1 <= limit <= 100:
                        raise ValueError("limit must be between 1 and 100")
                    with ProgressEventStore(progress_path) as store:
                        events = store.list_events(limit=limit)
                    self._send(200, {"api_version": 1, "events": events})
                elif re.fullmatch(r"/api/v1/initiatives/[0-9a-f]{32}/performance", parsed.path):
                    if initiatives_path is None or not initiatives_path.is_file():
                        self._send(503, {"error": "initiative store is not configured"})
                        return
                    initiative_id = parsed.path.split("/")[4]
                    from muse.initiative_engine import InitiativeStore
                    with InitiativeStore(initiatives_path) as initiatives:
                        try:
                            snapshots = initiatives.list_performance(initiative_id=initiative_id, limit=100)
                            summary = initiatives.aggregate_performance(initiative_id=initiative_id)
                            analysis = initiatives.analyze_performance(initiative_id=initiative_id)
                        except KeyError:
                            self._send(404, {"error": "initiative not found"})
                            return
                    self._send(200, {"api_version": 1, "snapshots": snapshots, "summary_by_currency": summary, "analysis": analysis})
                elif parsed.path == "/api/v1/video-attempts":
                    if initiatives_path is None or not initiatives_path.is_file():
                        self._send(200, {"api_version": 1, "metrics": {
                            "total": 0, "succeeded": 0, "failed": 0, "queued": 0,
                            "running": 0, "leased": 0, "pending": 0,
                            "terminal": 0, "success_rate": None,
                            "success_rate_numerator": 0, "success_rate_denominator": 0,
                            "scope": "persisted_video_attempts",
                        }, "attempts": [], "attempts_truncated": False})
                        return
                    from muse.initiative_engine import InitiativeStore
                    with InitiativeStore(initiatives_path) as attempts_store:
                        metrics = attempts_store.aggregate_video_attempt_metrics()
                        # Product library returns up to 100 versions; expose the ledger maximum so older products can still resolve exact attempts.
                        attempts = attempts_store.list_video_attempts(limit=200, prioritize_active=True)
                    metrics["success_rate_numerator"] = metrics["succeeded"]
                    metrics["success_rate_denominator"] = metrics["terminal"]
                    public_attempts = [{
                        "attempt_id": item["attempt_id"], "mode": item["mode"],
                        "status": item["status"], "created_at": item["created_at"],
                        "started_at": item["started_at"], "finished_at": item["finished_at"],
                        "initiative_id": item["initiative_id"], "evolution_cycle_id": item["evolution_cycle_id"] or None,
                        "product_id": item["product_id"], "version_id": item["version_id"],
                        "failure_code": item["failure_code"], "error_type": item["error_type"],
                        "worker_id": item.get("worker_id"), "lease_expires_at": item.get("lease_expires_at"),
                        "attempt_count": item.get("attempt_count", 0),
                        "cancel_requested": bool(item.get("cancel_requested", 0)),
                    } for item in attempts]
                    self._send(200, {
                        "api_version": 1, "metrics": metrics, "attempts": public_attempts,
                        "attempts_truncated": metrics["total"] > len(attempts),
                    })
                elif parsed.path == "/api/v1/daemon/status":
                    self._send(200, {"api_version": 1, **daemon.status()})
                elif parsed.path == "/api/v1/pipelines/recipes":
                    self._send(200, {"api_version": 1, "recipes": pipeline.recipes()})
                elif parsed.path == "/api/v1/pipelines/jobs":
                    limit = int(parse_qs(parsed.query).get("limit", ["30"])[0])
                    self._send(200, {"api_version": 1, "count": len(pipeline.list_jobs(limit=limit)),
                                     "jobs": pipeline.list_jobs(limit=limit)})
                elif re.fullmatch(r"/api/v1/pipelines/jobs/[0-9a-f]{32}", parsed.path):
                    job_id = parsed.path.rsplit("/", 1)[-1]
                    job = pipeline.get_job(job_id)
                    if job is None:
                        self._send(404, {"error": "pipeline job not found"})
                    else:
                        self._send(200, {"api_version": 1, "job": job})
                elif parsed.path == "/api/v1/video-engines":
                    inventory = discover_media_tools()
                    tools = {item.tool_id: item.executable for item in inventory.tools}
                    engines = inspect_video_engines(timeout_seconds=0.5)
                    self._send(200, {
                        "api_version": 1,
                        "ffmpeg_available": bool(tools.get("ffmpeg")),
                        "ffprobe_available": bool(tools.get("ffprobe")),
                        "ffmpeg_version": inventory.ffmpeg_version,
                        "hardware_encoders": list(inventory.hardware_encoders),
                        "comfyui": engines["comfyui"],
                        "velorn": engines["velorn"],
                    })
                elif parsed.path == "/api/v1/fusion/jobs":
                    from muse.skill_fusion import SkillFusionService
                    limit = int(parse_qs(parsed.query).get("limit", ["20"])[0])
                    service = SkillFusionService(workspace_root, capability_path)
                    self._send(200, {"api_version": 1, "jobs": service.list_recent(limit=limit)})
                elif re.fullmatch(r"/api/v1/fusion/jobs/[a-f0-9]{32}", parsed.path):
                    from muse.skill_fusion import SkillFusionService
                    fusion_id = parsed.path.rsplit("/", 1)[-1]
                    result = SkillFusionService(workspace_root, capability_path).get(fusion_id)
                    if result is None:
                        self._send(404, {"error": "fusion submission not found"})
                    else:
                        self._send(200, {"api_version": 1, "fusion": result})
                elif parsed.path == "/api/v1/capabilities":
                    self._send(200, {"api_version": 1, "capabilities": build_capabilities(capability_path)})
                elif parsed.path == "/api/v1/feedback":
                    if improvement_path is None:
                        self._send(503, {"error": "feedback store is not configured"})
                        return
                    limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                    feedback = build_feedback(improvement_path, limit=limit)
                    self._send(200, {"api_version": 1, "count": len(feedback), "feedback": feedback})
                elif parsed.path == "/api/v1/self-improvement":
                    if improvement_path is None:
                        self._send(503, {"error": "improvement store is not configured"})
                        return
                    self._send(200, {"api_version": 1, **build_self_improvement_status(improvement_path, progress_path)})
                elif parsed.path == "/api/v1/improvements":
                    if improvement_path is None:
                        self._send(503, {"error": "improvement store is not configured"})
                        return
                    proposals = build_improvements(improvement_path)
                    self._send(200, {"api_version": 1, "count": len(proposals), "proposals": proposals})
                elif parsed.path.startswith("/api/v1/improvements/"):
                    if improvement_path is None:
                        self._send(503, {"error": "improvement store is not configured"})
                        return
                    proposal_id = parsed.path.rsplit("/", 1)[-1]
                    detail = build_improvement_detail(improvement_path, proposal_id)
                    if detail is None:
                        self._send(404, {"error": "proposal not found"})
                    else:
                        self._send(200, {"api_version": 1, "proposal": detail})
                elif parsed.path == "/api/v1/prometheus/core":
                    self._send(200, {
                        "api_version": 1,
                        **build_prometheus_core(
                            task_db=task_path,
                            progress_db=progress_path,
                            capability_db=capability_path,
                            improvement_db=improvement_path,
                            knowledge_db=knowledge_path,
                            report_dir=reports_path,
                            products_dir=products_path,
                        ),
                    })
                elif parsed.path == "/api/v1/galaxy":
                    self._send(200, {
                        "api_version": 1,
                        **build_agent_galaxy(
                            task_path,
                            agent_db=agent_path,
                            capability_db=capability_path,
                        ),
                    })
                elif parsed.path == "/api/v1/metaphysics/celestial":
                    self._send(200, {"api_version": 1, **build_celestial_catalog()})
                elif re.fullmatch(r"/api/v1/knowledge/claims/[0-9a-f]{32}/dossier", parsed.path):
                    if knowledge_path is None or not knowledge_path.is_file():
                        self._send(503, {"error": "knowledge store is not configured"})
                        return
                    claim_id = parsed.path.split("/")[-2]
                    evidence_limit = int(parse_qs(parsed.query).get("evidence_limit", ["12"])[0])
                    dossier = build_claim_dossier(knowledge_path, claim_id, evidence_limit=evidence_limit)
                    if dossier is None:
                        self._send(404, {"error": "claim not found"})
                    else:
                        self._send(200, {"api_version": 1, "dossier": dossier})
                elif parsed.path == "/api/v1/knowledge/graph":
                    qs = parse_qs(parsed.query)
                    limit = int(qs.get("limit", ["160"])[0])
                    raw_type = qs.get("node_type", [None])[0]
                    node_type = raw_type if raw_type else None
                    raw_query = qs.get("q", [None])[0]
                    query_text = raw_query if raw_query else None
                    self._send(200, {
                        "api_version": 1,
                        **build_knowledge_graph(
                            knowledge_path,
                            limit=limit,
                            node_type=node_type,
                            query=query_text,
                        ),
                    })
                elif re.fullmatch(r"/api/v1/knowledge/nodes/[0-9a-f]{32}", parsed.path):
                    if knowledge_path is None or not knowledge_path.is_file():
                        self._send(503, {"error": "knowledge store is not configured"})
                        return
                    node_id = parsed.path.rsplit("/", 1)[-1]
                    detail = build_knowledge_node_enriched(knowledge_path, node_id)
                    if detail is None:
                        self._send(404, {"error": "knowledge node not found"})
                    else:
                        self._send(200, {"api_version": 1, **detail})
                elif parsed.path == "/api/v1/agents":
                    if agent_path is None:
                        agents = []
                        org_chart = {"nodes": [], "edges": [], "total": 0, "truncated": False, "unresolved_parent_count": 0}
                    else:
                        limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                        if not 1 <= limit <= 200:
                            raise ValueError("limit must be between 1 and 200")
                        agents = build_agents(agent_path, limit=limit)
                        org_chart = build_agent_org_chart(agent_path, limit=500)
                    self._send(200, {
                        "api_version": 1, "registry_configured": agent_path is not None,
                        "count": len(agents), "agents": agents, "org_chart": org_chart,
                    })
                elif parsed.path == "/api/v1/inspirations":
                    if knowledge_path is None:
                        self._send(503, {"error": "inspiration store is not configured"})
                        return
                    limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                    inspirations = build_inspirations(knowledge_path, limit=limit)
                    self._send(200, {"api_version": 1, "count": len(inspirations), "inspirations": inspirations})
                elif re.fullmatch(r"/api/v1/video-jobs/[0-9a-f]{32}", parsed.path):
                    job_id = parsed.path.rsplit("/", 1)[-1]
                    with video_jobs_lock:
                        job = video_jobs.get(job_id)
                    if job is not None:
                        self._send(200, {"api_version": 1, "job": {**dict(job), "worker_available": True}})
                        return
                    if initiatives_path is None or not initiatives_path.is_file():
                        self._send(404, {"error": "video job not found"})
                        return
                    from muse.initiative_engine import InitiativeStore
                    with InitiativeStore(initiatives_path) as attempts_store:
                        attempt = attempts_store.get_video_attempt(job_id)
                    if attempt is None:
                        self._send(404, {"error": "video job not found"})
                        return
                    recovered = {
                        "id": job_id,
                        "status": attempt["status"],
                        "worker_available": False,
                    }
                    if attempt["status"] == "failed":
                        recovered["error"] = (
                            "视频任务已失败 · " + str(attempt.get("failure_code") or "video.product_job_failed")
                            + " · " + str(attempt.get("error_type") or "UnknownError")
                        )
                    elif attempt["status"] == "succeeded":
                        recovered["product"] = {
                            "initiative_id": attempt.get("product_id"),
                            "version_id": attempt.get("version_id"),
                            "kind": "video_creator",
                        }
                    self._send(200, {"api_version": 1, "job": recovered})
                elif re.fullmatch(r"/api/v1/video-recipe-traces/[A-Za-z0-9_-]{1,64}", parsed.path):
                    if initiatives_path is None or not initiatives_path.is_file():
                        self._send(503, {"error": "initiative store is not configured"})
                        return
                    attempt_id = parsed.path.rsplit("/", 1)[-1]
                    from muse.video_recipe_trace import build_video_recipe_trace
                    trace = build_video_recipe_trace(
                        workspace_root, attempt_id,
                        initiative_db=initiatives_path, task_db=task_path,
                    )
                    if trace is None:
                        self._send(404, {"error": "video recipe trace not found"})
                    else:
                        self._send(200, {"api_version": 1, "trace": trace})
                elif re.fullmatch(r"/api/v1/initiatives/[0-9a-f]{32}/timeline", parsed.path):
                    if initiatives_path is None or not initiatives_path.is_file():
                        self._send(503, {"error": "initiative store is not configured"})
                        return
                    initiative_id = parsed.path.split("/")[-2]
                    limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                    timeline = build_initiative_timeline(
                        initiatives_path, initiative_id, limit=limit,
                    )
                    if timeline is None:
                        self._send(404, {"error": "initiative not found"})
                    else:
                        self._send(200, {"api_version": 1, **timeline})
                elif parsed.path == "/api/v1/products":
                    if products_path is None:
                        self._send(503, {"error": "product store is not configured"})
                        return
                    limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                    products = build_products(products_path, limit=limit)
                    self._send(200, {"api_version": 1, "count": len(products), "products": products})
                elif parsed.path.startswith("/api/v1/products/"):
                    if products_path is None:
                        self._send(503, {"error": "product store is not configured"})
                        return
                    match = re.fullmatch(r"/api/v1/products/([0-9a-f]{32})/([0-9a-f]{20})/(download|preview|video|subtitle-review|contact-sheet|visual-review|revision-brief)", parsed.path)
                    if not match:
                        self._send(400, {"error": "invalid product path"})
                        return
                    initiative_id, version, action = match.groups()
                    try:
                        product_dir, manifest, files = _verified_product_files(products_path, initiative_id, version)
                    except FileNotFoundError:
                        self._send(404, {"error": "product not found"})
                        return
                    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, json.JSONDecodeError):
                        self._send(409, {"error": "product integrity check failed"})
                        return
                    is_video = manifest.get("product_kind") == "video_creator"
                    if action == "revision-brief":
                        if not is_video:
                            self._send(404, {"error": "video product not found"})
                            return
                        try:
                            from muse.video_human_review import VisualReviewLedger, verify_visual_review_evidence, build_visual_revision_brief
                            evidence = verify_visual_review_evidence(product_dir, initiative_id, version, files["video.mp4"][0])
                            with VisualReviewLedger("data/reviews/visual-content-reviews.sqlite3", workspace_root=workspace_root) as ledger:
                                records = ledger.list_reviews(initiative_id, version)
                            latest = records[-1] if records else None
                            status = None
                            if latest is not None:
                                from muse.video_publish_package import _review_record_status
                                status = _review_record_status(
                                    latest, category="visual_content", initiative_id=initiative_id,
                                    version_id=version, video_sha256=evidence["video_sha256"],
                                    required_artifacts=evidence["artifact_sha256"],
                                    release_manifest_sha256=evidence["release_manifest_sha256"],
                                )
                                if status == "evidence_mismatch_or_incomplete":
                                    self._send(409, {"error": "revision brief source evidence is no longer valid"})
                                    return
                            brief = build_visual_revision_brief(latest) if latest else None
                            if brief is not None:
                                brief["evidence_status"] = status
                            self._send(200, {"api_version": 1, "active": brief is not None, "brief": brief,
                                             "can_auto_modify_source": False, "new_version_created": False})
                        except FileNotFoundError:
                            self._send(404, {"error": "verified contact sheet is required"})
                        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                            self._send(409, {"error": "revision brief evidence or review history is invalid"})
                        return
                    if action == "visual-review":
                        if not is_video:
                            self._send(404, {"error": "video product not found"})
                            return
                        from muse.video_human_review import VisualReviewLedger, verify_visual_review_evidence
                        try:
                            evidence = verify_visual_review_evidence(
                                product_dir, initiative_id, version, files["video.mp4"][0],
                            )
                            ledger_path = "data/reviews/visual-content-reviews.sqlite3"
                            with VisualReviewLedger(ledger_path, workspace_root=workspace_root) as ledger:
                                records = ledger.list_reviews(initiative_id, version)
                            from muse.video_publish_package import _review_record_status
                            for record in records:
                                record["evidence_status"] = _review_record_status(
                                    record, category="visual_content", initiative_id=initiative_id,
                                    version_id=version, video_sha256=evidence["video_sha256"],
                                    required_artifacts=evidence["artifact_sha256"],
                                    release_manifest_sha256=evidence["release_manifest_sha256"],
                                )
                            self._send(200, {
                                "api_version": 1, "identity_assurance": "self_asserted_local_session",
                                "evidence": {
                                    "video_sha256": evidence["video_sha256"],
                                    "artifact_sha256": evidence["artifact_sha256"],
                                    "release_manifest_sha256": evidence["release_manifest_sha256"],
                                },
                                "count": len(records), "reviews": records,
                            })
                        except FileNotFoundError:
                            self._send(404, {"error": "verified contact sheet is required before visual review"})
                        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                            self._send(409, {"error": "visual review evidence is unavailable or mismatched"})
                        return
                    if action == "contact-sheet":
                        if not is_video:
                            self._send(404, {"error": "video contact sheet not found"})
                            return
                        try:
                            from muse.video_human_review import verify_visual_review_evidence
                            evidence = verify_visual_review_evidence(
                                product_dir, initiative_id, version, files["video.mp4"][0],
                            )
                            sheet_path = Path(evidence["contact_sheet_path"])
                            if sheet_path.is_symlink() or not sheet_path.is_file():
                                raise FileNotFoundError("verified contact sheet not found")
                            sheet_bytes = sheet_path.read_bytes()
                            if len(sheet_bytes) > 10 * 1024 * 1024:
                                self._send(413, {"error": "contact sheet evidence exceeds the read limit"})
                                return
                        except FileNotFoundError:
                            self._send(404, {"error": "verified contact sheet not found"})
                            return
                        except (OSError, ValueError, KeyError, TypeError):
                            self._send(409, {"error": "contact sheet evidence is missing or mismatched"})
                            return
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header("Content-Length", str(len(sheet_bytes)))
                        self.send_header("Content-Disposition", 'inline; filename="contact-sheet.png"')
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        self.wfile.write(sheet_bytes)
                        return
                    if action == "subtitle-review":
                        if not is_video:
                            self._send(404, {"error": "video product not found"})
                            return
                        try:
                            review = _find_product_subtitle_review(products_workspace, manifest)
                        except FileNotFoundError:
                            self._send(404, {"error": "matching subtitle review not found"})
                            return
                        except ValueError as exc:
                            self._send(409, {"error": str(exc)})
                            return
                        self._send(200, {"api_version": 1, "review": review})
                        return
                    if is_video:
                        if action == "preview":
                            name, content_type, disposition = "video.mp4", "video/mp4", "inline"
                        elif action == "video":
                            name, content_type, disposition = "video.mp4", "video/mp4", "attachment"
                        else:
                            name, content_type, disposition = "product.zip", "application/zip", "attachment"
                        file_path = files[name][0]
                        size = file_path.stat().st_size
                        if size > 2 * 1024 * 1024 * 1024:
                            self._send(413, {"error": "video product exceeds 2 GiB"})
                            return
                        body = None
                    else:
                        if action == "video":
                            self._send(404, {"error": "video file not found"})
                            return
                        name = "product.zip" if action == "download" else "index.html"
                        body = files[name][1]
                        file_path = files[name][0]
                        size = len(body)
                        content_type = "application/zip" if action == "download" else "text/html; charset=utf-8"
                        disposition = "attachment" if action == "download" else "inline"
                        if size > 20 * 1024 * 1024:
                            self._send(413, {"error": "product exceeds 20 MiB"})
                            return
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(size))
                    filename = (f"muse-video-{initiative_id[:8]}.mp4" if is_video and name == "video.mp4"
                                else f"muse-product-{initiative_id[:8]}.zip" if name == "product.zip" else "index.html")
                    self.send_header("Content-Disposition", disposition + '; filename="' + filename + '"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    if body is not None:
                        self.wfile.write(body)
                    else:
                        with file_path.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                self.wfile.write(chunk)
                elif parsed.path == "/api/v1/artifacts":
                    if reports_path is None:
                        self._send(503, {"error": "artifact store is not configured"})
                        return
                    limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                    artifacts = build_artifacts(reports_path, limit=limit)
                    self._send(200, {"api_version": 1, "count": len(artifacts), "artifacts": artifacts})
                elif parsed.path.startswith("/api/v1/artifacts/"):
                    if reports_path is None:
                        self._send(503, {"error": "artifact store is not configured"})
                        return
                    name = parsed.path.rsplit("/", 1)[-1]
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}\.md", name):
                        self._send(400, {"error": "invalid artifact name"})
                        return
                    root = reports_path.resolve()
                    target = (root / name).resolve()
                    if target.parent != root or not target.is_file() or target.is_symlink():
                        self._send(404, {"error": "artifact not found"})
                        return
                    if target.stat().st_size > 10 * 1024 * 1024:
                        self._send(413, {"error": "artifact exceeds 10 MiB"})
                        return
                    body = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Content-Disposition", 'attachment; filename="' + name + '"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._send(404, {"error": "not found"})
            except (ValueError, OSError) as exc:
                self._send(400, {"error": str(exc)})
            except Exception:
                self._send(503, {"error": "status source unavailable"})
        def do_OPTIONS(self) -> None:
            if not self.client_address or self.client_address[0] not in {"127.0.0.1", "::1"}:
                self._send(403, {"error": "loopback clients only"})
                return
            origin = self.headers.get("Origin")
            if not origin or not _allowed_origin(origin):
                self._send(403, {"error": "origin not allowed"})
                return
            requested_method = self.headers.get("Access-Control-Request-Method", "").upper()
            requested_headers = {
                value.strip().lower()
                for value in self.headers.get("Access-Control-Request-Headers", "").split(",")
                if value.strip()
            }
            if requested_method not in {"GET", "POST"} or not requested_headers.issubset({"authorization", "accept", "content-type"}):
                self._send(403, {"error": "preflight request not allowed"})
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Accept, Content-Type")
            self.send_header("Access-Control-Max-Age", "300")
            self.send_header("Vary", "Origin")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            if not self.client_address or self.client_address[0] not in {"127.0.0.1", "::1"}:
                self._send(403, {"error": "loopback clients only"})
                return
            if not _allowed_origin(self.headers.get("Origin")):
                self._send(403, {"error": "origin not allowed"})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + bearer):
                self._send(401, {"error": "unauthorized"})
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/api/v1/plugins/discover" or parsed.path.startswith("/api/v1/plugins/"):
                from muse.plugin_platform import PluginError, PluginRegistry, commander_task_queue_adapter
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 64 * 1024:
                        raise ValueError("plugin request body must contain 1-65536 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("plugin request body must be a JSON object")
                    with PluginRegistry(plugin_path) as plugins:
                        if parsed.path == "/api/v1/plugins/discover":
                            result = plugins.discover(payload)
                            self._send(201, {"api_version": 1, "plugin": result})
                        else:
                            approve_match = re.fullmatch(r"/api/v1/plugins/([a-z0-9.-]{1,96})/approve", parsed.path)
                            enable_match = re.fullmatch(r"/api/v1/plugins/([a-z0-9.-]{1,96})/enable", parsed.path)
                            disable_match = re.fullmatch(r"/api/v1/plugins/([a-z0-9.-]{1,96})/disable", parsed.path)
                            request_match = re.fullmatch(
                                r"/api/v1/plugins/([a-z0-9.-]{1,96})/capabilities/([a-z0-9._-]{1,96})/request",
                                parsed.path,
                            )
                            if approve_match:
                                if payload != {"confirmation": "APPROVE-PLUGIN"}:
                                    raise ValueError("explicit APPROVE-PLUGIN confirmation required")
                                result = plugins.approve(approve_match.group(1), approved_by="local-host")
                                self._send(200, {"api_version": 1, "plugin": result})
                            elif enable_match:
                                if payload != {"confirmation": "ENABLE-PLUGIN"}:
                                    raise ValueError("explicit ENABLE-PLUGIN confirmation required")
                                result = plugins.enable(enable_match.group(1), enabled_by="local-host")
                                self._send(200, {"api_version": 1, "plugin": result})
                            elif disable_match:
                                if payload != {"confirmation": "DISABLE-PLUGIN"}:
                                    raise ValueError("explicit DISABLE-PLUGIN confirmation required")
                                result = plugins.disable(disable_match.group(1), disabled_by="local-host")
                                self._send(200, {"api_version": 1, "plugin": result})
                            elif request_match:
                                if set(payload) != {"payload"} or not isinstance(payload["payload"], dict):
                                    raise ValueError("capability request requires one JSON object field: payload")
                                result = plugins.commander_request(
                                    request_match.group(1), request_match.group(2), payload["payload"],
                                    enqueue=commander_task_queue_adapter(task_path), requested_by="local-host",
                                )
                                self._send(202, {"api_version": 1, **result})
                            else:
                                self._send(404, {"error": "plugin operation not found"})
                except PluginError as exc:
                    self._send(409, {"error": str(exc)})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except Exception:
                    self._send(503, {"error": "plugin management operation unavailable"})
                return
            if parsed.path.startswith("/api/v1/companion/"):
                from muse.companion_security import CompanionDeviceStore, CompanionSecurityError
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 8192:
                        raise ValueError("request body must contain 1-8192 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be a JSON object")
                    with CompanionDeviceStore(companion_db_path) as companion:
                        if parsed.path == "/api/v1/companion/pairing/start":
                            if set(payload) - {"expires_in_seconds"}:
                                raise ValueError("unsupported pairing start fields")
                            challenge = companion.create_pairing_challenge(
                                expires_in_seconds=payload.get("expires_in_seconds", 180),
                            )
                            self._send(201, {"api_version": 1, "challenge": challenge})
                        else:
                            approve_match = re.fullmatch(r"/api/v1/companion/pairings/([a-f0-9]{32})/approve", parsed.path)
                            deny_match = re.fullmatch(r"/api/v1/companion/pairings/([a-f0-9]{32})/deny", parsed.path)
                            revoke_match = re.fullmatch(r"/api/v1/companion/devices/([a-f0-9]{32})/revoke", parsed.path)
                            if payload:
                                raise ValueError("this operation does not accept request fields")
                            if approve_match:
                                result = companion.approve_pairing(approve_match.group(1), approved_by="local-host")
                                self._send(200, {"api_version": 1, "pairing": result})
                            elif deny_match:
                                changed = companion.deny_pairing(deny_match.group(1), denied_by="local-host")
                                self._send(200 if changed else 404, {"api_version": 1, "denied": changed})
                            elif revoke_match:
                                changed = companion.revoke_device(revoke_match.group(1), revoked_by="local-host")
                                self._send(200 if changed else 404, {"api_version": 1, "revoked": changed})
                            else:
                                self._send(404, {"error": "companion operation not found"})
                except CompanionSecurityError as exc:
                    self._send(409, {"error": str(exc)})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except Exception:
                    self._send(503, {"error": "companion management operation unavailable"})
                return
            if parsed.path == "/api/v1/fusion/submit" or re.fullmatch(r"/api/v1/fusion/jobs/[a-f0-9]{32}/(?:approve|rollback)", parsed.path):
                from muse.skill_fusion import FusionError, SkillFusionService
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 12 * 1024 * 1024:
                        raise ValueError("request body must contain 1-12582912 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be a JSON object")
                    service = SkillFusionService(workspace_root, capability_path)
                    if parsed.path == "/api/v1/fusion/submit":
                        if set(payload) - {"source_url", "archive_base64", "file_name"}:
                            raise ValueError("unsupported fusion submission fields")
                        result = service.submit(
                            source_url=payload.get("source_url"),
                            archive_base64=payload.get("archive_base64"),
                            file_name=payload.get("file_name"),
                        )
                        self._send(201, result)
                    else:
                        operation = parsed.path.rsplit("/", 1)[-1]
                        expected_confirmation = "FUSE-CAPABILITIES" if operation == "approve" else "ROLLBACK-FUSION"
                        if set(payload) != {"confirmation"} or payload.get("confirmation") != expected_confirmation:
                            raise ValueError("explicit fusion operation confirmation is required")
                        fusion_id = parsed.path.split("/")[-2]
                        if operation == "approve":
                            result = service.approve(fusion_id, approved_by="local-user")
                        else:
                            result = service.rollback(fusion_id, rolled_back_by="local-user")
                        self._send(200, result)
                except FusionError as exc:
                    self._send(409, {"error": str(exc)})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "fusion storage is unavailable"})
                except Exception:
                    self._send(503, {"error": "fusion operation unavailable"})
                return
            # ── Creative Studio: local style-driven video studio ──
            if parsed.path in {
                "/api/v1/studio/search-media", "/api/v1/studio/generate-script",
                "/api/v1/studio/render", "/api/v1/studio/philosophy-inject",
            }:
                from muse.creative_studio_api import (
                    CreativeStudioStore, generate_script, philosophy_inject,
                    search_media, submit_render, validate_render_request,
                )
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 65536:
                        raise ValueError("request body must contain 1-65536 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    request = validate_render_request(payload)
                    if parsed.path == "/api/v1/studio/generate-script":
                        self._send(200, generate_script(request))
                    elif parsed.path == "/api/v1/studio/search-media":
                        self._send(200, search_media(request))
                    elif parsed.path == "/api/v1/studio/philosophy-inject":
                        self._send(200, philosophy_inject(
                            request, philosophy_db=studio_db.parent.parent / "philosophy" / "muse.sqlite3",
                        ))
                    else:
                        with CreativeStudioStore(studio_db) as studio:
                            self._send(201, submit_render(request, store=studio))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except Exception:
                    self._send(503, {"error": "creative studio operation unavailable"})
                return
            visual_review_match = re.fullmatch(
                r"/api/v1/products/([0-9a-f]{32})/([0-9a-f]{20})/visual-review",
                parsed.path,
            )
            if visual_review_match:
                if products_path is None:
                    self._send(503, {"error": "product store is not configured"})
                    return
                from muse.video_human_review import (
                    VisualReviewEvidenceError, VisualReviewLedger,
                    validate_visual_review_payload, verify_visual_review_evidence,
                )
                try:
                    if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                        self._send(415, {"error": "application/json required"})
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 64 * 1024:
                        raise ValueError("request body must contain 1-65536 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = validate_visual_review_payload(json.loads(raw.decode("utf-8")))
                    initiative_id, version = visual_review_match.groups()
                    product_dir, manifest, files = _verified_product_files(products_path, initiative_id, version)
                    if manifest.get("product_kind") != "video_creator":
                        self._send(404, {"error": "video product not found"})
                        return
                    evidence = verify_visual_review_evidence(
                        product_dir, initiative_id, version, files["video.mp4"][0],
                    )
                    ledger_path = "data/reviews/visual-content-reviews.sqlite3"
                    with VisualReviewLedger(ledger_path, workspace_root=workspace_root) as ledger:
                        record = ledger.append(initiative_id, version, payload, evidence)
                    from muse.video_publish_package import _review_record_status
                    record["evidence_status"] = _review_record_status(
                        record, category="visual_content", initiative_id=initiative_id,
                        version_id=version, video_sha256=evidence["video_sha256"],
                        required_artifacts=evidence["artifact_sha256"],
                        release_manifest_sha256=evidence["release_manifest_sha256"],
                    )
                    self._send(201, {
                        "api_version": 1, "identity_assurance": "self_asserted_local_session",
                        "record": record, "append_only": True, "digital_signature": False,
                    })
                except VisualReviewEvidenceError as exc:
                    self._send(409, {"error": str(exc)})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except FileNotFoundError:
                    self._send(404, {"error": "verified contact sheet is required before visual review"})
                except (KeyError, TypeError):
                    self._send(409, {"error": "visual review product evidence is incomplete"})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "visual review ledger unavailable"})
                return
            subtitle_approval_match = re.fullmatch(
                r"/api/v1/products/([0-9a-f]{32})/([0-9a-f]{20})/subtitle-review/approve",
                parsed.path,
            )
            if subtitle_approval_match:
                if products_path is None:
                    self._send(503, {"error": "product store is not configured"})
                    return
                try:
                    if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                        self._send(415, {"error": "application/json required"})
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 64 * 1024:
                        raise ValueError("request body must contain 1-65536 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    required = {
                        "reviewed_segment_indexes", "corrections", "reviewer", "reviewer_id",
                        "reviewer_role", "rationale", "evidence_refs", "confirmation",
                    }
                    if not isinstance(payload, dict) or set(payload) != required:
                        raise ValueError("body requires reviewed segments, corrections, reviewer identity, role, rationale, evidence references and confirmation only")
                    if payload["confirmation"] != "APPROVE-HUMAN-REVIEW":
                        raise ValueError("explicit human approval confirmation is required")
                    initiative_id, version = subtitle_approval_match.groups()
                    _, manifest, _ = _verified_product_files(products_path, initiative_id, version)
                    if manifest.get("product_kind") != "video_creator":
                        self._send(404, {"error": "video product not found"})
                        return
                    review_document = _find_product_subtitle_review(products_workspace, manifest)
                    from muse.subtitle_review import apply_human_subtitle_review
                    approved_review = apply_human_subtitle_review(
                        review_document,
                        reviewed_segment_indexes=payload["reviewed_segment_indexes"],
                        corrections=payload["corrections"],
                        reviewer=payload["reviewer"],
                        approve=True,
                        reviewer_id=payload["reviewer_id"],
                        reviewer_role=payload["reviewer_role"],
                        rationale=payload["rationale"],
                        evidence_refs=payload["evidence_refs"],
                    )
                    result = VideoProductBuilder(products_workspace).package_approved_subtitle_revision(
                        initiative_id, version, approved_review,
                    )
                    self._send(201, {
                        "api_version": 1,
                        "product": {
                            "initiative_id": result["initiative_id"],
                            "version_id": result["version_id"],
                            "creator_initiative_id": result.get("creator_initiative_id"),
                            "validated": result["validated"],
                            "status": result["status"],
                            "subtitle_review": result.get("subtitle_review"),
                            "quality_gate": result.get("quality_gate"),
                            "relative_path": result["relative_path"],
                        },
                    })
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except FileNotFoundError:
                    self._send(404, {"error": "matching subtitle review or product not found"})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "subtitle review approval unavailable"})
                except Exception as exc:
                    if type(exc).__name__ == "VideoProductError":
                        self._send(409, {"error": str(exc)})
                    else:
                        self._send(503, {"error": "subtitle review approval unavailable"})
                return
            # ── Bounded local runtime lifecycle + reproducible pipeline control ──
            if parsed.path in {
                "/api/v1/daemon/start", "/api/v1/daemon/pause",
                "/api/v1/daemon/resume", "/api/v1/daemon/stop", "/api/v1/daemon/once",
            }:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, {"error": "invalid content length"})
                    return
                if length < 0:
                    self._send(400, {"error": "invalid content length"})
                    return
                if length > 1024:
                    self._send(413, {"error": "runtime control accepts no meaningful body"})
                    return
                if length:
                    self.rfile.read(length)
                try:
                    action = parsed.path.rsplit("/", 1)[-1]
                    if action == "start":
                        result = daemon.start()
                    elif action == "pause":
                        result = daemon.pause()
                    elif action == "resume":
                        result = daemon.resume()
                    elif action == "stop":
                        result = daemon.stop()
                    else:
                        result = {"changed": True, "action": "once", **daemon.run_once()}
                    self._send(200, {"api_version": 1, "result": result})
                except DaemonError as exc:
                    self._send(409, {"error": str(exc)})
                except (ValueError, OSError):
                    self._send(503, {"error": "runtime control unavailable"})
                return
            if parsed.path == "/api/v1/pipelines/submit":
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 2048:
                        raise ValueError("request body must be 1-2048 bytes")
                    raw = self.rfile.read(length)
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict) or set(payload) - {"recipe_id", "title", "inspiration_id", "blueprint_path"}:
                        raise ValueError("body accepts recipe_id and optional title/inspiration_id/blueprint_path")
                    recipe_id = payload.get("recipe_id")
                    if not isinstance(recipe_id, str) or not recipe_id:
                        raise ValueError("recipe_id is required")
                    title = payload.get("title")
                    if title is not None and (not isinstance(title, str) or len(title) > 200):
                        raise ValueError("title must be at most 200 characters")
                    insp_id = payload.get("inspiration_id")
                    if insp_id is not None and (not isinstance(insp_id, str) or len(insp_id) > 64):
                        raise ValueError("inspiration_id is invalid")
                    bp_path = payload.get("blueprint_path")
                    if bp_path is not None and (not isinstance(bp_path, str) or len(bp_path) > 300):
                        raise ValueError("blueprint_path is invalid")
                    job = pipeline.submit(recipe_id, title=title, inspiration_id=insp_id, blueprint_path=bp_path)
                    self._send(202, {"api_version": 1, "job": job})
                except PipelineError as exc:
                    self._send(409, {"error": str(exc)})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                return
            initiative_performance_match = re.fullmatch(r"/api/v1/initiatives/([0-9a-f]{32})/performance", parsed.path)
            if initiative_performance_match:
                if initiatives_path is None or not initiatives_path.is_file():
                    self._send(503, {"error": "initiative store is not configured"})
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 8192:
                        raise ValueError("request body must contain 1-8192 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    allowed = {"idempotency_key", "platform", "source", "currency", "published_at", "source_reference", "notes", "metrics", "content_profile"}
                    required = {"idempotency_key", "platform", "source", "metrics"}
                    if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - allowed:
                        raise ValueError("body requires idempotency_key, platform, source and metrics; only documented optional fields are accepted")
                    from muse.initiative_engine import InitiativeStore
                    with InitiativeStore(initiatives_path) as initiatives:
                        try:
                            saved = initiatives.record_performance(
                                initiative_performance_match.group(1),
                                idempotency_key=payload["idempotency_key"], platform=payload["platform"],
                                source=payload["source"], currency=payload.get("currency", "SGD"),
                                published_at=payload.get("published_at"),
                                source_reference=payload.get("source_reference", ""),
                                notes=payload.get("notes", ""), metrics=payload["metrics"],
                                content_profile=payload.get("content_profile", {}),
                            )
                        except KeyError:
                            self._send(404, {"error": "initiative not found"})
                            return
                    self._send(201, {"api_version": 1, "snapshot": saved})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "initiative performance store unavailable"})
                return
            initiative_feedback_match = re.fullmatch(r"/api/v1/initiatives/([0-9a-f]{32})/feedback", parsed.path)
            if initiative_feedback_match:
                if initiatives_path is None or not initiatives_path.is_file():
                    self._send(503, {"error": "initiative store is not configured"})
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 8192:
                        raise ValueError("request body must contain 1-8192 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    allowed = {"feedback", "decision", "reviewer", "source", "evolution_cycle_id"}
                    required = {"feedback", "decision"}
                    if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - allowed:
                        raise ValueError("body requires feedback and decision with only documented optional fields")
                    from muse.initiative_engine import InitiativeStore
                    with InitiativeStore(initiatives_path) as initiatives:
                        try:
                            saved = initiatives.record_feedback(
                                initiative_feedback_match.group(1), payload["feedback"],
                                decision=payload["decision"], reviewer=payload.get("reviewer", "creator"),
                                source=payload.get("source", "dashboard"),
                                evolution_cycle_id=payload.get("evolution_cycle_id"),
                            )
                        except KeyError:
                            self._send(404, {"error": "initiative not found"})
                            return
                    self._send(201, {"api_version": 1, "feedback": {
                        "id": saved["id"], "decision": saved["decision"],
                        "created_at": saved["created_at"],
                        "evolution_cycle_id": saved["evolution_cycle_id"],
                    }})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "initiative feedback store unavailable"})
                return
            if parsed.path == "/api/v1/video-products":
                if products_path is None:
                    self._send(503, {"error": "product store is not configured"})
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 8192:
                        raise ValueError("request body must contain 1-8192 bytes")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("body must be a JSON object")
                    mode = payload.get("mode", "local_assets")
                    if mode not in {"local_assets", "prompt_to_video"}:
                        raise ValueError("mode must be local_assets or prompt_to_video")
                    title = payload.get("title")
                    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
                        raise ValueError("title must contain 1-160 characters")
                    creator_initiative_id = payload.get("creator_initiative_id")
                    evolution_cycle_id = payload.get("evolution_cycle_id")
                    if creator_initiative_id is not None and (
                        not isinstance(creator_initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", creator_initiative_id)
                    ):
                        raise ValueError("creator_initiative_id must be a 32-character lowercase UUID hex string")
                    if evolution_cycle_id is not None and (
                        not isinstance(evolution_cycle_id, str) or not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id)
                    ):
                        raise ValueError("evolution_cycle_id must be a valid Muse Evolution Cycle ID")
                    motion_profile = payload.get("motion_profile", "none")
                    if motion_profile not in {"none", "brand_intro_outro", "brand_scene_transitions"}:
                        raise ValueError("motion_profile must be none, brand_intro_outro or brand_scene_transitions")
                    if payload.get("aspect_ratio", "16:9") not in {"16:9", "9:16", "1:1"}:
                        raise ValueError("aspect_ratio must be 16:9, 9:16 or 1:1")
                    if mode == "prompt_to_video":
                        if motion_profile == "brand_scene_transitions":
                            raise ValueError("brand_scene_transitions is available for multi-scene local assets; AI clip uses brand_intro_outro")
                        allowed = {"mode", "title", "prompt", "negative_prompt", "aspect_ratio", "duration_seconds", "steps", "seed", "creator_initiative_id", "evolution_cycle_id", "motion_profile", "recipe_sha256"}
                        if set(payload) - allowed or "prompt" not in payload:
                            raise ValueError("AI video body requires title and prompt with only documented generation settings")
                        prompt = payload["prompt"]
                        negative_prompt = payload.get("negative_prompt", "")
                        duration_seconds = payload.get("duration_seconds", 2.0)
                        steps = payload.get("steps", 8)
                        seed = payload.get("seed")
                        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 2000:
                            raise ValueError("prompt must contain 1-2000 characters")
                        if not isinstance(negative_prompt, str) or len(negative_prompt) > 1000:
                            raise ValueError("negative_prompt must be at most 1000 characters")
                        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or not 1 <= duration_seconds <= 5:
                            raise ValueError("duration_seconds must be between 1 and 5")
                        if isinstance(steps, bool) or not isinstance(steps, int) or not 8 <= steps <= 30:
                            raise ValueError("steps must be between 8 and 30")
                        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1):
                            raise ValueError("seed must be an unsigned 32-bit integer")
                        recipe_sha256 = payload.get("recipe_sha256")
                        if recipe_sha256 is None and creator_initiative_id is not None and initiatives_path is not None:
                            from muse.initiative_engine import InitiativeStore
                            with InitiativeStore(initiatives_path) as source_store:
                                source_initiative = source_store.get_by_id(creator_initiative_id)
                            if source_initiative is not None:
                                try:
                                    source_concept = json.loads(source_initiative["concept"])
                                except (TypeError, json.JSONDecodeError):
                                    source_concept = {}
                                source_brief = source_concept.get("creator_brief") if isinstance(source_concept, dict) else None
                                source_video = source_brief.get("video") if isinstance(source_brief, dict) else None
                                if isinstance(source_video, dict):
                                    recipe_sha256 = source_video.get("recipe_sha256")
                        if recipe_sha256 is not None and (
                            not isinstance(recipe_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", recipe_sha256)
                        ):
                            raise ValueError("creator brief recipe_sha256 must be a lowercase SHA-256 digest")
                        payload["recipe_sha256"] = recipe_sha256
                        self._send(409, {
                            "code": "video.commander_admission_required",
                            "error": "AI video generation must be submitted through Commander approval and Video Admission",
                            "approval_required": True,
                        })
                        return
                    else:
                        allowed = {"mode", "source", "title", "audio", "audio_mode", "aspect_ratio", "fps", "image_seconds", "encoder", "creator_initiative_id", "evolution_cycle_id", "motion_profile"}
                        if set(payload) - allowed or "source" not in payload:
                            raise ValueError("local asset mode requires source and title with only documented optional settings")
                        workspace = products_path.parent.parent
                        VideoProductBuilder(workspace)._source(payload["source"], payload.get("audio"))
                        fps = payload.get("fps", 30)
                        if isinstance(fps, bool) or not isinstance(fps, int) or fps not in {24, 25, 30, 50, 60}:
                            raise ValueError("fps must be one of 24, 25, 30, 50 or 60")
                        image_seconds = payload.get("image_seconds", 4.0)
                        if isinstance(image_seconds, bool) or not isinstance(image_seconds, (int, float)) or not 0.5 <= image_seconds <= 60:
                            raise ValueError("image_seconds must be between 0.5 and 60")
                        if payload.get("encoder", "auto") not in {"auto", "amf", "software"}:
                            raise ValueError("encoder must be auto, amf or software")
                        if payload.get("audio_mode", "music") not in {"music", "narration"}:
                            raise ValueError("audio_mode must be music or narration")
                    if not video_job_slot.acquire(blocking=False):
                        self._send(409, {"error": "another video render is already running"})
                        return
                    job_id = secrets.token_hex(16)
                    now = datetime.now(timezone.utc).isoformat()
                    from muse.initiative_engine import InitiativeStore
                    try:
                        with InitiativeStore(initiatives_path) as attempts_store:
                            attempts_store.start_video_attempt(
                                job_id, mode=mode, initiative_id=creator_initiative_id,
                                evolution_cycle_id=payload.get("evolution_cycle_id"),
                                recipe_sha256=payload.get("recipe_sha256"),
                            )
                    except Exception:
                        video_job_slot.release()
                        raise
                    with video_jobs_lock:
                        if len(video_jobs) >= 50:
                            for old_id in list(video_jobs):
                                if video_jobs[old_id].get("status") in {"succeeded", "failed"}:
                                    del video_jobs[old_id]
                                    if len(video_jobs) < 40:
                                        break
                        video_jobs[job_id] = {"id": job_id, "status": "queued", "created_at": now, "updated_at": now}

                    thread = Thread(target=_run_video_job, args=(job_id, payload), daemon=True, name="MuseVideoProduct")
                    try:
                        thread.start()
                    except RuntimeError:
                        video_job_slot.release()
                        try:
                            with InitiativeStore(initiatives_path) as attempts_store:
                                attempts_store.fail_video_attempt(
                                    job_id, error_type="RuntimeError",
                                    failure_code="video.worker_start_failed",
                                    initiative_id=creator_initiative_id,
                                )
                        except Exception:
                            pass
                        with video_jobs_lock:
                            video_jobs[job_id]["status"] = "failed"
                            video_jobs[job_id]["error"] = "video worker could not start"
                        raise
                    self._send(202, {"api_version": 1, "job": {"id": job_id, "status": "queued"}})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, OSError) as exc:
                    self._send(400, {"error": str(exc)})
                except Exception:
                    self._send(503, {"error": "video product request unavailable"})
                return
            if parsed.path == "/api/v1/radar/scan":
                if improvement_path is None:
                    self._send(503, {"error": "improvement store is not configured"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, {"error": "invalid content length"})
                    return
                if length != 0:
                    self._send(400, {"error": "radar scan does not accept a request body"})
                    return
                try:
                    result = scan_capabilities(task_path, improvement_path, lookback_hours=24)
                    self._send(200, {
                        "api_version": 1,
                        "signals_seen": result["signals_seen"],
                        "created": result["created"],
                        "existing": result["existing"],
                        "trend": result["trend"],
                        "approval_required": True,
                    })
                except (ValueError, FileNotFoundError) as exc:
                    self._send(400, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "capability scan unavailable"})
                except Exception:
                    self._send(503, {"error": "capability scan unavailable"})
                return
            if parsed.path == "/api/v1/inspirations/evolve":
                if knowledge_path is None:
                    self._send(503, {"error": "inspiration store is not configured"})
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, {"error": "invalid content length"})
                    return
                if length < 0:
                    self._send(400, {"error": "invalid content length"})
                    return
                if length > 4096:
                    self._send(413, {"error": "request body exceeds 4096 bytes"})
                    return
                try:
                    payload: dict[str, Any] = {}
                    if length > 0:
                        raw = self.rfile.read(length)
                        if len(raw) != length:
                            raise ValueError("incomplete request body")
                        _validate_json_nesting(raw)
                        payload = json.loads(raw.decode("utf-8"))
                    allowed_fields = {"inspiration_id", "request_id", "enqueue_video", "aspect_ratios", "create_offline_product"}
                    if not isinstance(payload, dict) or set(payload) - allowed_fields:
                        raise ValueError("body accepts request_id and optional inspiration_id, enqueue_video, aspect_ratios, create_offline_product")
                    request_id = payload.get("request_id")
                    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
                        raise ValueError("request_id must be a 32-char hex string")
                    insp_id = payload.get("inspiration_id")
                    if insp_id is not None and (not isinstance(insp_id, str) or not re.fullmatch(r"[0-9a-f]{32}", insp_id)):
                        raise ValueError("inspiration_id must be a 32-char hex string")
                    enqueue_video = payload.get("enqueue_video", False)
                    if not isinstance(enqueue_video, bool):
                        raise ValueError("enqueue_video must be boolean")
                    create_offline_product = payload.get("create_offline_product", False)
                    if not isinstance(create_offline_product, bool):
                        raise ValueError("create_offline_product must be boolean")
                    ratios = payload.get("aspect_ratios") or ["9:16"]
                    if (not isinstance(ratios, list) or not 1 <= len(ratios) <= 2
                            or any(not isinstance(r, str) or r not in {"9:16", "16:9"} for r in ratios)
                            or len(set(ratios)) != len(ratios)):
                        raise ValueError("aspect_ratios must contain one or two unique supported ratios")
                    task_payload = {
                        "kind": "inspiration_evolution", "request_id": request_id,
                        "inspiration_id": insp_id, "enqueue_video": enqueue_video,
                        "aspect_ratios": ratios,
                        "create_offline_product": create_offline_product,
                    }
                    idempotency_key = "inspiration-evolution:" + request_id
                    with TaskQueue(task_path) as queue:
                        existing = queue.get_by_idempotency_key(idempotency_key)
                        if existing is not None:
                            existing_payload = existing.get("payload")
                            existing_request = (
                                {key: existing_payload.get(key, False) if key == "create_offline_product" else existing_payload.get(key)
                                 for key in task_payload}
                                if isinstance(existing_payload, dict) else None
                            )
                            if existing_request != task_payload:
                                raise ValueError("request_id was already used for a different inspiration evolution")
                        task = queue.enqueue(
                            "Evolve creator inspiration", task_payload,
                            priority=65, workload_class="research", recovery_policy="retry",
                            idempotency_key=idempotency_key,
                        )
                    self._send(202, {
                        "api_version": 1, "accepted": True, "task_id": task["id"],
                        "status": task["status"], "deduplicated": existing is not None,
                        "execution": "commander_task_queue",
                    })
                except KeyError as exc:
                    self._send(404, {"error": str(exc)})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, PipelineError) as exc:
                    self._send(400, {"error": str(exc)})
                except RecursionError:
                    self._send(400, {"error": "request nesting exceeds the supported limit"})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "inspiration evolution store unavailable"})
                return
            if parsed.path == "/api/v1/inspirations":
                if knowledge_path is None:

                    self._send(503, {"error": "inspiration store is not configured"})
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, {"error": "invalid content length"})
                    return
                if length < 1:
                    self._send(400, {"error": "request body is required"})
                    return
                if length > 8192:
                    self._send(413, {"error": "request body exceeds 8192 bytes"})
                    return
                try:
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    required_fields = {"content", "allow_external_research"}
                    optional_fields = {"title", "tags", "research_domains", "source"}
                    if (
                        not isinstance(payload, dict)
                        or not required_fields.issubset(payload)
                        or set(payload) - required_fields - optional_fields
                    ):
                        raise ValueError("body must contain content and allow_external_research, with optional title/tags/research_domains/source")
                    content = payload["content"]
                    allow_external = payload["allow_external_research"]
                    if not isinstance(content, str) or not content.strip() or len(content.strip()) > 2000:
                        raise ValueError("content must contain 1-2000 characters")
                    if not isinstance(allow_external, bool):
                        raise ValueError("allow_external_research must be boolean")
                    content = content.strip()
                    raw_title = payload.get("title")
                    if raw_title is not None and (
                        not isinstance(raw_title, str) or not raw_title.strip() or len(raw_title.strip()) > 80
                    ):
                        raise ValueError("title must contain 1-80 characters")
                    title = raw_title.strip() if isinstance(raw_title, str) else (content.splitlines()[0].strip()[:80] or "未命名灵感")

                    def normalize_labels(field: str) -> list[str]:
                        values = payload.get(field, [])
                        if not isinstance(values, list) or len(values) > 12:
                            raise ValueError(f"{field} must be a list containing at most 12 labels")
                        normalized: list[str] = []
                        seen: set[str] = set()
                        for value in values:
                            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 60:
                                raise ValueError(f"{field} labels must contain 1-60 characters")
                            label = " ".join(value.split())
                            if label.casefold() not in seen:
                                normalized.append(label)
                                seen.add(label.casefold())
                        return normalized

                    tags = normalize_labels("tags")
                    research_domains = normalize_labels("research_domains")
                    source_kind = payload.get("source", "muse_local_dashboard")
                    if not isinstance(source_kind, str) or source_kind not in {"chatgpt_daily_inspiration", "muse_local_dashboard", "user_manual"}:
                        raise ValueError("source must be chatgpt_daily_inspiration, muse_local_dashboard, or user_manual")
                    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    external_ref = "creator-inspiration:sha256:" + content_sha256
                    with ResearchKnowledgeStore(knowledge_path) as knowledge:
                        node = knowledge.upsert_node(
                            "claim", title, summary=content,
                            external_ref=external_ref,
                            metadata={
                                "origin": "creator_inspiration_inbox",
                                "epistemic_status": "creator_inspired_hypothesis_only",
                                "creator_context_is_evidence": False,
                                "requires_support_and_counter_evidence": True,
                                "review_state": "not_researched",
                                "allow_external_research": allow_external,
                                "submitted_via": "muse_local_dashboard",
                                "source_kind": source_kind,
                                "content_sha256": content_sha256,
                                "tags": tags,
                                "research_domains": research_domains,
                            },
                            provenance=(source_kind,),
                            status="proposed",
                        )
                    self._send(201, {"api_version": 1, "inspiration": {
                        "id": node["id"], "title": node["title"], "content": node["summary"],
                        "created_at": node["created_at"], "status": node.get("metadata", {}).get("review_state", "not_researched"),
                        "allow_external_research": allow_external,
                        "tags": tags, "research_domains": research_domains, "source": source_kind,
                    }})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "inspiration store unavailable"})
                return
            if parsed.path == "/api/v1/galaxy/route-preview":
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, {"error": "invalid content length"})
                    return
                if length < 1 or length > 2048:
                    self._send(413 if length > 2048 else 400, {"error": "request body must be 1-2048 bytes"})
                    return
                try:
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict) or set(payload) != {"goal"}:
                        raise ValueError("body accepts goal only")
                    result = preview_specialist_route(
                        payload["goal"],
                        task_db=task_path,
                        agent_db=agent_path,
                    )
                    self._send(200, {"api_version": 1, **result})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "route preview unavailable"})
                return
            if parsed.path == "/api/v1/metaphysics/yijing":
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, {"error": "invalid content length"})
                    return
                if length < 1 or length > 4096:
                    self._send(413 if length > 4096 else 400, {"error": "request body must be 1-4096 bytes"})
                    return
                try:
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("incomplete request body")
                    payload = json.loads(raw.decode("utf-8"))
                    allowed = {"mode", "seed", "line_values", "lower_bits", "upper_bits", "goal", "frameworks"}
                    if not isinstance(payload, dict) or set(payload) - allowed:
                        raise ValueError("body contains unsupported fields")
                    result = run_yijing_inquiry(payload)
                    self._send(200, {"api_version": 1, **result})
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                return
            if parsed.path != "/api/v1/feedback":
                self._send(405, {"error": "mutation not allowed"})
                return
            if improvement_path is None:
                self._send(503, {"error": "feedback store is not configured"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._send(415, {"error": "application/json required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid content length"})
                return
            if length < 1:
                self._send(400, {"error": "request body is required"})
                return
            if length > 4096:
                self._send(413, {"error": "request body exceeds 4096 bytes"})
                return
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("incomplete request body")
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"category", "tag", "severity"}:
                    raise ValueError("body must contain category, tag and severity only")
                if not isinstance(payload["category"], str) or not isinstance(payload["tag"], str):
                    raise ValueError("category and tag must be strings")
                severity = payload["severity"]
                if isinstance(severity, bool) or not isinstance(severity, int):
                    raise ValueError("severity must be an integer from 1 to 5")
                with ImprovementStore(improvement_path) as store:
                    saved = store.record_feedback(
                        payload["category"], payload["tag"], severity=severity
                    )
                safe = {key: saved[key] for key in ("category", "tag", "severity", "created_at")}
                self._send(201, {"api_version": 1, "feedback": safe})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
            except (OSError, sqlite3.Error):
                self._send(503, {"error": "feedback store unavailable"})

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

    server = BoundedStatusServer(("127.0.0.1", port), StatusHandler)
    return server, bearer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start Muse loopback status and structured-feedback API")
    parser.add_argument("--task-db", required=True)
    parser.add_argument("--progress-db", required=True)
    parser.add_argument("--capability-db", required=True)
    parser.add_argument("--agent-db")
    parser.add_argument("--improvement-db")
    parser.add_argument("--knowledge-db")
    parser.add_argument("--report-dir")
    parser.add_argument("--products-dir")
    parser.add_argument("--gpu-resource-db")
    parser.add_argument("--memory-db")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    # Derive the remaining stores from the task DB layout when the caller
    # (Tauri/CLI) does not pass them explicitly, so every desktop panel sees
    # real project data instead of a 503 empty shell.
    task_root = Path(args.task_db).resolve().parent.parent
    knowledge_db = args.knowledge_db or str(task_root / "research" / "knowledge.sqlite3")
    report_dir = args.report_dir or str(task_root / "research" / "reports")
    products_dir = args.products_dir or str(task_root / "products")
    gpu_resource_db = args.gpu_resource_db or str(task_root / "gpu-resources.sqlite3")
    memory_db = args.memory_db or str(task_root / "memory" / "muse.sqlite3")
    server, token = create_status_server(
        task_db=args.task_db, progress_db=args.progress_db,
        capability_db=args.capability_db, agent_db=args.agent_db,
        improvement_db=args.improvement_db, port=args.port,
        knowledge_db=knowledge_db if Path(knowledge_db).exists() else None,
        report_dir=report_dir if Path(report_dir).is_dir() else None,
        products_dir=products_dir if Path(products_dir).is_dir() else None,
        gpu_resource_db=gpu_resource_db if Path(gpu_resource_db).exists() else None,
        memory_db=memory_db,
    )
    print(json.dumps({"address": "127.0.0.1", "port": server.server_address[1], "token": token}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
