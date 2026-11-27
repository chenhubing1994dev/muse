"""Safe local Remotion rendering and FFmpeg assembly for Muse video products.

Remotion renders deterministic motion-graphics bumpers. FFmpeg combines them
with the original video while preserving its audio track and requested canvas.
No network access, shell interpolation, or source-media mutation is performed.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import math
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable


class RemotionMotionError(RuntimeError):
    """Remotion motion graphics could not be rendered or safely assembled."""



_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _failure_detail(completed: Any, fallback: str, *, head: int = 1500, tail: int = 1500) -> str:
    """B391-P2-36: keep the START of a failing tool's output (where Node/FFmpeg state the cause) and its end.

    The previous ``[-1200:]`` slice kept only stack frames, so the real error line was never reported.
    ANSI colour codes from the Remotion CLI are stripped.
    """
    text = getattr(completed, "stderr", None) or getattr(completed, "stdout", None) or fallback
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = _ANSI_ESCAPE.sub("", str(text)).strip() or fallback
    if len(text) <= head + tail + 20:
        return text
    return text[:head] + "\n… [" + str(len(text) - head - tail) + " characters omitted] …\n" + text[-tail:]

def _run_process(runner: Callable, command: list[str], **kwargs):
    try:
        return runner(command, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RemotionMotionError("a local Remotion/FFmpeg process could not be completed") from exc



def _stage_remotion_binaries(source: Path, target: Path) -> None:
    """Copy the Remotion compositor toolchain into a directory with clean ACLs.

    On some Windows hosts the installed compositor directory inherits a DACL that
    denies ``Everyone`` a right required to load its native executables; those
    executables then exit with STATUS_ACCESS_DENIED (0xC0000022). The system temp
    root has a normal DACL, so staging there lets the identical binaries run.
    Nothing under node_modules or the user's data is modified.
    """
    required = {"remotion.exe", "ffmpeg.exe", "ffprobe.exe"}
    if not source.is_dir():
        raise RemotionMotionError("the local Remotion compositor toolchain is missing")
    available = {item.name for item in source.iterdir() if item.is_file()}
    if not required <= available:
        raise RemotionMotionError("the local Remotion compositor toolchain is incomplete")
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_file() and not item.is_symlink() and item.suffix.casefold() in {".exe", ".dll"}:
            shutil.copyfile(item, target / item.name)
    if not all((target / name).is_file() for name in required):
        raise RemotionMotionError("the staged Remotion compositor toolchain is incomplete")


@contextlib.contextmanager
def _remotion_binaries(source: Path, runner: Callable, timeout: int):
    """Stage Remotion's native compositor outside the restricted project ACL."""
    directory = Path(tempfile.mkdtemp(prefix=".muse-remotion-binaries-"))
    try:
        _stage_remotion_binaries(source, directory)
        probe = _run_process(
            runner, [str(directory / "ffprobe.exe"), "-version"],
            capture_output=True, text=True, timeout=min(timeout, 45), check=False,
        )
        if probe.returncode != 0:
            raise RemotionMotionError(
                "Remotion compositor binaries are not executable: "
                + _failure_detail(probe, "staged Remotion ffprobe could not start")
            )
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _parse_rate(value: object) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    try:
        numerator, denominator = (float(part) for part in value.split("/", 1))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator <= 0:
        return None
    rate = numerator / denominator
    return rate if math.isfinite(rate) and rate > 0 else None


