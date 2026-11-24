"""Run technical and optical-flow diagnostics for the first creator pilot previews."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "outputs" / "creator_pilot_20261124"
VIDEOS = {
    "silent_base": OUT / "ai-verification-three-steps-9x16.mp4",
    "cantonese_voice_review": OUT / "ai-verification-three-steps-9x16-voice-review-only.mp4",
}


def main() -> int:
    from muse.media import discover_media_tools
    from muse.video_qa import inspect_video_file
    from muse.video_motion_diagnostics import inspect_video_motion

    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    if not tools.get("ffmpeg") or not tools.get("ffprobe"):
        raise RuntimeError("FFmpeg/FFprobe unavailable")
    report = {
        "schema": "muse.creator.preview-qa.v1",
        "status": "review_required",
        "videos": {},
        "release_policy": {
            "technical_decode_is_not_editorial_approval": True,
            "rights_review_required": True,
            "human_listening_review_required": True,
            "publish_api_called": False,
            "commercial_use_allowed": False,
        },
    }
    for name, path in VIDEOS.items():
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(path)
        technical = inspect_video_file(
            path,
            ffprobe_path=tools["ffprobe"],
            ffmpeg_path=tools["ffmpeg"],
            expected_width=1080,
            expected_height=1920,
            expected_fps=24,
            min_duration_seconds=20,
            max_duration_seconds=30,
            require_audio=True,
        )
        motion = inspect_video_motion(path, max_frames=180)
        report["videos"][name] = {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": technical.get("sha256"),
            "technical": technical,
            "motion_diagnostics": motion,
        }
    voice = report["videos"]["cantonese_voice_review"]
    report["status"] = (
        "technical_pass_review_required"
        if voice["technical"].get("status") == "passed"
        else "technical_failure"
    )
    target = OUT / "voiceover-preview-qa.json"
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "qa_report": str(target.relative_to(ROOT)).replace("\\", "/"),
        "base_video_warnings": [item.get("code") for item in report["videos"]["silent_base"]["technical"].get("warnings", [])],
        "voice_preview_warnings": [item.get("code") for item in voice["technical"].get("warnings", [])],
        "voice_integrated_lufs": voice["technical"].get("audio_quality", {}).get("integrated_lufs"),
        "voice_motion": voice["motion_diagnostics"].get("summary"),
        "publishable": False,
        "commercial_use_allowed": False,
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "technical_pass_review_required" else 1


if __name__ == "__main__":
    raise SystemExit(main())
