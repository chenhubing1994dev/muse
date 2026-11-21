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


def compose_video_studio_package(
    prompt_spec_data: Mapping[str, Any],
    pipeline_id: str,
    story_bible: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a director brief and pass its validated storyboard to VideoStudio."""
    spec = video_prompt_spec_from_dict(prompt_spec_data)
    compiled = compile_video_prompt(spec, story_bible)
    studio_preview = build_video_studio_preview(compiled["storyboard"], pipeline_id)
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
