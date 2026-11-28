"""B391 P1: interactive control-plane API for the Muse Super Agent OS.

All routes live under ``/api/v1/control/`` and are mounted by :mod:`muse.status_api`, which already
enforces loopback-only clients, the Origin allowlist and the Bearer token before calling
:func:`handle_control`. This module never creates a scheduler: every write goes through the existing
SQLite :class:`~muse.task_queue.TaskQueue` (Commander remains the only dispatcher), the existing
:class:`~muse.memory.MemoryStore` candidate approval, or the Research Wiki store.

Write routes require an explicit confirmation phrase and never start execution:

* ``POST /api/v1/control/tasks`` – submit a Commander request (always ``waiting_approval``).
* ``POST /api/v1/control/tasks/<id>/cancel`` – cancel a queued / waiting Ticket.
* ``POST /api/v1/control/tasks/<id>/retry`` – re-submit a failed / cancelled Ticket as a *new*
  waiting_approval Ticket (internal ``_`` fields are dropped; approval is required again).
* ``POST /api/v1/control/approvals/<id>/decide`` – approve / reject a *generic* waiting Ticket.
  Tickets with dedicated gates (video approvals, signed reviews, contextual inquiries,
  video_pipeline) are refused and point to their dedicated endpoint.
* ``POST /api/v1/control/memory/candidates/<id>/decide`` – approve / reject a memory candidate.
* ``POST /api/v1/control/xuanshu/hypotheses`` – symbolic lens → labelled testable hypotheses,
  optionally written to the Research Wiki as ``proposed`` claims.
* ``POST /api/v1/control/xuanshu/dispatch`` – submit a Wiki claim to Commander (waiting_approval).
* ``POST /api/v1/control/creation/feedback`` – ``LINK-FEEDBACK-TO-CLAIM``: creator feedback on a vault
  video is written to the creator ledger and linked to a Wiki claim as ``claim --tested_by--> record``
  (idempotent per request_id; claim status is never changed automatically).
* ``GET /api/v1/control/knowledge/claims`` / ``claims/<id>/tested-by`` – hypothesis list and the
  evidence-chain drawer (symbolic origin vs. empirical feedback records kept apart).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs
from muse.error_journal import record_suppressed_error

APPROVER = "local-dashboard-user"
MAX_JSON_DEPTH = 32
_SUBMIT_LOCK = threading.Lock()
APPROVER_SCOPE = "local_single_user_session"
MAX_BODY_BYTES = 16 * 1024
_TASK_ID = r"[0-9a-f]{32}"
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_TASK_STATUSES = frozenset({"queued", "running", "waiting_approval", "succeeded", "failed", "cancelled"})
# Ticket kinds that must be decided through their own dedicated, stricter endpoint.
_DEDICATED_GATES = {
    "business_opportunity_video_approval": "/api/v1/video-approval-requests/<id>/approve",
    "video_pipeline": "Commander video dispatcher (no manual approval)",
    "contextual_inquiry_review": "/api/v1/contextual-inquiry-reviews/<id>",
}
# Internal payload markers that bind a Ticket to a dedicated gate even when ``kind`` is generic.
_DEDICATED_PAYLOAD_MARKERS = frozenset({"_signed_review", "_contextual_inquiry", "_video_admission"})
WORKFLOW_LOOP = (
    ("inspiration", "灵感发现"), ("research", "研究求证"), ("knowledge", "知识沉淀"),
    ("muse", "∞ Muse"), ("creation", "创作生成"), ("verification", "验证迭代"), ("evolution", "进化超越"),
)


class ControlNotFound(LookupError):
    pass


@dataclass
class ControlContext:
    workspace_root: Path
    task_path: Path
    memory_path: Path | None = None
    knowledge_path: Path | None = None
    improvement_path: Path | None = None
    gpu_resource_path: Path | None = None
    vault_path: Path | None = None
    products_provider: Callable[[], list[dict[str, Any]]] | None = None
    gpu_status_provider: Callable[[], dict[str, Any]] | None = None
    video_engines_provider: Callable[[], dict[str, Any]] | None = None
    # B391-P2-10: Product Vault resolver (hash computed server-side) and creator-ledger writer.
    video_product_resolver: Callable[[str, str], dict[str, Any]] | None = None
    feedback_ledger_writer: Callable[..., dict[str, Any]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ───────────────────────────── helpers ─────────────────────────────

def _query(query: str) -> dict[str, str]:
    return {key: values[-1] for key, values in parse_qs(query or "", keep_blank_values=False).items() if values}


def _limit(raw: str | None, default: int, maximum: int) -> int:
    if raw is None:
        return default
    if not re.fullmatch(r"[0-9]{1,4}", raw):
        raise ValueError("limit must be a positive integer")
    value = int(raw)
    if not 1 <= value <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def _text(payload: dict[str, Any], name: str, maximum: int, *, required: bool = True) -> str:
    value = payload.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value.strip()) > maximum:
        raise ValueError(f"{name} must contain {'1' if required else '0'}-{maximum} characters")
    return value.strip()


def _only(payload: dict[str, Any], allowed: set[str]) -> None:
    extra = set(payload) - allowed
    if extra:
        raise ValueError("unsupported fields: " + ", ".join(sorted(extra))[:200])


def _confirm(payload: dict[str, Any], phrase: str) -> None:
    if payload.get("confirmation") != phrase:
        raise PermissionError(f"explicit {phrase} confirmation is required")


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    """Payload-free Ticket projection (titles and lifecycle only)."""
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    approval = payload.get("_approval") if isinstance(payload.get("_approval"), dict) else {}
    kind = payload.get("kind") or payload.get("request_kind") or "commander_request"
    signed = payload.get("_signed_review") if isinstance(payload.get("_signed_review"), dict) else {}
    return {
        "id": task.get("id"),
        "title": str(task.get("title") or "")[:200],
        "status": task.get("status"),
        "kind": str(kind)[:80],
        "priority": task.get("priority"),
        "attempts": task.get("attempts"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "lease_owner": task.get("lease_owner"),
        "lease_expires_at": task.get("lease_expires_at"),
        "depends_on": task.get("depends_on") if isinstance(task.get("depends_on"), list) else [],
        "approved_by": approval.get("approved_by"),
        "signed_review_pending": signed.get("status") == "pending",
        "dedicated_gate": _DEDICATED_GATES.get(str(payload.get("kind") or "")),
    }


def _event_projection(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    safe_payload = {
        key: payload[key] for key in ("decision", "status", "error_type", "error_code", "reason", "step_id")
        if key in payload and isinstance(payload[key], (str, int, float, bool))
    }
    return {
        "event_type": event.get("event_type"), "actor": event.get("actor"),
        "created_at": event.get("created_at"), "payload": safe_payload,
    }


# ───────────────────────────── task center ─────────────────────────────

def list_tasks(ctx: ControlContext, query: dict[str, str]) -> dict[str, Any]:
    from muse.task_queue import TaskQueue
    status = query.get("status")
    if status is not None and status not in _TASK_STATUSES:
        raise ValueError("status is not a supported Ticket status")
    kind = query.get("kind")
    if kind is not None and not re.fullmatch(r"[a-z0-9_]{1,80}", kind):
        raise ValueError("kind must be a lowercase identifier")
    limit = _limit(query.get("limit"), 50, 200)
    with TaskQueue(ctx.task_path) as queue:
        snapshot = queue.snapshot(limit=1)
        tasks = queue.list_tasks(kind=kind, limit=200 if status else limit)
    items = [_public_task(task) for task in tasks if status is None or task.get("status") == status][:limit]
    return {"counts": snapshot["counts"], "count": len(items), "tasks": items}


def task_detail(ctx: ControlContext, task_id: str) -> dict[str, Any]:
    from muse.task_queue import TaskQueue
    with TaskQueue(ctx.task_path) as queue:
        task = queue.get(task_id)
        if task is None:
            raise ControlNotFound("task not found")
        events = queue.events(task_id, limit=100)
        try:
            workflow = queue.workflow_status(task_id)
        except Exception as _muse_suppressed:
            record_suppressed_error("control_center_api.py:185", _muse_suppressed)
            workflow = None
    detail = _public_task(task)
    detail["events"] = [_event_projection(event) for event in events]
    if isinstance(workflow, dict):
        detail["workflow"] = {
            key: workflow.get(key) for key in ("status", "revision", "steps", "waves", "current_wave")
            if key in workflow
        }
    return {"task": detail}


def submit_task(ctx: ControlContext, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from muse.task_queue import TaskQueue
    _only(payload, {"request_id", "title", "goal", "request_kind", "source_ref", "confirmation"})
    _confirm(payload, "SUBMIT-TO-COMMANDER")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("request_id must be a 32-character lowercase hex string")
    title = _text(payload, "title", 160)
    goal = _text(payload, "goal", 4000)
    request_kind = payload.get("request_kind", "research")
    if request_kind not in {"research", "creative", "engineering", "operations"}:
        raise ValueError("request_kind must be research, creative, engineering or operations")
    source_ref = _text(payload, "source_ref", 200, required=False)
    identity = {
        "goal": goal, "source": "muse_os_task_center", "request_kind": request_kind,
        "approval_required": True, "execution_authorized": False,
    }
    if source_ref:
        identity["source_ref"] = source_ref
    key = "control-submit:" + request_id
    workload = {"research": "research", "creative": "media", "engineering": "coding", "operations": "operations"}[request_kind]
    def _same(task: dict[str, Any]) -> bool:
        existing_payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        return task.get("title") == title and all(existing_payload.get(k) == v for k, v in identity.items())

    def _replay(task: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not _same(task):
            raise PermissionError("request_id was already used for a different request")
        return 200, {"task": _public_task(task), "replayed": True, "execution": "not_started"}

    with _SUBMIT_LOCK, TaskQueue(ctx.task_path) as queue:
        # B391-P2-05: check-then-insert is serialised in-process; the IntegrityError branch below covers
        # a second process racing on the same UNIQUE idempotency key.
        existing = queue.get_by_idempotency_key(key)
        if existing is not None:
            return _replay(existing)
        try:
            task = queue.enqueue(
                title, identity, recovery_policy="manual", idempotency_key=key,
                require_approval=True, workload_class=workload,
            )
        except sqlite3.IntegrityError:
            # B391-P2-05: a concurrent submit won the UNIQUE(idempotency_key) race.
            winner = queue.get_by_idempotency_key(key)
            if winner is None:
                raise
            return _replay(winner)
        if not _same(task):
            # enqueue() returns the existing row when the key appeared between our check and insert.
            raise PermissionError("request_id was already used for a different request")
    return 201, {"task": _public_task(task), "replayed": False, "execution": "not_started",
                 "next_action": "explicit_human_approval_required"}


def cancel_task(ctx: ControlContext, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from muse.task_queue import TaskQueue
    _only(payload, {"confirmation", "reason"})
    _confirm(payload, "CANCEL-TASK")
    reason = _text(payload, "reason", 300, required=False)
    with TaskQueue(ctx.task_path) as queue:
        if queue.get(task_id) is None:
            raise ControlNotFound("task not found")
        task = queue.cancel(task_id, cancelled_by=APPROVER, reason=reason or "cancelled from Muse OS")
    return {"task": _public_task(task)}


def retry_task(ctx: ControlContext, task_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from muse.task_queue import TaskQueue
    _only(payload, {"confirmation", "request_id"})
    _confirm(payload, "RETRY-TASK")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("request_id must be a 32-character lowercase hex string")
    with _SUBMIT_LOCK, TaskQueue(ctx.task_path) as queue:
        original = queue.get(task_id)
        if original is None:
            raise ControlNotFound("task not found")
        if original.get("status") not in {"failed", "cancelled"}:
            raise PermissionError("only failed or cancelled Tickets can be retried")
        original_payload = original.get("payload") if isinstance(original.get("payload"), dict) else {}
        if str(original_payload.get("kind") or "") in _DEDICATED_GATES:
            raise PermissionError("this Ticket kind must be re-created through its dedicated workflow")
        if any(str(k) in _DEDICATED_PAYLOAD_MARKERS for k in original_payload):
            # B391-P2-08: signed-review / contextual-inquiry tickets must not lose their gate on retry.
            raise PermissionError("this Ticket carries a dedicated review gate; re-create it through its dedicated workflow")
        clean = {k: v for k, v in original_payload.items() if not str(k).startswith("_")}
        clean.update({"retry_of": task_id, "approval_required": True, "execution_authorized": False})
        key = "control-retry:" + request_id
        existing = queue.get_by_idempotency_key(key)
        if existing is not None:
            existing_payload = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
            if existing_payload.get("retry_of") != task_id:
                # B391-P2-06: never silently replay a retry that belongs to a different Ticket.
                raise PermissionError("request_id was already used to retry a different Ticket")
            return 200, {"task": _public_task(existing), "replayed": True}
        workload = original.get("workload_class")
        if workload not in {"general", "research", "coding", "media", "writing", "operations"}:
            workload = "general"
        task = queue.enqueue(
            str(original.get("title") or "retry")[:160], clean, recovery_policy="manual",
            idempotency_key=key, require_approval=True, workload_class=workload,
        )
        queue.record_event(task_id, "control.retry_requested", actor=APPROVER,
                           payload={"retry_task_id": task["id"]})
    return 201, {"task": _public_task(task), "replayed": False, "execution": "not_started"}


# ───────────────────────────── approval center ─────────────────────────────

def list_approvals(ctx: ControlContext) -> dict[str, Any]:
    from muse.task_queue import TaskQueue
    items: list[dict[str, Any]] = []
    with TaskQueue(ctx.task_path) as queue:
        for task in queue.list_tasks(limit=200):
            if task.get("status") != "waiting_approval":
                continue
            public = _public_task(task)
            if public["signed_review_pending"]:
                channel, endpoint = "signed_review", "/api/v1/signed-reviews/<id>/decide"
            elif public["dedicated_gate"]:
                channel, endpoint = "dedicated_gate", public["dedicated_gate"]
            else:
                channel, endpoint = "commander_ticket", "/api/v1/control/approvals/<id>/decide"
            items.append({**public, "approval_channel": channel, "decide_endpoint": endpoint,
                          "can_decide_here": channel == "commander_ticket"})
    memory_candidates: list[dict[str, Any]] = []
    if ctx.memory_path is not None and ctx.memory_path.is_file():
        from muse.memory import MemoryStore
        with MemoryStore(ctx.memory_path) as memory:
            for candidate in memory.list_pending(limit=50):
                memory_candidates.append({
                    key: candidate.get(key) for key in (
                        "id", "content", "project", "source", "confidence", "kind", "track", "created_at",
                    )
                })
    proposals: list[dict[str, Any]] = []
    if ctx.improvement_path is not None and ctx.improvement_path.is_file():
        try:
            from muse.improvement import ImprovementStore
            with ImprovementStore(ctx.improvement_path) as store:
                listing = store.list_proposals(status="ready_for_review")
            for proposal in listing[:200]:
                if isinstance(proposal, dict):
                    proposals.append({key: proposal.get(key) for key in (
                        "id", "title", "status", "priority", "updated_at")})
        except Exception as exc:  # optional source; never hide that it failed
            proposals.append({"status": "unavailable", "reason": type(exc).__name__})
    return {
        "commander_tickets": items,
        "memory_candidates": memory_candidates,
        "improvement_proposals": proposals,
        "approver_scope": APPROVER_SCOPE,
        "policy": "Human decisions are persisted as human.decision events; approval never starts execution by itself.",
    }


def decide_ticket(ctx: ControlContext, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from muse.task_queue import TaskQueue
    _only(payload, {"decision", "confirmation", "reason"})
    decision = payload.get("decision")
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    _confirm(payload, "APPROVE-TICKET" if decision == "approve" else "REJECT-TICKET")
    reason = _text(payload, "reason", 300, required=False)
    with TaskQueue(ctx.task_path) as queue:
        task = queue.get(task_id)
        if task is None:
            raise ControlNotFound("task not found")
        public = _public_task(task)
        if task.get("status") != "waiting_approval":
            raise PermissionError("task is not waiting for approval")
        if public["dedicated_gate"] or public["signed_review_pending"]:
            raise PermissionError("this Ticket must be decided through its dedicated gate: "
                                  + str(public["dedicated_gate"] or "/api/v1/signed-reviews/<id>/decide"))
        if decision == "approve":
            updated = queue.approve(task_id, approved_by=APPROVER)
        else:
            updated = queue.cancel(task_id, cancelled_by=APPROVER, reason=reason or "rejected in Muse OS approval center")
            queue.record_event(task_id, "human.decision", actor=APPROVER,
                               payload={"decision": "rejected", "reason": reason[:300]})
    return {"task": _public_task(updated), "decision": decision, "approver_scope": APPROVER_SCOPE,
            "execution": "not_started"}


# ───────────────────────────── dual-track memory ─────────────────────────────

def _memory_store(ctx: ControlContext):
    from muse.memory import MemoryStore
    if ctx.memory_path is None:
        raise ControlNotFound("memory store is not configured")
    ctx.memory_path.parent.mkdir(parents=True, exist_ok=True)
    return MemoryStore(ctx.memory_path)


def list_memory(ctx: ControlContext, query: dict[str, str]) -> dict[str, Any]:
    track = query.get("track", "user")
    if track not in {"user", "agent"}:
        raise ValueError("track must be user or agent")
    limit = _limit(query.get("limit"), 50, 200)
    text = query.get("q", "").strip()
    if len(text) > 200:
        raise ValueError("q must be at most 200 characters")
    with _memory_store(ctx) as memory:
        rows = memory.search(text, track=track, limit=limit) if text else memory.list_memories(track=track, limit=limit)
        counts = {name: len(memory.list_memories(track=name, limit=200)) for name in ("user", "agent")}
        pending = len(memory.list_pending(limit=200))
    return {
        "track": track, "counts": counts, "pending_candidates": pending, "count": len(rows),
        "memories": rows,
        "track_semantics": {
            "user": "用户事实（User Track）：仅经人工审批写入，作为个人事实证据",
            "agent": "工作经验（Agent Track）：Agent 的执行经验，不得当作用户个人事实",
        },
    }


def memory_history(ctx: ControlContext, memory_id: int) -> dict[str, Any]:
    with _memory_store(ctx) as memory:
        history = memory.history(memory_id)
        current = memory.get(memory_id)
    if not history and current is None:
        raise ControlNotFound("memory not found")
    return {"memory_id": memory_id, "current": current, "deleted": current is None, "revisions": history}


def decide_memory_candidate(ctx: ControlContext, candidate_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    _only(payload, {"decision", "confirmation", "reason"})
    decision = payload.get("decision")
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    _confirm(payload, "APPROVE-MEMORY" if decision == "approve" else "REJECT-MEMORY")
    with _memory_store(ctx) as memory:
        if memory.get_candidate(candidate_id) is None:
            raise ControlNotFound("memory candidate not found")
        if decision == "approve":
            result = memory.approve_candidate(candidate_id, approved_by=APPROVER)
        else:
            result = memory.reject_candidate(candidate_id, rejected_by=APPROVER,
                                             reason=_text(payload, "reason", 300, required=False) or "rejected in Muse OS")
    return {"candidate": result, "decision": decision, "approver_scope": APPROVER_SCOPE}


def obsidian_status(ctx: ControlContext) -> dict[str, Any]:
    vault = ctx.vault_path
    if vault is None or not vault.is_dir():
        return {"status": "not_configured", "vault_path_present": False,
                "policy": "SQLite is authoritative; the Obsidian Vault is a one-way read-only mirror."}
    try:
        from muse.obsidian_vault import audit_vault
        with _memory_store(ctx) as memory:
            audit = audit_vault(memory, vault)
    except Exception as exc:
        return {"status": "audit_failed", "vault_path_present": True, "error_type": type(exc).__name__}
    # B391-P2-12: audit_vault() returns its counters at the top level (there is no "counts" key).
    counter_keys = ("unchanged", "legacy_metadata", "modified", "missing", "conflicts", "orphaned_managed",
                    "unmanaged_notes_ignored")
    counts = {key: audit.get(key) for key in counter_keys if isinstance(audit, dict) and isinstance(audit.get(key), int)}
    if isinstance(audit, dict) and isinstance(audit.get("counts"), dict):
        counts.update({k: v for k, v in audit["counts"].items() if isinstance(v, int)})
    return {"status": "audited", "vault_path_present": True, "counts": counts or None,
            "conflicts": counts.get("conflicts"),
            "policy": "SQLite is authoritative; the Obsidian Vault is a one-way read-only mirror."}


# ───────────────────────────── telemetry & readiness ─────────────────────────────

def telemetry(ctx: ControlContext) -> dict[str, Any]:
    import time
    from muse.resource_governor import probe_host_resources
    snapshot = probe_host_resources()
    try:
        disk = shutil.disk_usage(ctx.workspace_root)
        storage = {"total_bytes": disk.total, "free_bytes": disk.free, "used_bytes": disk.used}
    except OSError as exc:
        storage = {"status": "unknown", "error_type": type(exc).__name__}
    gpu = {"status": "unknown", "reason": "gpu status provider not configured"}
    if ctx.gpu_status_provider is not None:
        try:
            gpu = ctx.gpu_status_provider()
        except Exception as exc:
            gpu = {"status": "unknown", "error_type": type(exc).__name__}
    cpu = snapshot.logical_cpu_count
    return {
        "cpu": {"logical_cpu_count": cpu},
        "memory": {"available_bytes": snapshot.available_memory_bytes, "total_bytes": snapshot.total_memory_bytes},
        "storage": storage,
        "gpu": gpu,
        "concurrency": {
            "hard_limit": 32,
            "cpu_bound_limit": min(32, cpu) if isinstance(cpu, int) else None,
            "note": "实际并发还受可用 RAM、Runner、配额与在线 Agent 约束；未知即未知，不做猜测。",
        },
        "measured_at_unix": int(time.time()),
    }


def creation_readiness(ctx: ControlContext) -> dict[str, Any]:
    """Decide which video path is actually available. File checks only; no model is started."""
    root = ctx.workspace_root
    engines: dict[str, Any] = {}
    if ctx.video_engines_provider is not None:
        try:
            engines = ctx.video_engines_provider() or {}
        except Exception as exc:
            engines = {"error_type": type(exc).__name__}
    gpu: dict[str, Any] = {}
    if ctx.gpu_status_provider is not None:
        try:
            gpu = ctx.gpu_status_provider() or {}
        except Exception as exc:
            gpu = {"status": "unknown", "error_type": type(exc).__name__}
    from muse.media import discover_media_tools
    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    remotion_ok = (root / "video-motion" / "node_modules" / "@remotion").is_dir() and (root / "video-motion" / "src").is_dir()
    tts_model = (root / "data" / "models" / "canto-tts-nano" / "browser_poc_manifest.json").is_file()
    tts_runtime = (root / ".muse-canto-tts-py314").is_dir()
    asr_model = (root / "models" / "whisper-small-cantonese" / "cts" / "model.bin").is_file()
    sources_dir = root / "data" / "video_sources"
    sources = sorted(p.name for p in sources_dir.iterdir() if p.is_dir()) if sources_dir.is_dir() else []
    local_checks = {
        "ffmpeg": bool(tools.get("ffmpeg")), "ffprobe": bool(tools.get("ffprobe")),
        "remotion_installed": remotion_ok, "canto_tts_model_files": tts_model,
        "canto_tts_py314_packages": tts_runtime, "cantonese_asr_model_files": asr_model,
        "local_video_sources": bool(sources),
    }
    comfy = engines.get("comfyui") if isinstance(engines.get("comfyui"), dict) else {}
    planner = engines.get("commander_planner") if isinstance(engines.get("commander_planner"), dict) else {}
    ai_checks = {
        "comfyui_ready": comfy.get("status") == "ready",
        "commander_planner_ready": planner.get("status") == "ready",
        "gpu_idle": gpu.get("status") == "idle",
    }
    local_ready = all(local_checks.values())
    ai_ready = all(ai_checks.values()) and local_checks["ffmpeg"] and local_checks["ffprobe"] and remotion_ok
    chosen = "ai_shot_generation" if ai_ready else ("local_motion" if local_ready else "blocked")
    return {
        "chosen_path": chosen,
        "paths": {
            "ai_shot_generation": {
                "label": "AI 镜头生成（Commander 审批 + Video Admission + GPU 租约 + Wan/ComfyUI）",
                "ready": ai_ready, "checks": ai_checks,
                "missing": [name for name, ok in ai_checks.items() if not ok],
            },
            "local_motion": {
                "label": "纯本地动效（CantoTTS 粤语配音 + Whisper ASR + Remotion + FFmpeg）",
                "ready": local_ready, "checks": local_checks,
                "missing": [name for name, ok in local_checks.items() if not ok],
                "video_sources": sources[:50],
            },
        },
        "evidence_level": "file_presence_only",
        "note": "就绪判断只检查文件与服务探测，不启动模型；真实可用性以实际运行的 QA 报告为准。",
    }


# ───────────────────────────── portal summary ─────────────────────────────

def summary(ctx: ControlContext) -> dict[str, Any]:
    from muse.task_queue import TaskQueue
    with TaskQueue(ctx.task_path) as queue:
        counts = queue.snapshot(limit=1)["counts"]
    knowledge: dict[str, Any] = {"status": "not_initialized"}
    if ctx.knowledge_path is not None and ctx.knowledge_path.is_file():
        try:
            from muse.research_knowledge import ResearchKnowledgeStore
            with ResearchKnowledgeStore(ctx.knowledge_path) as store:
                by_type = {
                    row[0]: row[1] for row in store.connection.execute(
                        "SELECT node_type, count(*) FROM knowledge_nodes WHERE deleted_at IS NULL GROUP BY node_type"
                    ).fetchall()
                }
                knowledge = {"status": "available", **store.stats(), "nodes_by_type": by_type}
        except Exception as exc:
            knowledge = {"status": "unavailable", "error_type": type(exc).__name__}
    memory: dict[str, Any] = {"status": "not_initialized"}
    if ctx.memory_path is not None and ctx.memory_path.is_file():
        try:
            from muse.memory import MemoryStore
            with MemoryStore(ctx.memory_path) as store:
                memory = {"status": "available",
                          "user": len(store.list_memories(track="user", limit=200)),
                          "agent": len(store.list_memories(track="agent", limit=200)),
                          "pending_candidates": len(store.list_pending(limit=200))}
        except Exception as exc:
            memory = {"status": "unavailable", "error_type": type(exc).__name__}
    products: list[dict[str, Any]] = []
    if ctx.products_provider is not None:
        try:
            products = ctx.products_provider() or []
        except Exception as _muse_suppressed:
            record_suppressed_error("control_center_api.py:587", _muse_suppressed)
            products = []
    videos = [item for item in products if item.get("kind") == "video_creator" and item.get("video_path")]
    videos.sort(key=lambda item: str(item.get("modified_at") or ""), reverse=True)
    latest_video = videos[0] if videos else None
    loop = []
    stats_by_node = {
        "inspiration": (knowledge.get("nodes_by_type") or {}).get("idea") if isinstance(knowledge, dict) else None,
        "research": (knowledge.get("nodes_by_type") or {}).get("source") if isinstance(knowledge, dict) else None,
        "knowledge": knowledge.get("nodes") if isinstance(knowledge, dict) else None,
        "muse": counts.get("running"),
        "creation": len(videos),
        "verification": counts.get("waiting_approval"),
        "evolution": memory.get("agent") if isinstance(memory, dict) else None,
    }
    targets = {"inspiration": "knowledge", "research": "knowledge", "knowledge": "knowledge", "muse": "overview",
               "creation": "tasks", "verification": "tasks", "evolution": "memory"}
    for key, label in WORKFLOW_LOOP:
        loop.append({"id": key, "label": label, "value": stats_by_node.get(key), "os_view": targets[key]})
    return {
        "queue": counts, "knowledge": knowledge, "memory": memory,
        "products": {"count": len(products), "videos": len(videos), "latest_video": latest_video},
        "workflow_loop": loop,
    }


# ───────────────────────────── xuanshu → wiki → commander ─────────────────────────────

def xuanshu_hypotheses(ctx: ControlContext, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from muse.metaphysics_label import symbolic_to_hypotheses, write_hypotheses_to_wiki
    from muse.xuanshu_fusion import fuse
    from muse.xuanshu_symbolic_fallback import today_in_shanghai
    _only(payload, {"question", "seed", "write_to_wiki", "confirmation"})
    question = _text(payload, "question", 500)
    seed = _text(payload, "seed", 200)
    write = payload.get("write_to_wiki", False)
    if not isinstance(write, bool):
        raise ValueError("write_to_wiki must be a boolean")
    fused = fuse(question, seed=seed, symbolic_fallback=True, moment=today_in_shanghai())
    hypotheses = symbolic_to_hypotheses(fused)
    body: dict[str, Any] = {"fusion": fused, "hypotheses": hypotheses, "written": None}
    if write:
        _confirm(payload, "WRITE-HYPOTHESES")
        if ctx.knowledge_path is None:
            raise ControlNotFound("Research Wiki store is not configured")
        # T2: the Wiki write runs inside a Muse Commander ticket (receipt + cost ledger + review).
        import hashlib as _hashlib
        from muse.commander_routes import HANDLER_XUANSHU, submit_and_wait
        routed = submit_and_wait(
            ctx.workspace_root, handler=HANDLER_XUANSHU,
            route_args={"knowledge_path": str(ctx.knowledge_path), "hypotheses": hypotheses},
            title="Write symbolic hypotheses to Research Wiki", objective="写入待验证象征假设",
            request_key=_hashlib.sha256(json.dumps(hypotheses, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        )
        body["commander"] = {key: routed.get(key) for key in ("ticket_id", "status", "risk", "receipt_sha256", "gate")}
        if routed["status"] != "succeeded":
            raise RuntimeError(f"commander ticket {routed['status']}")
        body["written"] = routed["output"]
        return 201, body
    return 200, body


def xuanshu_dispatch(ctx: ControlContext, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from muse.metaphysics_label import METAPHYSICS_DISPLAY_LABEL
    _only(payload, {"claim_id", "target", "request_id", "confirmation"})
    claim_id = _text(payload, "claim_id", 128)
    target = payload.get("target")
    if target not in {"research", "video"}:
        raise ValueError("target must be research or video")
    if ctx.knowledge_path is None or not ctx.knowledge_path.is_file():
        raise ControlNotFound("Research Wiki store is not configured")
    from muse.research_knowledge import ResearchKnowledgeStore
    with ResearchKnowledgeStore(ctx.knowledge_path) as store:
        node = store.get_node(claim_id)
    if node is None or node.get("node_type") != "claim":
        raise ControlNotFound("claim not found")
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    if metadata.get("epistemic_display_label") != METAPHYSICS_DISPLAY_LABEL:
        raise PermissionError("only labelled symbolic hypotheses can be dispatched from the Xuanshu lab")
    def _sentence(value: Any) -> str:
        return str(value or "").strip().rstrip("。.")
    goal = (f"【{METAPHYSICS_DISPLAY_LABEL}】为以下待验证假设设计并执行"
            f"{'公开来源研究（需含反证检索）' if target == 'research' else '一条样片验证实验（走正规视频审批链路）'}："
            f"{_sentence(node.get('title'))}。检验方法：{_sentence(metadata.get('test'))}。"
            f"替代解释：{_sentence(metadata.get('alternative_explanation'))}。"
            "不得把象征推演当作证据。")
    return submit_task(ctx, {
        "request_id": payload.get("request_id"), "title": f"验证假设：{str(node.get('title'))[:120]}",
        "goal": goal[:4000], "request_kind": "research" if target == "research" else "creative",
        "source_ref": f"research-wiki:{claim_id}", "confirmation": payload.get("confirmation"),
    })


# ───────────────────────────── creator feedback → Research Wiki tested_by (B391-P2-10) ─────────────────────────────

_VERDICTS = {"supports": "支持假设", "refutes": "反驳假设", "inconclusive": "无法判断"}
_CLAIM_ROUTE_ID = r"[A-Za-z0-9_-]{1,128}"
TESTED_BY_POLICY = ("实测反馈只追加 tested_by 回链与反馈记录；不会自动改写 claim 状态，也不会改写玄学推演本体结论。"
                    "单条创作者反馈不是独立证据，状态变更仍需带证据的人工审查。")


def _knowledge_store(ctx: ControlContext, *, must_exist: bool = True):
    from muse.research_knowledge import ResearchKnowledgeStore
    if ctx.knowledge_path is None or (must_exist and not ctx.knowledge_path.is_file()):
        raise ControlNotFound("Research Wiki store is not configured")
    return ResearchKnowledgeStore(ctx.knowledge_path)


def _feedback_record_view(node: dict[str, Any]) -> dict[str, Any]:
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return {
        "id": node.get("id"), "title": node.get("title"), "node_type": node.get("node_type"),
        "record_kind": meta.get("record_kind") or meta.get("origin") or node.get("node_type"),
        "recorded_at": meta.get("recorded_at") or node.get("created_at"),
        "video_sha256": meta.get("video_sha256"), "creator_verdict": meta.get("creator_verdict"),
        "ledger_decision": meta.get("ledger_decision"), "feedback": node.get("summary") or "",
        "product_initiative_id": meta.get("product_initiative_id"), "product_version": meta.get("product_version"),
        "empirical_observation": bool(meta.get("empirical_observation", False)),
        "independent_evidence": bool(meta.get("independent_evidence", False)),
    }


def list_claims(ctx: ControlContext, query: dict[str, str]) -> dict[str, Any]:
    from muse.metaphysics_label import METAPHYSICS_DISPLAY_LABEL
    limit = _limit(query.get("limit"), 100, 200)
    if ctx.knowledge_path is None or not ctx.knowledge_path.is_file():
        return {"configured": False, "count": 0, "claims": []}
    with _knowledge_store(ctx) as store:
        claims = store.list_nodes(node_type="claim", limit=limit)
        tested = store.list_edges(edge_type="tested_by", limit=500)
    counts: dict[str, int] = {}
    for edge in tested:
        counts[edge["source_id"]] = counts.get(edge["source_id"], 0) + 1
    items = []
    for claim in claims:
        meta = claim.get("metadata") if isinstance(claim.get("metadata"), dict) else {}
        items.append({
            "id": claim.get("id"), "title": str(claim.get("title") or "")[:300], "status": claim.get("status"),
            "symbolic_hypothesis": meta.get("epistemic_display_label") == METAPHYSICS_DISPLAY_LABEL,
            "epistemic_display_label": meta.get("epistemic_display_label"),
            "tested_by_count": counts.get(claim.get("id"), 0),
        })
    return {"configured": True, "count": len(items), "claims": items}


def claim_tested_by(ctx: ControlContext, claim_id: str) -> dict[str, Any]:
    from muse.metaphysics_label import METAPHYSICS_DISPLAY_LABEL
    with _knowledge_store(ctx) as store:
        claim = store.get_node(claim_id)
        if claim is None or claim.get("node_type") != "claim":
            raise ControlNotFound("claim not found")
        edges = store.list_edges(node_id=claim_id, limit=500)
        targets = {}
        for edge in edges:
            other = edge["target_id"] if edge["source_id"] == claim_id else edge["source_id"]
            if other not in targets:
                targets[other] = store.get_node(other)
    meta = claim.get("metadata") if isinstance(claim.get("metadata"), dict) else {}
    symbolic = meta.get("epistemic_display_label") == METAPHYSICS_DISPLAY_LABEL
    inspirations = [
        {"id": e["target_id"] if e["source_id"] == claim_id else e["source_id"],
         "title": (targets.get(e["target_id"] if e["source_id"] == claim_id else e["source_id"]) or {}).get("title")}
        for e in edges if e["edge_type"] == "inspired_by"
    ]
    records = []
    for edge in edges:
        if edge["edge_type"] != "tested_by" or edge["source_id"] != claim_id:
            continue
        node = targets.get(edge["target_id"])
        if node:
            view = _feedback_record_view(node)
            view["edge_id"] = edge["id"]
            view["linked_at"] = edge.get("created_at")
            records.append(view)
    records.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
    return {
        "claim_id": claim_id, "claim_title": claim.get("title"), "claim_status": claim.get("status"),
        "origin": {
            "kind": "symbolic_hypothesis" if symbolic else (meta.get("origin") or "research_claim"),
            "epistemic_display_label": meta.get("epistemic_display_label"),
            "is_evidence": False if symbolic else bool(meta.get("is_evidence", False)),
            "is_prediction": False if symbolic else bool(meta.get("is_prediction", False)),
            "test": meta.get("test"), "alternative_explanation": meta.get("alternative_explanation"),
            "inspired_by": inspirations, "created_at": claim.get("created_at"),
        },
        "feedback_records": records,
        "tested_by_count": len(records),
        "policy": TESTED_BY_POLICY,
    }


def creation_feedback(ctx: ControlContext, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    with _SUBMIT_LOCK:
        return _creation_feedback(ctx, payload)


def _creation_feedback(ctx: ControlContext, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Creator feedback on a vault video → creator ledger + Research Wiki ``claim --tested_by--> record``.

    Idempotent per ``request_id`` on both stores; a reused request_id with a different payload is 409.
    The video SHA-256 is computed from the Product Vault by the server, never accepted from the client.
    """
    _only(payload, {"initiative_id", "version", "claim_id", "verdict", "decision", "feedback",
                    "request_id", "confirmation"})
    _confirm(payload, "LINK-FEEDBACK-TO-CLAIM")
    initiative_id, version, request_id = (payload.get(k) for k in ("initiative_id", "version", "request_id"))
    if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
        raise ValueError("initiative_id must be a 32-character lowercase hex product initiative ID")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{20}", version):
        raise ValueError("version must be a 20-character lowercase hex product version ID")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("request_id must be a 32-character lowercase hex string")
    claim_id = _text(payload, "claim_id", 128)
    verdict = payload.get("verdict")
    if verdict not in _VERDICTS:
        raise ValueError("verdict must be supports, refutes or inconclusive")
    decision = payload.get("decision")
    if decision not in {"accept", "revise", "reject"}:
        raise ValueError("decision must be accept, revise or reject")
    feedback = _text(payload, "feedback", 2000)
    if "\x00" in feedback:
        raise ValueError("feedback must not contain NUL characters")
    if ctx.video_product_resolver is None or ctx.feedback_ledger_writer is None:
        raise ControlNotFound("Product Vault or creator ledger is not configured")
    with _knowledge_store(ctx) as store:
        claim = store.get_node(claim_id)
    if claim is None or claim.get("node_type") != "claim":
        raise ControlNotFound("claim not found")
    product = None
    if ctx.products_provider is not None:
        for row in ctx.products_provider() or []:
            if (isinstance(row, dict) and row.get("kind") == "video_creator"
                    and row.get("initiative_id") == initiative_id and row.get("version_id") == version):
                product = row
                break
    if product is None:
        raise ControlNotFound("video product not found")
    creator_initiative_id = product.get("creator_initiative_id")
    if not product.get("creator_feedback_enabled") or not isinstance(creator_initiative_id, str):
        raise PermissionError("this video is not linked to a Creator Initiative, so the creator ledger cannot record feedback")
    try:
        resolved = ctx.video_product_resolver(initiative_id, version)
    except FileNotFoundError:
        raise ControlNotFound("video product not found") from None
    except (ValueError, OSError) as exc:
        raise PermissionError(f"video product failed integrity verification ({type(exc).__name__})") from None
    video_sha256 = resolved.get("video_sha256") if isinstance(resolved, dict) else None
    if not isinstance(video_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", video_sha256):
        raise PermissionError("video product hash is unavailable; feedback was not recorded")
    import hashlib
    from datetime import datetime, timezone
    feedback_sha256 = hashlib.sha256(feedback.encode("utf-8")).hexdigest()
    fingerprint = {"claim_id": claim_id, "creator_verdict": verdict, "ledger_decision": decision,
                   "feedback_sha256": feedback_sha256, "product_initiative_id": initiative_id,
                   "product_version": version, "video_sha256": video_sha256}
    external_ref = "creator-feedback:" + request_id
    with _knowledge_store(ctx) as store:
        row = store.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE natural_key=? AND deleted_at IS NULL",
            ("experiment:" + external_ref,),
        ).fetchone()
        prior = store.get_node(row[0]) if row else None
    if prior is not None:
        prior_meta = prior.get("metadata") if isinstance(prior.get("metadata"), dict) else {}
        if any(prior_meta.get(k) != v for k, v in fingerprint.items()):
            raise PermissionError("request_id was already used for different feedback")
    try:
        ledger = ctx.feedback_ledger_writer(creator_initiative_id, feedback=feedback, decision=decision,
                                            idempotency_key=request_id)
    except KeyError:
        raise ControlNotFound("creator initiative not found") from None
    recorded_at = (prior.get("metadata") or {}).get("recorded_at") if prior else None
    recorded_at = recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    metadata = {
        **fingerprint,
        "origin": "creator_claim_feedback", "record_kind": "empirical_test_feedback",
        "empirical_observation": True, "independent_evidence": False,
        "epistemic_status": "creator_feedback_observation_not_independent_evidence",
        "creator_initiative_id": creator_initiative_id, "ledger_feedback_id": str(ledger.get("id") or ""),
        "request_id": request_id, "recorded_at": recorded_at,
        "claim_status_unchanged": True, "approval_required_for_status_change": True,
    }
    title = f"成片实测反馈：{str(resolved.get('title') or product.get('name') or initiative_id[:8])[:120]}（{_VERDICTS[verdict]}）"
    with _knowledge_store(ctx) as store:
        node = prior or store.upsert_node(
            "experiment", title[:500], summary=feedback, external_ref=external_ref, metadata=metadata,
            provenance=[f"product:{initiative_id}:{version}", f"claim:{claim_id}",
                        f"initiative-feedback:{creator_initiative_id}:{metadata['ledger_feedback_id']}"],
        )
        edge = store.add_edge(claim_id, node["id"], "tested_by", metadata={
            "relationship_status": "executed", "record_kind": "empirical_test_feedback",
            "creator_verdict": verdict, "video_sha256": video_sha256, "claim_status_unchanged": True,
        })
        node = store.get_node(node["id"])
    replayed = prior is not None
    return (200 if replayed else 201), {
        "replayed": replayed,
        "ledger": {"id": ledger.get("id"), "decision": ledger.get("decision", decision),
                   "deduplicated": bool(ledger.get("deduplicated", False)), "initiative_id": creator_initiative_id},
        "feedback_record": node, "edge": edge, "claim_status": claim.get("status"), "policy": TESTED_BY_POLICY,
    }


