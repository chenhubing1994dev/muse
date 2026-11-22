"""Compose director prompts directly into a VideoStudio profile preview.

This adapter joins existing prompt composition and VideoStudio planning. It does not
create jobs, submit Commander plans, persist checkpoints, or render media.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from muse.video_prompt_composer import compile_video_prompt, video_prompt_spec_from_dict
from muse.video_studio import PIPELINE_PROFILES, build_video_studio_preview


def _build_worker_handoff_candidate(compiled: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a compiled storyboard into the current Worker contract without dispatching it."""
    storyboard = compiled["storyboard"]
    scenes = storyboard["scenes"]
    blockers: list[str] = []
    single_scene = len(scenes) == 1
    if not 1 <= len(scenes) <= 20:
        blockers.append("worker_requires_1_to_20_scenes")
    total_duration = 0.0
    previous_scene_ids: set[str] = set()
    for scene in scenes:
        scene_id = scene["id"]
        duration = scene["duration_seconds"]
        total_duration += float(duration)
        if not 1 <= float(duration) <= 5:
            blockers.append(f"scene_duration_out_of_worker_range:{scene_id}")
        if any(dependency not in previous_scene_ids for dependency in scene.get("depends_on", [])):
            blockers.append(f"scene_dependency_order_not_worker_safe:{scene_id}")
        previous_scene_ids.add(scene_id)
    if total_duration > 60:
        blockers.append("worker_scene_total_exceeds_60_seconds")
    audio_plan = compiled["audio_plan"]
    if (audio_plan.get("audio_mood") or audio_plan.get("cues")
            or audio_plan.get("tempo_bpm") is not None
            or audio_plan.get("align_scene_cuts_to_beats")):
        blockers.append("audio_plan_requires_audio_pipeline_mapping")
    single_scene_negative = "\n".join(
        item for item in (storyboard["negative_prompt"], scenes[0].get("negative_prompt", "") if single_scene else "")
        if item
    )
    if len(single_scene_negative) > 1000:
        blockers.append("combined_single_scene_negative_prompt_exceeds_1000_characters")
    if not single_scene:
        for scene in scenes:
            combined_scene_negative = "\n".join(
                item for item in (storyboard["negative_prompt"], scene.get("negative_prompt", "")) if item
            )
            if len(combined_scene_negative) > 1000:
                blockers.append(f"combined_scene_negative_prompt_exceeds_1000_characters:{scene['id']}")
    candidate = None
    if not blockers and single_scene:
        candidate = {
            "title": storyboard["title"],
            "prompt": scenes[0]["prompt"],
            "negative_prompt": single_scene_negative,
            "aspect_ratio": storyboard["aspect_ratio"],
            "duration_seconds": float(scenes[0]["duration_seconds"]),
            "steps": 8,
        }
    elif not blockers:
        candidate = {
            "title": storyboard["title"],
            "prompt": compiled["master_prompt"],
            "negative_prompt": storyboard["negative_prompt"],
            "aspect_ratio": storyboard["aspect_ratio"],
            "duration_seconds": round(total_duration, 3),
            "steps": 8,
            "scenes": [
                {
                    "prompt": scene["prompt"],
                    "duration_seconds": float(scene["duration_seconds"]),
                    **({"negative_prompt": scene["negative_prompt"]} if scene.get("negative_prompt") else {}),
                }
                for scene in scenes
            ],
        }
    return {
        "status": "ready_for_review" if not blockers else "blocked",
        "contract": "creator_brief.video",
        "worker_mode": "single_prompt" if single_scene else "multi_scene",
        "blockers": blockers,
        "creator_brief_video": candidate,
        "scene_mapping": [
            {"source_scene_id": scene["id"], "worker_scene_id": f"shot_{index:03d}"}
            for index, scene in enumerate(scenes, start=1)
        ],
        "execution": "not_started",
        "commander_submission": "not_submitted",
        "dispatch_authority": "Muse Commander",
    }


def compose_video_studio_package(
    prompt_spec_data: Mapping[str, Any],
    pipeline_id: str,
    story_bible: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a director brief and pass its validated storyboard to VideoStudio."""
    spec = video_prompt_spec_from_dict(prompt_spec_data)
    compiled = compile_video_prompt(spec, story_bible)
    studio_preview = build_video_studio_preview(compiled["storyboard"], pipeline_id)
    worker_handoff = _build_worker_handoff_candidate(compiled)
    return {
        "status": "preview_only",
        "title": compiled["storyboard"]["title"],
        "composition": {
            "video_prompt_spec_version": compiled["video_prompt_spec_version"],
            "master_prompt": compiled["master_prompt"],
            "storyboard": compiled["storyboard"],
            "clip_manifest": compiled["clip_manifest"],
            "audio_plan": compiled["audio_plan"],
            "model_guidance": compiled["model_guidance"],
            "source_ledger": compiled["source_ledger"],
            "continuity_validation": compiled["continuity_validation"],
        },
        "video_studio": studio_preview,
        "worker_handoff": worker_handoff,
        "execution": "not_started",
        "commander_submission": "not_submitted",
        "dispatch_authority": "Muse Commander",
        "checkpoint_persistence_available": studio_preview["commander_batching"]["checkpoint_persistence_available"],
        "execution_eligible": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compose a director prompt into a VideoStudio profile preview; never dispatches or renders"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pipelines", help="list available VideoStudio profiles")
    compose = sub.add_parser("compose", help="compile a prompt spec and preview one VideoStudio profile")
    compose.add_argument("prompt_spec_json")
    compose.add_argument("--story-bible", dest="story_bible_json")
    compose.add_argument("--pipeline", choices=tuple(profile.pipeline_id for profile in PIPELINE_PROFILES),
                         default="animated_explainer")
    args = parser.parse_args(argv)

    if args.command == "pipelines":
        print(json.dumps({
            "status": "catalog_ready",
            "profile_count": len(PIPELINE_PROFILES),
            "execution": "not_started",
            "pipelines": [profile.as_dict() for profile in PIPELINE_PROFILES],
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        prompt_data = json.loads(Path(args.prompt_spec_json).read_text(encoding="utf-8"))
        story_bible = (
            json.loads(Path(args.story_bible_json).read_text(encoding="utf-8"))
            if args.story_bible_json else None
        )
        result = compose_video_studio_package(prompt_data, args.pipeline, story_bible)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "invalid_video_studio_package", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
