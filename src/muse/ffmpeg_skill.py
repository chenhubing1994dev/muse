"""Muse's local FFmpeg editing capability contract and probe-first diagnostics.

This module adapts the verification-driven design of kajisho5/ffmpeg-skill
without vendoring or executing third-party code. It inventories capabilities;
it does not grant permissions or replace Muse Commander.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence


UPSTREAM = {
    "name": "kajisho5/ffmpeg-skill",
    "url": "https://github.com/kajisho5/ffmpeg-skill",
    "license": "MIT",
    "reviewed_version": "3.12.1",
    "integration": "independent-adapter-no-vendored-source",
}
# Upstream's 42 public tool families. Muse support describes existing, observed
# project code; it is not a claim that every upstream flag/feature is implemented.
_TOOL_ROWS = (
    ("probe", "analysis", "muse.media + this module", "native", ("ffprobe",), False),
    ("scenes", "analysis", "muse.video_highlights + video_motion_diagnostics + muse.video_contact_sheet.create_contact_sheet", "partial", ("ffmpeg",), True),
    ("look", "analysis", "muse.video_motion_diagnostics", "partial", ("ffmpeg",), True),
    ("cut", "execution", "muse.ffmpeg_skill.cut_media + muse.video_highlights", "partial", ("ffmpeg", "ffprobe"), True),
    ("join", "execution", "muse.ffmpeg_skill.join_media", "partial", ("ffmpeg", "ffprobe", "filter:concat"), True),
    ("silence", "analysis_and_execution", "muse.video_qa (detection only)", "partial", ("ffmpeg", "filter:silencedetect"), False),
    ("fit", "execution", "muse.ffmpeg_skill.fit_media + muse.video_highlights", "partial", ("ffmpeg", "ffprobe", "filter:scale", "filter:crop", "filter:pad", "filter:fps"), True),
    ("crop", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:crop"), True),
    ("cropdetect", "analysis", "not yet integrated", "planned", ("ffmpeg", "filter:cropdetect"), True),
    ("deinterlace", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:yadif"), True),
    ("denoise", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:hqdn3d"), True),
    ("redact", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:delogo"), True),
    ("sphere", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:v360"), True),
    ("straighten", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:rotate"), True),
    ("insert", "execution", "not yet integrated", "planned", ("ffmpeg",), True),
    ("background", "execution", "not yet integrated", "planned", ("ffmpeg",), True),
    ("reverse", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:reverse"), True),
    ("stabilize", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:vidstabdetect", "filter:vidstabtransform"), True),
    ("sequence", "execution", "not yet integrated", "planned", ("ffmpeg",), True),
    ("waveform", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:showwaves"), True),
    ("freeze", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:tpad"), True),
    ("pad", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:pad"), True),
    ("speedramp", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:setpts", "filter:atempo"), True),
    ("loop", "execution", "not yet integrated", "planned", ("ffmpeg",), True),
    ("broll", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:overlay"), True),
    ("metadata", "execution", "not yet integrated", "planned", ("ffmpeg",), False),
    ("grid", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:xstack"), True),
    ("audio", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:acompressor"), False),
    ("sync", "analysis_and_execution", "not yet integrated", "planned", ("ffmpeg",), False),
    ("loudness", "analysis_and_execution", "muse.video_qa (measurement only)", "partial", ("ffmpeg", "filter:ebur128", "filter:astats"), False),
    ("caption", "execution", "muse.video_highlights (SRT burn-in)", "partial", ("ffmpeg", "filter:subtitles"), True),
    ("overlay", "execution", "muse.video_editable_export (handoff only)", "partial", ("ffmpeg", "filter:overlay"), True),
    ("graphics", "execution", "muse.media_compositor + Remotion", "partial", ("ffmpeg", "filter:drawtext"), True),
    ("color", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:colorspace"), True),
    ("export", "execution", "muse.video_editable_export + video_product_builder", "partial", ("ffmpeg",), True),
    ("proxy", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:scale"), True),
    ("check", "verification", "muse.video_qa", "native", ("ffmpeg", "filter:blackdetect", "filter:freezedetect", "filter:silencedetect", "filter:ebur128", "filter:astats"), False),
    ("report", "verification", "muse.video_publish_package", "partial", ("ffmpeg",), True),
    ("render", "execution", "muse.video_factory + video_product_builder", "partial", ("ffmpeg",), True),
    ("batch", "orchestration", "Muse Commander/TaskQueue (not upstream batch semantics)", "partial", ("ffmpeg",), False),
    ("multicam", "execution", "not yet integrated", "planned", ("ffmpeg",), True),
    ("verify", "verification", "muse.video_qa + existing test suite", "partial", ("ffmpeg", "ffprobe"), True),
)
@dataclass(frozen=True)
class ToolCapability:
    name: str
    role: str
    muse_adapter: str
    muse_support: str
    required_components: tuple[str, ...]
    requires_visual_review: bool
    mutates_input: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


TOOL_CATALOG = tuple(ToolCapability(*row) for row in _TOOL_ROWS)


def _parse_component_listing(text: str, kind: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if kind == "bsfs" and line.strip().casefold().startswith("bitstream filters:"):
            continue
        flags = parts[0]
        if kind in {"encoders", "decoders"} and len(flags) == 6 and re.fullmatch(r"[A-Z.]{6}", flags) and len(parts) > 1:
            found.add(parts[1])
        elif kind == "filters" and len(flags) == 3 and re.fullmatch(r"[TSC.]{3}", flags) and len(parts) > 1:
            found.add(parts[1])
        elif kind == "bsfs" and len(parts) == 1 and not line.lstrip().startswith("#"):
            found.add(parts[0])
    return found

def _run_listing(executable: str, args: list[str], runner: Callable) -> dict[str, object]:
    try:
        result = runner([executable, *args], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unknown", "items": [], "error": f"{type(exc).__name__}: {exc}"}
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0:
        return {"status": "unknown", "items": [], "error": f"exit_code={result.returncode}"}
    return {"status": "available", "items": sorted(_parse_component_listing(output, args[0][1:]))}


def inspect_ffmpeg_capabilities(
    *,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable = subprocess.run,
) -> dict[str, object]:
    """Detect FFmpeg components; failed listings stay unknown, never missing."""
    ffmpeg = ffmpeg_path or which("ffmpeg")
    ffprobe = ffprobe_path or which("ffprobe")
    report: dict[str, object] = {
        "status": "unknown",
        "executables": {
            "ffmpeg": {"status": "available" if ffmpeg else "missing", "path": ffmpeg},
            "ffprobe": {"status": "available" if ffprobe else "missing", "path": ffprobe},
        },
        "version": None,
        "components": {},
        "hardware_encoders": [],
        "tools": {},
    }
    components: dict[str, dict[str, object]] = {}
    if ffmpeg:
        try:
            version_result = runner([ffmpeg, "-hide_banner", "-version"], capture_output=True,
                                    text=True, timeout=20, check=False)
            version_text = (version_result.stdout or "") + "\n" + (version_result.stderr or "")
            match = re.search(r"ffmpeg version\s+([^\s]+)", version_text, re.IGNORECASE)
            if version_result.returncode == 0:
                report["version"] = match.group(1) if match else "unparsed"
            else:
                components["ffmpeg_version"] = {"status": "unknown", "items": [], "error": "version query failed"}
        except (OSError, subprocess.SubprocessError) as exc:
            components["ffmpeg_version"] = {"status": "unknown", "items": [], "error": type(exc).__name__}
        for kind, flag in (("encoders", "-encoders"), ("decoders", "-decoders"),
                           ("filters", "-filters"), ("bsfs", "-bsfs")):
            components[kind] = _run_listing(ffmpeg, [flag], runner)
        encoder_items = components["encoders"].get("items", [])
        report["hardware_encoders"] = sorted(
            item for item in encoder_items
            if any(marker in item for marker in ("_amf", "_nvenc", "_qsv", "_vaapi", "_videotoolbox"))
        ) if components["encoders"]["status"] == "available" else []
    else:
        for kind in ("encoders", "decoders", "filters", "bsfs"):
            components[kind] = {"status": "missing", "items": [], "error": "ffmpeg executable not found"}
    report["components"] = components

    tools: dict[str, dict[str, object]] = {}
    for spec in TOOL_CATALOG:
        missing: list[str] = []
        unknown: list[str] = []
        for requirement in spec.required_components:
            if requirement == "ffmpeg":
                state = report["executables"]["ffmpeg"]["status"]
            elif requirement == "ffprobe":
                state = report["executables"]["ffprobe"]["status"]
            else:
                kind, name = requirement.split(":", 1)
                listing = components.get(kind.removeprefix("filter:"), {})
                if kind == "filter":
                    listing = components.get("filters", {})
                state = listing.get("status", "unknown")
                if state == "available":
                    state = "available" if name in listing.get("items", []) else "missing"
            if state == "missing":
                missing.append(requirement)
            elif state != "available":
                unknown.append(requirement)
        tools[spec.name] = {
            "muse_support": spec.muse_support,
            "readiness": "missing" if missing else ("unknown" if unknown else "available"),
            "missing": missing, "unknown": unknown,
            "requires_visual_review": spec.requires_visual_review,
            "mutates_input": False,
        }
    report["tools"] = tools
    states = [item["readiness"] for item in tools.values()]
    report["status"] = "missing" if "missing" in states else ("unknown" if "unknown" in states else "ready")
    return report


def probe_media(
    path: str | Path,
    *,
    ffprobe_path: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable = subprocess.run,
) -> dict[str, object]:
    """Read real media facts before editing; this operation writes no files."""
    source = Path(path).expanduser()
    if source.is_symlink() or not source.is_file() or source.stat().st_size <= 0:
        raise ValueError("input must be a non-empty regular local file")
    executable = ffprobe_path or which("ffprobe")
    if not executable:
        return {"status": "blocked", "reason": "ffprobe_missing", "path": str(source)}
    result = runner(
        [str(executable), "-v", "error", "-show_format", "-show_streams",
         "-show_chapters", "-of", "json", str(source)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        return {"status": "failed", "reason": "ffprobe_failed",
                "detail": (result.stderr or "")[-1200:], "path": str(source)}
    try:
        document = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"status": "failed", "reason": "invalid_probe_json",
                "detail": str(exc), "path": str(source)}
    if not isinstance(document, dict) or not isinstance(document.get("streams"), list):
        return {"status": "failed", "reason": "invalid_probe_shape", "path": str(source)}

    format_info = document.get("format") if isinstance(document.get("format"), dict) else {}
    streams = [item for item in document["streams"] if isinstance(item, dict)]
    video = [item for item in streams if item.get("codec_type") == "video"]
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    first_video = video[0] if video else {}

    duration_raw = format_info.get("duration") or first_video.get("duration")
    try:
        duration = float(duration_raw)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (TypeError, ValueError):
        duration = None

    def rate(value: object) -> float | None:
        try:
            raw = str(value)
            if "/" in raw:
                numerator, denominator = raw.split("/", 1)
                result_rate = float(numerator) / float(denominator)
            else:
                result_rate = float(raw)
            return result_rate if math.isfinite(result_rate) and result_rate > 0 else None
        except (ValueError, ZeroDivisionError, TypeError):
            return None

    avg_fps = rate(first_video.get("avg_frame_rate"))
    nominal_fps = rate(first_video.get("r_frame_rate"))
    vfr = (avg_fps is not None and nominal_fps is not None
           and not math.isclose(avg_fps, nominal_fps, rel_tol=0.01, abs_tol=0.05))
    rotation = next((
        item.get("tags", {}).get("rotate")
        for item in streams
        if isinstance(item.get("tags"), dict) and item["tags"].get("rotate")
    ), None)
    return {
        "status": "passed",
        "path": str(source.resolve()),
        "file_bytes": source.stat().st_size,
        "duration_seconds": duration,
        "format_name": format_info.get("format_name"),
        "video_stream_count": len(video),
        "audio_stream_count": len(audio),
        "video": {
            "codec": first_video.get("codec_name"),
            "width": first_video.get("width"), "height": first_video.get("height"),
            "avg_fps": avg_fps, "nominal_fps": nominal_fps,
            "variable_frame_rate_suspected": vfr,
            "pixel_format": first_video.get("pix_fmt"),
            "color_space": first_video.get("color_space"),
            "color_transfer": first_video.get("color_transfer"),
            "color_primaries": first_video.get("color_primaries"),
            "rotation": rotation,
        } if video else None,
        "audio": [{
            "codec": item.get("codec_name"), "sample_rate": item.get("sample_rate"),
            "channels": item.get("channels"), "channel_layout": item.get("channel_layout"),
            "bit_rate": item.get("bit_rate"),
        } for item in audio],
        "chapters": len(document.get("chapters", [])) if isinstance(document.get("chapters"), list) else 0,
        "editing_guidance": {
            "probe_first": True,
            "prefer_stream_copy_when_compatible": True,
            "vfr_requires_frame_timing_review": bool(vfr),
            "hdr_source_requires_explicit_delivery_decision": bool(
                first_video.get("color_transfer") in {"smpte2084", "arib-std-b67"}
            ),
        },
    }


def _cut_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _cut_signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def build_cut_command(
    source: str | Path,
    output: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    mode: str = "accurate",
    has_audio: bool = True,
    video_encoder: str = "libx264",
    ffmpeg_path: str = "ffmpeg",
) -> list[str]:
    """Build a closed-argument MP4 cut command; no user filtergraph or shell."""
    start = _cut_number(start_seconds, "start_seconds")
    end = _cut_number(end_seconds, "end_seconds")
    if start < 0 or end <= start:
        raise ValueError("cut range must be non-negative and increasing")
    if mode not in {"accurate", "copy"}:
        raise ValueError("mode must be accurate or copy")
    if video_encoder not in {"libx264", "h264_amf"}:
        raise ValueError("video_encoder must be libx264 or h264_amf")
    args = [ffmpeg_path, "-hide_banner", "-nostdin", "-n"]
    if mode == "copy":
        # Fast seek is keyframe-aligned; report this precision trade-off to callers.
        args.extend(["-ss", f"{start:.6f}"])
    args.extend(["-i", str(Path(source).expanduser())])
    if mode == "accurate":
        # Decode from the preceding keyframe, then seek on the decoded timeline.
        args.extend(["-ss", f"{start:.6f}"])
    args.extend([
        "-t", f"{end - start:.6f}", "-map", "0:v:0", "-map", "0:a:0?",
        "-sn", "-dn",
    ])
    if mode == "copy":
        args.extend(["-c", "copy"])
    else:
        if video_encoder == "h264_amf":
            args.extend(["-c:v", "h264_amf", "-pix_fmt", "nv12"])
        else:
            args.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"])
        if has_audio:
            args.extend(["-c:a", "aac", "-b:a", "160k"])
        else:
            args.append("-an")
    args.extend(["-movflags", "+faststart", "-f", "mp4", str(Path(output).expanduser())])
    return args


def plan_cut(
    source: str | Path,
    output: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    mode: str = "accurate",
    encoder: str = "auto",
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str | None = None,
    runner: Callable = subprocess.run,
) -> dict[str, object]:
    """Probe and validate a video cut without creating or modifying files."""
    source_path = Path(source).expanduser()
    target = Path(output).expanduser()
    start = _cut_number(start_seconds, "start_seconds")
    end = _cut_number(end_seconds, "end_seconds")
    if start < 0 or end <= start:
        raise ValueError("cut range must be non-negative and increasing")
    if end - start < 0.25 or end - start > 7200:
        raise ValueError("cut duration must be between 0.25 seconds and 2 hours")
    if mode not in {"accurate", "copy"}:
        raise ValueError("mode must be accurate or copy")
    if encoder not in {"auto", "libx264", "h264_amf"}:
        raise ValueError("encoder must be auto, libx264 or h264_amf")
    if source_path.is_symlink() or not source_path.is_file() or source_path.stat().st_size <= 0:
        raise ValueError("source must be a non-empty regular local file")
    if source_path.resolve() == target.resolve(strict=False):
        raise ValueError("source and output must be different files")
    if target.is_symlink() or target.exists():
        raise FileExistsError(target)
    if target.suffix.casefold() != ".mp4" or not target.parent.is_dir():
        raise ValueError("output must be a new MP4 inside an existing directory")

    source_probe = probe_media(source_path, ffprobe_path=ffprobe_path, runner=runner)
    if source_probe.get("status") != "passed":
        raise RuntimeError("source probe failed: " + str(source_probe.get("reason", source_probe.get("status"))))
    if int(source_probe.get("video_stream_count", 0)) < 1:
        raise ValueError("cut currently requires a video stream")
    duration = source_probe.get("duration_seconds")
    if not isinstance(duration, (int, float)) or not math.isfinite(float(duration)) or end > float(duration) + 0.001:
        raise ValueError("cut range exceeds the probed source duration")
    has_audio = int(source_probe.get("audio_stream_count", 0)) > 0
    video = source_probe.get("video") if isinstance(source_probe.get("video"), dict) else {}
    guidance = source_probe.get("editing_guidance") if isinstance(source_probe.get("editing_guidance"), dict) else {}
    hdr_source = bool(guidance.get("hdr_source_requires_explicit_delivery_decision", False))
    if hdr_source and mode != "copy":
        raise ValueError("HDR source needs an explicit delivery decision; first-stage cut only permits explicit stream-copy preservation")
    encoder_listing = {"status": "not_required", "items": []}
    if mode == "copy":
        encoder_selected = "copy"
    else:
        encoder_listing = _run_listing(ffmpeg_path, ["-encoders"], runner)
        available_encoders = set(encoder_listing.get("items", [])) if encoder_listing.get("status") == "available" else set()
        if encoder == "h264_amf" and "h264_amf" not in available_encoders:
            raise ValueError("h264_amf was not reported by the local FFmpeg encoder listing")
        encoder_selected = "h264_amf" if encoder == "h264_amf" or (encoder == "auto" and "h264_amf" in available_encoders) else "libx264"
    command = build_cut_command(
        source_path, target, start, end, mode=mode, has_audio=has_audio,
        video_encoder=encoder_selected if encoder_selected != "copy" else "libx264",
        ffmpeg_path=ffmpeg_path,
    )
    return {
        "status": "planned",
        "operation": "cut",
        "mode": mode,
        "encoder_requested": encoder,
        "encoder_selected": encoder_selected,
        "encoder_listing_status": encoder_listing.get("status"),
        "source": str(source_path.resolve()),
        "output": str(target.resolve(strict=False)),
        "start_seconds": start,
        "end_seconds": end,
        "expected_duration_seconds": end - start,
        "source_signature": list(_cut_signature(source_path)),
        "source_probe": source_probe,
        "command_preview": command,
        "precision": "frame-accurate-decode-and-reencode" if mode == "accurate" else "fast-keyframe-aligned-stream-copy",
        "warnings": [
            warning for warning, active in (
                ("stream-copy seeks may snap to a nearby keyframe", mode == "copy"),
                ("variable-frame-rate timing needs editorial frame review", bool(video.get("variable_frame_rate_suspected"))),
                ("HDR source is stream-copied without tone mapping; confirm target playback", hdr_source and mode == "copy"),
                ("FFmpeg encoder listing unavailable; auto selected software libx264", encoder == "auto" and mode == "accurate" and encoder_listing.get("status") != "available"),
            ) if active
        ],
        "writes_files": False,
    }


def cut_media(
    source: str | Path,
    output: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    mode: str = "accurate",
    encoder: str = "auto",
    dry_run: bool = False,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str | None = None,
    timeout_seconds: int = 1800,
    runner: Callable = subprocess.run,
    qa_inspector: Callable | None = None,
) -> dict[str, object]:
    """Execute a probe-fenced MP4 cut and publish only after Muse full-decode QA."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    plan = plan_cut(
        source, output, start_seconds, end_seconds, mode=mode, encoder=encoder,
        ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, runner=runner,
    )
    if dry_run:
        return plan

    source_path = Path(source).expanduser()
    target = Path(output).expanduser()
    source_signature = tuple(plan["source_signature"])
    expected_duration = float(plan["expected_duration_seconds"])
    tolerance = max(1.0, expected_duration * 0.05) if mode == "copy" else max(0.25, expected_duration * 0.02)
    has_audio = int(plan["source_probe"].get("audio_stream_count", 0)) > 0
    encoder_selected = str(plan["encoder_selected"])
    encoder_fallback_used = False

    with tempfile.TemporaryDirectory(prefix=".muse-cut-", dir=str(target.parent)) as temporary:
        temporary_output = Path(temporary) / "verified-cut.mp4"
        command = build_cut_command(
            source_path, temporary_output, start_seconds, end_seconds,
            mode=mode, has_audio=has_audio,
            video_encoder=encoder_selected if encoder_selected != "copy" else "libx264",
            ffmpeg_path=ffmpeg_path,
        )
        try:
            rendered = runner(
                command, capture_output=True, text=True, timeout=timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FFmpeg cut timed out; temporary output was discarded") from exc
        render_failed = rendered.returncode != 0 or not temporary_output.is_file() or temporary_output.stat().st_size <= 0
        if render_failed and encoder == "auto" and encoder_selected == "h264_amf":
            temporary_output.unlink(missing_ok=True)
            fallback_command = build_cut_command(
                source_path, temporary_output, start_seconds, end_seconds,
                mode=mode, has_audio=has_audio, video_encoder="libx264",
                ffmpeg_path=ffmpeg_path,
            )
            try:
                rendered = runner(
                    fallback_command, capture_output=True, text=True, timeout=timeout_seconds, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("AMF failed and software fallback timed out; temporary output was discarded") from exc
            if rendered.returncode != 0 or not temporary_output.is_file() or temporary_output.stat().st_size <= 0:
                raise RuntimeError("AMF encode failed and libx264 fallback failed: " + (rendered.stderr or "")[-1200:])
            encoder_selected = "libx264"
            encoder_fallback_used = True
        elif render_failed:
            raise RuntimeError("FFmpeg cut failed: " + (rendered.stderr or "")[-1200:])

        output_probe = probe_media(temporary_output, ffprobe_path=ffprobe_path, runner=runner)
        output_duration = output_probe.get("duration_seconds")
        if (output_probe.get("status") != "passed"
                or int(output_probe.get("video_stream_count", 0)) < 1
                or not isinstance(output_duration, (int, float))
                or abs(float(output_duration) - expected_duration) > tolerance):
            raise RuntimeError("cut output failed FFprobe stream/duration verification")

        if qa_inspector is None:
            from muse.video_qa import inspect_video_file
            technical_qa = inspect_video_file(
                temporary_output, ffprobe_path=ffprobe_path or shutil.which("ffprobe"),
                ffmpeg_path=ffmpeg_path or shutil.which("ffmpeg"),
                min_duration_seconds=0.1,
                max_duration_seconds=expected_duration + tolerance,
                require_audio=has_audio,
                runner=runner,
                timeout_seconds=timeout_seconds,
            )
        else:
            technical_qa = qa_inspector(temporary_output)
        if not isinstance(technical_qa, dict) or technical_qa.get("status") != "passed":
            raise RuntimeError("Muse full-decode technical QA failed; output was not published")
        if _cut_signature(source_path) != source_signature:
            raise RuntimeError("source file changed during cut; output was not published")

        # Same-volume hard-link creation is atomic and fails if another process
        # created the destination; never replace or expose an unverified file.
        os.link(temporary_output, target)

    return {
        "status": "completed",
        "operation": "cut",
        "mode": mode,
        "output": str(target.resolve()),
        "bytes": target.stat().st_size,
        "duration_seconds": round(float(output_duration), 3),
        "precision": plan["precision"],
        "encoder": encoder_selected,
        "encoder_fallback_used": encoder_fallback_used,
        "source_unchanged": True,
        "source_probe": plan["source_probe"],
        "output_probe": output_probe,
        "technical_qa": technical_qa,
    }


def _join_paths_and_probe(
    sources: Sequence[str | Path], output: str | Path, *, ffprobe_path: str | None, runner: Callable
):
    if not isinstance(sources, (list, tuple)) or not 2 <= len(sources) <= 32:
        raise ValueError("join requires between 2 and 32 source files")
    paths = [Path(item).expanduser() for item in sources]
    if len({str(path.resolve(strict=False)).casefold() for path in paths}) != len(paths):
        raise ValueError("join source paths must be unique")
    target = Path(output).expanduser()
    if target.suffix.casefold() != ".mp4" or not target.parent.is_dir():
        raise ValueError("join output must be a new MP4 inside an existing directory")
    if target.is_symlink() or target.exists():
        raise FileExistsError(target)
    target_resolved = target.resolve(strict=False)
    signatures, probes = [], []
    ref_video = ref_audio = None
    has_audio = None
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("each join source must be a non-empty regular local file")
        if path.resolve() == target_resolved:
            raise ValueError("join output must differ from every source")
        signatures.append(_cut_signature(path))
        probe = probe_media(path, ffprobe_path=ffprobe_path, runner=runner)
        if probe.get("status") != "passed" or int(probe.get("video_stream_count", 0)) != 1:
            raise ValueError("each join source must probe successfully with exactly one video stream")
        duration = probe.get("duration_seconds")
        if not isinstance(duration, (int, float)) or not math.isfinite(float(duration)) or duration <= 0:
            raise ValueError("each join source must have a finite positive duration")
        video = probe.get("video") if isinstance(probe.get("video"), dict) else {}
        if video.get("variable_frame_rate_suspected") or video.get("rotation"):
            raise ValueError("join currently blocks VFR or rotated sources; normalize them first")
        if probe.get("editing_guidance", {}).get("hdr_source_requires_explicit_delivery_decision"):
            raise ValueError("join currently blocks HDR sources pending explicit color delivery")
        vsig = (video.get("width"), video.get("height"), video.get("avg_fps"),
                video.get("color_space"), video.get("color_transfer"), video.get("color_primaries"))
        if not all(isinstance(value, (int, float)) and value > 0 for value in vsig[:3]):
            raise ValueError("join requires known positive dimensions and frame rate")
        if ref_video is None:
            ref_video = vsig
        elif vsig != ref_video:
            raise ValueError("join sources must match dimensions, frame rate and color metadata")
        audio_rows = probe.get("audio") if isinstance(probe.get("audio"), list) else []
        if len(audio_rows) > 1:
            raise ValueError("join supports at most one audio stream per source")
        current_audio = len(audio_rows) == 1
        if has_audio is None:
            has_audio = current_audio
        elif has_audio != current_audio:
            raise ValueError("join sources must either all contain audio or all be silent")
        if current_audio:
            audio = audio_rows[0]
            asig = (audio.get("sample_rate"), audio.get("channels"), audio.get("channel_layout"))
            if not all(value not in (None, "") for value in asig):
                raise ValueError("join requires known audio sample rate, channels and layout")
            if ref_audio is None:
                ref_audio = asig
            elif asig != ref_audio:
                raise ValueError("join audio layouts and sample rates must match")
        probes.append(probe)
    return paths, target, probes, signatures, bool(has_audio)


def build_join_command(sources: Sequence[str | Path], output: str | Path, *,
                       has_audio: bool, video_encoder: str = "libx264",
                       ffmpeg_path: str = "ffmpeg") -> list[str]:
    if not 2 <= len(sources) <= 32:
        raise ValueError("join requires between 2 and 32 source files")
    if video_encoder not in {"libx264", "h264_amf"}:
        raise ValueError("video_encoder must be libx264 or h264_amf")
    args = [ffmpeg_path, "-hide_banner", "-nostdin", "-n"]
    for source in sources:
        args.extend(["-i", str(Path(source).expanduser())])
    pads = []
    for index in range(len(sources)):
        pads.append(f"[{index}:v:0]")
        if has_audio:
            pads.append(f"[{index}:a:0]")
    graph = "".join(pads) + f"concat=n={len(sources)}:v=1:a={1 if has_audio else 0}[vout]"
    if has_audio:
        graph += "[aout]"
    args.extend(["-filter_complex", graph, "-map", "[vout]"])
    if has_audio:
        args.extend(["-map", "[aout]"])
    if video_encoder == "h264_amf":
        args.extend(["-c:v", "h264_amf", "-pix_fmt", "nv12"])
    else:
        args.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"])
    if has_audio:
        args.extend(["-c:a", "aac", "-b:a", "160k"])
    else:
        args.append("-an")
    args.extend(["-movflags", "+faststart", "-f", "mp4", str(Path(output).expanduser())])
    return args


def plan_join(sources: Sequence[str | Path], output: str | Path, *,
              encoder: str = "auto", ffmpeg_path: str = "ffmpeg",
              ffprobe_path: str | None = None, runner: Callable = subprocess.run) -> dict[str, object]:
    if encoder not in {"auto", "libx264", "h264_amf"}:
        raise ValueError("encoder must be auto, libx264 or h264_amf")
    paths, target, probes, signatures, has_audio = _join_paths_and_probe(
        sources, output, ffprobe_path=ffprobe_path, runner=runner)
    listing = _run_listing(ffmpeg_path, ["-encoders"], runner)
    available = set(listing.get("items", [])) if listing.get("status") == "available" else set()
    if encoder == "h264_amf" and "h264_amf" not in available:
        raise ValueError("h264_amf was not reported by local FFmpeg")
    selected = "h264_amf" if encoder == "h264_amf" or (encoder == "auto" and "h264_amf" in available) else "libx264"
    duration = sum(float(probe["duration_seconds"]) for probe in probes)
    return {
        "status": "planned", "operation": "join", "source_count": len(paths),
        "sources": [str(path.resolve()) for path in paths], "output": str(target.resolve(strict=False)),
        "expected_duration_seconds": duration, "source_signatures": [list(item) for item in signatures],
        "has_audio": has_audio, "encoder_requested": encoder, "encoder_selected": selected,
        "encoder_listing_status": listing.get("status"), "source_probes": probes,
        "command_preview": build_join_command(paths, target, has_audio=has_audio,
                                               video_encoder=selected, ffmpeg_path=ffmpeg_path),
        "warnings": ["join re-encodes all sources; not bit-exact stream copy",
                     "visual continuity and audio transitions require editorial review"],
        "writes_files": False,
    }


def join_media(sources: Sequence[str | Path], output: str | Path, *,
               encoder: str = "auto", dry_run: bool = False, ffmpeg_path: str = "ffmpeg",
               ffprobe_path: str | None = None, timeout_seconds: int = 3600,
               runner: Callable = subprocess.run, qa_inspector: Callable | None = None) -> dict[str, object]:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    plan = plan_join(sources, output, encoder=encoder, ffmpeg_path=ffmpeg_path,
                     ffprobe_path=ffprobe_path, runner=runner)
    if dry_run:
        return plan
    paths = [Path(item).expanduser() for item in sources]
    target = Path(output).expanduser()
    signatures = [tuple(item) for item in plan["source_signatures"]]
    expected = float(plan["expected_duration_seconds"])
    has_audio, selected, fallback_used = bool(plan["has_audio"]), str(plan["encoder_selected"]), False
    tolerance = max(0.5, expected * 0.01)
    with tempfile.TemporaryDirectory(prefix=".muse-join-", dir=str(target.parent)) as temporary:
        temp_output = Path(temporary) / "verified-join.mp4"
        command = build_join_command(paths, temp_output, has_audio=has_audio,
                                     video_encoder=selected, ffmpeg_path=ffmpeg_path)
        try:
            rendered = runner(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FFmpeg join timed out; temporary output was discarded") from exc
        failed = rendered.returncode != 0 or not temp_output.is_file() or temp_output.stat().st_size <= 0
        if failed and encoder == "auto" and selected == "h264_amf":
            temp_output.unlink(missing_ok=True)
            fallback = build_join_command(paths, temp_output, has_audio=has_audio,
                                          video_encoder="libx264", ffmpeg_path=ffmpeg_path)
            try:
                rendered = runner(fallback, capture_output=True, text=True, timeout=timeout_seconds, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("AMF join failed and software fallback timed out") from exc
            if rendered.returncode != 0 or not temp_output.is_file() or temp_output.stat().st_size <= 0:
                raise RuntimeError("AMF join and libx264 fallback failed: " + (rendered.stderr or "")[-1200:])
            selected, fallback_used = "libx264", True
        elif failed:
            raise RuntimeError("FFmpeg join failed: " + (rendered.stderr or "")[-1200:])
        output_probe = probe_media(temp_output, ffprobe_path=ffprobe_path, runner=runner)
        actual = output_probe.get("duration_seconds")
        if (output_probe.get("status") != "passed" or int(output_probe.get("video_stream_count", 0)) != 1
                or abs(float(actual or 0) - expected) > tolerance
                or int(output_probe.get("audio_stream_count", 0)) != int(has_audio)):
            raise RuntimeError("join output failed FFprobe stream/duration verification")
        if qa_inspector is None:
            from muse.video_qa import inspect_video_file
            qa = inspect_video_file(temp_output, ffprobe_path=ffprobe_path or shutil.which("ffprobe"),
                                    ffmpeg_path=ffmpeg_path or shutil.which("ffmpeg"),
                                    min_duration_seconds=0.1, max_duration_seconds=expected + tolerance,
                                    require_audio=has_audio, runner=runner, timeout_seconds=timeout_seconds)
        else:
            qa = qa_inspector(temp_output)
        if not isinstance(qa, dict) or qa.get("status") != "passed":
            raise RuntimeError("Muse full-decode technical QA failed; output was not published")
        for path, signature in zip(paths, signatures):
            if _cut_signature(path) != signature:
                raise RuntimeError("join source changed during render; output was not published")
        os.link(temp_output, target)
    return {"status": "completed", "operation": "join", "output": str(target.resolve()),
            "source_count": len(paths), "bytes": target.stat().st_size,
            "duration_seconds": round(float(actual), 3), "encoder": selected,
            "encoder_fallback_used": fallback_used, "source_unchanged": True,
            "output_probe": output_probe, "technical_qa": qa}


def build_fit_command(source: str | Path, output: str | Path, *,
                       width: int, height: int, mode: str, focus_x: float, focus_y: float,
                       fps: float, has_audio: bool, video_encoder: str = "libx264",
                       ffmpeg_path: str = "ffmpeg") -> list[str]:
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 128 or value > 3840 or value % 2
           for value in (width, height)):
        raise ValueError("fit dimensions must be even integers between 128 and 3840")
    if mode not in {"cover", "contain"}:
        raise ValueError("fit mode must be cover or contain")
    fx, fy, rate = _cut_number(focus_x, "focus_x"), _cut_number(focus_y, "focus_y"), _cut_number(fps, "fps")
    if not 0 <= fx <= 1 or not 0 <= fy <= 1:
        raise ValueError("focus coordinates must be normalized between 0 and 1")
    if not 1 <= rate <= 120:
        raise ValueError("fit fps must be between 1 and 120")
    if video_encoder not in {"libx264", "h264_amf"}:
        raise ValueError("video_encoder must be libx264 or h264_amf")
    if mode == "cover":
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:x='iw*{fx:.6f}-ow/2':y='ih*{fy:.6f}-oh/2',"
            f"setsar=1,fps={rate:.6f}"
        )
    else:
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={rate:.6f}"
        )
    args = [ffmpeg_path, "-hide_banner", "-nostdin", "-n", "-i", str(Path(source).expanduser()),
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-vf", video_filter]
    if video_encoder == "h264_amf":
        args.extend(["-c:v", "h264_amf", "-pix_fmt", "nv12"])
    else:
        args.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"])
    if has_audio:
        args.extend(["-c:a", "aac", "-b:a", "160k"])
    else:
        args.append("-an")
    args.extend(["-movflags", "+faststart", "-f", "mp4", str(Path(output).expanduser())])
    return args


def plan_fit(source: str | Path, output: str | Path, *,
             width: int = 1080, height: int = 1920, mode: str = "cover",
             focus_x: float = 0.5, focus_y: float = 0.5, fps: float | None = None,
             face_aware: bool = False, max_face_samples: int = 12,
             encoder: str = "auto", ffmpeg_path: str = "ffmpeg",
             ffprobe_path: str | None = None, runner: Callable = subprocess.run) -> dict[str, object]:
    source_path, target = Path(source).expanduser(), Path(output).expanduser()
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 128 or value > 3840 or value % 2
           for value in (width, height)):
        raise ValueError("fit dimensions must be even integers between 128 and 3840")
    if mode not in {"cover", "contain"}:
        raise ValueError("fit mode must be cover or contain")
    fx, fy = _cut_number(focus_x, "focus_x"), _cut_number(focus_y, "focus_y")
    if not 0 <= fx <= 1 or not 0 <= fy <= 1:
        raise ValueError("focus coordinates must be normalized between 0 and 1")
    if encoder not in {"auto", "libx264", "h264_amf"}:
        raise ValueError("encoder must be auto, libx264 or h264_amf")
    if source_path.is_symlink() or not source_path.is_file() or source_path.stat().st_size <= 0:
        raise ValueError("fit source must be a non-empty regular local file")
    if source_path.resolve() == target.resolve(strict=False):
        raise ValueError("fit source and output must be different files")
    if target.is_symlink() or target.exists():
        raise FileExistsError(target)
    if target.suffix.casefold() != ".mp4" or not target.parent.is_dir():
        raise ValueError("fit output must be a new MP4 inside an existing directory")
    probe = probe_media(source_path, ffprobe_path=ffprobe_path, runner=runner)
    if probe.get("status") != "passed" or int(probe.get("video_stream_count", 0)) != 1:
        raise ValueError("fit requires a successfully probed source with exactly one video stream")
    duration = probe.get("duration_seconds")
    if not isinstance(duration, (int, float)) or not math.isfinite(float(duration)) or duration <= 0:
        raise ValueError("fit source duration is unavailable")
    video = probe.get("video") if isinstance(probe.get("video"), dict) else {}
    guidance = probe.get("editing_guidance") if isinstance(probe.get("editing_guidance"), dict) else {}
    if guidance.get("hdr_source_requires_explicit_delivery_decision"):
        raise ValueError("fit currently blocks HDR sources pending explicit color delivery")
    if video.get("rotation"):
        raise ValueError("fit currently blocks rotated sources; normalize orientation first")
    if fps is None:
        if video.get("variable_frame_rate_suspected"):
            raise ValueError("VFR source requires an explicit target fps for fit")
        fps_value = video.get("avg_fps")
    else:
        fps_value = _cut_number(fps, "fps")
    if not isinstance(fps_value, (int, float)) or not math.isfinite(float(fps_value)) or not 1 <= float(fps_value) <= 120:
        raise ValueError("fit requires a known fps between 1 and 120")
    if not isinstance(face_aware, bool):
        raise ValueError("face_aware must be a boolean")
    if isinstance(max_face_samples, bool) or not isinstance(max_face_samples, int) or not 3 <= max_face_samples <= 30:
        raise ValueError("max_face_samples must be between 3 and 30")
    face_framing: dict[str, object] = {"status": "not_requested", "face_center_x": None}
    requested_focus_x = fx
    if face_aware:
        try:
            from muse.video_face_framing import detect_clip_face_center
            face_framing = detect_clip_face_center(source_path, 0.0, float(duration), max_samples=max_face_samples)
        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            face_framing = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}", "face_center_x": None}
        detected_focus = face_framing.get("face_center_x")
        if face_framing.get("status") == "detected" and isinstance(detected_focus, (int, float)) and math.isfinite(float(detected_focus)) and 0 <= float(detected_focus) <= 1:
            fx = float(detected_focus)
        else:
            face_framing["fallback"] = "manual_focus"

    listing = _run_listing(ffmpeg_path, ["-encoders"], runner)
    available = set(listing.get("items", [])) if listing.get("status") == "available" else set()
    if encoder == "h264_amf" and "h264_amf" not in available:
        raise ValueError("h264_amf was not reported by local FFmpeg")
    selected = "h264_amf" if encoder == "h264_amf" or (encoder == "auto" and "h264_amf" in available) else "libx264"
    has_audio = int(probe.get("audio_stream_count", 0)) > 0
    command = build_fit_command(source_path, target, width=width, height=height, mode=mode,
                                focus_x=fx, focus_y=fy, fps=float(fps_value), has_audio=has_audio,
                                video_encoder=selected, ffmpeg_path=ffmpeg_path)
    return {
        "status": "planned", "operation": "fit", "source": str(source_path.resolve()),
        "output": str(target.resolve(strict=False)), "width": width, "height": height,
        "mode": mode, "focus": {"x": fx, "y": fy},
        "requested_focus": {"x": requested_focus_x, "y": fy},
        "face_aware": face_aware, "face_framing": face_framing, "fps": float(fps_value),
        "duration_seconds": float(duration), "has_audio": has_audio,
        "encoder_requested": encoder, "encoder_selected": selected,
        "source_signature": list(_cut_signature(source_path)), "source_probe": probe,
        "command_preview": command, "visual_review_required": True,
        "warnings": ["fit changes the picture; review the result visually before delivery",
                     "cover crops edges; focus coordinates are manual unless a face-aware detection is accepted",
                     "contain adds black bars; it does not synthesize background content",
                     *([] if not face_aware or face_framing.get("status") == "detected" else ["face-aware detection did not meet its confidence gate; manual focus retained"])],
        "writes_files": False,
    }


