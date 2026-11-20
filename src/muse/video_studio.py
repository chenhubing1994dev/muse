"""OpenMontage-inspired video production profiles and plan-only Commander DAGs.

This module defines reusable production recipes; it does not execute media tools,
create jobs, spend money, download sources, or publish. Muse Commander remains the
only execution and approval authority.
"""
from __future__ import annotations

import argparse
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


def assess_video_studio_commander_admission(plan: CommanderPlan, *, max_steps: int = 16) -> dict[str, Any]:
    """Fail closed before dispatch when the preview exceeds Commander planning limits."""
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 2 <= max_steps <= 32:
        raise ValueError("max_steps must be between 2 and 32")
    invalid_roles = sorted({step.role for step in plan.steps} - CommanderPlanner.ALLOWED_ROLES)
    result = {
        "step_count": len(plan.steps),
        "max_steps": max_steps,
        "invalid_roles": invalid_roles,
        "capability_registry_checked": False,
        "role_capability_authorization_checked": False,
        "execution_eligible": False,
    }
    if invalid_roles:
        return result | {"status": "blocked_role_allowlist", "reason": "one or more plan roles are not admitted by CommanderPlanner"}
    if len(plan.steps) > max_steps:
        return result | {"status": "blocked_step_limit", "reason": "split the DAG into bounded, checkpointed Commander batches before dispatch"}
    return result | {"status": "pending_capability_registry", "reason": "role allowlist passed; local capability availability, role-to-capability authorization, resource/cost checks and human gates remain unverified"}


def build_video_studio_preview(storyboard: Mapping[str, Any], pipeline_id: str) -> dict[str, Any]:
    profile, plan = build_video_studio_plan(storyboard, pipeline_id)
    normalized = validate_storyboard(storyboard)
    return {
        "status": "validated",
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
        "commander_admission": assess_video_studio_commander_admission(plan),
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