# ───────────────────────────── router ─────────────────────────────

def max_body_bytes(_path: str) -> int:
    return MAX_BODY_BYTES


def _route_get(ctx: ControlContext, path: str, query: dict[str, str]) -> dict[str, Any]:
    if path == "/api/v1/control/summary":
        return summary(ctx)
    if path == "/api/v1/control/tasks":
        return list_tasks(ctx, query)
    match = re.fullmatch(rf"/api/v1/control/tasks/({_TASK_ID})", path)
    if match:
        return task_detail(ctx, match.group(1))
    if path == "/api/v1/control/approvals":
        return list_approvals(ctx)
    if path == "/api/v1/control/memory":
        return list_memory(ctx, query)
    match = re.fullmatch(r"/api/v1/control/memory/([0-9]{1,12})/history", path)
    if match:
        return memory_history(ctx, int(match.group(1)))
    if path == "/api/v1/control/memory/obsidian":
        return obsidian_status(ctx)
    if path == "/api/v1/control/telemetry":
        return telemetry(ctx)
    if path == "/api/v1/control/creation/readiness":
        return creation_readiness(ctx)
    if path == "/api/v1/control/knowledge/claims":
        return list_claims(ctx, query)
    match = re.fullmatch(rf"/api/v1/control/knowledge/claims/({_CLAIM_ROUTE_ID})/tested-by", path)
    if match:
        return claim_tested_by(ctx, match.group(1))
    raise ControlNotFound("unknown control route")


