"""OpenMontage-inspired video production profiles and plan-only Commander DAGs.

This module defines reusable production recipes; it does not execute media tools,
create jobs, spend money, download sources, or publish. Muse Commander remains the
only execution and approval authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from muse.commander_planner import CommanderPlan, CommanderPlanner, PlannedStep
from muse.video_workflow import validate_storyboard

@dataclass(frozen=True)
class VideoPipelineProfile:
    pipeline_id: str
    name: str
    category: str
    description: str
    before_shots: tuple[str, ...]
    after_shots: tuple[str, ...]
    scene_mode: str
    coverage: str
    available_modules: tuple[str, ...]
    planned_gaps: tuple[str, ...]
    creative_gates: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "execution_mode": "plan_only",
            "commander_is_scheduler": True,
        }


@dataclass(frozen=True)
class VideoStudioBatch:
    """One bounded Commander sub-plan; persistence and dispatch remain external."""

    batch_id: str
    checkpoint_key_template: str
    index: int
    total_batches: int
    max_steps: int
    step_ids: tuple[str, ...]
    required_completed_steps: tuple[str, ...]
    dag_sha256: str
    batch_fingerprint: str
    step_stage_ids: tuple[tuple[str, str], ...]
    plan: CommanderPlan
    input_sha256: str | None = None


    @property
    def role(self) -> str:
        wanted = set(self.step_ids)
        roles = {step.role for step in self.plan.steps if step.step_id in wanted}
        if len(roles) != 1:
            raise ValueError("video batch must contain exactly one execution role")
        return next(iter(roles))

    @property
    def required_capabilities(self) -> tuple[str, ...]:
        wanted = set(self.step_ids)
        return tuple(sorted({
            capability for step in self.plan.steps if step.step_id in wanted
            for capability in step.required_capabilities
        }))

    def as_dict(self) -> dict[str, Any]:
        stage_by_step_id = dict(self.step_stage_ids)
        return {
            "batch_id": self.batch_id,
            "checkpoint_key_template": self.checkpoint_key_template,
            "dag_sha256": self.dag_sha256,
            "batch_fingerprint": self.batch_fingerprint,
            "role": self.role,
            "required_capabilities": list(self.required_capabilities),
            "input_sha256": self.input_sha256,
            "step_stage_ids": dict(self.step_stage_ids),
            "index": self.index,
            "total_batches": self.total_batches,
            "max_steps": self.max_steps,
            "step_ids": list(self.step_ids),
            "required_completed_steps": list(self.required_completed_steps),
            "status": "preview_only_worker_integration_pending",
            "execution_eligible": False,
            "commander_plan": {
                "plan_id": self.plan.plan_id,
                "source": self.plan.source,
                "parallel_waves": [list(wave) for wave in self.plan.parallel_waves],
                "steps": [{
                    "id": step.step_id,
                    "step_id": step.step_id,
                    "stage_id": stage_by_step_id.get(step.step_id),
                    "role": step.role,
                    "depends_on": list(step.depends_on),
                    "required_capabilities": list(step.required_capabilities),
                } for step in self.plan.steps],
            },
        }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"video manifest contains duplicate key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class _StageSpec:
    role: str
    objective: str
    capabilities: tuple[str, ...]  # Planning hints only; never grant execution permission.


STAGE_MANIFEST_PATH = Path(__file__).with_name("video_stages.yaml")
_STAGE_FIELDS = frozenset({"stage_id", "role", "objective", "capability_hints"})
_STAGE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def load_video_stage_specs(manifest_path: str | Path | None = None) -> dict[str, _StageSpec]:
    """Load declarative stage descriptions; capability hints are not executable grants."""
    path = Path(manifest_path) if manifest_path is not None else STAGE_MANIFEST_PATH
    try:
        raw = path.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > 64 * 1024:
            raise ValueError("video stage manifest exceeds the 64 KiB limit")
        manifest = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid video stage manifest: {exc}") from None
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "format", "stages"}:
        raise ValueError("video stage manifest has an unsupported top-level shape")
    if manifest["schema_version"] != 1 or isinstance(manifest["schema_version"], bool) or manifest["format"] != "yaml-1.2-json-subset":
        raise ValueError("unsupported video stage manifest schema or format")
    records = manifest["stages"]
    if not isinstance(records, list) or not 10 <= len(records) <= 64:
        raise ValueError("video stage manifest must contain 10-64 stages")
    specs: dict[str, _StageSpec] = {}
    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict) or set(item) != _STAGE_FIELDS:
            raise ValueError(f"video stage {index} has missing or unsupported fields")
        stage_id, role, objective, hints = (item[key] for key in ("stage_id", "role", "objective", "capability_hints"))
        if not isinstance(stage_id, str) or not _STAGE_ID.fullmatch(stage_id) or stage_id in specs:
            raise ValueError(f"video stage {index} has an invalid or duplicate stage_id")
        if not isinstance(role, str) or role not in CommanderPlanner.ALLOWED_ROLES:
            raise ValueError(f"video stage {stage_id} uses a role not admitted by CommanderPlanner")
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 500:
            raise ValueError(f"video stage {stage_id} has an invalid objective")
        if not isinstance(hints, list) or any(not isinstance(hint, str) or not hint.strip() or len(hint) > 100 for hint in hints):
            raise ValueError(f"video stage {stage_id} has invalid capability_hints")
        if len(hints) != len(set(hints)):
            raise ValueError(f"video stage {stage_id} has duplicate capability_hints")
        specs[stage_id] = _StageSpec(role, objective.strip(), tuple(hints))
    if not {"intake", "preflight", "publish_gate"} <= set(specs):
        raise ValueError("video stage manifest is missing reserved lifecycle stages")
    return specs


_STAGE_SPECS = load_video_stage_specs()


def _stage_id_for_step(step: PlannedStep) -> str:
    """Resolve a planned step to one fixed manifest stage; ambiguous mappings fail closed."""
    if step.step_id in _STAGE_SPECS:
        return step.step_id
    capabilities = set(step.required_capabilities)
    matches = [
        stage_id for stage_id, spec in _STAGE_SPECS.items()
        if capabilities.intersection(spec.capabilities)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"plan step {step.step_id} has no unique fixed video stage mapping"
        )
    return matches[0]

PIPELINE_MANIFEST_PATH = Path(__file__).with_name("video_pipelines.yaml")
_PROFILE_FIELDS = frozenset({
    "pipeline_id", "name", "category", "description", "before_shots", "after_shots",
    "scene_mode", "coverage", "available_modules", "planned_gaps", "creative_gates",
})
_SCENE_MODES = frozenset({"generated_shot", "source_footage", "clip_edit", "captured_demo", "animated_scene"})
_COVERAGE_LEVELS = frozenset({"native", "partial", "planned"})
_PIPELINE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_MANIFEST_BYTES = 128 * 1024


def load_video_pipeline_profiles(manifest_path: str | Path | None = None) -> tuple[VideoPipelineProfile, ...]:
    """Load the dependency-free YAML 1.2 JSON-compatible manifest and validate its contract."""
    path = Path(manifest_path) if manifest_path is not None else PIPELINE_MANIFEST_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"video pipeline manifest cannot be read: {path.name}") from exc
    if len(raw.encode("utf-8")) > _MAX_MANIFEST_BYTES:
        raise ValueError("video pipeline manifest exceeds the 128 KiB limit")
    try:
        manifest = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid video pipeline manifest: {exc}") from None
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "format", "pipelines"}:
        raise ValueError("video pipeline manifest has an unsupported top-level shape")
    if manifest["schema_version"] != 1 or isinstance(manifest["schema_version"], bool):
        raise ValueError("unsupported video pipeline manifest schema_version")
    if manifest["format"] != "yaml-1.2-json-subset":
        raise ValueError("video pipeline manifest must use the documented YAML 1.2 JSON subset")
    records = manifest["pipelines"]
    if not isinstance(records, list) or len(records) != 12:
        raise ValueError("video pipeline manifest must contain exactly 12 profiles")

    profiles: list[VideoPipelineProfile] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict) or set(item) != _PROFILE_FIELDS:
            raise ValueError(f"video pipeline profile {index} has missing or unsupported fields")
        pipeline_id = item["pipeline_id"]
        if not isinstance(pipeline_id, str) or not _PIPELINE_ID.fullmatch(pipeline_id) or pipeline_id in seen_ids:
            raise ValueError(f"video pipeline profile {index} has an invalid or duplicate pipeline_id")
        seen_ids.add(pipeline_id)

        text_fields = ("name", "category", "description")
        text_values: dict[str, str] = {}
        for field in text_fields:
            value = item[field]
            if not isinstance(value, str) or not value.strip() or len(value) > (500 if field == "description" else 120):
                raise ValueError(f"video pipeline profile {pipeline_id} has an invalid {field}")
            text_values[field] = value.strip()

        list_fields = ("before_shots", "after_shots", "available_modules", "planned_gaps", "creative_gates")
        list_values: dict[str, tuple[str, ...]] = {}
        for field in list_fields:
            value = item[field]
            if not isinstance(value, list) or any(not isinstance(entry, str) or not entry.strip() for entry in value):
                raise ValueError(f"video pipeline profile {pipeline_id} has an invalid {field}")
            normalized = tuple(entry.strip() for entry in value)
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"video pipeline profile {pipeline_id} has duplicate entries in {field}")
            list_values[field] = normalized

        stage_ids = list_values["before_shots"] + list_values["after_shots"]
        if (not stage_ids or len(stage_ids) != len(set(stage_ids))
                or any(stage not in _STAGE_SPECS for stage in stage_ids)
                or any(stage in {"intake", "preflight"} for stage in stage_ids)):
            raise ValueError(f"video pipeline profile {pipeline_id} references an unknown or duplicate stage; intake and preflight are reserved")
        scene_mode = item["scene_mode"]
        coverage = item["coverage"]
        if (not isinstance(scene_mode, str) or scene_mode not in _SCENE_MODES
                or not isinstance(coverage, str) or coverage not in _COVERAGE_LEVELS):
            raise ValueError(f"video pipeline profile {pipeline_id} has an invalid scene_mode or coverage")

        profiles.append(VideoPipelineProfile(
            pipeline_id=pipeline_id,
            name=text_values["name"],
            category=text_values["category"],
            description=text_values["description"],
            before_shots=list_values["before_shots"],
            after_shots=list_values["after_shots"],
            scene_mode=scene_mode,
            coverage=coverage,
            available_modules=list_values["available_modules"],
            planned_gaps=list_values["planned_gaps"],
            creative_gates=list_values["creative_gates"],
        ))
    return tuple(profiles)


PIPELINE_PROFILES: tuple[VideoPipelineProfile, ...] = load_video_pipeline_profiles()
_PROFILE_BY_ID = {profile.pipeline_id: profile for profile in PIPELINE_PROFILES}

def list_video_pipeline_profiles() -> list[dict[str, Any]]:
    """Return the stable 12-profile catalogue with honest coverage labels."""
    return [profile.as_dict() for profile in PIPELINE_PROFILES]


def build_video_studio_plan(storyboard: Mapping[str, Any], pipeline_id: str) -> tuple[VideoPipelineProfile, CommanderPlan]:
    """Build a plan-only Commander DAG from a profile and validated storyboard."""
    profile = _PROFILE_BY_ID.get(pipeline_id)
    if profile is None:
        raise ValueError(f"unknown video pipeline: {pipeline_id}")
    normalized = validate_storyboard(storyboard)
    steps: list[PlannedStep] = []
    previous = "intake"
    steps.append(PlannedStep(previous, "operator", _STAGE_SPECS[previous].objective, (), ()))

    for stage_id in profile.before_shots:
        spec = _STAGE_SPECS[stage_id]
        steps.append(PlannedStep(stage_id, spec.role, spec.objective, (previous,), spec.capabilities))
        previous = stage_id

    preflight = _STAGE_SPECS["preflight"]
    steps.append(PlannedStep("preflight", preflight.role, preflight.objective, (previous,), preflight.capabilities))

    shot_ids: list[str] = []
    for scene in normalized["scenes"]:
        step_id = f"shot_{scene['id']}"
        shot_ids.append(step_id)
        if profile.scene_mode == "generated_shot":
            role, capability, action = "developer", ("comfyui_generation",), "生成并校验"
        elif profile.scene_mode == "source_footage":
            role, capability, action = "researcher", ("stock_media_search",), "检索、核对来源并选择"
        elif profile.scene_mode == "clip_edit":
            role, capability, action = "developer", ("video_highlights",), "剪辑并校验"
        elif profile.scene_mode == "captured_demo":
            role, capability, action = "operator", ("screen_capture",), "录制并校验"
        else:
            role, capability, action = "developer", ("motion_graphics",), "制作动画镜头并校验"
        dependencies = ("preflight", *tuple(f"shot_{dep}" for dep in scene["depends_on"]))
        steps.append(PlannedStep(
            step_id, role,
            f"{action}镜头 {scene['id']}（{scene['duration_seconds']:g}s）",
            dependencies, capability,
        ))

    previous_ids = tuple(shot_ids)
    for stage_id in profile.after_shots:
        spec = _STAGE_SPECS[stage_id]
        steps.append(PlannedStep(stage_id, spec.role, spec.objective, previous_ids, spec.capabilities))
        previous_ids = (stage_id,)

    pending = {step.step_id: set(step.depends_on) for step in steps}
    completed: set[str] = set()
    waves: list[tuple[str, ...]] = []
    while pending:
        ready = tuple(step.step_id for step in steps
                      if step.step_id in pending and pending[step.step_id] <= completed)
        if not ready:
            raise ValueError("video studio plan contains a dependency cycle")
        waves.append(ready)
        completed.update(ready)
        for step_id in ready:
            del pending[step_id]

    plan = CommanderPlan(
        plan_id=uuid.uuid4().hex,
        goal=f"Muse 视频制片流水线：{normalized['title']}（{profile.name}）",
        steps=tuple(steps),
        source=f"openmontage_inspired_video_studio:{profile.pipeline_id}",
        parallel_waves=tuple(waves),
    )
    return profile, plan


def assess_video_studio_commander_admission(
    plan: CommanderPlan,
    *,
    max_steps: int = 16,
    capability_registry: Any | None = None,
    role_registry: Any | None = None,
    handler_registry: Mapping[str, Any] | None = None,
    authorization_scope: str = "video_studio",
) -> dict[str, Any]:
    """Check registered capabilities and scoped role grants without authorizing execution.

    Registries are injected from the existing Commander runtime. This function never
    creates policies, registers capabilities, launches handlers, or dispatches work.
    A successful metadata/policy check remains pending until a trusted execution
    handler, resource budget, and all human gates are independently verified.
    """
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 2 <= max_steps <= 32:
        raise ValueError("max_steps must be between 2 and 32")
    if (not isinstance(authorization_scope, str) or not authorization_scope
            or len(authorization_scope) > 64):
        raise ValueError("authorization_scope must be non-empty bounded text")
    invalid_roles = sorted({step.role for step in plan.steps} - CommanderPlanner.ALLOWED_ROLES)
    required_capabilities = sorted({
        capability for step in plan.steps for capability in step.required_capabilities
    })
    result = {
        "step_count": len(plan.steps),
        "max_steps": max_steps,
        "invalid_roles": invalid_roles,
        "authorization_scope": authorization_scope,
        "required_capabilities": required_capabilities,
        "missing_capabilities": [],
        "unauthorized_steps": [],
        "missing_handlers": [],
        "capability_registry_checked": False,
        "role_capability_authorization_checked": False,
        "handler_registry_checked": False,
        "execution_eligible": False,
        "dispatch_started": False,
    }
    if invalid_roles:
        return result | {"status": "blocked_role_allowlist", "reason": "one or more plan roles are not admitted by CommanderPlanner"}
    if len(plan.steps) > max_steps:
        return result | {"status": "blocked_step_limit", "reason": "split the DAG into bounded, checkpointed Commander batches before dispatch"}
    if capability_registry is None:
        return result | {"status": "pending_capability_registry", "reason": "the existing Commander CapabilityRegistry was not supplied"}
    try:
        missing = []
        for capability in required_capabilities:
            records = capability_registry.find_by_capability(capability, status="ready")
            if not isinstance(records, (list, tuple)) or not any(
                isinstance(record, Mapping) and record.get("status") == "ready"
                for record in records
            ):
                missing.append(capability)
    except Exception as exc:
        return result | {
            "capability_registry_checked": True,
            "status": "blocked_capability_registry_error",
            "reason": f"capability lookup failed closed ({type(exc).__name__})",
        }
    result["capability_registry_checked"] = True
    result["missing_capabilities"] = missing
    if missing:
        return result | {
            "status": "blocked_missing_capabilities",
            "reason": "one or more required capabilities are not registered and ready",
        }
    if role_registry is None:
        return result | {
            "status": "pending_role_capability_authorization",
            "reason": "the existing scoped AgentRoleRegistry was not supplied",
        }
    unauthorized: list[dict[str, str]] = []
    for step in plan.steps:
        try:
            role_registry.authorize(
                step.role, step.required_capabilities, scope=authorization_scope,
            )
        except Exception as exc:
            unauthorized.append({"step_id": step.step_id, "role": step.role, "error_type": type(exc).__name__})
    result["role_capability_authorization_checked"] = True
    result["unauthorized_steps"] = unauthorized
    if unauthorized:
        return result | {
            "status": "blocked_role_capability_authorization",
            "reason": "one or more steps lack an explicit scoped role grant",
        }
    if handler_registry is None:
        return result | {
            "status": "pending_execution_handler",
            "reason": "capability readiness and scoped role grants passed; the trusted Commander handler registry was not supplied",
        }
    if not isinstance(handler_registry, Mapping):
        return result | {
            "status": "blocked_handler_registry_error",
            "reason": "handler registry must be an injected capability-to-callable mapping",
        }
    missing_handlers = [
        capability for capability in required_capabilities
        if not callable(handler_registry.get(capability))
    ]
    result["handler_registry_checked"] = True
    result["missing_handlers"] = missing_handlers
    if missing_handlers:
        return result | {
            "status": "blocked_missing_execution_handlers",
            "reason": "one or more ready capabilities have no trusted executable Commander handler",
        }
    return result | {
        "status": "pending_resource_and_human_gates",
        "reason": "capability readiness, scoped role grants and handler presence passed; resource/cost policy and human gates remain unverified",
    }


def build_video_studio_batches(
    plan: CommanderPlan,
    *,
    max_steps: int = 16,
    input_sha256: str | None = None,
) -> tuple[VideoStudioBatch, ...]:
    """Create deterministic, bounded plan-only batches without breaking DAG dependencies.

    Each batch reserves one Commander step for a checkpoint gate. Cross-batch
    dependencies become explicit `required_completed_steps` preconditions; the
    gate must not be dispatched until a future durable checkpoint adapter verifies
    those preconditions and the parent Video Attempt's approval/recipe identity.
    """
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 2 <= max_steps <= 32:
        raise ValueError("max_steps must be between 2 and 32")
    if input_sha256 is not None and (
        not isinstance(input_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", input_sha256)
    ):
        raise ValueError("input_sha256 must be a lowercase SHA-256 digest or None")
    if not isinstance(plan, CommanderPlan) or not isinstance(plan.steps, tuple) or not plan.steps:
        raise ValueError("plan must be a non-empty CommanderPlan")
    if (not isinstance(plan.goal, str) or not plan.goal.strip() or len(plan.goal) > 1000
            or not isinstance(plan.source, str) or not plan.source.strip() or len(plan.source) > 256):
        raise ValueError("plan goal and source must be non-empty bounded strings")

    by_id: dict[str, PlannedStep] = {}
    for step in plan.steps:
        if not isinstance(step, PlannedStep):
            raise ValueError("plan contains an invalid step")
        if not isinstance(step.step_id, str) or not step.step_id or len(step.step_id) > 64 or step.step_id in by_id:
            raise ValueError("plan step IDs must be unique bounded strings")
        if not isinstance(step.role, str) or step.role not in CommanderPlanner.ALLOWED_ROLES:
            raise ValueError(f"plan step {step.step_id} has a role not admitted by Commander")
        if not isinstance(step.objective, str) or not step.objective.strip() or len(step.objective) > 1000:
            raise ValueError(f"plan step {step.step_id} has an invalid objective")
        if (not isinstance(step.required_capabilities, tuple)
                or any(not isinstance(capability, str) or not capability.strip() or len(capability) > 128
                       for capability in step.required_capabilities)):
            raise ValueError(f"plan step {step.step_id} has invalid required capabilities")
        if not isinstance(step.depends_on, tuple) or any(not isinstance(dep, str) or not dep for dep in step.depends_on):
            raise ValueError(f"plan step {step.step_id} has invalid dependencies")
        if len(set(step.depends_on)) != len(step.depends_on) or step.step_id in step.depends_on:
            raise ValueError(f"plan step {step.step_id} has duplicate or self dependencies")
        by_id[step.step_id] = step
    for step in plan.steps:
        if any(dep not in by_id for dep in step.depends_on):
            raise ValueError(f"plan step {step.step_id} references an unknown dependency")

    # Stable Kahn traversal: retain the source order inside every ready wave.
    pending = set(by_id)
    completed: set[str] = set()
    ordered: list[PlannedStep] = []
    while pending:
        ready = [step for step in plan.steps if step.step_id in pending and set(step.depends_on) <= completed]
        if not ready:
            raise ValueError("video studio plan contains a dependency cycle")
        for step in ready:
            ordered.append(step)
            completed.add(step.step_id)
            pending.remove(step.step_id)

    fingerprint_payload = {
        "goal": plan.goal,
        "stage_manifest_sha256": hashlib.sha256(STAGE_MANIFEST_PATH.read_bytes()).hexdigest(),
        "source": plan.source,
        "steps": [{
            "id": step.step_id,
            "role": step.role,
            "objective": step.objective,
            "depends_on": list(step.depends_on),
            "required_capabilities": list(step.required_capabilities),
        } for step in ordered],
        "max_steps": max_steps,
        "input_sha256": input_sha256,
    }
    fingerprint = hashlib.sha256(json.dumps(
        fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    capacity = max_steps - 1  # One step is reserved for the durable-checkpoint entry gate.
    chunks: list[list[PlannedStep]] = []
    current_chunk: list[PlannedStep] = []
    current_role: str | None = None
    for step in ordered:
        if current_chunk and (step.role != current_role or len(current_chunk) >= capacity):
            chunks.append(current_chunk)
            current_chunk = []
            current_role = None
        if not current_chunk:
            current_role = step.role
        current_chunk.append(step)
    if current_chunk:
        chunks.append(current_chunk)
    positions = {step.step_id: index for index, step in enumerate(ordered)}
    prior_ids: set[str] = set()
    batches: list[VideoStudioBatch] = []

    for index, chunk in enumerate(chunks, start=1):
        chunk_ids = {step.step_id for step in chunk}
        chunk_roles = {step.role for step in chunk}
        if len(chunk_roles) != 1:
            raise ValueError("generated video batch must contain exactly one execution role")
        batch_role = next(iter(chunk_roles))
        external_dependencies = {
            dep for step in chunk for dep in step.depends_on if dep not in chunk_ids
        }
        if any(dep not in prior_ids for dep in external_dependencies):
            raise ValueError("batch order would require a dependency from a future batch")
        required_completed_steps = tuple(sorted(external_dependencies, key=positions.__getitem__))

        gate_id = "__video_batch_entry__"
        while gate_id in chunk_ids:
            gate_id = "_" + gate_id
        gate = PlannedStep(
            gate_id,
            "operator",
            "仅在外层检查点适配器核验父 Video Attempt、审批与 required_completed_steps 后放行本批；当前为预览占位",
            (),
            ("video_attempt_checkpoint_read",),
        )
        batch_steps = [gate]
        for step in chunk:
            internal_dependencies = tuple(dep for dep in step.depends_on if dep in chunk_ids)
            batch_steps.append(PlannedStep(
                step.step_id, step.role, step.objective,
                (gate_id, *internal_dependencies), step.required_capabilities,
            ))

        batch_pending = {step.step_id: set(step.depends_on) for step in batch_steps}
        batch_completed: set[str] = set()
        batch_waves: list[tuple[str, ...]] = []
        while batch_pending:
            wave = tuple(step.step_id for step in batch_steps
                         if step.step_id in batch_pending and batch_pending[step.step_id] <= batch_completed)
            if not wave:
                raise ValueError("generated Commander batch contains a dependency cycle")
            batch_waves.append(wave)
            batch_completed.update(wave)
            for step_id in wave:
                del batch_pending[step_id]

        step_stage_ids = tuple((step.step_id, _stage_id_for_step(step)) for step in chunk)
        batch_fingerprint_payload = {
            "dag_sha256": fingerprint,
            "role": batch_role,
            "batch_index": index,
            "batch_total": len(chunks),
            "max_steps": max_steps,
            "step_ids": [step.step_id for step in chunk],
            "step_stage_ids": [list(item) for item in step_stage_ids],
            "required_completed_steps": list(required_completed_steps),
            "steps": [{
                "id": step.step_id,
                "role": step.role,
                "objective": step.objective,
                "depends_on": list(step.depends_on),
                "required_capabilities": list(step.required_capabilities),
            } for step in chunk],
        }
        batch_fingerprint = hashlib.sha256(json.dumps(
            batch_fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        batch_plan = CommanderPlan(
            plan_id=hashlib.sha256(f"{fingerprint}:{index}".encode("ascii")).hexdigest()[:32],
            goal=f"{plan.goal} [batch {index}/{len(chunks)}]",
            steps=tuple(batch_steps),
            source=f"video_studio_batch:{plan.source}",
            parallel_waves=tuple(batch_waves),
        )
        batches.append(VideoStudioBatch(
            batch_id=f"vbatch_{fingerprint[:16]}_{index:03d}",
            checkpoint_key_template=f"video-studio:{{attempt_id}}:{fingerprint}:{batch_fingerprint}:{index:03d}",
            dag_sha256=fingerprint,
            batch_fingerprint=batch_fingerprint,
            input_sha256=input_sha256,
            step_stage_ids=step_stage_ids,
            index=index,
            total_batches=len(chunks),
            max_steps=max_steps,
            step_ids=tuple(step.step_id for step in chunk),
            required_completed_steps=required_completed_steps,
            plan=batch_plan,
        ))
        prior_ids.update(chunk_ids)

    return tuple(batches)


def build_video_studio_preview(
    storyboard: Mapping[str, Any],
    pipeline_id: str,
    *,
    max_steps: int = 16,
    capability_registry: Any | None = None,
    role_registry: Any | None = None,
    handler_registry: Mapping[str, Any] | None = None,
    authorization_scope: str = "video_studio",
) -> dict[str, Any]:
    """Build a non-executing preview, optionally checking Commander-owned registries."""
    profile, plan = build_video_studio_plan(storyboard, pipeline_id)
    normalized = validate_storyboard(storyboard)
    storyboard_sha256 = hashlib.sha256(json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    batches = build_video_studio_batches(
        plan, max_steps=max_steps, input_sha256=storyboard_sha256,
    )
    admission = assess_video_studio_commander_admission(
        plan, max_steps=max_steps, capability_registry=capability_registry,
        role_registry=role_registry, handler_registry=handler_registry,
        authorization_scope=authorization_scope,
    )
    return {
        "status": "preview_only",
        "storyboard_validation": "passed",
        "storyboard_sha256": storyboard_sha256,
        "pipeline": profile.as_dict(),
        "storyboard": {
            "title": normalized["title"],
            "scene_count": len(normalized["scenes"]),
            "aspect_ratio": normalized["aspect_ratio"],
            "fps": normalized["fps"],
        },
        "commander_plan": {
            "plan_id": plan.plan_id,
            "source": plan.source,
            "parallel_waves": [list(wave) for wave in plan.parallel_waves],
            "steps": [{
                "id": step.step_id,
                "role": step.role,
                "depends_on": list(step.depends_on),
                "required_capabilities": list(step.required_capabilities),
            } for step in plan.steps],
        },
        "creative_gates": list(profile.creative_gates),
        "commander_admission": admission,
        "commander_batching": {
            "status": "preview_only_worker_integration_pending",
            "batch_count": len(batches),
            "max_steps_per_batch": max_steps,
            "checkpoint_persistence_available": False,
            "execution_eligible": False,
            "batches": [batch.as_dict() for batch in batches],
        },
        "execution": "not_started",
        "commander_submission": "not_submitted",
        "note": "预览只生成 Commander DAG；实际能力、权限、资源、成本与审批须由 Muse Commander 在执行前重新核验。",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse declarative video studio profiles and plan-only Commander previews")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pipelines", help="list the 12 video production profiles")
    plan_parser = sub.add_parser("plan", help="preview a profile-specific Commander DAG; never executes it")
    plan_parser.add_argument("storyboard_json")
    plan_parser.add_argument("--pipeline", choices=tuple(_PROFILE_BY_ID), default="animated_explainer")
    args = parser.parse_args(argv)

    if args.command == "pipelines":
        print(json.dumps({
            "status": "catalog_ready",
            "profile_count": len(PIPELINE_PROFILES),
            "execution": "not_started",
            "manifest_format": "yaml-1.2-json-subset",
            "manifest": PIPELINE_MANIFEST_PATH.name,
            "pipelines": list_video_pipeline_profiles(),
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        storyboard = json.loads(Path(args.storyboard_json).read_text(encoding="utf-8"))
        result = build_video_studio_preview(storyboard, args.pipeline)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "invalid_video_studio_plan", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