def fit_media(source: str | Path, output: str | Path, *,
              width: int = 1080, height: int = 1920, mode: str = "cover",
              focus_x: float = 0.5, focus_y: float = 0.5, fps: float | None = None,
              face_aware: bool = False, max_face_samples: int = 12,
              encoder: str = "auto", dry_run: bool = False, ffmpeg_path: str = "ffmpeg",
              ffprobe_path: str | None = None, timeout_seconds: int = 3600,
              runner: Callable = subprocess.run, qa_inspector: Callable | None = None) -> dict[str, object]:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    plan = plan_fit(source, output, width=width, height=height, mode=mode,
                    focus_x=focus_x, focus_y=focus_y, fps=fps,
                    face_aware=face_aware, max_face_samples=max_face_samples, encoder=encoder,
                    ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, runner=runner)
    if dry_run:
        return plan
    source_path, target = Path(source).expanduser(), Path(output).expanduser()
    signature = tuple(plan["source_signature"])
    selected, fallback_used = str(plan["encoder_selected"]), False
    duration, has_audio = float(plan["duration_seconds"]), bool(plan["has_audio"])
    with tempfile.TemporaryDirectory(prefix=".muse-fit-", dir=str(target.parent)) as temporary:
        temp_output = Path(temporary) / "verified-fit.mp4"
        command = build_fit_command(source_path, temp_output, width=width, height=height, mode=mode,
                                    focus_x=float(plan["focus"]["x"]), focus_y=float(plan["focus"]["y"]), fps=float(plan["fps"]),
                                    has_audio=has_audio, video_encoder=selected, ffmpeg_path=ffmpeg_path)
        try:
            rendered = runner(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FFmpeg fit timed out; temporary output was discarded") from exc
        failed = rendered.returncode != 0 or not temp_output.is_file() or temp_output.stat().st_size <= 0
        if failed and encoder == "auto" and selected == "h264_amf":
            temp_output.unlink(missing_ok=True)
            fallback = build_fit_command(source_path, temp_output, width=width, height=height, mode=mode,
                                          focus_x=float(plan["focus"]["x"]), focus_y=float(plan["focus"]["y"]), fps=float(plan["fps"]),
                                          has_audio=has_audio, video_encoder="libx264", ffmpeg_path=ffmpeg_path)
            try:
                rendered = runner(fallback, capture_output=True, text=True, timeout=timeout_seconds, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("AMF fit failed and software fallback timed out") from exc
            if rendered.returncode != 0 or not temp_output.is_file() or temp_output.stat().st_size <= 0:
                raise RuntimeError("AMF fit and libx264 fallback failed: " + (rendered.stderr or "")[-1200:])
            selected, fallback_used = "libx264", True
        elif failed:
            raise RuntimeError("FFmpeg fit failed: " + (rendered.stderr or "")[-1200:])
        output_probe = probe_media(temp_output, ffprobe_path=ffprobe_path, runner=runner)
        actual_duration = output_probe.get("duration_seconds")
        output_video = output_probe.get("video") if isinstance(output_probe.get("video"), dict) else {}
        if (output_probe.get("status") != "passed" or int(output_probe.get("video_stream_count", 0)) != 1
                or output_video.get("width") != width or output_video.get("height") != height
                or abs(float(actual_duration or 0) - duration) > max(0.5, duration * 0.02)
                or int(output_probe.get("audio_stream_count", 0)) != int(has_audio)):
            raise RuntimeError("fit output failed FFprobe dimensions/stream/duration verification")
        if qa_inspector is None:
            from muse.video_qa import inspect_video_file
            qa = inspect_video_file(temp_output, ffprobe_path=ffprobe_path or shutil.which("ffprobe"),
                                    ffmpeg_path=ffmpeg_path or shutil.which("ffmpeg"),
                                    min_duration_seconds=0.1, max_duration_seconds=duration + max(0.5, duration * 0.02),
                                    require_audio=has_audio, runner=runner, timeout_seconds=timeout_seconds)
        else:
            qa = qa_inspector(temp_output)
        if not isinstance(qa, dict) or qa.get("status") != "passed":
            raise RuntimeError("Muse full-decode technical QA failed; fit output was not published")
        if _cut_signature(source_path) != signature:
            raise RuntimeError("fit source changed during render; output was not published")
        os.link(temp_output, target)
    return {"status": "completed", "operation": "fit", "output": str(target.resolve()),
            "bytes": target.stat().st_size, "width": width, "height": height,
            "duration_seconds": round(float(actual_duration), 3), "encoder": selected,
            "encoder_fallback_used": fallback_used, "source_unchanged": True,
            "visual_review_required": True, "focus": plan["focus"],
            "face_aware": plan["face_aware"], "face_framing": plan["face_framing"],
            "warnings": plan["warnings"], "output_probe": output_probe, "technical_qa": qa}




def build_contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract_name": "muse.local_ffmpeg_editing",
        "upstream": UPSTREAM,
        "requirements": {"ffmpeg": ">=6.1", "ffprobe": ">=6.1", "python": ">=3.10"},
        "workflow": [
            "probe", "plan", "check_capabilities", "edit", "probe_output",
            "full_decode", "visual_review_if_picture_changed",
        ],
        "operations": {
            "cut": {
                "supported_modes": ["accurate", "copy"],
                "default_mode": "accurate",
                "scope": "video_stream_mp4",
                "dry_run": True,
                "publish": "same_volume_atomic_hard_link_after_full_decode_qa",
                "hdr_policy": "accurate_reencode_blocked; copy_preserves_source_without_tone_mapping",
                "encoders": {"default": "auto", "preference": ["h264_amf", "libx264"], "fallback": "libx264_if_amf_process_fails"},
            },
            "join": {
                "supported_mode": "strict_compatible_filter_concat_reencode",
                "max_sources": 32, "dry_run": True,
                "publish": "same_volume_atomic_hard_link_after_full_decode_qa",
                "source_mutation": False,
            },
            "fit": {
                "modes": ["cover", "contain"], "default_size": [1080, 1920],
                "manual_focus_coordinates": "normalized_0_to_1", "optional_face_aware": True,
                "face_detection_fallback": "manual_focus", "vfr_requires_explicit_fps": True,
                "visual_review_required": True, "dry_run": True,
                "publish": "same_volume_atomic_hard_link_after_full_decode_qa",
            },
            "contact_sheet": {
                "format": "png", "sample_count_range": [3, 30], "columns_range": [1, 6],
                "thumbnail_width_range": [96, 640],
                "dry_run": True, "visual_review_required": True,
                "publish": "same_volume_atomic_hard_link_after_png_signature_ffprobe_and_full_decode",
            },
        },
        "safety": {
            "local_only": True,
            "cloud_upload": False,
            "api_keys_required": False,
            "source_mutation": False,
            "overwrite_default": False,
            "raw_filtergraph_from_untrusted_input": False,
            "unknown_capabilities_fail_closed": True,
            "technical_qa_is_not_editorial_or_release_approval": True,
        },
        "tools": [item.as_dict() for item in TOOL_CATALOG],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse local FFmpeg skill contract and capability doctor")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("contract", help="print the machine-readable 42-tool integration contract")
    doctor = sub.add_parser("doctor", help="detect local FFmpeg components and per-tool readiness")
    cut = sub.add_parser("cut", help="probe, plan and safely cut a local MP4")
    cut.add_argument("input")
    cut.add_argument("output")
    cut.add_argument("--start", type=float, required=True)
    cut.add_argument("--end", type=float, required=True)
    cut.add_argument("--mode", choices=("accurate", "copy"), default="accurate")
    cut.add_argument("--encoder", choices=("auto", "libx264", "h264_amf"), default="auto")
    cut.add_argument("--dry-run", action="store_true")
    cut.add_argument("--ffmpeg", default="ffmpeg")
    cut.add_argument("--ffprobe")
    join = sub.add_parser("join", help="probe, plan and safely join compatible local MP4 clips")
    join.add_argument("output")
    join.add_argument("inputs", nargs="+")
    join.add_argument("--encoder", choices=("auto", "libx264", "h264_amf"), default="auto")
    join.add_argument("--dry-run", action="store_true")
    join.add_argument("--ffmpeg", default="ffmpeg")
    join.add_argument("--ffprobe")
    fit = sub.add_parser("fit", help="fit a local video to a target frame with a manual focal point")
    fit.add_argument("input")
    fit.add_argument("output")
    fit.add_argument("--width", type=int, default=1080)
    fit.add_argument("--height", type=int, default=1920)
    fit.add_argument("--mode", choices=("cover", "contain"), default="cover")
    fit.add_argument("--focus-x", type=float, default=0.5)
    fit.add_argument("--focus-y", type=float, default=0.5)
    fit.add_argument("--face-aware", action="store_true")
    fit.add_argument("--max-face-samples", type=int, default=12)
    fit.add_argument("--fps", type=float)
    fit.add_argument("--encoder", choices=("auto", "libx264", "h264_amf"), default="auto")
    fit.add_argument("--dry-run", action="store_true")
    fit.add_argument("--ffmpeg", default="ffmpeg")
    fit.add_argument("--ffprobe")
    contact = sub.add_parser("contact-sheet", help="create a keyframe overview PNG for human visual review")
    contact.add_argument("input")
    contact.add_argument("output")
    contact.add_argument("--samples", type=int, default=12)
    contact.add_argument("--columns", type=int, default=4)
    contact.add_argument("--thumbnail-width", type=int, default=320)
    contact.add_argument("--dry-run", action="store_true")
    contact.add_argument("--ffmpeg", default="ffmpeg")
    contact.add_argument("--ffprobe")
    doctor.add_argument("--ffmpeg")
    doctor.add_argument("--ffprobe")
    probe = sub.add_parser("probe", help="inspect a local media file before editing")
    probe.add_argument("input")
    probe.add_argument("--ffprobe")
    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            result = build_contract()
        elif args.command == "doctor":
            result = inspect_ffmpeg_capabilities(ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe)
        elif args.command == "cut":
            result = cut_media(
                args.input, args.output, args.start, args.end, mode=args.mode,
                encoder=args.encoder, dry_run=args.dry_run,
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            )
        elif args.command == "join":
            result = join_media(
                args.inputs, args.output, encoder=args.encoder, dry_run=args.dry_run,
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            )
        elif args.command == "fit":
            result = fit_media(
                args.input, args.output, width=args.width, height=args.height,
                mode=args.mode, focus_x=args.focus_x, focus_y=args.focus_y, fps=args.fps,
                face_aware=args.face_aware, max_face_samples=args.max_face_samples,
                encoder=args.encoder, dry_run=args.dry_run,
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            )
        elif args.command == "contact-sheet":
            from muse.video_contact_sheet import create_contact_sheet
            result = create_contact_sheet(
                args.input, args.output, samples=args.samples, columns=args.columns,
                thumbnail_width=args.thumbnail_width, dry_run=args.dry_run,
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            )
        else:
            result = probe_media(args.input, ffprobe_path=args.ffprobe)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.command == "contract" or result.get("status") in {"ready", "passed", "planned", "completed"} else (2 if result.get("status") == "blocked" else 1)

if __name__ == "__main__":
    raise SystemExit(main())