def _route_post(ctx: ControlContext, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if path == "/api/v1/control/tasks":
        return submit_task(ctx, payload)
    match = re.fullmatch(rf"/api/v1/control/tasks/({_TASK_ID})/(cancel|retry)", path)
    if match:
        if match.group(2) == "cancel":
            return 200, cancel_task(ctx, match.group(1), payload)
        return retry_task(ctx, match.group(1), payload)
    match = re.fullmatch(rf"/api/v1/control/approvals/({_TASK_ID})/decide", path)
    if match:
        return 200, decide_ticket(ctx, match.group(1), payload)
    match = re.fullmatch(r"/api/v1/control/memory/candidates/([0-9]{1,12})/decide", path)
    if match:
        return 200, decide_memory_candidate(ctx, int(match.group(1)), payload)
    if path == "/api/v1/control/xuanshu/hypotheses":
        return xuanshu_hypotheses(ctx, payload)
    if path == "/api/v1/control/xuanshu/dispatch":
        return xuanshu_dispatch(ctx, payload)
    if path == "/api/v1/control/creation/feedback":
        return creation_feedback(ctx, payload)
    raise ControlNotFound("unknown control route")


def handle_control(method: str, path: str, query: str, body: bytes, ctx: ControlContext) -> tuple[int, dict[str, Any]]:
    """Return ``(http_status, json_body)``; never raises. Error texts are bounded."""
    try:
        if method == "GET":
            return 200, {"api_version": 1, **_route_get(ctx, path, _query(query))}
        if method == "POST":
            if len(body) > MAX_BODY_BYTES:
                return 413, {"error": "request body too large"}
            try:
                payload = parse_json_object(body)
            except ValueError as exc:
                return 400, {"error": str(exc)[:300]}
            status, result = _route_post(ctx, path, payload)
            return status, {"api_version": 1, **result}
        return 405, {"error": "method not allowed"}
    except ControlNotFound as exc:
        return 404, {"error": str(exc)[:300]}
    except PermissionError as exc:
        return 409, {"error": str(exc)[:300]}
    except (ValueError, TypeError, KeyError) as exc:
        return 400, {"error": str(exc)[:300]}
    except sqlite3.Error as exc:
        return 503, {"error": f"control store unavailable ({type(exc).__name__})"}
    except Exception as exc:
        return 503, {"error": f"control operation unavailable ({type(exc).__name__})"}


def _json_depth_ok(raw: str, limit: int) -> bool:
    """Bound container nesting before json.loads (strings are skipped, escapes honoured)."""
    depth = 0
    in_string = False
    escaped = False
    for ch in raw:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > limit:
                return False
        elif ch in "]}":
            depth -= 1
    return True


def _reject_non_finite(token: str) -> Any:
    raise ValueError("request body must not contain non-finite numbers (NaN/Infinity)")


def _finite_float(token: str) -> float:
    value = float(token)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("request body must not contain non-finite numbers (NaN/Infinity)")
    return value


def parse_json_object(body: bytes) -> dict[str, Any]:
    """B391-P2-01..03: UTF-8 JSON object, no duplicate keys, no NaN/Infinity, bounded nesting."""
    if not body:
        return {}
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("request body must be UTF-8 JSON without duplicate keys") from None
    if text.startswith("\ufeff"):
        raise ValueError("request body must not start with a byte-order mark")
    if not _json_depth_ok(text, MAX_JSON_DEPTH):
        raise ValueError(f"request body exceeds the maximum JSON nesting depth of {MAX_JSON_DEPTH}")
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_non_finite,
                             parse_float=_finite_float)
    except ValueError as exc:
        message = str(exc)
        if "non-finite" in message:
            raise ValueError(message) from None
        raise ValueError("request body must be UTF-8 JSON without duplicate keys") from None
    except RecursionError:
        raise ValueError(f"request body exceeds the maximum JSON nesting depth of {MAX_JSON_DEPTH}") from None
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
