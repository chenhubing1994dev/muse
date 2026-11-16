"""Strict storyboard validation and deterministic video-production DAG planning.

This module creates plans only. It does not generate media, enqueue work, install
models, or invoke ComfyUI/Velorn; execution remains with Muse Commander.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from typing import Any, Mapping

from muse.commander_planner import CommanderPlan, PlannedStep

STORYBOARD_SCHEMA_VERSION = 1
MAX_STORYBOARD_BYTES = 65_536
MAX_SCENES = 24
_SCENE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_ASPECT_RATIOS = frozenset({"16:9", "9:16", "1:1"})
_FPS = frozenset({24, 25, 30, 50, 60})


def _scene_execution_waves(scenes: list[dict[str, Any]]) -> list[list[str]]:
    """Return deterministic topological levels for parallel shot execution."""
    remaining = {scene["id"]: set(scene["depends_on"]) for scene in scenes}
    completed: set[str] = set()
    waves: list[list[str]] = []
    while remaining:
        ready = [
            scene["id"] for scene in scenes
            if scene["id"] in remaining and remaining[scene["id"]].issubset(completed)
        ]
        if not ready:
            raise ValueError("scene dependencies contain a cycle")
        waves.append(ready)
        completed.update(ready)
        for scene_id in ready:
            del remaining[scene_id]
    return waves


def validate_storyboard(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a bounded, versioned storyboard JSON object."""
    if not isinstance(value, Mapping):
        raise ValueError("storyboard must be a JSON object")
    allowed = {"schema_version", "title", "aspect_ratio", "fps", "style", "negative_prompt", "scenes"}
    if set(value) - allowed or not {"schema_version", "title", "aspect_ratio", "fps", "scenes"} <= set(value):
        raise ValueError("storyboard has missing or unsupported top-level fields")
    if value["schema_version"] != STORYBOARD_SCHEMA_VERSION or isinstance(value["schema_version"], bool):
        raise ValueError("unsupported storyboard schema_version")
    title = value["title"]
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError("title must contain 1-160 characters")
    aspect_ratio = value["aspect_ratio"]
    if aspect_ratio not in _ASPECT_RATIOS:
        raise ValueError("aspect_ratio must be 16:9, 9:16 or 1:1")
    fps = value["fps"]
    if isinstance(fps, bool) or not isinstance(fps, int) or fps not in _FPS:
        raise ValueError("fps must be one of 24, 25, 30, 50 or 60")
    style = value.get("style", "")
    negative_prompt = value.get("negative_prompt", "")
    for field, text, maximum in (("style", style, 500), ("negative_prompt", negative_prompt, 1000)):
        if not isinstance(text, str) or len(text) > maximum:
            raise ValueError(f"{field} must be text of at most {maximum} characters")
    scenes = value["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= MAX_SCENES:
        raise ValueError(f"scenes must contain 1-{MAX_SCENES} entries")

    normalized_scenes = []
    seen: set[str] = set()
    scene_fields = {"id", "prompt", "duration_seconds", "camera", "negative_prompt", "depends_on"}
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, Mapping) or set(scene) - scene_fields:
            raise ValueError(f"scene {index} has an invalid object shape")
        if not {"id", "prompt", "duration_seconds"} <= set(scene):
            raise ValueError(f"scene {index} is missing required fields")
        scene_id = scene["id"]
        prompt = scene["prompt"]
        duration = scene["duration_seconds"]
        if not isinstance(scene_id, str) or not _SCENE_ID.fullmatch(scene_id) or scene_id in seen:
            raise ValueError(f"scene {index} has an invalid or duplicate id")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 2000:
            raise ValueError(f"scene {index} prompt must contain 1-2000 characters")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 1 <= duration <= 20:
            raise ValueError(f"scene {index} duration_seconds must be between 1 and 20")
        camera = scene.get("camera", "")
        scene_negative = scene.get("negative_prompt", "")
        if not isinstance(camera, str) or len(camera) > 160:
            raise ValueError(f"scene {index} camera must be at most 160 characters")
        if not isinstance(scene_negative, str) or len(scene_negative) > 1000:
            raise ValueError(f"scene {index} negative_prompt must be at most 1000 characters")
        dependencies = scene.get("depends_on", [])
        if (not isinstance(dependencies, list) or len(dependencies) > 12
                or any(not isinstance(dep, str) or not _SCENE_ID.fullmatch(dep) for dep in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise ValueError(f"scene {index} has invalid depends_on entries")
        if scene_id in dependencies:
            raise ValueError(f"scene {index} cannot depend on itself")
        seen.add(scene_id)
        normalized_scenes.append({
            "id": scene_id,
            "prompt": prompt.strip(),
            "duration_seconds": float(duration),
            "camera": camera.strip(),
            "negative_prompt": scene_negative.strip(),
            "depends_on": list(dependencies),
        })

    known_scene_ids = {scene["id"] for scene in normalized_scenes}
    for scene in normalized_scenes:
        missing = set(scene["depends_on"]) - known_scene_ids
        if missing:
            raise ValueError(f"scene {scene['id']} depends on unknown scenes: {sorted(missing)}")

    _scene_execution_waves(normalized_scenes)  # validate that dependencies are acyclic

    normalized = {
        "schema_version": STORYBOARD_SCHEMA_VERSION,
        "title": title.strip(),
        "aspect_ratio": aspect_ratio,
        "fps": fps,
        "style": style.strip(),
        "negative_prompt": negative_prompt.strip(),
        "scenes": normalized_scenes,
    }
    try:
        encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("storyboard must contain finite JSON-compatible values") from None
    if len(encoded) > MAX_STORYBOARD_BYTES:
        raise ValueError("storyboard exceeds the 64 KiB size limit")
    return normalized


def build_video_commander_plan(value: Mapping[str, Any]) -> CommanderPlan:
    """Build a validated DAG: preflight -> parallel shots -> assemble -> review."""
    storyboard = validate_storyboard(value)
    scene_waves = _scene_execution_waves(storyboard["scenes"])
    steps: list[PlannedStep] = [
        PlannedStep(
            step_id="preflight",
            role="operator",
            objective="检查模型、工作流节点、素材引用、磁盘空间与视频参数；只做只读检查",
            depends_on=(),
            required_capabilities=("video_preflight",),
        )
    ]
    shot_ids = []
    for scene in storyboard["scenes"]:
        step_id = f"shot_{scene['id']}"
        shot_ids.append(step_id)
        steps.append(PlannedStep(
            step_id=step_id,
            role="developer",
            objective=f"生成并校验镜头 {scene['id']}；时长 {scene['duration_seconds']:g}s",
            depends_on=("preflight", *tuple(f"shot_{dep}" for dep in scene["depends_on"])),
            required_capabilities=("comfyui_generation",),
        ))
    steps.extend((
        PlannedStep(
            step_id="assemble",
            role="operator",
            objective="按分镜顺序组装全部镜头、音轨与字幕并输出 MP4",
            depends_on=tuple(shot_ids),
            required_capabilities=("ffmpeg", "video_assembly"),
        ),
        PlannedStep(
            step_id="quality_review",
            role="reviewer",
            objective="检查时长、画幅、帧率、黑帧、静音、字幕同步与镜头连续性",
            depends_on=("assemble",),
            required_capabilities=("media_review",),
        ),
    ))
    waves = (("preflight",), *(tuple(f"shot_{scene_id}" for scene_id in wave) for wave in scene_waves), ("assemble",), ("quality_review",))
    return CommanderPlan(
        plan_id=uuid.uuid4().hex,
        goal=f"本地 AI 视频制作：{storyboard['title']}",
        steps=tuple(steps),
        source="deterministic_video_storyboard",
        parallel_waves=waves,
    )