def _probe(path: Path, *, ffprobe_path: str, runner: Callable, timeout: int) -> dict[str, Any]:
    result = _run_process(
        runner,
        [ffprobe_path, "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=min(timeout, 45), check=False,
    )
    if result.returncode != 0:
        raise RemotionMotionError("FFprobe could not inspect a Remotion motion asset")
    try:
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        video = next(item for item in streams if item.get("codec_type") == "video")
        duration = float(payload.get("format", {}).get("duration", 0))
        fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    except (TypeError, ValueError, StopIteration, json.JSONDecodeError) as exc:
        raise RemotionMotionError("Remotion motion asset has invalid media metadata") from exc
    if not math.isfinite(duration) or duration <= 0 or fps is None:
        raise RemotionMotionError("Remotion motion asset has invalid duration or frame rate")
    return {
        "duration_seconds": duration, "width": video.get("width"),
        "height": video.get("height"), "fps": fps,
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
    }


class RemotionMotionAdapter:
    """Workspace-bound adapter that renders branded motion through local Remotion."""

    def __init__(
        self, workspace: str | Path, *, ffmpeg_path: str, ffprobe_path: str,
        timeout_seconds: int = 900,
    ) -> None:
        self.workspace = Path(workspace)
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.timeout_seconds = timeout_seconds

    def render(
        self, base_video: str | Path, output_video: str | Path, *,
        title: str, width: int, height: int, fps: int,
        audio_mode: str = "narration",
    ) -> dict[str, Any]:
        return render_motion_package(
            self.workspace, base_video, output_video, title=title,
            subtitle="MUSE · RESEARCH / CREATE / EVOLVE",
            width=width, height=height, fps=fps,
            ffmpeg_path=self.ffmpeg_path, ffprobe_path=self.ffprobe_path,
            timeout_seconds=self.timeout_seconds, audio_mode=audio_mode,
        )

    def render_scene_timeline(
        self, scene_paths: list[str | Path], audio_source: str | Path,
        output_video: str | Path, *, width: int, height: int, fps: int,
        image_seconds: float = 4.0, transition_seconds: float = 0.45,
        shot_directives: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Normalize source scenes and render camera/VFX presets through Remotion."""
        if not isinstance(scene_paths, list) or not 1 <= len(scene_paths) <= 40:
            raise ValueError("scene_paths must contain between 1 and 40 scenes")
        if not math.isfinite(image_seconds) or not 0.5 <= image_seconds <= 60:
            raise ValueError("image_seconds must be between 0.5 and 60")
        if not math.isfinite(transition_seconds) or not 0.1 <= transition_seconds <= 2:
            raise ValueError("transition_seconds must be between 0.1 and 2")
        return render_scene_timeline(
            self.workspace, scene_paths, audio_source, output_video,
            width=width, height=height, fps=fps,
            ffmpeg_path=self.ffmpeg_path, ffprobe_path=self.ffprobe_path,
            timeout_seconds=self.timeout_seconds, image_seconds=image_seconds,
            transition_seconds=transition_seconds, shot_directives=shot_directives,
        )


def _validate_shot_directives(
    value: list[dict[str, Any]] | None, scene_count: int,
) -> list[dict[str, str]]:
    """Validate bounded, deterministic per-shot camera and VFX presets."""
    if value is None:
        return [{"camera_move": "none", "vfx": "none", "accent": "#d8b46a"} for _ in range(scene_count)]
    if not isinstance(value, list) or len(value) != scene_count:
        raise ValueError("shot_directives must contain exactly one object per scene")
    camera_moves = {"none", "push_in", "pull_out", "drift_left", "drift_right", "rise"}
    vfx_presets = {"none", "embers", "mist", "moonlight", "ink"}
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"camera_move", "vfx", "accent"}:
            raise ValueError("each shot directive must contain only camera_move, vfx and accent")
        camera_move = item.get("camera_move", "none")
        vfx = item.get("vfx", "none")
        accent = item.get("accent", "#d8b46a")
        if not isinstance(camera_move, str) or camera_move not in camera_moves:
            raise ValueError("shot camera_move is not a supported preset")
        if not isinstance(vfx, str) or vfx not in vfx_presets:
            raise ValueError("shot vfx is not a supported preset")
        if not isinstance(accent, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
            raise ValueError("shot accent must be a six-digit hex color")
        normalized.append({"camera_move": camera_move, "vfx": vfx, "accent": accent})
    return normalized


def _scene_timing_records(
    clips: list[dict[str, Any]], *, transition_frames: int, fps: int,
) -> list[dict[str, Any]]:
    """Return authoritative scene intervals on a timeline with overlapping transitions."""
    if not isinstance(clips, list) or not clips:
        raise ValueError("clips must be a non-empty list")
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise ValueError("fps must be a positive integer")
    if (isinstance(transition_frames, bool) or not isinstance(transition_frames, int)
            or transition_frames < 0):
        raise ValueError("transition_frames must be a non-negative integer")
    frame_counts = []
    for index, clip in enumerate(clips, start=1):
        frames = clip.get("durationInFrames") if isinstance(clip, dict) else None
        if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0:
            raise ValueError(f"scene frame duration must be a positive integer (scene {index})")
        frame_counts.append(frames)
    if len(frame_counts) > 1 and transition_frames > min(frame_counts):
        raise ValueError("transition_frames cannot exceed the shortest scene")

    cursor = 0
    records: list[dict[str, Any]] = []
    for index, frames in enumerate(frame_counts):
        start_frame = cursor
        end_frame = start_frame + frames
        transition_in = transition_frames if index > 0 else 0
        transition_out = transition_frames if index < len(frame_counts) - 1 else 0
        records.append({
            "scene_index": index + 1,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "duration_frames": frames,
            "timeline_start_seconds": round(start_frame / fps, 6),
            "timeline_end_seconds": round(end_frame / fps, 6),
            "duration_seconds": round(frames / fps, 6),
            "transition_in_seconds": round(transition_in / fps, 6),
            "transition_out_seconds": round(transition_out / fps, 6),
        })
        cursor = end_frame - transition_out
    return records


def render_scene_timeline(
    workspace: str | Path,
    scene_paths: list[str | Path],
    audio_source: str | Path,
    output_video: str | Path,
    *,
    width: int,
    height: int,
    fps: int,
    ffmpeg_path: str,
    ffprobe_path: str,
    timeout_seconds: int = 900,
    image_seconds: float = 4.0,
    transition_seconds: float = 0.45,
    shot_directives: list[dict[str, Any]] | None = None,
    runner: Callable = subprocess.run,
) -> dict[str, Any]:
    """Render a real multi-scene Remotion timeline and mux the existing audio track."""
    if not isinstance(scene_paths, list) or not 1 <= len(scene_paths) <= 40:
        raise ValueError("scene_paths must contain between 1 and 40 scenes")
    directives = _validate_shot_directives(shot_directives, len(scene_paths))
    sources: list[Path] = []
    for value in scene_paths:
        source = Path(value)
        if source.is_symlink() or not source.is_file():
            raise RemotionMotionError("every scene source must be a regular non-symlink file")
        resolved = source.resolve(strict=True)
        if resolved.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
            raise RemotionMotionError("scene source has an unsupported media extension")
        sources.append(resolved)
    root_input = Path(workspace)
    if root_input.is_symlink():
        raise RemotionMotionError("the Muse workspace must not be a symlink")
    root = root_input.resolve(strict=True)
    motion_root = root / "video-motion"
    if motion_root.is_symlink() or not motion_root.is_dir():
        raise RemotionMotionError("the local video-motion Remotion project is not installed")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (width, height, fps)):
        raise ValueError("scene timeline dimensions and fps must be integers")
    if not 64 <= width <= 3840 or not 64 <= height <= 3840 or not 1 <= fps <= 60:
        raise ValueError("scene timeline dimensions or fps are outside the supported range")
    if not math.isfinite(image_seconds) or not 0.5 <= image_seconds <= 60:
        raise ValueError("image_seconds must be between 0.5 and 60")
    if not math.isfinite(transition_seconds) or not 0.1 <= transition_seconds <= 2:
        raise ValueError("transition_seconds must be between 0.1 and 2")
    if not ffmpeg_path or not ffprobe_path:
        raise ValueError("FFmpeg and FFprobe are required")
    audio_input = Path(audio_source)
    if audio_input.is_symlink() or not audio_input.is_file() or audio_input.suffix.casefold() != ".mp4":
        raise RemotionMotionError("scene timeline audio source must be a regular MP4")
    output_input = Path(output_video)
    if output_input.is_symlink() or output_input.exists() or output_input.suffix.casefold() != ".mp4":
        raise RemotionMotionError("scene timeline output must be a new MP4 path")
    output = output_input.resolve()
    if output.parent != audio_input.resolve().parent or not output.parent.is_dir():
        raise RemotionMotionError("scene timeline output must be beside its audio source")
    node = shutil.which("node")
    cli = motion_root / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    entry = motion_root / "src" / "index.tsx"
    package_path = motion_root / "package.json"
    if not node or not cli.is_file() or not entry.is_file() or not package_path.is_file():
        raise RemotionMotionError("Node.js and the installed local Remotion CLI are required")
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        remotion_version = package.get("dependencies", {}).get("remotion")
    except (OSError, json.JSONDecodeError) as exc:
        raise RemotionMotionError("the local Remotion package manifest is invalid") from exc
    if not isinstance(remotion_version, str) or not remotion_version:
        raise RemotionMotionError("the Remotion version could not be determined")
    browser_candidates = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "C:/Users/Default/AppData/Local")) / "Google/Chrome/Application/chrome.exe",
    )
    browser = next((candidate for candidate in browser_candidates if candidate.is_file()), None)
    public_root = motion_root / "public"
    if public_root.is_symlink():
        raise RemotionMotionError("Remotion public directory must not be a symlink")
    public_root.mkdir(exist_ok=True)
    stage_name = "muse-scenes-" + uuid.uuid4().hex
    public_stage = public_root / stage_name
    public_stage.mkdir()
    output.parent.mkdir(parents=True, exist_ok=True)
    created_output = False
    try:
        with contextlib.ExitStack() as stack:
            binaries = stack.enter_context(_remotion_binaries(
                motion_root / "node_modules" / "@remotion" / "compositor-win32-x64-msvc",
                runner, timeout_seconds,
            ))
            with tempfile.TemporaryDirectory(prefix=".muse-scene-timeline-", dir=str(output.parent)) as temporary:
                work = Path(temporary)
                clips: list[dict[str, Any]] = []
                filter_value = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                    f"fps={fps},setsar=1,format=yuv420p"
                )
                for index, source in enumerate(sources):
                    normalized = work / f"scene-{index:04d}.mp4"
                    command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
                    if source.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                        command.extend(["-loop", "1", "-framerate", str(fps), "-i", str(source), "-t", str(image_seconds)])
                    else:
                        command.extend(["-i", str(source)])
                    command.extend(["-vf", filter_value, "-an", "-c:v", "libx264", "-preset", "veryfast",
                                    "-crf", "20", "-r", str(fps), "-pix_fmt", "yuv420p", str(normalized)])
                    rendered = _run_process(runner, command, capture_output=True, text=True,
                                             timeout=timeout_seconds, check=False)
                    if rendered.returncode != 0 or not normalized.is_file() or normalized.stat().st_size == 0:
                        detail = _failure_detail(rendered, "FFmpeg scene normalization failed")
                        raise RemotionMotionError(f"scene {index + 1} normalization failed: {detail}")
                    info = _probe(normalized, ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
                    frames = max(2, round(info["duration_seconds"] * fps))
                    staged_name = f"scene-{index:04d}.mp4"
                    shutil.copyfile(normalized, public_stage / staged_name)
                    directive = directives[index]
                    clips.append({"src": f"{stage_name}/{staged_name}", "durationInFrames": frames,
                                  "cameraMove": directive["camera_move"], "vfx": directive["vfx"],
                                  "accent": directive["accent"]})
                min_frames = min(item["durationInFrames"] for item in clips)
                transition_frames = min(max(1, round(transition_seconds * fps)), max(1, min_frames // 3))
                total_frames = sum(item["durationInFrames"] for item in clips) - transition_frames * (len(clips) - 1)
                scene_timings = _scene_timing_records(clips, transition_frames=transition_frames, fps=fps)
                props = {"scenes": clips, "width": width, "height": height, "fps": fps,
                         "transitionDurationInFrames": transition_frames}
                props_path = work / "timeline.json"
                props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
                timeline = work / "timeline.mp4"
                command = [node, str(cli), "render", "src/index.tsx", "MuseSceneTimeline", str(timeline),
                           f"--props={props_path}", "--codec=h264", "--pixel-format=yuv420p",
                           f"--binaries-directory={binaries}"]
                if browser is not None:
                    command.extend(["--browser-executable", str(browser)])
                completed = _run_process(runner, command, cwd=str(motion_root), capture_output=True,
                                         text=True, timeout=timeout_seconds, check=False)
                if completed.returncode != 0 or not timeline.is_file() or timeline.stat().st_size == 0:
                    detail = _failure_detail(completed, "Remotion timeline render failed")
                    raise RemotionMotionError(f"Remotion scene timeline failed: {detail}")
                timeline_info = _probe(timeline, ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
                if (timeline_info["width"] != width or timeline_info["height"] != height
                        or abs(timeline_info["fps"] - fps) > 0.5 or timeline_info["has_audio"]):
                    raise RemotionMotionError("Remotion scene timeline does not match the requested silent video canvas")
                expected_duration = total_frames / fps
                if abs(timeline_info["duration_seconds"] - expected_duration) > max(0.15, 2 / fps):
                    raise RemotionMotionError("Remotion scene timeline duration does not match its transition frame plan")
                audio_info = _probe(audio_input, ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
                command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                           "-i", str(timeline)]
                if audio_info["has_audio"]:
                    command.extend(["-i", str(audio_input), "-map", "0:v:0", "-map", "1:a:0",
                                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-af", "apad",
                                    "-t", f"{expected_duration:.6f}"])
                else:
                    command.extend(["-map", "0:v:0", "-c:v", "copy"])
                command.extend(["-movflags", "+faststart", str(output)])
                muxed = _run_process(runner, command, capture_output=True, text=True,
                                     timeout=timeout_seconds, check=False)
                if muxed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                    output.unlink(missing_ok=True)
                    detail = _failure_detail(muxed, "FFmpeg audio mux failed")
                    raise RemotionMotionError(f"scene timeline audio mux failed: {detail}")
                created_output = True
        final_info = _probe(output, ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
        if (final_info["width"] != width or final_info["height"] != height
                or abs(final_info["fps"] - fps) > 0.5
                or final_info["has_audio"] != _probe(audio_input, ffprobe_path=ffprobe_path,
                    runner=runner, timeout=timeout_seconds)["has_audio"]
                or abs(final_info["duration_seconds"] - total_frames / fps) > max(0.15, 2 / fps)):
            raise RemotionMotionError("the final scene-transition MP4 failed metadata verification")
        return {
            "status": "completed", "profile": "brand_scene_transitions",
            "renderer": "remotion", "remotion_version": remotion_version,
            "composition_id": "MuseSceneTimeline", "scene_count": len(sources),
            "transition": "alternating_crossfade_wipe", "transition_seconds": round(transition_frames / fps, 3),
            "scene_duration_seconds": round(total_frames / fps, 3),
            "scene_timings": scene_timings, "shot_directives": directives,
            "audio_preserved": final_info["has_audio"], "width": width, "height": height, "fps": fps,
            "bytes": output.stat().st_size,
        }
    except Exception:
        if created_output:
            output.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(public_stage, ignore_errors=True)


def render_motion_package(
    workspace: str | Path,
    base_video: str | Path,
    output_video: str | Path,
    *,
    title: str,
    subtitle: str,
    width: int,
    height: int,
    fps: int,
    ffmpeg_path: str,
    ffprobe_path: str,
    timeout_seconds: int = 900,
    intro_seconds: float = 2.0,
    outro_seconds: float = 1.8,
    audio_mode: str = "narration",
    runner: Callable = subprocess.run,
) -> dict[str, Any]:
    """Render branded Remotion bumpers and transition them around an existing MP4."""
    workspace_input = Path(workspace)
    if workspace_input.is_symlink():
        raise RemotionMotionError("the Muse workspace must not be a symlink")
    root = workspace_input.resolve(strict=True)
    motion_root = root / "video-motion"
    base_input = Path(base_video)
    if base_input.is_symlink():
        raise RemotionMotionError("the source video must not be a symlink")
    base = base_input.resolve(strict=True)
    output_input = Path(output_video)
    if output_input.is_symlink() or output_input.exists():
        raise RemotionMotionError("the motion output must be a new MP4 path")
    output = output_input.resolve()
    if not root.is_dir() or motion_root.is_symlink() or not motion_root.is_dir():
        raise RemotionMotionError("the local video-motion Remotion project is not installed")
    if base.suffix.casefold() != ".mp4" or not base.is_file():
        raise RemotionMotionError("the source video must be a regular MP4")
    if output.suffix.casefold() != ".mp4" or output == base:
        raise RemotionMotionError("the motion output must be a new MP4 path")
    if output.parent != base.parent or not output.parent.is_dir():
        raise RemotionMotionError("the motion output must be beside the staged source video")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError("motion title must contain 1-160 characters")
    if not isinstance(subtitle, str) or len(subtitle) > 240:
        raise ValueError("motion subtitle must be at most 240 characters")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (width, height, fps)):
        raise ValueError("motion dimensions and frame rate must be integers")
    if not 64 <= width <= 3840 or not 64 <= height <= 3840 or not 1 <= fps <= 60:
        raise ValueError("motion dimensions or frame rate are outside the supported range")
    if audio_mode not in {"music", "narration"}:
        raise ValueError("audio_mode must be music or narration")
    if not math.isfinite(intro_seconds) or not 1 <= intro_seconds <= 5:
        raise ValueError("intro_seconds must be between 1 and 5")
    if not math.isfinite(outro_seconds) or not 1 <= outro_seconds <= 5:
        raise ValueError("outro_seconds must be between 1 and 5")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")

    node = shutil.which("node")
    cli = motion_root / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    entry = motion_root / "src" / "index.tsx"
    package_path = motion_root / "package.json"
    if not node or not cli.is_file() or not entry.is_file() or not package_path.is_file():
        raise RemotionMotionError("Node.js and the installed local Remotion CLI are required")
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        remotion_version = package.get("dependencies", {}).get("remotion")
    except (OSError, json.JSONDecodeError) as exc:
        raise RemotionMotionError("the local Remotion package manifest is invalid") from exc
    if not isinstance(remotion_version, str) or not remotion_version:
        raise RemotionMotionError("the Remotion version could not be determined")

    base_info = _probe(base, ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
    if base_info["width"] != width or base_info["height"] != height or abs(base_info["fps"] - fps) > 0.5:
        raise RemotionMotionError("the source MP4 does not match the requested motion canvas")
    if base_info["duration_seconds"] < 1:
        raise RemotionMotionError("the source MP4 is too short for safe motion transitions")

    browser_candidates = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "C:/Users/Default/AppData/Local")) / "Google/Chrome/Application/chrome.exe",
    )
    browser = next((candidate for candidate in browser_candidates if candidate.is_file()), None)
    output.parent.mkdir(parents=True, exist_ok=True)
    transition_seconds = min(0.5, intro_seconds / 4, outro_seconds / 4, base_info["duration_seconds"] / 4)
    intro_frames = max(1, round(intro_seconds * fps))
    outro_frames = max(1, round(outro_seconds * fps))
    with contextlib.ExitStack() as stack:
        binaries = stack.enter_context(_remotion_binaries(
            motion_root / "node_modules" / "@remotion" / "compositor-win32-x64-msvc",
            runner, timeout_seconds,
        ))
        with tempfile.TemporaryDirectory(prefix=".muse-remotion-", dir=str(output.parent)) as temporary:
            work = Path(temporary)
            rendered: dict[str, Path] = {}
            for kind, duration, frames in (
                ("intro", intro_seconds, intro_frames), ("outro", outro_seconds, outro_frames),
            ):
                props = {
                    "title": title.strip() if kind == "intro" else "让灵感，成为作品。",
                    "subtitle": subtitle if kind == "intro" else "MUSE · RESEARCH / CREATE / EVOLVE",
                    "accent": "#d8b46a", "width": width, "height": height,
                    "fps": fps, "durationInFrames": frames, "kind": kind,
                }
                props_path = work / f"{kind}.json"
                props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
                target = work / f"{kind}.mp4"
                command = [
                    node, str(cli), "render", "src/index.tsx", "MuseBumper", str(target),
                    f"--props={props_path}", "--codec=h264", "--pixel-format=yuv420p",
                    f"--binaries-directory={binaries}",
                ]
                if browser is not None:
                    command.extend(["--browser-executable", str(browser)])
                completed = _run_process(
                    runner, command, cwd=str(motion_root), capture_output=True, text=True,
                    timeout=timeout_seconds, check=False,
                )
                if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
                    detail = _failure_detail(completed, "Remotion returned no MP4")
                    raise RemotionMotionError(f"Remotion {kind} render failed: {detail}")
                bumper = _probe(target, ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
                if (bumper["width"] != width or bumper["height"] != height
                        or abs(bumper["fps"] - fps) > 0.5 or bumper["has_audio"]):
                    raise RemotionMotionError(f"Remotion {kind} output does not match the target canvas")
                rendered[kind] = target

            intro_info = _probe(rendered["intro"], ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
            outro_info = _probe(rendered["outro"], ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
            main_duration = base_info["duration_seconds"]
            intro_duration = intro_info["duration_seconds"]
            outro_duration = outro_info["duration_seconds"]
            total_duration = intro_duration + main_duration + outro_duration - 2 * transition_seconds
            first_offset = intro_duration - transition_seconds
            second_offset = intro_duration + main_duration - 2 * transition_seconds
            filters = [
                f"[0:v]fps={fps},settb=AVTB,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v0]",
                f"[1:v]fps={fps},settb=AVTB,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v1]",
                f"[2:v]fps={fps},settb=AVTB,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v2]",
                f"[v0][v1]xfade=transition=fade:duration={transition_seconds:.6f}:offset={first_offset:.6f}[vm]",
                f"[vm][v2]xfade=transition=fade:duration={transition_seconds:.6f}:offset={second_offset:.6f},format=yuv420p[v]",
            ]
            command = [
                ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", str(rendered["intro"]), "-i", str(base), "-i", str(rendered["outro"]),
            ]
            if base_info["has_audio"] and audio_mode == "music":
                command.extend(["-stream_loop", "-1", "-i", str(base)])
                filters.append(
                    f"[3:a:0]atrim=duration={total_duration:.6f},"
                    f"afade=t=in:st=0:d=0.35,"
                    f"afade=t=out:st={max(0.0, total_duration - 0.8):.6f}:d=0.8[a]"
                )
                command.extend(["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]"])
                command.extend(["-c:a", "aac", "-b:a", "192k"])
            elif base_info["has_audio"]:
                delay_ms = round(first_offset * 1000)
                fade_out_start = max(0.0, first_offset + main_duration - 0.5)
                filters.append(
                    f"[1:a:0]adelay={delay_ms}:all=1,"
                    f"afade=t=in:st={first_offset:.6f}:d=0.35,"
                    f"afade=t=out:st={fade_out_start:.6f}:d=0.5,"
                    f"apad,atrim=duration={total_duration:.6f}[a]"
                )
                command.extend(["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]"])
                command.extend(["-c:a", "aac", "-b:a", "192k"])
            else:
                command.extend(["-filter_complex", ";".join(filters), "-map", "[v]"])
            command.extend([
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", str(fps), "-movflags", "+faststart",
                "-t", f"{total_duration:.6f}", str(output),
            ])
            combined = _run_process(
                runner, command, capture_output=True, text=True, timeout=timeout_seconds, check=False,
            )
            if combined.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                output.unlink(missing_ok=True)
                detail = _failure_detail(combined, "FFmpeg returned no MP4")
                raise RemotionMotionError(f"Remotion motion assembly failed: {detail}")

    final_info = _probe(output, ffprobe_path=ffprobe_path, runner=runner, timeout=timeout_seconds)
    if (final_info["width"] != width or final_info["height"] != height
            or abs(final_info["fps"] - fps) > 0.5
            or final_info["has_audio"] != base_info["has_audio"]
            or abs(final_info["duration_seconds"] - total_duration) > max(0.15, 2 / fps)):
        output.unlink(missing_ok=True)
        raise RemotionMotionError("the final Remotion-enhanced MP4 failed metadata verification")
    return {
        "status": "completed", "profile": "brand_intro_outro",
        "renderer": "remotion", "remotion_version": remotion_version,
        "composition_id": "MuseBumper", "template_version": 1,
        "intro_seconds": round(intro_duration, 3), "outro_seconds": round(outro_seconds, 3),
        "transition": "crossfade", "transition_seconds": round(transition_seconds, 3),
        "audio_mode": audio_mode if final_info["has_audio"] else None,
        "source_duration_seconds": round(main_duration, 3),
        "duration_seconds": round(final_info["duration_seconds"], 3),
        "width": width, "height": height, "fps": fps,
        "has_audio": final_info["has_audio"], "bytes": output.stat().st_size,
    }
