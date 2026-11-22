"""Commander-issued, one-shot admission grants for VideoStudio execution.

This module contains the in-process contract between the Commander admission
provider and VideoTaskWorker. It never dispatches media work or creates policy
registrations. Capability, role, handler, resource, cost and human gates are
injected by the existing Commander runtime; missing evidence fails closed.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class VideoAdmissionBlocked(PermissionError):
    """A policy or evidence gate denied video execution without failing the Attempt."""


@dataclass(frozen=True)
class VideoAdmissionRequest:
    attempt_id: str
    worker_id: str
    lease_epoch: int
    recipe_sha256: str
    input_sha256: str
    dag_sha256: str
    batch_id: str
    batch_fingerprint: str
    scope: str
    role: str
    required_capabilities: tuple[str, ...]
    approval_task_id: str
    approval_event_id: str
    approved_by: str
    approval_evidence: Mapping[str, Any]
    batch: Any
    plan: Any
    step_ids: tuple[str, ...]
    all_batches: tuple[Any, ...]
    expected_input_sha256: Mapping[str, str]
    workspace_root: Path
    actual_handler: Callable[..., Any]
    resource_context: Mapping[str, Any]
    cost_context: Mapping[str, Any]

    def binding_payload(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "worker_id": self.worker_id,
            "lease_epoch": self.lease_epoch,
            "recipe_sha256": self.recipe_sha256,
            "input_sha256": self.input_sha256,
            "dag_sha256": self.dag_sha256,
            "batch_id": self.batch_id,
            "batch_fingerprint": self.batch_fingerprint,
            "scope": self.scope,
            "role": self.role,
            "required_capabilities": list(self.required_capabilities),
            "approval_task_id": self.approval_task_id,
            "approval_event_id": self.approval_event_id,
            "approved_by": self.approved_by,
            "step_ids": list(self.step_ids),
            "resource_context": dict(self.resource_context),
            "cost_context": dict(self.cost_context),
        }


def build_video_admission_request(
    *,
    attempt: Mapping[str, Any],
    video_brief: Mapping[str, Any],
    step_id: str,
    input_sha256: str,
    approval_evidence: Mapping[str, Any],
    actual_handler: Callable[..., Any],
    workspace_root: str | Path,
    commander_plan: Any,
    commander_batches: tuple[Any, ...],
    admission_batch: Any,
    scene_context: Mapping[str, Any],
    lease_context: Any | None = None,
) -> VideoAdmissionRequest:
    """Build a request from the Worker-supplied real DAG and official singleton batch.

    This factory only packages requested inputs. It does not approve GPU resources,
    estimate cost, validate human approval, register capabilities, or issue grants;
    those remain mandatory injected Commander gates in RuntimeVideoAdmissionProvider.
    """
    if not isinstance(attempt, Mapping) or not isinstance(video_brief, Mapping):
        raise VideoAdmissionBlocked("live Attempt and video brief mappings are required")
    if not isinstance(step_id, str) or not step_id.strip() or len(step_id) > 64:
        raise VideoAdmissionBlocked("video admission step ID is invalid")
    if not isinstance(input_sha256, str) or not _SHA256_RE.fullmatch(input_sha256):
        raise VideoAdmissionBlocked("exact per-step generation input digest is required")
    if not isinstance(approval_evidence, Mapping):
        raise VideoAdmissionBlocked("latest human approval evidence is required")
    attempt_id = attempt.get("attempt_id")
    worker_id = attempt.get("worker_id")
    lease_epoch = attempt.get("attempt_count")
    recipe_sha256 = attempt.get("recipe_sha256")
    if not isinstance(attempt_id, str) or not attempt_id.strip() or not isinstance(worker_id, str) or not worker_id.strip():
        raise VideoAdmissionBlocked("live Attempt identity is incomplete")
    if isinstance(lease_epoch, bool) or not isinstance(lease_epoch, int) or lease_epoch < 1:
        raise VideoAdmissionBlocked("live Attempt lease epoch is invalid")
    if not isinstance(recipe_sha256, str) or not _SHA256_RE.fullmatch(recipe_sha256):
        raise VideoAdmissionBlocked("live Attempt recipe fingerprint is invalid")
    approval_task_id = approval_evidence.get("approval_task_id")
    approval_event_id = approval_evidence.get("approval_event_id")
    approved_by = approval_evidence.get("approved_by")
    if (not isinstance(approval_task_id, str) or not approval_task_id.strip()
            or approval_task_id != attempt.get("approval_task_id")
            or not isinstance(approval_event_id, str) or not approval_event_id.strip()
            or not isinstance(approved_by, str) or not approved_by.strip()):
        raise VideoAdmissionBlocked("approval evidence does not match the current Attempt")
    if not callable(actual_handler):
        raise VideoAdmissionBlocked("actual local video handler is not callable")
    if not isinstance(scene_context, Mapping):
        raise VideoAdmissionBlocked("per-scene requested generation context is missing")
    plan_steps = tuple(getattr(commander_plan, "steps", ()))
    if not plan_steps or not hasattr(commander_plan, "parallel_waves"):
        raise VideoAdmissionBlocked("a complete Commander DAG with parallel waves is required")
    matching_steps = tuple(step for step in plan_steps if getattr(step, "step_id", None) == step_id)
    if len(matching_steps) != 1:
        raise VideoAdmissionBlocked("original Commander DAG must contain exactly one matching scene step")
    step = matching_steps[0]
    if not isinstance(commander_batches, tuple) or not commander_batches:
        raise VideoAdmissionBlocked("official Commander batch sequence is missing")
    if not any(batch is admission_batch for batch in commander_batches):
        raise VideoAdmissionBlocked("selected admission batch is not from the Worker-supplied batch sequence")
    batch_id = getattr(admission_batch, "batch_id", None)
    dag_sha256 = getattr(admission_batch, "dag_sha256", None)
    batch_fingerprint = getattr(admission_batch, "batch_fingerprint", None)
    batch_step_ids = tuple(getattr(admission_batch, "step_ids", ()))
    required_completed_steps = tuple(getattr(admission_batch, "required_completed_steps", ()))
    if (not isinstance(batch_id, str) or not batch_id.strip()
            or not isinstance(dag_sha256, str) or not _SHA256_RE.fullmatch(dag_sha256)
            or not isinstance(batch_fingerprint, str) or not _SHA256_RE.fullmatch(batch_fingerprint)
            or batch_step_ids != (step_id,) or required_completed_steps
            or any(getattr(batch, "dag_sha256", None) != dag_sha256 for batch in commander_batches)):
        raise VideoAdmissionBlocked("Wan admission requires an official dependency-free singleton batch from one DAG")
    role = getattr(step, "role", None)
    required_capabilities = tuple(getattr(step, "required_capabilities", ()))
    normalized_capabilities = tuple(sorted(set(required_capabilities)))
    if (not isinstance(role, str) or not role.strip() or not required_capabilities
            or len(normalized_capabilities) != len(required_capabilities)
            or any(not isinstance(item, str) or not item.strip() for item in required_capabilities)
            or getattr(admission_batch, "role", role) != role
            or tuple(getattr(admission_batch, "required_capabilities", normalized_capabilities)) != normalized_capabilities):
        raise VideoAdmissionBlocked("official batch role/capabilities do not match the Commander DAG step")
    gpu_lease_context: dict[str, Any] = {"status": "missing"}
    if lease_context is not None:
        assert_valid = getattr(lease_context, "assert_valid", None)
        if not callable(assert_valid):
            raise VideoAdmissionBlocked("live GpuLeaseSession is required for lease-bound video admission")
        try:
            assert_valid()
        except Exception as exc:
            raise VideoAdmissionBlocked("GPU lease is not valid at admission time") from exc
        lease_id = getattr(lease_context, "lease_id", None)
        owner_id = getattr(lease_context, "owner_id", None)
        coordination = getattr(lease_context, "coordination", None)
        if (
            not isinstance(lease_id, str) or not lease_id.strip() or len(lease_id) > 128
            or not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128
            or not isinstance(coordination, Mapping) or not coordination
        ):
            raise VideoAdmissionBlocked("live GPU lease identity and model coordination evidence are required")
        gpu_lease_context = {
            "status": "active", "lease_id": lease_id, "owner_id": owner_id,
            "coordination": dict(coordination),
        }
    root = Path(workspace_root).resolve()
    requested_generation = {
        "scene": dict(scene_context),
        "video": {
            key: video_brief.get(key)
            for key in ("aspect_ratio", "duration_seconds", "steps", "model_id", "model")
            if key in video_brief
        },
    }
    return VideoAdmissionRequest(
        attempt_id=attempt_id,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
        recipe_sha256=recipe_sha256,
        input_sha256=input_sha256,
        dag_sha256=dag_sha256,
        batch_id=batch_id,
        batch_fingerprint=batch_fingerprint,
        scope="video_studio",
        role=role,
        required_capabilities=required_capabilities,
        approval_task_id=approval_task_id,
        approval_event_id=approval_event_id,
        approved_by=approved_by,
        approval_evidence=dict(approval_evidence),
        batch=admission_batch,
        plan=commander_plan,
        step_ids=(step_id,),
        all_batches=commander_batches,
        expected_input_sha256={step_id: input_sha256},
        workspace_root=root,
        actual_handler=actual_handler,
        resource_context={
            "workspace_root": str(root),
            "requested_generation": requested_generation,
            "gpu_lease": gpu_lease_context,
        },
        cost_context={
            "execution_mode": "local_gpu",
            "requested_generation": requested_generation,
            "cost_assessment_required": True,
        },
    )


class CommanderVideoAdmissionProvider(Protocol):
    """Injected by Commander; returns a persisted active grant, never a preview."""

    def issue_video_admission_grant(
        self, store: Any, request: VideoAdmissionRequest,
    ) -> Mapping[str, Any]:
        ...


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False, default=str,
        )
    except (TypeError, ValueError) as exc:
        raise VideoAdmissionBlocked("admission evidence is not canonical JSON data") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _callable_target(handler: Any) -> Any:
    return getattr(handler, "__func__", handler)


def _callable_descriptor(handler: Any) -> dict[str, str]:
    target = _callable_target(handler)
    if not callable(target):
        raise VideoAdmissionBlocked("Commander handler or gate is not callable")
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None)
    if not isinstance(module, str) or not module or not isinstance(qualname, str) or not qualname:
        raise VideoAdmissionBlocked("Commander handler identity is unavailable")
    try:
        source = inspect.getsource(target)
    except (OSError, TypeError):
        source_path = inspect.getsourcefile(target)
        if not source_path:
            raise VideoAdmissionBlocked("Commander handler source fingerprint is unavailable")
        try:
            source = Path(source_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise VideoAdmissionBlocked("Commander handler source fingerprint is unreadable") from exc
    return {
        "module": module,
        "qualname": qualname,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }


class RuntimeVideoAdmissionProvider:
    """Commander gate evaluator which persists grants into InitiativeStore.

    Resource and cost gates must return {"status": "approved", "evidence": {...}}.
    The evidence is hashed together with the exact attempt/batch/approval binding.
    There are deliberately no default GPU or cost estimates.
    """

    def __init__(
        self,
        *,
        capability_registry: Any,
        role_registry: Any,
        handler_registry: Mapping[str, Any],
        resource_gate: Callable[[VideoAdmissionRequest], Mapping[str, Any]],
        cost_gate: Callable[[VideoAdmissionRequest], Mapping[str, Any]],
        human_gate: Callable[[VideoAdmissionRequest], Mapping[str, Any]],
        scope: str = "video_studio",
        grant_ttl_seconds: int = 60,
    ) -> None:
        if capability_registry is None or not callable(getattr(capability_registry, "find_by_capability", None)):
            raise ValueError("an existing Commander CapabilityRegistry is required")
        if role_registry is None or not callable(getattr(role_registry, "authorize", None)):
            raise ValueError("an existing scoped AgentRoleRegistry is required")
        if not isinstance(handler_registry, Mapping):
            raise ValueError("handler_registry must be an injected capability-to-callable mapping")
        if not all(callable(gate) for gate in (resource_gate, cost_gate, human_gate)):
            raise ValueError("resource, cost and human gate providers are all required")
        if not isinstance(scope, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", scope):
            raise ValueError("scope must be a bounded lowercase identifier")
        if isinstance(grant_ttl_seconds, bool) or not isinstance(grant_ttl_seconds, int) or not 1 <= grant_ttl_seconds <= 60:
            raise ValueError("grant_ttl_seconds must be between 1 and 60")
        self.capability_registry = capability_registry
        self.role_registry = role_registry
        self.handler_registry = dict(handler_registry)
        self.resource_gate = resource_gate
        self.cost_gate = cost_gate
        self.human_gate = human_gate
        self.scope = scope
        self.grant_ttl_seconds = grant_ttl_seconds

    @staticmethod
    def _filtered_plan(request: VideoAdmissionRequest) -> Any:
        plan = request.plan
        if not hasattr(plan, "steps") or not hasattr(plan, "parallel_waves"):
            raise VideoAdmissionBlocked("batch has no Commander DAG plan")
        wanted = set(request.step_ids)
        if not wanted or len(wanted) != len(request.step_ids):
            raise VideoAdmissionBlocked("batch step IDs are empty or duplicated")
        steps = tuple(step for step in plan.steps if step.step_id in wanted)
        if len(steps) != len(wanted):
            raise VideoAdmissionBlocked("batch step IDs do not match its Commander DAG")
        waves = tuple(
            tuple(step_id for step_id in wave if step_id in wanted)
            for wave in plan.parallel_waves
        )
        waves = tuple(wave for wave in waves if wave)
        return replace(plan, steps=steps, parallel_waves=waves)

    @staticmethod
    def _gate_evidence(
        gate: Callable[[VideoAdmissionRequest], Mapping[str, Any]],
        request: VideoAdmissionRequest,
        label: str,
    ) -> Mapping[str, Any]:
        try:
            result = gate(request)
        except Exception as exc:
            raise VideoAdmissionBlocked(f"{label} gate failed closed ({type(exc).__name__})") from exc
        if not isinstance(result, Mapping) or result.get("status") != "approved":
            reason = result.get("reason") if isinstance(result, Mapping) else None
            raise VideoAdmissionBlocked(str(reason or f"{label} gate did not approve"))
        evidence = result.get("evidence")
        if not isinstance(evidence, Mapping) or not evidence:
            raise VideoAdmissionBlocked(f"{label} gate returned no auditable evidence")
        return dict(evidence)

    def issue_video_admission_grant(
        self, store: Any, request: VideoAdmissionRequest,
    ) -> Mapping[str, Any]:
        if not isinstance(request, VideoAdmissionRequest):
            raise TypeError("request must be a VideoAdmissionRequest")
        if request.scope != self.scope:
            raise VideoAdmissionBlocked("request scope does not match the injected Commander provider")
        lease_context = request.resource_context.get("gpu_lease") if isinstance(request.resource_context, Mapping) else None
        if (
            not isinstance(lease_context, Mapping)
            or lease_context.get("status") != "active"
            or not isinstance(lease_context.get("lease_id"), str)
            or not lease_context["lease_id"].strip()
            or len(lease_context["lease_id"]) > 128
            or not isinstance(lease_context.get("owner_id"), str)
            or not lease_context["owner_id"].strip()
            or len(lease_context["owner_id"]) > 128
            or not isinstance(lease_context.get("coordination"), Mapping)
            or not lease_context["coordination"]
        ):
            raise VideoAdmissionBlocked("active GPU lease binding is required before resource admission")
        for name, value in (
            ("attempt_id", request.attempt_id), ("worker_id", request.worker_id),
            ("batch_id", request.batch_id), ("approval_task_id", request.approval_task_id),
            ("approval_event_id", request.approval_event_id), ("approved_by", request.approved_by),
            ("role", request.role),
        ):
            if not isinstance(value, str) or not value.strip():
                raise VideoAdmissionBlocked(f"{name} is missing")
        if isinstance(request.lease_epoch, bool) or not isinstance(request.lease_epoch, int) or request.lease_epoch < 1:
            raise VideoAdmissionBlocked("Attempt lease epoch is invalid")
        for name in ("recipe_sha256", "input_sha256", "dag_sha256", "batch_fingerprint"):
            if not isinstance(getattr(request, name), str) or not _SHA256_RE.fullmatch(getattr(request, name)):
                raise VideoAdmissionBlocked(f"{name} is not a SHA-256 fingerprint")
        try:
            current_attempt = store.get_video_attempt(request.attempt_id)
        except Exception as exc:
            raise VideoAdmissionBlocked(f"InitiativeStore Attempt lookup failed ({type(exc).__name__})") from exc
        if (
            not isinstance(current_attempt, Mapping)
            or current_attempt.get("recipe_sha256") != request.recipe_sha256
            or current_attempt.get("approval_task_id") != request.approval_task_id
        ):
            raise VideoAdmissionBlocked("request recipe or approval task does not match the current InitiativeStore Attempt")
        if not isinstance(request.approval_evidence, Mapping):
            raise VideoAdmissionBlocked("current TaskQueue approval evidence is missing")
        approval = request.approval_evidence
        if (
            approval.get("approval_task_id") != request.approval_task_id
            or approval.get("approval_event_id") != request.approval_event_id
            or approval.get("approved_by") != request.approved_by
        ):
            raise VideoAdmissionBlocked("approval evidence does not match the requested grant")
        batch = request.batch
        if (
            getattr(batch, "batch_id", None) != request.batch_id
            or getattr(batch, "batch_fingerprint", None) != request.batch_fingerprint
            or getattr(batch, "dag_sha256", None) != request.dag_sha256
            or tuple(getattr(batch, "step_ids", ())) != tuple(request.step_ids)
        ):
            raise VideoAdmissionBlocked("request binding does not match the persisted Commander batch")
        plan = self._filtered_plan(request)
        roles = {step.role for step in plan.steps}
        capabilities = sorted({cap for step in plan.steps for cap in step.required_capabilities})
        if len(roles) != 1 or request.role not in roles:
            raise VideoAdmissionBlocked("one batch must have exactly one explicitly authorized role")
        if tuple(capabilities) != tuple(sorted(set(request.required_capabilities))):
            raise VideoAdmissionBlocked("requested capabilities do not match the Commander batch")
        try:
            from muse.video_studio import assess_video_studio_commander_admission
            assessment = assess_video_studio_commander_admission(
                plan, max_steps=32, capability_registry=self.capability_registry,
                role_registry=self.role_registry, handler_registry=self.handler_registry,
                authorization_scope=request.scope,
            )
        except Exception as exc:
            raise VideoAdmissionBlocked(f"Commander capability/role/handler assessment failed ({type(exc).__name__})") from exc
        if assessment.get("status") != "pending_resource_and_human_gates":
            raise VideoAdmissionBlocked(str(assessment.get("reason") or assessment.get("status") or "Commander preflight denied"))
        try:
            role_policy = self.role_registry.get_scoped(request.scope, request.role)
        except Exception as exc:
            raise VideoAdmissionBlocked(f"scoped role policy lookup failed ({type(exc).__name__})") from exc
        if not isinstance(role_policy, Mapping):
            raise VideoAdmissionBlocked("scoped role policy is missing")
        capability_records: list[dict[str, Any]] = []
        handler_descriptors: list[dict[str, str]] = []
        for capability in capabilities:
            records = self.capability_registry.find_by_capability(capability, status="ready")
            ready_records = [record for record in records if isinstance(record, Mapping) and record.get("status") == "ready"]
            if not ready_records:
                raise VideoAdmissionBlocked(f"required capability is not ready: {capability}")
            capability_records.extend(dict(record) for record in ready_records)
            handler = self.handler_registry.get(capability)
            descriptor = _callable_descriptor(handler)
            if capability == "comfyui_generation":
                actual = _callable_descriptor(request.actual_handler)
                if (descriptor["module"], descriptor["qualname"], descriptor["source_sha256"]) != (
                    actual["module"], actual["qualname"], actual["source_sha256"]
                ):
                    raise VideoAdmissionBlocked("registered video handler does not match the Worker executor")
            handler_descriptors.append({"capability": capability, **descriptor})
        handler_digest = _sha256({
            "scope": request.scope,
            "role": request.role,
            "required_capabilities": capabilities,
            "role_policy": dict(role_policy),
            "capability_records": sorted(capability_records, key=lambda item: str(item.get("capability_id", ""))),
            "handlers": handler_descriptors,
            "human_gate": _callable_descriptor(self.human_gate),
            "assessment_status": assessment.get("status"),
        })
        resource_evidence = self._gate_evidence(self.resource_gate, request, "resource")
        if (
            resource_evidence.get("gpu_lease_status") != "active"
            or resource_evidence.get("gpu_lease_id") != lease_context["lease_id"]
            or resource_evidence.get("gpu_lease_owner_id") != lease_context["owner_id"]
            or resource_evidence.get("gpu_lease_coordination") != lease_context["coordination"]
        ):
            raise VideoAdmissionBlocked("resource evidence does not bind the active GPU lease")
        cost_evidence = self._gate_evidence(self.cost_gate, request, "cost")
        try:
            human_result = self.human_gate(request)
        except Exception as exc:
            raise VideoAdmissionBlocked(f"human approval gate failed closed ({type(exc).__name__})") from exc
        if (
            not isinstance(human_result, Mapping)
            or human_result.get("status") != "approved"
            or human_result.get("approval_task_id") != request.approval_task_id
            or human_result.get("approval_event_id") != request.approval_event_id
            or human_result.get("approved_by") != request.approved_by
        ):
            raise VideoAdmissionBlocked("latest human approval gate did not confirm the bound approval event")
        binding = request.binding_payload()
        resource_digest = _sha256({
            "gate": _callable_descriptor(self.resource_gate),
            "binding": binding,
            "evidence": dict(resource_evidence),
        })
        cost_digest = _sha256({
            "gate": _callable_descriptor(self.cost_gate),
            "binding": binding,
            "evidence": dict(cost_evidence),
        })
        try:
            grant = store.create_video_admission_grant(
                request.attempt_id, worker_id=request.worker_id, lease_epoch=request.lease_epoch,
                input_sha256=request.input_sha256,
                dag_sha256=request.dag_sha256, batch_id=request.batch_id,
                batch_fingerprint=request.batch_fingerprint, handler_digest=handler_digest,
                resource_digest=resource_digest, cost_digest=cost_digest,
                approval_task_id=request.approval_task_id, approval_event_id=request.approval_event_id,
                approved_by=request.approved_by, scope=request.scope, role=request.role,
                required_capabilities=capabilities, ttl_seconds=self.grant_ttl_seconds,
            )
        except Exception as exc:
            raise VideoAdmissionBlocked(f"InitiativeStore refused the Commander grant ({type(exc).__name__})") from exc
        if not isinstance(grant, Mapping) or grant.get("status") != "active" or not grant.get("grant_id"):
            raise VideoAdmissionBlocked("InitiativeStore did not return an active persisted grant")
        return dict(grant)


__all__ = [
    "CommanderVideoAdmissionProvider",
    "RuntimeVideoAdmissionProvider",
    "VideoAdmissionBlocked",
    "VideoAdmissionRequest",
]
