"""One-command local video assembly from an asset folder using FFmpeg.

This is a deterministic editing/export stage, not an AI video generator. AI shots
can be supplied later by ComfyUI/Velorn and then assembled by the same pipeline.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence


VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"})


def _natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", path.name)]


def discover_video_assets(directory: str | Path) -> list[Path]:
    """Return supported image/video assets in natural filename order."""
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise ValueError("input directory does not exist or is not a directory")
    assets = [item for item in root.iterdir()
              if item.is_file() and item.suffix.casefold() in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS]
    assets.sort(key=_natural_key)
    if not assets:
        raise ValueError("input directory contains no supported video or image assets")
    if len(assets) > 100:
        raise ValueError("at most 100 scenes are accepted per one-click render")
    return assets


def _concat_quote(path: Path) -> str:
    value = path.resolve().as_posix()
    if any(char in value for char in ("\n", "\r", "\x00")):
        raise ValueError("asset path contains unsupported control characters")
    return "file '" + value.replace("'", "'\\''") + "'"


def _run(command: list[str], runner: Callable, timeout: int) -> subprocess.CompletedProcess:
    return runner(command, capture_output=True, text=True, timeout=timeout, check=False)


def _parse_frame_rate(value: object) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, denominator = value.split("/", 1)
    try:
        top, bottom = float(numerator), float(denominator)
    except ValueError:
        return None
    if not math.isfinite(top) or not math.isfinite(bottom) or bottom <= 0:
        return None
    rate = top / bottom
    return rate if math.isfinite(rate) and rate > 0 else None


def assemble_video_from_folder(
    input_directory: str | Path,
    output_path: str | Path,
    *,
    ffmpeg_path: str,
    ffprobe_path: str = "ffprobe",
    available_encoders: set[str] | frozenset[str] | tuple[str, ...] = (),
    audio_path: str | Path | None = None,
    audio_mode: str = "music",
    image_seconds: float = 4.0,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    bitrate: str = "12M",
    encoder_preference: str = "auto",
    timeout_seconds: int = 3600,
    runner: Callable = subprocess.run,
    gpu_resource_db: str | Path | None = None,
) -> dict[str, object]:
    """Render sorted still/video scenes, concatenate, optionally mix one audio track,
    and verify the resulting MP4 with ffprobe. Source assets are never modified.
    """
    if not isinstance(ffmpeg_path, str) or not ffmpeg_path.strip():
        raise ValueError("ffmpeg_path must be non-empty text")
    if audio_mode not in {"music", "narration"}:
        raise ValueError("audio_mode must be music or narration")
    if not math.isfinite(image_seconds) or not 0.5 <= image_seconds <= 60:
        raise ValueError("image_seconds must be between 0.5 and 60")
    if not (320 <= width <= 3840 and 320 <= height <= 3840 and 1 <= fps <= 60):
        raise ValueError("resolution or fps is outside the supported range")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")

    assets = discover_video_assets(input_directory)
    target = Path(output_path).expanduser()
    if target.suffix.casefold() != ".mp4":
        raise ValueError("output path must use the .mp4 extension")
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    audio = Path(audio_path).expanduser() if audio_path is not None else None
    if audio is not None and (not audio.is_file() or audio.suffix.casefold() not in AUDIO_EXTENSIONS):
        raise ValueError("audio_path must be an existing supported audio file")

    if encoder_preference not in {"auto", "amf", "software"}:
        raise ValueError("encoder_preference must be auto, amf or software")
    if encoder_preference == "amf" and "h264_amf" not in set(available_encoders):
        raise ValueError("AMD AMF H.264 encoder is not available in the detected FFmpeg build")
    encoder = ("h264_amf" if "h264_amf" in set(available_encoders) else "libx264")
    if encoder_preference == "amf":
        encoder = "h264_amf"
    elif encoder_preference == "software":
        encoder = "libx264"
    filter_value = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps},format=yuv420p"
    )
    created_output = False
    gpu_lease = None
    try:
        if encoder == "h264_amf":
            from muse.gpu_resources import GpuLeaseSession, GpuResourceCoordinator
            database = (
                Path(gpu_resource_db).expanduser()
                if gpu_resource_db is not None
                else Path(__file__).resolve().parents[2] / "data" / "gpu-resources.sqlite3"
            )
            coordinator = GpuResourceCoordinator(database)
            gpu_lease = GpuLeaseSession(
                coordinator, "video-factory-encode-" + uuid.uuid4().hex[:16],
                owner_kind="video_encoding", model_probe=lambda: _probe_encoder_gpu_state(Path(__file__).resolve().parents[2]),
            )
            gpu_lease.__enter__()
        with tempfile.TemporaryDirectory(prefix="muse-video-", dir=str(target.parent)) as work:
            work_dir = Path(work)
            rendered: list[Path] = []
            for index, asset in enumerate(assets):
                scene = work_dir / f"scene-{index:04d}.mp4"
                command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
                if asset.suffix.casefold() in IMAGE_EXTENSIONS:
                    command.extend(["-loop", "1", "-framerate", str(fps), "-i", str(asset),
                                    "-t", str(image_seconds)])
                else:
                    command.extend(["-i", str(asset)])
                command.extend([
                    "-vf", filter_value, "-an", "-c:v", encoder, "-b:v", bitrate,
                    "-r", str(fps), "-g", str(fps * 2), "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(scene),
                ])
                result = _run(command, runner, timeout_seconds)
                if result.returncode != 0 or not scene.is_file() or scene.stat().st_size == 0:
                    return {
                        "status": "failed", "stage": "scene_render", "scene": asset.name,
                        "exit_code": result.returncode, "error": (result.stderr or "")[-2000:],
                        "output": str(target),
                    }
                rendered.append(scene)

            concat_file = work_dir / "concat.txt"
            concat_file.write_text("\n".join(_concat_quote(item) for item in rendered) + "\n",
                                    encoding="utf-8")
            command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                       "-f", "concat", "-safe", "0", "-i", str(concat_file)]
            if audio is not None:
                if audio_mode == "music":
                    command.extend(["-stream_loop", "-1", "-i", str(audio)])
                else:
                    command.extend(["-i", str(audio)])
                command.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                                "-c:a", "aac", "-b:a", "192k"])
                if audio_mode == "narration":
                    command.extend(["-af", "apad"])
                command.extend(["-shortest"])
            else:
                command.extend(["-map", "0:v:0", "-c:v", "copy"])
            command.extend(["-movflags", "+faststart", str(target)])
            result = _run(command, runner, timeout_seconds)
            if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
                if target.exists():
                    target.unlink()
                return {
                    "status": "failed", "stage": "final_mux", "exit_code": result.returncode,
                    "error": (result.stderr or "")[-2000:], "output": str(target),
                }
            created_output = True

            probe = _run([
                ffprobe_path, "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate", "-of", "json", str(target),
            ], runner, min(timeout_seconds, 30))
            if probe.returncode != 0:
                raise RuntimeError("ffprobe could not verify the rendered MP4")
            metadata = json.loads(probe.stdout or "{}")
            streams = metadata.get("streams", [])
            video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
            duration = float(metadata.get("format", {}).get("duration", 0))
            if video_stream is None or not math.isfinite(duration) or duration <= 0:
                raise RuntimeError("rendered file has no valid video stream or duration")
            if video_stream.get("width") != width or video_stream.get("height") != height:
                raise RuntimeError("rendered video dimensions do not match the requested canvas")
            actual_fps = _parse_frame_rate(
                video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
            )
            if actual_fps is None or abs(actual_fps - fps) > 0.5:
                raise RuntimeError("rendered video frame rate does not match the requested fps")
            audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
            if audio is not None and audio_stream is None:
                raise RuntimeError("requested audio track is missing from the rendered MP4")
            return {
                "status": "completed", "output": str(target.resolve()),
                "scene_count": len(assets), "duration_seconds": round(duration, 3),
                "width": video_stream.get("width"), "height": video_stream.get("height"),
                "fps": round(actual_fps, 3), "has_audio": audio_stream is not None,
                "encoder": encoder, "audio_mode": audio_mode if audio else None,
                "bytes": target.stat().st_size,
            }
    except subprocess.TimeoutExpired:
        if created_output and target.exists():
            target.unlink()
        return {"status": "timed_out", "timeout_seconds": timeout_seconds, "output": str(target)}
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        if created_output and target.exists():
            target.unlink()
        return {"status": "failed", "stage": "verification_or_setup", "error": str(exc),
                "output": str(target)}
    finally:
        if gpu_lease is not None:
            try:
                gpu_lease.__exit__(None, None, None)
            except RuntimeError as exc:
                if target.exists():
                    target.unlink()
                return {"status": "failed", "stage": "gpu_resource_lock", "error": str(exc),
                        "output": str(target)}


def _probe_encoder_gpu_state(workspace: Path) -> dict[str, str]:
    """AMF encoding waits for Muse-managed inference to yield and blocks on unknown state."""
    from muse.gpu_resources import probe_gpu_workloads
    return probe_gpu_workloads(workspace)

def _select_verified_encoder(preference: str, capabilities: dict[str, dict[str, object]]) -> dict[str, str]:
    if preference not in {"auto", "amf", "software"}:
        raise ValueError("encoder preference must be auto, amf or software")
    available = {
        name for name in ("h264_amf", "libx264")
        if capabilities.get(name, {}).get("capability", {}).get("status") == "available"
    }
    if preference == "amf":
        if "h264_amf" not in available:
            raise RuntimeError("requested AMD AMF H.264 encoder failed its real MP4/FFprobe/full-decode smoke test")
        return {"encoder": "h264_amf", "requested": preference, "fallback_reason": ""}
    if preference == "software":
        if "libx264" not in available:
            raise RuntimeError("requested CPU libx264 encoder failed its real MP4/FFprobe/full-decode smoke test")
        return {"encoder": "libx264", "requested": preference, "fallback_reason": ""}
    if "h264_amf" in available:
        return {"encoder": "h264_amf", "requested": preference, "fallback_reason": ""}
    if "libx264" in available:
        return {
            "encoder": "libx264", "requested": preference,
            "fallback_reason": "AMF failed real encode/decode validation; explicitly selected verified CPU libx264",
        }
    raise RuntimeError("neither AMD AMF nor CPU libx264 passed real encode/decode validation")


def main(argv: Sequence[str] | None = None, *, commander_planner: Any | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-command local video assembly with FFmpeg")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("engines", help="read-only health check for local ComfyUI and Velorn MCP")
    sub.add_parser("capabilities", help="probe local encoders and report service health separately from model capability")
    create = sub.add_parser("create", help="create a topic/script/URL video package and submit one Commander approval Ticket")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--topic", help="video topic")
    source.add_argument("--script-file", help="UTF-8 narration script file")
    source.add_argument("--url", help="public HTTPS source URL")
    create.add_argument("--title", help="optional title for script or URL input")
    create.add_argument("--audience", default="一般观众")
    create.add_argument("--aspect-ratio", choices=("9:16", "16:9", "1:1"), default="9:16")
    create.add_argument("--duration", type=int, choices=tuple(range(30, 61, 5)), default=30)
    create.add_argument("--pipeline", default="animated_explainer")
    create.add_argument("--workspace", help="Muse project workspace; defaults to the current project")

    queue_approved = sub.add_parser("queue-approved", help="queue a video_pipeline execution Ticket after exact human approval")
    queue_approved.add_argument("approval_task_id")
    queue_approved.add_argument("--workspace", help="Muse project workspace; defaults to the current project")
    adapt = sub.add_parser("adapt-mpt", help="preview a MoneyPrinterTurbo parameter mapping; never executes providers")
    adapt.add_argument("params_json", help="JSON object or array of external video parameter candidates")
    plan = sub.add_parser("plan", help="validate a storyboard and preview its parallel Commander DAG")
    plan.add_argument("storyboard_json")
    plan.add_argument("--story-bible", dest="story_bible_json", help="optional Muse Story Bible JSON for continuity cross-validation")
    compose = sub.add_parser("compose", help="compile a director prompt spec into a validated storyboard and Commander DAG preview")
    compose.add_argument("prompt_spec_json", help="JSON VideoPromptSpec file")
    compose.add_argument("--story-bible", dest="story_bible_json", help="optional Muse Story Bible JSON for continuity locks")
    make = sub.add_parser("make", help="assemble images/video clips in a folder into one MP4")
    make.add_argument("input_directory")
    make.add_argument("output")
    make.add_argument("--audio")
    make.add_argument("--audio-mode", choices=("music", "narration"), default="music")
    make.add_argument("--image-seconds", type=float, default=4.0)
    make.add_argument("--size", choices=("1920x1080", "1080x1920", "1080x1080"), default="1920x1080")
    make.add_argument("--fps", type=int, default=30)
    make.add_argument("--bitrate", default="12M")
    make.add_argument("--encoder", choices=("auto", "amf", "software"), default="auto")
    args = parser.parse_args(argv)
    if args.command == "engines":
        from muse.video_engines import inspect_video_engines
        result = inspect_video_engines()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if any(item.get("status") == "ready" for item in result.values()) else 2

    if args.command == "capabilities":
        from muse.video_capabilities import probe_ffmpeg_encoders, probe_video_services
        from muse.media import discover_media_tools
        inventory = discover_media_tools()
        ffmpeg = next((item.executable for item in inventory.tools if item.tool_id == "ffmpeg"), None)
        ffprobe = next((item.executable for item in inventory.tools if item.tool_id == "ffprobe"), None)
        if not ffmpeg or not ffprobe:
            print(json.dumps({"status": "blocked", "reason": "FFmpeg and FFprobe are required"}, ensure_ascii=False, indent=2))
            return 2
        result = {
            "encoders": probe_ffmpeg_encoders(ffmpeg, ffprobe),
            "services": probe_video_services(Path(__file__).resolve().parents[2]),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        encoder_ready = any(item.get("capability", {}).get("status") == "available" for item in result["encoders"].values())
        return 0 if encoder_ready else 2

    if args.command == "create":
        from muse.video_factory_intake import create_video_task
        workspace = Path(args.workspace).expanduser() if args.workspace else Path(__file__).resolve().parents[2]
        try:
            if commander_planner is None:
                from muse.commander_planner import CommanderPlanner
                from muse.local_planner import LocalModelCommanderPlanner
                commander_planner = CommanderPlanner(
                    LocalModelCommanderPlanner(
                        memory_database=workspace / "data" / "memory" / "muse.sqlite3",
                        project="muse",
                    ),
                    strict_model=True,
                )
            result = create_video_task(
                workspace,
                commander_planner=commander_planner,
                topic=args.topic,
                script_file=args.script_file,
                source_url=args.url,
                title=args.title,
                audience=args.audience,
                aspect_ratio=args.aspect_ratio,
                duration_seconds=args.duration,
                pipeline_id=args.pipeline,
            )
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "blocked", "stage": "video_factory_intake", "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "queue-approved":
        from muse.video_factory_intake import queue_approved_video_pipeline
        workspace = Path(args.workspace).expanduser() if args.workspace else Path(__file__).resolve().parents[2]
        try:
            result = queue_approved_video_pipeline(workspace, args.approval_task_id)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "blocked", "stage": "commander_video_queue", "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "adapt-mpt":
        from muse.video_recipe import adapt_moneyprinterturbo_batch, adapt_moneyprinterturbo_params
        from muse.video_workflow import build_video_commander_plan

        def plan_preview(recipe):
            plan = build_video_commander_plan(recipe)
            return {
                "source": plan.source,
                "parallel_waves": [list(wave) for wave in plan.parallel_waves],
                "step_ids": [step.step_id for step in plan.steps],
            }

        try:
            source_params = json.loads(Path(args.params_json).read_text(encoding="utf-8"))
            if isinstance(source_params, list):
                batch = adapt_moneyprinterturbo_batch(source_params)
                for candidate in batch["candidates"]:
                    candidate["commander_plan_preview"] = (
                        plan_preview(candidate["recipe"])
                        if candidate["mapping_report"]["ready_for_commander_plan"] else None
                    )
                print(json.dumps(batch, ensure_ascii=False, indent=2))
            else:
                adapted = adapt_moneyprinterturbo_params(source_params)
                report = adapted["mapping_report"]
                preview = plan_preview(adapted["recipe"]) if report["ready_for_commander_plan"] else None
                print(json.dumps({
                    "status": report["status"],
                    "mapping_report": report,
                    "recipe": adapted["recipe"],
                    "commander_plan_preview": preview,
                    "execution": "not_started",
                    "commander_submission": "not_submitted",
                }, ensure_ascii=False, indent=2))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            print(json.dumps({"status": "invalid_mpt_params", "error": str(exc)}, ensure_ascii=False))
            return 2
        return 0

    if args.command == "compose":
        from muse.video_prompt_composer import compile_video_prompt, video_prompt_spec_from_dict
        from muse.video_workflow import build_video_commander_plan

        try:
            spec_data = json.loads(Path(args.prompt_spec_json).read_text(encoding="utf-8"))
            spec = video_prompt_spec_from_dict(spec_data)
            story_bible = (
                json.loads(Path(args.story_bible_json).read_text(encoding="utf-8"))
                if args.story_bible_json else None
            )
            compiled = compile_video_prompt(spec, story_bible)
            commander_plan = build_video_commander_plan(compiled["storyboard"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            print(json.dumps({"status": "invalid_video_prompt_package", "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({
            "status": "composed",
            "video_prompt_spec_version": compiled["video_prompt_spec_version"],
            "title": compiled["storyboard"]["title"],
            "master_prompt": compiled["master_prompt"],
            "storyboard": compiled["storyboard"],
            "clip_manifest": compiled["clip_manifest"],
            "audio_plan": compiled["audio_plan"],
            "model_guidance": compiled["model_guidance"],
            "source_ledger": compiled["source_ledger"],
            "continuity_validation": compiled["continuity_validation"],
            "commander_plan_preview": {
                "plan_id": commander_plan.plan_id,
                "source": commander_plan.source,
                "parallel_waves": [list(wave) for wave in commander_plan.parallel_waves],
                "steps": [{
                    "id": step.step_id,
                    "role": step.role,
                    "depends_on": list(step.depends_on),
                    "required_capabilities": list(step.required_capabilities),
                } for step in commander_plan.steps],
            },
            "execution": "not_started",
            "commander_submission": "not_submitted",
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan":
        from muse.video_workflow import build_video_commander_plan, validate_storyboard
        try:
            storyboard = validate_storyboard(json.loads(Path(args.storyboard_json).read_text(encoding="utf-8")))
            story_bible_summary = None
            if args.story_bible_json:
                from muse.story_bible import validate_story_bible, validate_story_continuity
                story_bible = validate_story_bible(json.loads(Path(args.story_bible_json).read_text(encoding="utf-8")))
                continuity_summary = validate_story_continuity(story_bible, storyboard)
                story_bible_summary = {
                    "schema_version": story_bible["schema_version"],
                    "character_count": len(story_bible["characters"]),
                    "location_count": len(story_bible["locations"]),
                    "prop_count": len(story_bible["props"]),
                    "continuity_scene_count": continuity_summary["continuity_scene_count"],
                }
            commander_plan = build_video_commander_plan(storyboard)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(json.dumps({"status": "invalid_story_package" if args.story_bible_json else "invalid_storyboard", "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({
            "status": "validated",
            "title": storyboard["title"],
            "scene_count": len(storyboard["scenes"]),
            "aspect_ratio": storyboard["aspect_ratio"],
            "fps": storyboard["fps"],
            "story_bible": story_bible_summary,
            "plan_id": commander_plan.plan_id,
            "source": commander_plan.source,
            "parallel_waves": [list(wave) for wave in commander_plan.parallel_waves],
            "steps": [{
                "id": step.step_id, "role": step.role,
                "depends_on": list(step.depends_on),
                "required_capabilities": list(step.required_capabilities),
            } for step in commander_plan.steps],
            "execution": "not_started",
        }, ensure_ascii=False, indent=2))
        return 0

    from muse.media import discover_media_tools
    inventory = discover_media_tools()
    ffmpeg = next((item.executable for item in inventory.tools if item.tool_id == "ffmpeg"), None)
    ffprobe = next((item.executable for item in inventory.tools if item.tool_id == "ffprobe"), None)
    if not ffmpeg or not ffprobe:
        print(json.dumps({"status": "blocked", "error": "FFmpeg and FFprobe must both be installed."},
                         ensure_ascii=False))
        return 2
    width, height = (int(part) for part in args.size.split("x"))
    from muse.video_capabilities import probe_ffmpeg_encoders
    encoder_capabilities = probe_ffmpeg_encoders(ffmpeg, ffprobe)
    try:
        encoder_selection = _select_verified_encoder(args.encoder, encoder_capabilities)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc),
                          "encoder_capabilities": encoder_capabilities}, ensure_ascii=False, indent=2))
        return 2
    verified_encoders = {encoder_selection["encoder"]}
    result = assemble_video_from_folder(
        args.input_directory, args.output, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
        available_encoders=verified_encoders, audio_path=args.audio,
        audio_mode=args.audio_mode, image_seconds=args.image_seconds,
        width=width, height=height, fps=args.fps, bitrate=args.bitrate,
        encoder_preference=args.encoder,
    )
    result["encoder_capabilities"] = encoder_capabilities
    result["encoder_selection"] = encoder_selection
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
