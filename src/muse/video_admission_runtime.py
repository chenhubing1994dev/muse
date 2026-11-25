"""Production composition root for Commander-owned local video admission.

This module only binds existing trusted registries and gates. It never creates
capability records, role grants, approvals, or cost defaults.
"""
from __future__ import annotations

import os
import re
import time
from contextlib import ExitStack
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any, Mapping


class VideoAdmissionRuntimeUnavailable(RuntimeError):
    """The existing local registries needed by video admission are unavailable."""


def _existing_database(value: str | Path, *, label: str, workspace: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = workspace / path
    if path.is_symlink() or not path.is_file():
        raise VideoAdmissionRuntimeUnavailable(
            f"{label} must be an existing regular non-symlink database"
        )
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise VideoAdmissionRuntimeUnavailable(f"{label} path cannot be resolved") from exc


def _positive_decimal_env(name: str, *, maximum: Decimal) -> Decimal:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise VideoAdmissionRuntimeUnavailable(f"{name} is not configured")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise VideoAdmissionRuntimeUnavailable(f"{name} must be a finite positive decimal") from None
    if not value.is_finite() or value <= 0 or value > maximum:
        raise VideoAdmissionRuntimeUnavailable(f"{name} is outside its safe positive range")
    return value


def _load_cost_policy() -> dict[str, Any]:
    """Require operator-supplied conservative whole-system power and electricity limits."""
    power_watts = _positive_decimal_env(
        "MUSE_VIDEO_SYSTEM_POWER_WATTS", maximum=Decimal("10000")
    )
    electricity_rate = _positive_decimal_env(
        "MUSE_ELECTRICITY_USD_PER_KWH", maximum=Decimal("1000")
    )
    cost_cap = _positive_decimal_env(
        "MUSE_VIDEO_COST_CAP_USD", maximum=Decimal("100000")
    )
    raw_runtime = os.environ.get("MUSE_VIDEO_MAX_RUNTIME_SECONDS", "").strip()
    if not re.fullmatch(r"[0-9]{4,5}", raw_runtime):
        raise VideoAdmissionRuntimeUnavailable(
            "MUSE_VIDEO_MAX_RUNTIME_SECONDS must be an integer from 1800 to 86400"
        )
    max_runtime_seconds = int(raw_runtime)
    # The current Worker/Builder timeout is 1800 seconds. A smaller configured
    # bound would understate the worst-case energy cost and is therefore rejected.
    if not 1800 <= max_runtime_seconds <= 86400:
        raise VideoAdmissionRuntimeUnavailable(
            "MUSE_VIDEO_MAX_RUNTIME_SECONDS must be at least the current 1800-second Worker timeout"
        )
    return {
        "system_power_watts": power_watts,
        "electricity_usd_per_kwh": electricity_rate,
        "cost_cap_usd": cost_cap,
        "max_runtime_seconds": max_runtime_seconds,
    }


def _cost_gate(request: Any) -> dict[str, Any]:
    try:
        policy = _load_cost_policy()
        context = request.cost_context
        if not isinstance(context, Mapping) or context.get("execution_mode") != "local_gpu":
            raise VideoAdmissionRuntimeUnavailable("only explicitly local-GPU execution is cost-admissible")
        generation = context.get("requested_generation")
        video = generation.get("video") if isinstance(generation, Mapping) else None
        if not isinstance(video, Mapping):
            raise VideoAdmissionRuntimeUnavailable("requested video settings are missing from the cost context")
        duration = Decimal(str(video.get("duration_seconds")))
        steps = video.get("steps")
        if (not duration.is_finite() or duration <= 0
                or isinstance(steps, bool) or not isinstance(steps, int) or steps < 1):
            raise VideoAdmissionRuntimeUnavailable("requested video duration or step count is invalid")
        energy_kwh = (
            policy["system_power_watts"] * Decimal(policy["max_runtime_seconds"])
            / Decimal("3600000")
        )
        estimated_cost = energy_kwh * policy["electricity_usd_per_kwh"]
        if estimated_cost > policy["cost_cap_usd"]:
            return {
                "status": "blocked",
                "reason": "conservative worst-case electricity estimate exceeds the per-attempt cost cap",
            }
        quantum = Decimal("0.000001")
        evidence = {
            "currency": "USD",
            "cost_scope": "configured whole-system electricity estimate only",
            "estimate_basis": "configured maximum system watts × configured maximum runtime",
            "system_power_watts": str(policy["system_power_watts"]),
            "electricity_usd_per_kwh": str(policy["electricity_usd_per_kwh"]),
            "max_runtime_seconds": policy["max_runtime_seconds"],
            "estimated_energy_kwh": format(energy_kwh.quantize(quantum, rounding=ROUND_CEILING), "f"),
            "estimated_cost_usd": format(estimated_cost.quantize(quantum, rounding=ROUND_CEILING), "f"),
            "cost_cap_usd": str(policy["cost_cap_usd"]),
            "requested_video_duration_seconds": str(duration),
            "requested_steps": steps,
        }
        return {"status": "approved", "evidence": evidence}
    except (AttributeError, TypeError, ValueError, InvalidOperation, VideoAdmissionRuntimeUnavailable) as exc:
        return {"status": "blocked", "reason": str(exc)[:300] or type(exc).__name__}


def _human_gate(task_database: Path, request: Any) -> dict[str, Any]:
    from muse.task_queue import TaskQueue

    if task_database.is_symlink() or not task_database.is_file():
        return {"status": "blocked", "reason": "Commander TaskQueue database is unavailable"}
    try:
        with TaskQueue(task_database) as queue:
            ticket = queue.get(request.approval_task_id)
            if ticket is None or ticket.get("status") != "queued":
                return {"status": "blocked", "reason": "exact Commander approval ticket is not queued"}
            payload = ticket.get("payload")
            approval = payload.get("_approval") if isinstance(payload, dict) else None
            plan = getattr(request, "plan", None)
            plan_id = getattr(plan, "plan_id", None)
            plan_source = getattr(plan, "source", None)
            if (
                not isinstance(payload, dict)
                or payload.get("kind") not in {
                    "business_opportunity_video_approval", "creator_initiative_video_approval",
                }
                or payload.get("attempt_id") != request.attempt_id
                or payload.get("approval_scope") != "one_video_attempt_only"
                or payload.get("_approved") is not True
                or payload.get("commander_plan_id") != plan_id
                or payload.get("commander_plan_source") != plan_source
                or plan_source != "model"
                or not isinstance(approval, dict)
                or approval.get("approved_by") != request.approved_by
            ):
                return {"status": "blocked", "reason": "approval ticket does not bind this exact video attempt"}
            decisions = [
                event for event in queue.events(request.approval_task_id, limit=500)
                if event.get("event_type") == "human.decision"
            ]
            if not decisions:
                return {"status": "blocked", "reason": "persisted human decision is missing"}
            latest = decisions[-1]
            if (
                latest.get("event_id") != request.approval_event_id
                or latest.get("actor") != request.approved_by
                or latest.get("payload") != {"decision": "approved"}
                or not TaskQueue.approval_timestamp_matches_event(
                    approval.get("approved_at"), latest.get("created_at")
                )
            ):
                return {"status": "blocked", "reason": "latest persisted human decision does not match this grant"}
            return {
                "status": "approved",
                "approval_task_id": request.approval_task_id,
                "approval_event_id": request.approval_event_id,
                "approved_by": request.approved_by,
            }
    except Exception as exc:
        return {"status": "blocked", "reason": f"Commander human approval lookup failed ({type(exc).__name__})"}


def _resource_gate(resource_database: Path, request: Any) -> dict[str, Any]:
    from muse.gpu_resources import GpuResourceCoordinator
    from muse.video_engines import ComfyUIClient
    from muse.wan_video import _require_gpu_headroom

    context = request.resource_context
    lease_context = context.get("gpu_lease") if isinstance(context, Mapping) else None
    if (
        not isinstance(lease_context, Mapping)
        or lease_context.get("status") != "active"
        or not isinstance(lease_context.get("lease_id"), str)
        or not isinstance(lease_context.get("owner_id"), str)
        or not isinstance(lease_context.get("coordination"), Mapping)
        or not lease_context.get("coordination")
    ):
        return {"status": "blocked", "reason": "live lease-bound GPU coordination evidence is missing"}
    if resource_database.is_symlink() or not resource_database.is_file():
        return {"status": "blocked", "reason": "GPU resource lease database is unavailable"}
    try:
        coordinator = GpuResourceCoordinator(resource_database)
        active = coordinator.active_lease()
        now = time.time()
        if (
            not isinstance(active, Mapping)
            or active.get("lease_id") != lease_context["lease_id"]
            or active.get("owner_id") != lease_context["owner_id"]
            or active.get("owner_kind") != "video_generation"
        ):
            return {"status": "blocked", "reason": "live GPU lease identity does not match the Worker lease"}
        heartbeat_at = active.get("heartbeat_at")
        expires_at = active.get("expires_at")
        if (
            isinstance(heartbeat_at, bool) or not isinstance(heartbeat_at, (int, float))
            or isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
            or expires_at <= now or now - heartbeat_at > 90
        ):
            return {"status": "blocked", "reason": "GPU lease heartbeat is stale or expired"}
        from muse.gpu_resources import SUPERVISOR_STALE_SECONDS
        supervisor = coordinator.supervisor_state()
        coordination_mode = lease_context["coordination"].get("mode")
        supervisor_evidence: dict[str, Any]
        if supervisor is None:
            if coordination_mode != "model_offline_supervisor_not_running":
                return {"status": "blocked", "reason": "GPU coordination mode does not match an offline supervisor"}
            supervisor_evidence = {"status": "offline", "coordination_mode": coordination_mode}
        else:
            supervisor_heartbeat = supervisor.get("heartbeat_at")
            if (
                isinstance(supervisor_heartbeat, bool)
                or not isinstance(supervisor_heartbeat, (int, float))
            ):
                return {"status": "blocked", "reason": "GPU model supervisor heartbeat is invalid"}
            supervisor_age = max(0, int(now - supervisor_heartbeat))
            if supervisor_age > SUPERVISOR_STALE_SECONDS:
                if coordination_mode != "model_offline_supervisor_not_running":
                    return {"status": "blocked", "reason": "stale GPU supervisor does not match the lease coordination mode"}
                supervisor_evidence = {
                    "status": "offline",
                    "stale_state_ignored": True,
                    "heartbeat_age_seconds": supervisor_age,
                    "coordination_mode": coordination_mode,
                }
            else:
                if (
                    coordination_mode != "supervisor_paused_owned_model"
                    or supervisor.get("action") != "paused_for_video"
                    or supervisor.get("acknowledged_lease_id") != lease_context["lease_id"]
                ):
                    return {"status": "blocked", "reason": "fresh GPU supervisor has not acknowledged this exact video lease"}
                supervisor_evidence = {
                    "status": "fresh",
                    "action": supervisor.get("action"),
                    "acknowledged_lease_id": supervisor.get("acknowledged_lease_id"),
                    "heartbeat_age_seconds": supervisor_age,
                    "coordination_mode": coordination_mode,
                }
        client = ComfyUIClient(timeout_seconds=3.0)
        memory = _require_gpu_headroom(client)
        health = client.health()
        if (
            not isinstance(health, Mapping)
            or health.get("status") != "ready"
            or isinstance(health.get("running_jobs"), bool)
            or not isinstance(health.get("running_jobs"), int)
            or isinstance(health.get("pending_jobs"), bool)
            or not isinstance(health.get("pending_jobs"), int)
            or health.get("running_jobs") != 0
            or health.get("pending_jobs") != 0
        ):
            return {"status": "blocked", "reason": "ComfyUI health or queue state is not safely idle"}
        evidence = {
            "gpu_lease_status": "active",
            "gpu_lease_id": lease_context["lease_id"],
            "gpu_lease_owner_id": lease_context["owner_id"],
            "gpu_lease_coordination": dict(lease_context["coordination"]),
            "gpu_memory": dict(memory),
            "comfyui_queue": {
                "running_jobs": health["running_jobs"],
                "pending_jobs": health["pending_jobs"],
                "device_count": health.get("device_count"),
            },
            "model_supervisor": supervisor_evidence,
        }
        return {"status": "approved", "evidence": evidence}
    except Exception as exc:
        return {"status": "blocked", "reason": f"live GPU resource check failed ({type(exc).__name__})"}


class VideoAdmissionRuntime:
    """Own the registry connections and expose a fail-closed Commander provider."""

    def __init__(
        self, stack: ExitStack, provider: Any, request_factory: Any,
        task_database: Path, capability_registry: Any, role_registry: Any,
        handler_registry: Mapping[str, Any],
    ) -> None:
        self._stack = stack
        self.provider = provider
        self.request_factory = request_factory
        self.task_database = task_database
        self.capability_registry = capability_registry
        self.role_registry = role_registry
        self.handler_registry = dict(handler_registry)
        self._closed = False

    def readiness(self) -> dict[str, Any]:
        if self._closed:
            return {"status": "blocked", "reason": "video admission runtime is closed"}
        try:
            records = self.capability_registry.find_by_capability(
                "comfyui_generation", status="ready"
            )
            if not records:
                return {"status": "blocked", "reason": "required capability is not ready: comfyui_generation"}
            if not any(
                isinstance(record, Mapping)
                and record.get("status") == "ready"
                and record.get("domain") == "video"
                and isinstance(record.get("trust"), str)
                and record.get("trust") not in {"", "unverified", "metadata_only"}
                and isinstance(record.get("license"), str)
                and record.get("license") not in {"", "unknown", "unverified"}
                for record in records
            ):
                return {"status": "blocked", "reason": "comfyui_generation capability lacks verified video metadata"}
            policy = self.role_registry.get_scoped("video_studio", "developer")
            if not isinstance(policy, Mapping):
                return {"status": "blocked", "reason": "scoped role is not registered: video_studio/developer"}
            allowed = policy.get("allowed_capabilities")
            if (
                not isinstance(allowed, list)
                or set(allowed) != {"comfyui_generation"}
                or policy.get("trust_tier") != "restricted"
            ):
                return {"status": "blocked", "reason": "video_studio/developer must have a restricted exact least-privilege capability grant"}
            if not callable(self.handler_registry.get("comfyui_generation")):
                return {"status": "blocked", "reason": "trusted local Wan handler is unavailable"}
            from muse.task_queue import TaskQueue
            with TaskQueue(self.task_database) as queue:
                all_tickets = queue.list_tasks(limit=200)
            if len(all_tickets) >= 200:
                return {"status": "blocked", "reason": "TaskQueue exceeds the bounded Commander DAG preflight limit"}
            for ticket in all_tickets:
                payload = ticket.get("payload")
                if not isinstance(payload, dict) or payload.get("kind") not in {
                    "business_opportunity_video_approval", "creator_initiative_video_approval",
                }:
                    continue
                ticket_status = ticket.get("status")
                if ticket_status not in {"queued", "waiting_approval", "running"}:
                    continue
                if ticket_status != "queued" or payload.get("_approved") is not True:
                    return {
                        "status": "blocked",
                        "reason": "video approval must be explicitly approved and queued before Worker claim",
                    }
                plan_id = payload.get("commander_plan_id")
                plan_source = payload.get("commander_plan_source")
                if (
                    not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 128
                    or plan_source != "model"
                ):
                    return {
                        "status": "blocked",
                        "reason": "queued video approval lacks Commander-issued persisted DAG provenance",
                    }
            policy_cost = _load_cost_policy()
            return {
                "status": "ready",
                "scope": "video_studio",
                "role": "developer",
                "capability": "comfyui_generation",
                "cost_policy_configured": True,
                "max_runtime_seconds": policy_cost["max_runtime_seconds"],
            }
        except Exception as exc:
            return {"status": "blocked", "reason": str(exc)[:300] or type(exc).__name__}

    def issue_video_admission_grant(self, store: Any, request: Any) -> Mapping[str, Any]:
        from muse.video_commander_admission import VideoAdmissionBlocked

        if self._closed:
            raise VideoAdmissionBlocked("video admission runtime is closed")
        plan = getattr(request, "plan", None)
        plan_id = getattr(plan, "plan_id", None)
        plan_source = getattr(plan, "source", None)
        if (
            not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 128
            or plan_source != "model"
        ):
            raise VideoAdmissionBlocked("only a CommanderPlanner-issued plan may receive a video admission grant")
        readiness = self.readiness()
        if readiness.get("status") != "ready":
            raise VideoAdmissionBlocked(
                str(readiness.get("reason") or "video admission preflight is not ready")
            )
        return self.provider.issue_video_admission_grant(store, request)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._stack.close()

    def __enter__(self) -> "VideoAdmissionRuntime":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def build_video_admission_runtime(
    workspace: str | Path,
    *,
    task_database: str | Path = "data/tasks/muse.sqlite3",
    capability_database: str | Path = "data/capabilities/muse.sqlite3",
    resource_database: str | Path = "data/gpu-resources.sqlite3",
) -> VideoAdmissionRuntime:
    """Bind existing registries; never auto-register a capability or scoped role."""
    from muse.capabilities import CapabilityRegistry
    from muse.orchestration import AgentRoleRegistry
    from muse.video_commander_admission import (
        RuntimeVideoAdmissionProvider, build_video_admission_request,
    )
    from muse.wan_video import generate_wan21_t2v

    root = Path(workspace).expanduser().resolve()
    task_path = _existing_database(task_database, label="Commander TaskQueue", workspace=root)
    capability_path = _existing_database(
        capability_database, label="Commander CapabilityRegistry", workspace=root,
    )
    if task_path == capability_path:
        raise VideoAdmissionRuntimeUnavailable("TaskQueue and CapabilityRegistry must be separate databases")
    resource_path = Path(resource_database)
    if not resource_path.is_absolute():
        resource_path = root / resource_path
    if resource_path.is_symlink():
        raise VideoAdmissionRuntimeUnavailable("GPU resource database must not be a symlink")
    resource_path = resource_path.resolve(strict=False)
    if resource_path in {task_path, capability_path}:
        raise VideoAdmissionRuntimeUnavailable(
            "GPU resource, TaskQueue and CapabilityRegistry databases must be separate files"
        )

    stack = ExitStack()
    try:
        capability_registry = stack.enter_context(CapabilityRegistry(capability_path))
        role_registry = stack.enter_context(AgentRoleRegistry(task_path))
        handler_registry = {"comfyui_generation": generate_wan21_t2v}

        def commander_request_factory(**kwargs: Any) -> Any:
            plan = kwargs.get("commander_plan")
            plan_id = getattr(plan, "plan_id", None)
            plan_source = getattr(plan, "source", None)
            if (
                not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 128
                or plan_source != "model"
            ):
                from muse.video_commander_admission import VideoAdmissionBlocked
                raise VideoAdmissionBlocked(
                    "video admission requires a CommanderPlanner-issued persisted DAG; "
                    "Worker/Checkpoint local fallback plans are not authorized"
                )
            return build_video_admission_request(**kwargs)

        provider = RuntimeVideoAdmissionProvider(
            capability_registry=capability_registry,
            role_registry=role_registry,
            handler_registry=handler_registry,
            resource_gate=lambda request: _resource_gate(resource_path, request),
            cost_gate=_cost_gate,
            human_gate=lambda request: _human_gate(task_path, request),
            scope="video_studio",
            grant_ttl_seconds=60,
        )
        return VideoAdmissionRuntime(
            stack, provider, commander_request_factory, task_path,
            capability_registry, role_registry, handler_registry,
        )
    except Exception:
        stack.close()
        raise


__all__ = [
    "VideoAdmissionRuntime",
    "VideoAdmissionRuntimeUnavailable",
    "build_video_admission_runtime",
]
