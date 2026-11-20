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
    ("scenes", "analysis", "muse.video_highlights + video_motion_diagnostics", "partial", ("ffmpeg",), True),
    ("look", "analysis", "muse.video_motion_diagnostics", "partial", ("ffmpeg",), True),
    ("cut", "execution", "muse.ffmpeg_skill.cut_media + muse.video_highlights", "partial", ("ffmpeg", "ffprobe"), True),
    ("join", "execution", "not yet integrated", "planned", ("ffmpeg", "filter:xfade"), True),
    ("silence", "analysis_and_execution", "muse.video_qa (detection only)", "partial", ("ffmpeg", "filter:silencedetect"), False),
    ("fit", "execution", "muse.video_highlights (9:16 center crop)", "partial", ("ffmpeg", "filter:scale", "filter:crop"), True),
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
    ffmpeg_path: str = "ffmpeg",
) -> list[str]:
    """Build a closed-argument MP4 cut command; no user filtergraph or shell."""
    start = _cut_number(start_seconds, "start_seconds")
    end = _cut_number(end_seconds, "end_seconds")
    if start < 0 or end <= start:
        raise ValueError("cut range must be non-negative and increasing")
    if mode not in {"accurate", "copy"}:
        raise ValueError("mode must be accurate or copy")
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
    command = build_cut_command(
        source_path, target, start, end, mode=mode, has_audio=has_audio,
        ffmpeg_path=ffmpeg_path,
    )
    return {
        "status": "planned",
        "operation": "cut",
        "mode": mode,
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
        source, output, start_seconds, end_seconds, mode=mode,
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

    with tempfile.TemporaryDirectory(prefix=".muse-cut-", dir=str(target.parent)) as temporary:
        temporary_output = Path(temporary) / "verified-cut.mp4"
        command = build_cut_command(
            source_path, temporary_output, start_seconds, end_seconds,
            mode=mode, has_audio=has_audio, ffmpeg_path=ffmpeg_path,
        )
        try:
            rendered = runner(
                command, capture_output=True, text=True, timeout=timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FFmpeg cut timed out; temporary output was discarded") from exc
        if rendered.returncode != 0 or not temporary_output.is_file() or temporary_output.stat().st_size <= 0:
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
        "source_unchanged": True,
        "source_probe": plan["source_probe"],
        "output_probe": output_probe,
        "technical_qa": technical_qa,
    }


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
    cut.add_argument("--dry-run", action="store_true")
    cut.add_argument("--ffmpeg", default="ffmpeg")
    cut.add_argument("--ffprobe")
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
                dry_run=args.dry_run, ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
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
