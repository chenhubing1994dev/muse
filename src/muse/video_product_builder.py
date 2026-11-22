"""Build versioned Muse video products from local assets or local Wan prompts.

Uses the existing FFmpeg factory and local ComfyUI/Wan generation; it never
installs models or calls cloud video APIs.
"""

import argparse
import hashlib
import json
import re
import math
import secrets
import shutil
import sys
import subprocess
import tempfile
import zipfile
from fractions import Fraction
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from muse.media import discover_media_tools
from muse.video_factory import AUDIO_EXTENSIONS, assemble_video_from_folder, discover_video_assets
from muse.video_qa import inspect_video_file, map_warnings_to_storyboard_scenes

_SOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_INPUT_BYTES = 8 * 1024 * 1024 * 1024
_MAX_PRODUCT_BYTES = 2 * 1024 * 1024 * 1024


class VideoProductError(RuntimeError):
    """A video product could not be safely built or verified."""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compile_cantonese_narration_payload(
    narration_script: str, narration_prosody_plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(narration_script, str) or not narration_script.strip():
        raise ValueError("narration_script is required for a structured prosody plan")
    if not isinstance(narration_prosody_plan, Mapping):
        raise ValueError("narration_prosody_plan must be a mapping")
    from muse.narration_prosody_bridge import build_cantonese_tts_segments
    payload = build_cantonese_tts_segments(narration_prosody_plan)
    expected_script = "\n".join(item["text"] for item in payload)
    if expected_script != narration_script:
        raise VideoProductError("structured narration segment text does not match narration_script")
    return payload


def _build_cantonese_narration_prosody_record(
    narration_script: str, plan: Mapping[str, Any], synthesis: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _compile_cantonese_narration_payload(narration_script, plan)
    plan_segments = plan.get("segments")
    runtime_segments = synthesis.get("segment_metadata")
    audio_sha256 = synthesis.get("audio_sha256")
    backend_capabilities = synthesis.get("backend_capabilities")
    applied_controls = synthesis.get("applied_controls")
    unsupported_controls = synthesis.get("unsupported_controls")
    if (synthesis.get("status") != "completed"
            or not isinstance(plan_segments, list) or len(plan_segments) != len(payload)
            or not isinstance(runtime_segments, list) or len(runtime_segments) != len(payload)
            or not isinstance(audio_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", audio_sha256)
            or backend_capabilities != ["pause_after"]
            or not isinstance(applied_controls, list) or set(applied_controls) - {"pause_after"}
            or not isinstance(unsupported_controls, list)
            or synthesis.get("segment_count") != len(payload)
            or synthesis.get("waveform_pause_accounting_verified") is not True):
        raise VideoProductError("structured narration runtime returned incomplete or inconsistent evidence")

    segment_records: list[dict[str, Any]] = []
    total_pause_frames = 0
    for index, (planned, item, actual) in enumerate(zip(plan_segments, payload, runtime_segments)):
        if not isinstance(planned, Mapping) or not isinstance(actual, Mapping):
            raise VideoProductError("structured narration segment evidence must contain mappings")
        text_sha256 = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
        pause_after_ms = int(item.get("pause_after_ms", 0))
        pause_frames = 48_000 * pause_after_ms // 1000
        if (actual.get("index") != index or actual.get("text_sha256") != text_sha256
                or actual.get("pause_after_ms") != pause_after_ms
                or actual.get("inserted_pause_frames") != pause_frames):
            raise VideoProductError(f"structured narration runtime evidence does not match segment {index}")
        requested = planned.get("requested_controls")
        unsupported = planned.get("unsupported_controls")
        planned_supported = planned.get("applied_controls")
        if (not isinstance(requested, list) or not isinstance(unsupported, list)
                or not isinstance(planned_supported, Mapping)):
            raise VideoProductError(f"structured narration plan controls are invalid for segment {index}")
        segment_records.append({
            "index": index,
            "text_sha256": text_sha256,
            "requested_controls": list(requested),
            "planned_supported_controls": sorted(planned_supported),
            "unsupported_controls": list(unsupported),
            "pause_after_ms": pause_after_ms,
            "inserted_pause_frames": pause_frames,
        })
        total_pause_frames += pause_frames

    if synthesis.get("inserted_pause_frames") != total_pause_frames:
        raise VideoProductError("structured narration total pause frames do not match per-segment evidence")
    if synthesis.get("waveform_pause_verified") is not bool(total_pause_frames):
        raise VideoProductError("structured narration waveform pause verification does not match requested pauses")

    record = {
        "schema": "muse.narration.prosody.runtime.v1",
        "backend": "canto-tts-nano",
        "backend_capabilities": list(backend_capabilities),
        "audio_effects_verified": False,
        "backend_runtime_validation": "passed",
        "waveform_pause_accounting_verified": True,
        "waveform_pause_verified": synthesis.get("waveform_pause_verified") is True,
        "human_listening_review_required": True,
        "requires_acoustic_review": True,
        "script_sha256": hashlib.sha256(narration_script.encode("utf-8")).hexdigest(),
        "segments": segment_records,
        "runtime": {
            "audio_sha256": audio_sha256,
            "sample_rate": synthesis.get("sample_rate"),
            "channels": synthesis.get("channels"),
            "frames": synthesis.get("frames"),
            "duration_seconds": synthesis.get("duration_seconds"),
            "segment_count": synthesis.get("segment_count"),
            "inserted_pause_frames": total_pause_frames,
            "inserted_pause_seconds": synthesis.get("inserted_pause_seconds"),
            "backend_capabilities": list(backend_capabilities),
            "applied_controls": list(applied_controls),
            "unsupported_controls": list(unsupported_controls),
        },
    }
    return record




def _probe_media_duration_seconds(path: Path, ffprobe_path: str, *, stream_type: str) -> float:
    """Read one media stream's duration; fail closed when ffprobe cannot prove it."""
    if stream_type not in {"video", "audio"}:
        raise ValueError("stream_type must be video or audio")
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "format=duration:stream=codec_type,duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise VideoProductError(f"FFprobe could not measure {stream_type} duration: " + str((result.stderr or "")[-500:]))
    try:
        document = json.loads(result.stdout)
        streams = document.get("streams") if isinstance(document, dict) else None
        stream = next(item for item in streams if isinstance(item, dict) and item.get("codec_type") == stream_type)
        raw_duration = stream.get("duration") or document.get("format", {}).get("duration")
        duration = float(raw_duration)
    except (TypeError, ValueError, KeyError, StopIteration, AttributeError):
        raise VideoProductError(f"FFprobe did not return a usable {stream_type} duration") from None
    if not math.isfinite(duration) or duration <= 0:
        raise VideoProductError(f"FFprobe returned an invalid {stream_type} duration")
    return duration




def _probe_video_stream_metadata(path: Path, ffprobe_path: str) -> tuple[int, int, float, int]:
    """Read actual video dimensions, frame rate and decoded frame count from FFprobe."""
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,nb_read_frames,nb_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if result.returncode != 0:
        raise VideoProductError("FFprobe could not inspect video stream metadata: " + str((result.stderr or "")[-500:]))
    try:
        document = json.loads(result.stdout)
        streams = document.get("streams") if isinstance(document, dict) else None
        stream = next(
            item for item in streams
            if isinstance(item, dict) and item.get("codec_type") == "video"
        )
        width = int(stream["width"])
        height = int(stream["height"])
        raw_rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
        frame_rate = float(Fraction(raw_rate))
        raw_frames = stream.get("nb_read_frames") or stream.get("nb_frames")
        frames = int(raw_frames)
    except (TypeError, ValueError, KeyError, StopIteration, AttributeError, ZeroDivisionError):
        raise VideoProductError("FFprobe did not return usable video dimensions, frame rate and frame count") from None
    if width <= 0 or height <= 0 or not math.isfinite(frame_rate) or frame_rate <= 0 or frames <= 0:
        raise VideoProductError("FFprobe returned invalid video stream metadata")
    return width, height, frame_rate, frames


def _validate_narration_timeline(video_duration: float, narration_duration: float, *, tolerance_seconds: float = 0.0) -> dict[str, Any]:
    """Reject narration that would be silently truncated by FFmpeg -shortest."""
    values = (video_duration, narration_duration, tolerance_seconds)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise VideoProductError("video and narration durations must be finite numbers")
    if video_duration <= 0 or narration_duration <= 0 or tolerance_seconds < 0:
        raise VideoProductError("video and narration durations must be positive")
    if narration_duration > video_duration + tolerance_seconds:
        raise VideoProductError(
            f"narration ({narration_duration:.3f}s) exceeds generated video ({video_duration:.3f}s); "
            "refusing to truncate voiceover—generate additional shots or extend the video first"
        )
    return {
        "status": "passed", "video_duration_seconds": round(float(video_duration), 6),
        "narration_duration_seconds": round(float(narration_duration), 6),
        "tolerance_seconds": float(tolerance_seconds),
        "policy": "narration_must_fit_video_no_silent_truncation",
    }


def _allocate_wan_scene_durations(
    scenes: list[dict[str, Any]], narration_duration: float, *, fps: int = 12,
    max_total_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Extend shots in Wan's 4n+1 frame increments so narration fits before GPU work."""
    if (isinstance(narration_duration, bool) or not isinstance(narration_duration, (int, float))
            or not math.isfinite(narration_duration) or narration_duration <= 0):
        raise VideoProductError("Cantonese narration duration must be finite and positive")
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise ValueError("fps must be a positive integer")
    frame_counts: list[int] = []
    for scene in scenes:
        frames = max(13, min(61, int(round(float(scene["duration_seconds"]) * fps))))
        frames = min(61, ((frames - 1 + 3) // 4) * 4 + 1)
        frame_counts.append(frames)
    target_frames = math.ceil((float(narration_duration) + 1.0 / fps) * fps - 1e-9)
    while sum(frame_counts) < target_frames:
        candidates = [index for index, frames in enumerate(frame_counts) if frames <= 57]
        if not candidates:
            raise VideoProductError(
                "Cantonese narration exceeds the maximum duration of the supplied Wan scenes; add more scenes"
            )
        index = min(candidates, key=lambda item: (frame_counts[item] / max(1.0, float(scenes[item]["duration_seconds"])), item))
        frame_counts[index] += 4
        planned_seconds = sum((frames - 1) / fps for frames in frame_counts)
        if planned_seconds > max_total_seconds + 1e-9:
            raise VideoProductError(
                "Cantonese narration cannot fit within the 60-second multi-scene preview limit; add more scenes"
            )
    planned_seconds = sum((frames - 1) / fps for frames in frame_counts)
    if planned_seconds > max_total_seconds + 1e-9:
        raise VideoProductError(
            "Cantonese narration cannot fit within the 60-second multi-scene preview limit; add more scenes"
        )
    return [
        {**scene, "duration_seconds": (frames - 1) / fps}
        for scene, frames in zip(scenes, frame_counts)
    ]


def _safe_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

def _validate_recipe_sha256(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("recipe_sha256 must be a lowercase SHA-256 digest")
    return value

def _resolve_recipe_sha256(
    workspace: str | Path, creator_initiative_id: str | None, supplied: str | None,
) -> str | None:
    requested = _validate_recipe_sha256(supplied)
    if creator_initiative_id is None:
        return requested
    database = Path(workspace) / "data" / "initiatives" / "muse.sqlite3"
    if database.is_symlink() or not database.is_file():
        return requested
    from muse.initiative_engine import InitiativeStore

    with InitiativeStore(database) as store:
        initiative = store.get_by_id(creator_initiative_id)
        active_attempts = store.connection.execute(
            """SELECT recipe_sha256 FROM initiative_video_attempts
               WHERE initiative_id=? AND status IN ('queued','running')
               ORDER BY created_at DESC LIMIT 8""",
            (creator_initiative_id,),
        ).fetchall()
    if initiative is None:
        return requested
    try:
        concept = json.loads(initiative["concept"])
    except (TypeError, json.JSONDecodeError):
        concept = {}
    brief = concept.get("creator_brief") if isinstance(concept, dict) else None
    video = brief.get("video") if isinstance(brief, dict) else None
    brief_hash = _validate_recipe_sha256(video.get("recipe_sha256")) if isinstance(video, dict) and video.get("recipe_sha256") is not None else None

    if active_attempts:
        active_hashes = {row["recipe_sha256"] for row in active_attempts}
        if len(active_hashes) != 1:
            raise ValueError("active video attempts for this initiative have conflicting recipe fingerprints")
        active_hash = _validate_recipe_sha256(next(iter(active_hashes)))
        if requested is not None and requested != active_hash:
            raise ValueError("requested recipe fingerprint does not match the active video attempt")
        if brief_hash is not None and brief_hash != active_hash:
            raise ValueError("creator brief recipe fingerprint does not match the active video attempt")
        return active_hash
    if requested is not None and brief_hash is not None and requested != brief_hash:
        raise ValueError("requested recipe fingerprint does not match the creator brief")
    return requested if requested is not None else brief_hash





def _revalidate_claim_grounded_contract(workspace: Path, contract: dict[str, Any]) -> None:
    """Rebuild a publishable contract from the live local ResearchKnowledgeStore."""
    from muse.narration_evidence_contract import (
        build_evidence_by_claim_from_dossier,
        build_narration_evidence_contract,
    )
    from muse.research_knowledge import ResearchKnowledgeStore

    database = workspace / "data" / "research" / "knowledge.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise VideoProductError("claim-grounded narration requires the live ResearchKnowledgeStore database")
    segments: list[dict[str, Any]] = []
    claim_ids: list[str] = []
    for item in contract["segments"]:
        segment = {
            "index": item["index"],
            "text": item["text"],
            "factual": item["factual"],
            "claim_ids": item["claim_ids"],
        }
        timecode = item.get("timecode")
        if isinstance(timecode, dict):
            segment["start_seconds"] = timecode.get("start_seconds")
            segment["end_seconds"] = timecode.get("end_seconds")
        segments.append(segment)
        for claim_id in item["claim_ids"]:
            if not isinstance(claim_id, str) or not re.fullmatch(r"[a-f0-9]{32}", claim_id):
                raise VideoProductError("claim-grounded narration contains an invalid Claim ID")
            if claim_id not in claim_ids:
                claim_ids.append(claim_id)

    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    try:
        with ResearchKnowledgeStore(database) as research:
            for claim_id in claim_ids:
                try:
                    dossier = research.claim_dossier(claim_id, evidence_limit=50)
                except ValueError:
                    continue
                evidence_by_claim.update(build_evidence_by_claim_from_dossier(dossier))
        rebuilt = build_narration_evidence_contract(segments, evidence_by_claim)
    except (TypeError, ValueError, KeyError, RuntimeError) as exc:
        raise VideoProductError(
            f"claim-grounded narration could not be revalidated against ResearchKnowledgeStore: {type(exc).__name__}"
        ) from None
    if rebuilt != contract:
        raise VideoProductError(
            "claim-grounded narration contract does not match live ResearchKnowledgeStore reviews and citations"
        )


def _inspect_product_video(
    path: Path, *, ffprobe_path: str | None, ffmpeg_path: str | None,
    expected_width: int | None = None, expected_height: int | None = None,
    expected_fps: float | None = None, require_audio: bool = False,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Persist a portable technical QA report for one rendered MP4."""
    raw = inspect_video_file(
        path, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path,
        expected_width=expected_width, expected_height=expected_height,
        expected_fps=expected_fps, require_audio=require_audio,
        timeout_seconds=timeout_seconds,
    )
    report = dict(raw) if isinstance(raw, dict) else {
        "status": "failed", "missing_tools": [],
        "checks": {"inspection": {"passed": False, "detail": "QA returned an invalid result"}},
        "metadata": {},
    }
    if report.get("status") not in {"passed", "failed", "blocked"}:
        report["status"] = "failed"
        checks = report.get("checks")
        if not isinstance(checks, dict):
            checks = {}
        checks["inspection"] = {"passed": False, "detail": "QA returned an unknown status"}
        report["checks"] = checks
    report["schema_version"] = 1
    report["path"] = "video.mp4"
    report["artifact_sha256"] = _hash_file(path)
    return report


def _finalize_video_metadata(
    source_metadata: dict[str, Any], qa_report: dict[str, Any], output_path: Path,
    *, motion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep generated-source facts while making the product metrics describe the final MP4."""
    result = dict(source_metadata)
    source_duration = (motion or {}).get("source_duration_seconds")
    if not isinstance(source_duration, (int, float)) or isinstance(source_duration, bool) or not math.isfinite(source_duration):
        source_duration = result.get("duration_seconds")
    source_frames = result.get("frames")
    if isinstance(source_duration, (int, float)) and not isinstance(source_duration, bool) and math.isfinite(source_duration):
        result["source_duration_seconds"] = float(source_duration)
    if isinstance(source_frames, int) and not isinstance(source_frames, bool) and source_frames >= 0:
        result["source_frames"] = source_frames

    media = qa_report.get("metadata") if isinstance(qa_report, dict) else None
    if isinstance(media, dict):
        for key in ("duration_seconds", "width", "height", "fps"):
            value = media.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                result[key] = value
        audio_codecs = media.get("audio_codecs")
        if isinstance(audio_codecs, list):
            result["has_audio"] = bool(audio_codecs)
    fps, duration = result.get("fps"), result.get("duration_seconds")
    if (isinstance(fps, (int, float)) and not isinstance(fps, bool) and math.isfinite(fps)
            and isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration)):
        result["frames"] = max(0, int(round(float(fps) * float(duration))))
    result["bytes"] = output_path.stat().st_size
    return result


def _default_scene_motion_directives(scene_count: int) -> list[dict[str, str]]:
    """Apply subtle deterministic camera motion so imported static shots do not remain inert."""
    if isinstance(scene_count, bool) or not isinstance(scene_count, int) or scene_count < 1:
        raise ValueError("scene_count must be a positive integer")
    camera_moves = ("push_in", "drift_left", "pull_out", "rise", "drift_right")
    return [
        {"camera_move": camera_moves[index % len(camera_moves)], "vfx": "none"}
        for index in range(scene_count)
    ]

_SCENE_COMPOSITION_FIELDS = (
    "subject_center_x", "subject_center_y", "subject_scale",
    "negative_space_left", "negative_space_right",
    "negative_space_top", "negative_space_bottom", "motion_level",
)


def _content_aware_scene_motion_directives(
    scene_analysis_metadata: Any, *, scene_count: int | None = None,
    metadata_source: str = "caller_supplied",
) -> list[dict[str, Any]]:
    """Choose conservative camera moves from caller-supplied, normalized composition facts.
    This deliberately does not infer visual facts from media. Missing, malformed or
    mismatched metadata falls back for the entire sequence to the stable legacy cycle.
    """
    count = scene_count
    if count is None and isinstance(scene_analysis_metadata, list):
        count = len(scene_analysis_metadata)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("scene_count must be a positive integer")

    valid = isinstance(scene_analysis_metadata, list) and len(scene_analysis_metadata) == count
    normalized: list[dict[str, float]] = []
    if valid:
        for scene in scene_analysis_metadata:
            if not isinstance(scene, dict) or any(
                isinstance(scene.get(field), bool)
                or not isinstance(scene.get(field), (int, float))
                or not math.isfinite(scene[field])
                or not 0.0 <= scene[field] <= 1.0
                for field in _SCENE_COMPOSITION_FIELDS
            ):
                valid = False
                break
            normalized.append({field: float(scene[field]) for field in _SCENE_COMPOSITION_FIELDS})

    if not valid:
        return _default_scene_motion_directives(count)
    directives: list[dict[str, Any]] = []
    for scene in normalized:
        move = "none"
        if scene["motion_level"] >= 0.7:
            move = "none"
        elif scene["subject_scale"] <= 0.3:
            move = "push_in"
        elif scene["subject_scale"] >= 0.7:
            move = "pull_out"
        if move == "none" and scene["subject_center_x"] <= 0.5:
            if scene["negative_space_right"] - scene["negative_space_left"] >= 0.25:
                move = "drift_right"
        elif move == "none" and scene["subject_center_x"] >= 0.5:
            if scene["negative_space_left"] - scene["negative_space_right"] >= 0.25:
                move = "drift_left"


        directives.append({
            "camera_move": move, "vfx": "none", "strategy": "explicit_composition_v1",
            "metadata_source": metadata_source, "scene_analysis": scene,
        })
    return directives

def _quality_gate_summary(report: dict[str, Any]) -> dict[str, Any]:
    status = report.get("status")
    if status not in {"passed", "failed", "blocked"}:
        status = "failed"
    reason = {
        "passed": "editorial_rights_disclosure_platform_and_account_review_required",
        "failed": "technical_qa_failed",
        "blocked": "technical_qa_blocked_missing_or_unavailable_tools",
    }[status]
    raw_warnings = report.get("warnings")
    warnings = [item for item in raw_warnings if isinstance(item, dict)] if isinstance(raw_warnings, list) else []
    warning_codes = sorted({item["code"] for item in warnings if isinstance(item.get("code"), str)})
    return {
        "schema_version": 1,
        "status": status,
        "passed": status == "passed",
        "ready_to_publish": False,
        "blocking_reason": reason,
        "review_warning_count": len(warnings),
        "review_warning_codes": warning_codes,
        "warning_review_required": bool(warnings),
        "report": "qa-report.json",
    }


class VideoProductBuilder:
    """Create a product-library entry from assets in data/video_sources/<key>."""

    BUILDER_VERSION = 15

    def __init__(self, workspace: str | Path, *, motion_workspace: str | Path | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise VideoProductError("workspace must be an existing directory")
        self.motion_workspace = Path(motion_workspace).resolve() if motion_workspace is not None else self.workspace
        if not self.motion_workspace.is_dir():
            raise VideoProductError("motion workspace must be an existing directory")


    def _roots(self) -> tuple[Path, Path]:
        data = self.workspace / "data"
        source_root = data / "video_sources"
        products_root = data / "products"
        for path in (data, source_root, products_root):
            if path.is_symlink():
                raise VideoProductError("video source and product roots must not contain symlinks")
        source_root.mkdir(parents=True, exist_ok=True)
        products_root.mkdir(parents=True, exist_ok=True)
        if source_root.resolve().parent != data.resolve() or products_root.resolve().parent != data.resolve():
            raise VideoProductError("video source or product root escapes the workspace")
        return source_root.resolve(), products_root.resolve()

    def _source(self, source_key: str, audio_name: str | None) -> tuple[Path, list[Path], Path | None]:
        if not isinstance(source_key, str) or not _SOURCE_KEY.fullmatch(source_key):
            raise ValueError("source must be a simple folder key (letters, numbers, _ or -)")
        source_root, _ = self._roots()
        source = source_root / source_key
        if source.is_symlink() or not source.is_dir() or source.resolve().parent != source_root:
            raise FileNotFoundError(f"copy source assets into data/video_sources/{source_key}")
        assets = discover_video_assets(source)
        for asset in assets:
            if asset.is_symlink() or asset.resolve().parent != source.resolve():
                raise VideoProductError("video source assets must be regular files inside the source folder")
        audio = None
        if audio_name is not None:
            if (not isinstance(audio_name, str) or not audio_name or Path(audio_name).name != audio_name
                    or Path(audio_name).suffix.casefold() not in AUDIO_EXTENSIONS):
                raise ValueError("audio must be a supported audio filename in the source folder")
            audio = source / audio_name
            if audio.is_symlink() or not audio.is_file() or audio.resolve().parent != source.resolve():
                raise FileNotFoundError("audio file not found inside the selected source folder")
        total = sum(path.stat().st_size for path in assets) + (audio.stat().st_size if audio else 0)
        if total > _MAX_INPUT_BYTES:
            raise ValueError("source assets exceed the 8 GiB input limit")
        return source, assets, audio

    def _apply_motion_profile(
        self, base_video: Path, output_video: Path, *, motion_profile: str,
        title: str, width: int, height: int, fps: int,
        ffmpeg_path: str, ffprobe_path: str, timeout_seconds: int, audio_mode: str = "narration",
        scene_assets: list[Path] | None = None, image_seconds: float = 4.0,
        shot_directives: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if motion_profile == "none":
            base_video.replace(output_video)
            return {"status": "disabled", "profile": "none"}
        if motion_profile not in {"brand_intro_outro", "brand_scene_transitions"}:
            raise ValueError("motion_profile must be none, brand_intro_outro or brand_scene_transitions")
        from muse.remotion_motion import RemotionMotionAdapter, RemotionMotionError
        try:
            adapter = RemotionMotionAdapter(
                self.motion_workspace, ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
                timeout_seconds=timeout_seconds,
            )
            scene_motion = None
            motion_source = base_video
            if motion_profile == "brand_scene_transitions":
                if not scene_assets:
                    raise ValueError("brand_scene_transitions requires at least one source scene")
                motion_source = base_video.parent / "scene-timeline.mp4"
                scene_motion = adapter.render_scene_timeline(
                    scene_assets, base_video, motion_source, width=width, height=height,
                    fps=fps, image_seconds=image_seconds,
                    shot_directives=shot_directives or _default_scene_motion_directives(len(scene_assets)),
                )
            result = adapter.render(
                motion_source, output_video, title=title,
                width=width, height=height, fps=fps, audio_mode=audio_mode,
            )
            if scene_motion is not None:
                result["scene_timeline"] = scene_motion
                result["transition"] = scene_motion["transition"] + "_plus_bookend_crossfade"
                result["scene_count"] = scene_motion["scene_count"]
                result["scene_transition_seconds"] = scene_motion["transition_seconds"]
        except (OSError, ValueError, RemotionMotionError) as exc:
            raise VideoProductError(f"Remotion motion stage failed: {exc}") from exc
        if result.get("status") != "completed" or not output_video.is_file():
            raise VideoProductError("Remotion motion stage did not produce a verified MP4")
        return result

    def build_from_source(
        self, source_key: str, title: str, *, audio_name: str | None = None,
        audio_mode: str = "music", aspect_ratio: str = "16:9", fps: int = 30, image_seconds: float = 4.0,
        encoder: str = "auto", timeout_seconds: int = 3600, motion_profile: str = "none",
        creator_initiative_id: str | None = None, evolution_cycle_id: str | None = None,
        scene_analysis_metadata: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise ValueError("title must contain 1-160 characters")
        if audio_mode not in {"music", "narration"}:
            raise ValueError("audio_mode must be music or narration")
        if isinstance(image_seconds, bool) or not isinstance(image_seconds, (int, float)) or not math.isfinite(image_seconds) or not 0.5 <= image_seconds <= 60:
            raise ValueError("image_seconds must be between 0.5 and 60")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be a positive integer")
        if aspect_ratio not in {"16:9", "9:16", "1:1"}:
            raise ValueError("aspect_ratio must be 16:9, 9:16 or 1:1")
        if isinstance(fps, bool) or not isinstance(fps, int) or fps not in {24, 25, 30, 50, 60}:
            raise ValueError("fps must be one of 24, 25, 30, 50 or 60")

        if encoder not in {"auto", "amf", "software"}:
            raise ValueError("encoder must be auto, amf or software")
        if motion_profile not in {"none", "brand_intro_outro", "brand_scene_transitions"}:
            raise ValueError("motion_profile must be none, brand_intro_outro or brand_scene_transitions")
        source, assets, audio = self._source(source_key, audio_name)
        analysis_source = "caller_supplied"
        scene_analysis_report = None
        if motion_profile == "brand_scene_transitions" and scene_analysis_metadata is None:
            try:
                from .scene_analysis import analyze_scene_assets
                analysis = analyze_scene_assets(assets)
                if isinstance(analysis, dict):
                    scene_analysis_report = {
                        key: analysis[key] for key in (
                            "status", "source", "semantic_recognition", "reason", "motion_diagnostics",
                        ) if key in analysis
                    }
                if analysis.get("status") == "completed" and isinstance(analysis.get("scenes"), list):
                    scene_analysis_metadata = analysis["scenes"]
                    analysis_source = str(analysis.get("source") or "local_visual_heuristic")
            except Exception:
                # Optional analysis must never prevent the established safe fallback.
                scene_analysis_metadata = None
                scene_analysis_report = {"status": "unavailable", "reason": "analysis_exception"}
        shot_directives = (
            _content_aware_scene_motion_directives(
                scene_analysis_metadata, scene_count=len(assets), metadata_source=analysis_source,
            )
            if motion_profile == "brand_scene_transitions" else None
        )
        inventory = discover_media_tools()
        tools = {item.tool_id: item.executable for item in inventory.tools}
        ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
        if not ffmpeg or not ffprobe:
            raise VideoProductError("FFmpeg and FFprobe must both be installed")
        source_facts = [{"name": path.name, "bytes": path.stat().st_size, "sha256": _hash_file(path)} for path in assets]
        audio_fact = ({"name": audio.name, "bytes": audio.stat().st_size, "sha256": _hash_file(audio)} if audio else None)
        identity = hashlib.sha256(("muse-video:" + source_key + ":" + title.strip()).encode("utf-8")).hexdigest()[:32]
        settings = {"aspect_ratio": aspect_ratio, "fps": fps, "image_seconds": float(image_seconds), "encoder": encoder, "audio_mode": audio_mode, "motion_profile": motion_profile}
        if shot_directives is not None:
            settings["shot_directives"] = shot_directives
        if scene_analysis_report is not None:
            settings["scene_analysis"] = scene_analysis_report
        fingerprint = hashlib.sha256(_safe_json({"builder_version": self.BUILDER_VERSION, "assets": source_facts, "audio": audio_fact, "settings": settings})).hexdigest()
        version = fingerprint[:20]
        _, products_root = self._roots()
        initiative_root = products_root / identity
        target = initiative_root / version
        for path in (initiative_root, target):
            if path.is_symlink():
                raise VideoProductError("video product destination must not contain symlinks")

        if target.exists():
            result = self.verify_path(products_root, identity, version)
            return self._record_creator_artifact(result, title=title, prompt="", creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id)
        initiative_root.mkdir(parents=True, exist_ok=True)
        if initiative_root.is_symlink() or initiative_root.resolve().parent != products_root:
            raise VideoProductError("video product destination escapes product root")
        width, height = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}[aspect_ratio]
        staging = Path(tempfile.mkdtemp(prefix=".video-product-", dir=initiative_root))
        try:
            base_rendered = staging / "base-video.mp4"
            rendered = staging / "video.mp4"
            result = assemble_video_from_folder(
                source, base_rendered, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
                available_encoders=inventory.hardware_encoders, audio_path=audio,
                audio_mode=audio_mode, image_seconds=float(image_seconds), width=width,
                height=height, fps=fps, encoder_preference=encoder, timeout_seconds=timeout_seconds,
            )
            if result.get("status") != "completed":
                raise VideoProductError("video assembly failed: " + str(result.get("error") or result.get("stage") or result.get("status")))
            source_root = self.workspace / "data" / "video_sources"
            if source.is_symlink() or source.resolve().parent != source_root.resolve() or any(path.is_symlink() or path.resolve().parent != source.resolve() for path in assets):
                raise VideoProductError("video source path changed or became unsafe during rendering")
            if audio and (audio.is_symlink() or audio.resolve().parent != source.resolve()):
                raise VideoProductError("audio source path changed or became unsafe during rendering")
            current_source_facts = [{"name": path.name, "bytes": path.stat().st_size, "sha256": _hash_file(path)} for path in assets]
            current_audio_fact = ({"name": audio.name, "bytes": audio.stat().st_size, "sha256": _hash_file(audio)} if audio else None)
            if current_source_facts != source_facts or current_audio_fact != audio_fact:
                raise VideoProductError("source assets changed during rendering; refusing to publish an inconsistent product")
            motion = self._apply_motion_profile(
                base_rendered, rendered, motion_profile=motion_profile, title=title.strip(),
                width=width, height=height, fps=fps, ffmpeg_path=ffmpeg,
                ffprobe_path=ffprobe, timeout_seconds=timeout_seconds, audio_mode=audio_mode,
                scene_assets=assets if motion_profile == "brand_scene_transitions" else None,
                image_seconds=float(image_seconds), shot_directives=shot_directives,
            )
            current_source_facts = [{"name": path.name, "bytes": path.stat().st_size, "sha256": _hash_file(path)} for path in assets]
            current_audio_fact = ({"name": audio.name, "bytes": audio.stat().st_size, "sha256": _hash_file(audio)} if audio else None)
            if current_source_facts != source_facts or current_audio_fact != audio_fact:
                raise VideoProductError("source assets changed during motion rendering; refusing to publish an inconsistent product")
            if not rendered.is_file() or rendered.stat().st_size > _MAX_PRODUCT_BYTES:
                raise VideoProductError("rendered MP4 is missing or exceeds the 2 GiB product limit")
            qa_report = _inspect_product_video(
                rendered, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
                expected_width=width, expected_height=height, expected_fps=float(fps),
                require_audio=audio is not None, timeout_seconds=timeout_seconds,
            )
            quality_gate = _quality_gate_summary(qa_report)
            result = _finalize_video_metadata(result, qa_report, rendered, motion=motion)
            storyboard_scenes = [
                {"id": f"shot-{index:02d}", "order": index, "source_asset": path.name,
                 "source_sha256": fact["sha256"], "generation_status": "imported_asset",
                 **({key: directive[key] for key in (
                     "camera_move", "vfx", "strategy", "metadata_source", "scene_analysis",
                 ) if key in directive} if shot_directives and (directive := shot_directives[index - 1]) else {})}
                for index, (path, fact) in enumerate(zip(assets, source_facts), start=1)
            ]

            scene_timeline = motion.get("scene_timeline") if isinstance(motion, dict) else None
            scene_timings = scene_timeline.get("scene_timings") if isinstance(scene_timeline, dict) else None
            if scene_timeline is not None:
                if not isinstance(scene_timings, list) or len(scene_timings) != len(storyboard_scenes):
                    raise VideoProductError("Remotion scene timeline is missing per-scene timing records")
                for scene, timing in zip(storyboard_scenes, scene_timings):
                    if (not isinstance(timing, dict)
                            or not isinstance(timing.get("timeline_start_seconds"), (int, float))
                            or not isinstance(timing.get("timeline_end_seconds"), (int, float))
                            or not isinstance(timing.get("duration_seconds"), (int, float))):
                        raise VideoProductError("Remotion scene timeline contains invalid per-scene timing records")
                    scene.update({
                        "duration_seconds": timing["duration_seconds"],
                        "timeline_start_seconds": timing["timeline_start_seconds"],
                        "timeline_end_seconds": timing["timeline_end_seconds"],
                        "transition_in_seconds": timing.get("transition_in_seconds", 0.0),
                        "transition_out_seconds": timing.get("transition_out_seconds", 0.0),
                    })
                qa_report["storyboard_mapping"] = map_warnings_to_storyboard_scenes(
                    qa_report.get("warnings", []), {"scenes": storyboard_scenes},
                )
            storyboard = {
                "schema_version": 1, "title": title.strip(), "production_mode": "local_asset_assembly",
                "final_output": {key: result[key] for key in ("duration_seconds", "frames", "width", "height", "fps", "has_audio", "bytes") if key in result},
                "ai_generation": "not_requested", "source_folder": source_key, "settings": settings,
                "motion": motion, "scenes": storyboard_scenes,
            }
            (staging / "qa-report.json").write_bytes(_safe_json(qa_report))
            readme = (
                f"# {title.strip()}\n\nMuse 视频产品（本地素材合成版）。本次流程只组合你提供的图片/视频素材，没有声称生成 AI 镜头。\n\n"
                f"- 来源目录：`data/video_sources/{source_key}`\n- 场景素材：{len(assets)}\n"
                f"- 画幅：{aspect_ratio}\n- 帧率：{fps} fps\n- 时长：{result['duration_seconds']} 秒\n"
                f"- 编码器：{result['encoder']}\n- 音轨：{audio_mode if audio else '无'}\n"
                f"- Remotion 动效：{motion.get('profile')}；转场：{motion.get('transition', '无')}\n"
                f"- 技术质检：{quality_gate['status']}；产品仍为 testing。\n"
                "通过技术质检不等于内容、事实、版权/肖像、披露或平台发布审批。\n"
                "- AI 镜头生成：未执行（ComfyUI/Velorn 需要单独配置并验收）\n\n"
                "文件：`video.mp4` 为成片；`storyboard.json` 记录导入素材顺序和制作参数。\n"
            ).encode("utf-8")
            storyboard_bytes = _safe_json(storyboard)
            (staging / "README.md").write_bytes(readme)
            (staging / "storyboard.json").write_bytes(storyboard_bytes)
            with zipfile.ZipFile(staging / "product.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(staging / "README.md", "README.md")
                archive.write(staging / "storyboard.json", "storyboard.json")
                archive.write(staging / "qa-report.json", "qa-report.json")
                archive.write(rendered, "video.mp4")
            files = {name: _hash_file(staging / name) for name in ("README.md", "storyboard.json", "qa-report.json", "video.mp4", "product.zip")}
            video_metadata = {key: value for key, value in result.items() if key != "output"}
            video_metadata["output"] = "video.mp4"
            video_metadata["motion"] = motion
            manifest = {
                "schema_version": 1, "builder_version": self.BUILDER_VERSION,
                "product_kind": "video_creator", "initiative_id": identity, "version_id": version,
                "title": title.strip(), "status": "testing", "generated_at": datetime.now(timezone.utc).isoformat(),
                "input_sha256": fingerprint, "files": files, "video_metadata": video_metadata,
                "quality_gate": quality_gate,
                "source_assets": source_facts, "audio_asset": audio_fact,
                "execution": {"arbitrary_code_executed": False, "network_access": False,
                              "local_tools_invoked": ["ffmpeg", "ffprobe"] + (["node", "remotion"] if motion_profile != "none" else []), "ai_generation": "not_requested"},
            }
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return self._publish_staging(
                staging, products_root, identity, version, title=title, prompt="",
                creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _resolve_attempt_staging_directory(self, supplied: str | Path) -> Path:
        raw = Path(supplied)
        candidate = raw if raw.is_absolute() else self.workspace / raw
        if any(part in {".", ".."} for part in candidate.parts[1:]):
            raise VideoProductError("attempt staging directory must not traverse directories")
        # Inspect the caller-supplied path before resolving it, then compare canonical
        # paths. This handles Windows 8.3 aliases without weakening symlink checks.
        supplied_cursor = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            supplied_cursor = supplied_cursor / part
            try:
                supplied_info = supplied_cursor.lstat()
            except FileNotFoundError as exc:
                raise VideoProductError("attempt staging directory must already exist") from exc
            if (supplied_cursor.is_symlink()
                    or bool(getattr(supplied_info, "st_file_attributes", 0) & 0x400)):
                raise VideoProductError("attempt staging path must not contain symlinks or reparse points")
        root = self.workspace.resolve(strict=True)
        try:
            resolved_candidate = candidate.resolve(strict=True)
            relative = resolved_candidate.relative_to(root)
        except (OSError, ValueError) as exc:
            raise VideoProductError("attempt staging directory must be inside the workspace") from exc
        parts = relative.parts
        if (len(parts) not in {3, 4} or parts[:2] != ("data", "video_attempt_staging")
                or not re.fullmatch(r"[0-9a-f]{32}", parts[2])
                or (len(parts) == 4 and not re.fullmatch(r"epoch_[1-9][0-9]{0,8}", parts[3]))):
            raise VideoProductError(
                "attempt staging directory must be data/video_attempt_staging/<attempt_id>[/epoch_<lease_epoch>]"
            )
        current = root
        for part in parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError as exc:
                raise VideoProductError("attempt staging directory must already exist") from exc
            if (current.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                    or not current.is_dir()):
                raise VideoProductError("attempt staging path must contain only regular directories")
        resolved = current.resolve(strict=True)
        if resolved != current or not resolved.is_relative_to(root):
            raise VideoProductError("attempt staging directory escapes the workspace")
        return resolved


    def _resolve_checkpoint_scene_artifact(self, relative_path: str) -> Path:
        if (not isinstance(relative_path, str) or not relative_path or len(relative_path) > 1024
                or "\\" in relative_path or ":" in relative_path):
            raise VideoProductError("resume artifact path must be a bounded workspace-relative POSIX path")
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise VideoProductError("resume artifact path must not be absolute or traverse directories")
        root = self.workspace.resolve(strict=True)
        current = root
        for part in relative.parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError as exc:
                raise VideoProductError("resume artifact is missing") from exc
            if current.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400):
                raise VideoProductError("resume artifact path must not contain symlinks or reparse points")
        resolved = current.resolve(strict=True)
        info = resolved.stat()
        if (not resolved.is_relative_to(root) or not resolved.is_file()
                or info.st_size <= 12 or info.st_size > _MAX_PRODUCT_BYTES):
            raise VideoProductError("resume artifact must be a regular bounded MP4 inside the workspace")
        with resolved.open("rb") as stream:
            if stream.read(12)[4:8] != b"ftyp":
                raise VideoProductError("resume artifact is not a valid MP4 container")
        return resolved

    def build_from_scenes(
        self, title: str, scenes: list[dict[str, Any]], *, negative_prompt: str = "",
        aspect_ratio: str = "16:9", steps: int = 8, timeout_seconds: int = 1800,
        motion_profile: str = "none", narration_script: str | None = None,
        narration_prosody_plan: Mapping[str, Any] | None = None,
        narration_model_dir: str | Path | None = None,
        narration_asr_model_dir: str | Path | None = None,
        narration_evidence_contract: dict[str, Any] | None = None,
        creator_initiative_id: str | None = None, evolution_cycle_id: str | None = None,
        recipe_sha256: str | None = None,
        staging_directory: str | Path | None = None,
        resume_scene_metadata: Mapping[str, Mapping[str, Any]] | None = None,
        reusable_step_ids: Sequence[str] = (),
        scene_checkpoint_callback: Callable[[str, Path, Mapping[str, Any]], Any] | None = None,
        scene_generation_guard: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        """Generate 2-20 independent local Wan shots, concatenate, then package once."""
        recipe_sha256 = _resolve_recipe_sha256(self.workspace, creator_initiative_id, recipe_sha256)
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise ValueError("title must contain 1-160 characters")
        if not isinstance(scenes, list) or not 2 <= len(scenes) <= 20:
            raise ValueError("scenes must contain between 2 and 20 scene specifications")
        if aspect_ratio not in {"16:9", "9:16", "1:1"}:
            raise ValueError("aspect_ratio must be 16:9, 9:16 or 1:1")
        if isinstance(steps, bool) or not isinstance(steps, int) or not 8 <= steps <= 30:
            raise ValueError("steps must be between 8 and 30")
        if motion_profile not in {"none", "brand_intro_outro"}:
            raise ValueError("motion_profile must be none or brand_intro_outro")
        if not isinstance(negative_prompt, str) or len(negative_prompt) > 1000:
            raise ValueError("negative_prompt must be at most 1000 characters")
        normalized: list[dict[str, Any]] = []
        planned_total = 0.0
        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict) or set(scene) - {"prompt", "duration_seconds", "seed", "negative_prompt"}:
                raise ValueError("each scene must contain only prompt, duration_seconds and optional seed")
            scene_prompt = scene.get("prompt")
            duration = scene.get("duration_seconds")
            seed = scene.get("seed")
            scene_negative_prompt = scene.get("negative_prompt", "")
            if not isinstance(scene_negative_prompt, str) or len(scene_negative_prompt) > 1000:
                raise ValueError(f"scene {index} negative_prompt must be at most 1000 characters")
            effective_negative_prompt = "\n".join(
                item for item in (negative_prompt, scene_negative_prompt) if item
            )
            if len(effective_negative_prompt) > 1000:
                raise ValueError(f"scene {index} combined negative_prompt exceeds 1000 characters")
            if not isinstance(scene_prompt, str) or not scene_prompt.strip() or len(scene_prompt.strip()) > 2000:
                raise ValueError(f"scene {index} prompt must contain 1-2000 characters")
            if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                    or not math.isfinite(duration) or not 1 <= duration <= 5):
                raise ValueError(f"scene {index} duration_seconds must be between 1 and 5")
            if seed is None:
                seed = secrets.randbelow(2**32)
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
                raise ValueError(f"scene {index} seed must be an unsigned 32-bit integer")
            planned_total += float(duration)
            normalized.append({
                "order": index, "prompt": scene_prompt.strip(),
                "duration_seconds": float(duration), "seed": seed,
                "negative_prompt": scene_negative_prompt,
                "effective_negative_prompt": effective_negative_prompt,
            })
        if planned_total > 60:
            raise ValueError("multi-scene preview duration must not exceed 60 seconds")
        if narration_script is not None:
            if not isinstance(narration_script, str) or not narration_script.strip() or len(narration_script) > 12000:
                raise ValueError("narration_script must contain 1-12000 characters")
            if narration_model_dir is None:
                raise ValueError("narration_model_dir is required when narration_script is provided")
            from muse.cantonese_tts import preflight_cantonese_tts_runtime
            preflight_cantonese_tts_runtime(self.workspace / ".muse-canto-tts-py314", narration_model_dir)
            if narration_asr_model_dir is not None and sys.version_info[:2] != (3, 14):
                raise VideoProductError("Cantonese ASR currently requires the Python 3.14 main runtime")
            if narration_prosody_plan is not None:
                _compile_cantonese_narration_payload(narration_script, narration_prosody_plan)
        elif narration_model_dir is not None or narration_asr_model_dir is not None:
            raise ValueError("narration model paths require narration_script")
        elif narration_prosody_plan is not None:
            raise ValueError("narration_prosody_plan requires narration_script")

        if narration_evidence_contract is not None:
            if narration_script is None:
                raise ValueError("narration_evidence_contract requires narration_script")
            _revalidate_claim_grounded_contract(self.workspace, narration_evidence_contract)

        if resume_scene_metadata is None:
            resume_map: dict[str, Mapping[str, Any]] = {}
        elif isinstance(resume_scene_metadata, Mapping):
            resume_map = dict(resume_scene_metadata)
        else:
            raise ValueError("resume_scene_metadata must be a mapping")
        if (isinstance(reusable_step_ids, (str, bytes))
                or not isinstance(reusable_step_ids, Sequence)):
            raise ValueError("reusable_step_ids must be a sequence of unique scene step IDs")
        reusable_ids = tuple(reusable_step_ids)
        if (any(not isinstance(item, str) or not re.fullmatch(r"shot_[0-9]{3}", item) for item in reusable_ids)
                or len(reusable_ids) != len(set(reusable_ids))
                or any(not isinstance(key, str) for key in resume_map)
                or set(resume_map) != set(reusable_ids)):
            raise ValueError("reusable_step_ids must exactly match resume_scene_metadata keys")
        expected_scene_ids = {f"shot_{index:03d}" for index in range(1, len(normalized) + 1)}
        if not set(reusable_ids) <= expected_scene_ids:
            raise ValueError("reusable_step_ids contain a scene outside the current request")
        if scene_generation_guard is not None and not callable(scene_generation_guard):
            raise ValueError("scene_generation_guard must be callable")
        checkpoint_mode = bool(resume_map or reusable_ids or scene_checkpoint_callback is not None)
        if checkpoint_mode:
            if staging_directory is None or not callable(scene_checkpoint_callback):
                raise ValueError("checkpoint mode requires an attempt staging directory and scene callback")
            if narration_script is not None:
                raise VideoProductError(
                    "scene checkpoint resume is not enabled with narration because TTS dynamically reallocates scene durations"
                )
        if staging_directory is None:
            staging_context = tempfile.TemporaryDirectory(prefix=".muse-wan-scenes-", dir=self.workspace)
        else:
            staging_context = nullcontext(self._resolve_attempt_staging_directory(staging_directory))

        inventory = discover_media_tools()
        tools = {item.tool_id: item.executable for item in inventory.tools}
        ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
        if not ffmpeg or not ffprobe:
            raise VideoProductError("FFmpeg and FFprobe are required for multi-scene generation")
        from muse.wan_video import generate_wan21_t2v

        with staging_context as temporary_dir:
            staging = Path(temporary_dir)
            effective_scenes = normalized
            narration_synthesis: dict[str, Any] | None = None
            narration_prosody_record: dict[str, Any] | None = None
            narration_asr: dict[str, Any] | None = None
            subtitle_path: Path | None = None
            narration_audio: Path | None = None
            if narration_script is not None:
                narration_audio = staging / "voiceover.wav"
                if narration_prosody_plan is not None:
                    from muse.cantonese_tts import synthesize_cantonese_segment_payload_with_runtime
                    payload = _compile_cantonese_narration_payload(narration_script, narration_prosody_plan)
                    narration_synthesis = synthesize_cantonese_segment_payload_with_runtime(
                        payload, narration_audio, model_dir=narration_model_dir,
                        packages_dir=self.workspace / ".muse-canto-tts-py314", quality="duration_filter",
                    )
                    narration_prosody_record = _build_cantonese_narration_prosody_record(
                        narration_script, narration_prosody_plan, narration_synthesis,
                    )
                else:
                    from muse.cantonese_tts import synthesize_cantonese_segments_with_runtime
                    narration_synthesis = synthesize_cantonese_segments_with_runtime(
                        narration_script, narration_audio, model_dir=narration_model_dir,
                        packages_dir=self.workspace / ".muse-canto-tts-py314", quality="duration_filter",
                    )
                if narration_synthesis.get("status") != "completed" or not narration_audio.is_file():
                    raise VideoProductError("Cantonese TTS did not produce a validated WAV; Wan generation was not started")
                narration_duration = _probe_media_duration_seconds(narration_audio, ffprobe, stream_type="audio")
                effective_scenes = _allocate_wan_scene_durations(normalized, narration_duration)
                if narration_asr_model_dir is not None:
                    from muse.cantonese_asr import transcribe_cantonese
                    subtitle_path = staging / "subtitles.srt"
                    narration_asr = transcribe_cantonese(
                        narration_audio, model_dir=narration_asr_model_dir, output_srt=subtitle_path,
                        reference_text=narration_script,
                    )
                    if narration_asr.get("status") != "passed":
                        comparison = narration_asr.get("comparison") or {}
                        raise VideoProductError(
                            f"Cantonese ASR CER gate failed (status={narration_asr.get('status')}, "
                            f"cer={comparison.get('cer')}); refusing to start Wan generation"
                        )
            planned_effective_total = sum(float(item["duration_seconds"]) for item in effective_scenes)
            clip_paths: list[Path] = []
            scene_records: list[dict[str, Any]] = []
            first_metadata: dict[str, Any] | None = None
            stream_signature: tuple[int, int, float] | None = None
            actual_total = 0.0
            total_frames = 0
            for scene in effective_scenes:
                index = scene["order"]
                step_id = f"shot_{index:03d}"
                clip_path = staging / f"shot_{index:02d}.mp4"
                reused = step_id in reusable_ids
                if reused:
                    entry = resume_map.get(step_id)
                    if not isinstance(entry, Mapping):
                        raise VideoProductError(f"resume metadata for {step_id} must be an object")
                    artifact_relative_path = entry.get("artifact_relative_path")
                    generation = entry.get("generation")
                    if not isinstance(generation, Mapping) or generation.get("status") != "completed":
                        raise VideoProductError(f"resume metadata for {step_id} lacks completed generation metadata")
                    source_clip = self._resolve_checkpoint_scene_artifact(artifact_relative_path)
                    attempt_staging_root = staging.parent if re.fullmatch(r"epoch_[1-9][0-9]{0,8}", staging.name) else staging
                    try:
                        source_clip.relative_to(attempt_staging_root)
                    except ValueError as exc:
                        raise VideoProductError(f"resume artifact for {step_id} is outside this Attempt staging directory") from exc
                    result = dict(generation)
                    clip_path = source_clip
                    generation_status = "reused"
                else:
                    if clip_path.exists() or clip_path.is_symlink():
                        info = clip_path.lstat()
                        if (clip_path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                                or not clip_path.is_file()):
                            raise VideoProductError(f"uncheckpointed scene output {index} is not a regular file")
                        # An output without a persisted checkpoint is untrusted after a crash.
                        clip_path.unlink()
                    wan_options: dict[str, Any] = {}
                    if scene_generation_guard is not None:
                        wan_options["admission_guard"] = (
                            lambda lease_context, current_step=step_id, current_scene=scene:
                            scene_generation_guard(current_step, current_scene, lease_context)
                        )
                    result = generate_wan21_t2v(
                        scene["prompt"], clip_path, negative_prompt=scene["effective_negative_prompt"],
                        aspect_ratio=aspect_ratio, duration_seconds=scene["duration_seconds"],
                        steps=steps, seed=scene["seed"], timeout_seconds=timeout_seconds,
                        resource_db=self.workspace / "data" / "gpu-resources.sqlite3",
                        workspace=self.workspace, **wan_options,
                    )
                    generation_status = "generated"
                if not isinstance(result, Mapping) or result.get("status") != "completed" or not clip_path.is_file():
                    raise VideoProductError(f"Wan scene {index} did not produce a completed MP4; refusing partial delivery")
                clip_info = clip_path.lstat()
                if (clip_path.is_symlink() or bool(getattr(clip_info, "st_file_attributes", 0) & 0x400)
                        or not clip_path.is_file() or clip_info.st_size <= 12
                        or clip_info.st_size > _MAX_PRODUCT_BYTES):
                    raise VideoProductError(f"Wan scene {index} output is not a regular bounded MP4")
                with clip_path.open("rb") as stream:
                    if stream.read(12)[4:8] != b"ftyp":
                        raise VideoProductError(f"Wan scene {index} output is not an MP4 container")
                raw_fps = result.get("fps")
                if isinstance(raw_fps, bool) or not isinstance(raw_fps, (int, float)) or not math.isfinite(raw_fps):
                    raise VideoProductError(f"Wan scene {index} returned invalid frame rate")
                signature = (result.get("width"), result.get("height"), float(raw_fps))
                if (isinstance(signature[0], bool) or not isinstance(signature[0], int)
                        or isinstance(signature[1], bool) or not isinstance(signature[1], int)
                        or signature[0] <= 0 or signature[1] <= 0 or signature[2] <= 0):
                    raise VideoProductError(f"Wan scene {index} returned incomplete video stream metadata")
                if stream_signature is None:
                    stream_signature = signature
                    first_metadata = dict(result)
                elif signature != stream_signature:
                    raise VideoProductError("Wan scenes have mismatched dimensions or frame rates; refusing unsafe stream-copy concatenation")
                measured_duration = _probe_media_duration_seconds(clip_path, ffprobe, stream_type="video")
                actual_total += measured_duration
                frames = result.get("frames")
                if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0:
                    raise VideoProductError(f"Wan scene {index} returned invalid frame count")
                if checkpoint_mode:
                    probed_width, probed_height, probed_fps, probed_frames = _probe_video_stream_metadata(clip_path, ffprobe)
                    if ((probed_width, probed_height, probed_frames) != (signature[0], signature[1], frames)
                            or abs(probed_fps - signature[2]) > max(0.001, signature[2] * 0.0001)):
                        raise VideoProductError(
                            f"Wan scene {index} FFprobe stream metadata does not match generation metadata"
                        )
                    signature = (probed_width, probed_height, probed_fps)
                    frames = probed_frames
                total_frames += frames
                if not reused and checkpoint_mode:
                    generation_metadata = {
                        "status": "completed",
                        "prompt_id": result.get("prompt_id"),
                        "model": result.get("model", "Wan 2.1 T2V 1.3B"),
                        "width": signature[0], "height": signature[1],
                        "fps": signature[2], "frames": frames,
                    }
                    scene_checkpoint_callback(step_id, clip_path, generation_metadata)
                clip_paths.append(clip_path)
                scene_records.append({
                    "order": index, "prompt": scene["prompt"], "prompt_id": result.get("prompt_id"),
                    "negative_prompt": scene["effective_negative_prompt"],
                    "seed": scene["seed"], "generation_status": generation_status,
                    "source_file": clip_path.name, "duration_seconds": measured_duration,
                    "frames": frames, "width": signature[0], "height": signature[1], "fps": signature[2],
                })

            concat_list = staging / "concat.txt"
            concat_lines = []
            for clip in clip_paths:
                safe_path = clip.resolve().as_posix().replace("'", "'\\''")
                concat_lines.append("file '" + safe_path + "'")
            concat_list.write_text(chr(10).join(concat_lines) + chr(10), encoding="utf-8")
            combined = staging / "combined.mp4"
            mux = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", "-movflags", "+faststart", str(combined)],
                capture_output=True, text=True, timeout=max(900, timeout_seconds * len(clip_paths)), check=False,
            )
            if mux.returncode != 0 or not combined.is_file() or combined.stat().st_size <= 12:
                raise VideoProductError("Wan multi-scene concatenation failed: " + str((mux.stderr or "")[-1000:]))
            with combined.open("rb") as stream:
                if stream.read(12)[4:8] != b"ftyp":
                    raise VideoProductError("multi-scene concatenation did not produce an MP4")
            combined_duration = _probe_media_duration_seconds(combined, ffprobe, stream_type="video")
            if abs(combined_duration - actual_total) > max(0.25, 1.0 / (stream_signature[2] if stream_signature else 12.0)):
                raise VideoProductError("concatenated video duration does not match the sum of generated Wan scenes")
            if narration_audio is not None:
                narration_duration = _probe_media_duration_seconds(narration_audio, ffprobe, stream_type="audio")
                _validate_narration_timeline(combined_duration, narration_duration, tolerance_seconds=0.0)
            aggregate = {
                "status": "completed", "prompt_id": (first_metadata or {}).get("prompt_id"),
                "model": "Wan 2.1 T2V 1.3B", "seed": effective_scenes[0]["seed"],
                "aspect_ratio": aspect_ratio, "fps": stream_signature[2],
                "duration_seconds": combined_duration, "frames": total_frames,
                "width": stream_signature[0], "height": stream_signature[1],
                "steps": steps, "bytes": combined.stat().st_size, "has_audio": False,
                "scene_count": len(scene_records),
            }
            if recipe_sha256 is not None:
                aggregate["recipe_sha256"] = recipe_sha256
            combined_prompt = ("多镜头分镜：" + "；".join(item["prompt"] for item in effective_scenes))[:2000]
            package_args = {
                "scene_asset_paths": clip_paths,
                "negative_prompt": negative_prompt, "generation_metadata": aggregate,
                "scene_records": scene_records, "motion_profile": motion_profile,
                "creator_initiative_id": creator_initiative_id, "evolution_cycle_id": evolution_cycle_id,
                "narration_evidence_contract": narration_evidence_contract,
                "narration_prosody": narration_prosody_record,
            }
            if narration_audio is not None:
                packaged = self.package_generated_clip(
                    title.strip(), combined_prompt, combined,
                    narration_path=narration_audio, narration_script=narration_script,
                    subtitle_path=subtitle_path, narration_asr=narration_asr, **package_args,
                )
                packaged["narration_synthesis"] = {
                    key: narration_synthesis[key] for key in (
                        "status", "engine", "model_repo", "model_revision", "license",
                        "language", "sample_rate", "channels", "frames", "duration_seconds", "bytes",
                        "segment_count", "inserted_pause_frames", "inserted_pause_seconds",
                        "audio_sha256", "applied_controls", "backend_capabilities", "unsupported_controls",
                    ) if key in narration_synthesis
                }
                if narration_prosody_record is not None:
                    packaged["narration_prosody"] = narration_prosody_record
                if narration_asr is not None:
                    packaged["narration_asr"] = narration_asr
            else:
                packaged = self.package_generated_clip(title.strip(), combined_prompt, combined, **package_args)
            packaged["scene_generation"] = {
                "status": "completed", "scene_count": len(scene_records),
                "requested_duration_seconds": planned_total,
                "planned_duration_seconds": planned_effective_total,
                "generated_duration_seconds": combined_duration,
                "duration_planning": "cantonese_narration_frame_quantized" if narration_audio is not None else "caller_requested",
                "scenes": scene_records,
                "concatenation": "ffmpeg_concat_demuxer_stream_copy",
            }
            return packaged

    def build_from_prompt(
        self, title: str, prompt: str, *, negative_prompt: str = "",
        aspect_ratio: str = "16:9", duration_seconds: float = 2.0,
        steps: int = 8, seed: int | None = None, timeout_seconds: int = 1800,
        motion_profile: str = "none",
        creator_initiative_id: str | None = None, evolution_cycle_id: str | None = None,
        narration_script: str | None = None,
        narration_prosody_plan: Mapping[str, Any] | None = None,
        narration_model_dir: str | Path | None = None,
        narration_asr_model_dir: str | Path | None = None,
        narration_evidence_contract: dict[str, Any] | None = None,
        recipe_sha256: str | None = None,
        generation_guard: Callable[[Any], Any] | None = None,
    ) -> dict[str, Any]:
        """Generate one local Wan 2.1 clip and optionally package verified Cantonese narration."""
        recipe_sha256 = _resolve_recipe_sha256(self.workspace, creator_initiative_id, recipe_sha256)
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise ValueError("title must contain 1-160 characters")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 2000:
            raise ValueError("prompt must contain 1-2000 characters")
        if not isinstance(negative_prompt, str) or len(negative_prompt) > 1000:
            raise ValueError("negative_prompt must be at most 1000 characters")
        if aspect_ratio not in {"16:9", "9:16", "1:1"}:
            raise ValueError("aspect_ratio must be 16:9, 9:16 or 1:1")
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or not math.isfinite(duration_seconds) or not 1 <= duration_seconds <= 5:
            raise ValueError("duration_seconds must be between 1 and 5")
        if isinstance(steps, bool) or not isinstance(steps, int) or not 8 <= steps <= 30:
            raise ValueError("steps must be between 8 and 30")
        if seed is None:
            seed = secrets.randbelow(2**32)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
            raise ValueError("seed must be an unsigned 32-bit integer")
        if motion_profile not in {"none", "brand_intro_outro"}:
            raise ValueError("motion_profile must be none or brand_intro_outro")
        if narration_script is not None and (
            not isinstance(narration_script, str) or not narration_script.strip() or len(narration_script) > 20_000
        ):
            raise ValueError("narration_script must contain 1-20000 characters")
        if narration_script is not None and narration_model_dir is None:
            raise ValueError("narration_model_dir is required when narration_script is provided")
        if narration_prosody_plan is not None:
            if narration_script is None:
                raise ValueError("narration_prosody_plan requires narration_script")
            _compile_cantonese_narration_payload(narration_script, narration_prosody_plan)

        if narration_script is None and (narration_model_dir is not None or narration_asr_model_dir is not None):
            raise ValueError("narration model paths require narration_script")
        if narration_evidence_contract is not None and narration_script is None:
            raise ValueError("narration_evidence_contract requires narration_script")
        if narration_evidence_contract is not None:
            _revalidate_claim_grounded_contract(self.workspace, narration_evidence_contract)
        if generation_guard is not None and not callable(generation_guard):
            raise ValueError("generation_guard must be callable")
        from muse.wan_video import generate_wan21_t2v
        settings = {
            "model": "Wan 2.1 T2V 1.3B", "aspect_ratio": aspect_ratio,
            "duration_seconds": float(duration_seconds), "steps": steps, "seed": seed,
            "fps": 12, "resolution_tier": "preview_512", "negative_prompt": negative_prompt,
            "vae_decode_mode": "tiled_temporal_8", "motion_profile": motion_profile,
        }
        identity = hashlib.sha256(("muse-video-ai:" + title.strip() + ":" + prompt.strip()).encode("utf-8")).hexdigest()[:32]
        fingerprint_data = {
            "builder_version": self.BUILDER_VERSION, "prompt": prompt.strip(),
            "settings": settings, "motion_profile": motion_profile,
            "narration_script": narration_script,
            "narration_prosody_plan": narration_prosody_plan,
        }
        if narration_evidence_contract is not None:
            fingerprint_data["narration_evidence_contract"] = narration_evidence_contract
        if recipe_sha256 is not None:
            fingerprint_data["recipe_sha256"] = recipe_sha256
        fingerprint = hashlib.sha256(_safe_json(fingerprint_data)).hexdigest()
        version = fingerprint[:20]
        _, products_root = self._roots()
        initiative_root = products_root / identity
        target = initiative_root / version
        for path in (initiative_root, target):
            if path.is_symlink():
                raise VideoProductError("AI video product destination must not contain symlinks")
        if target.exists():
            result = self.verify_path(products_root, identity, version)
            return self._record_creator_artifact(result, title=title, prompt=prompt, creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id)
        if narration_script is not None:
            from muse.cantonese_tts import preflight_cantonese_tts_runtime
            preflight_cantonese_tts_runtime(self.workspace / ".muse-canto-tts-py314", narration_model_dir)
            if narration_asr_model_dir is not None and sys.version_info[:2] != (3, 14):
                raise VideoProductError("Cantonese ASR currently requires the Python 3.14 main runtime")
        initiative_root.mkdir(parents=True, exist_ok=True)
        if initiative_root.is_symlink() or initiative_root.resolve().parent != products_root:
            raise VideoProductError("AI video product destination escapes product root")
        staging = Path(tempfile.mkdtemp(prefix=".wan-video-product-", dir=initiative_root))
        try:
            base_rendered = staging / "base-video.mp4"
            rendered = staging / "video.mp4"
            wan_options: dict[str, Any] = {}
            if generation_guard is not None:
                wan_options["admission_guard"] = generation_guard
            generated = generate_wan21_t2v(
                prompt.strip(), base_rendered, negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio, duration_seconds=float(duration_seconds),
                steps=steps, seed=seed, timeout_seconds=timeout_seconds,
                resource_db=self.workspace / "data" / "gpu-resources.sqlite3",
                workspace=self.workspace, **wan_options,
            )
            if generated.get("status") != "completed" or not base_rendered.is_file():
                raise VideoProductError("local Wan generation did not return a completed MP4")
            if recipe_sha256 is not None:
                generated["recipe_sha256"] = recipe_sha256
            if narration_script is not None:
                return self.package_generated_clip_with_cantonese_narration(
                    title.strip(), prompt.strip(), base_rendered,
                    narration_script=narration_script, model_dir=narration_model_dir,
                    narration_prosody_plan=narration_prosody_plan,
                    asr_model_dir=narration_asr_model_dir,
                    negative_prompt=negative_prompt, generation_metadata=generated,
                    motion_profile=motion_profile, creator_initiative_id=creator_initiative_id,
                    evolution_cycle_id=evolution_cycle_id,
                    narration_evidence_contract=narration_evidence_contract,
                )
            inventory = discover_media_tools()
            tools = {item.tool_id: item.executable for item in inventory.tools}
            motion = self._apply_motion_profile(
                base_rendered, rendered, motion_profile=motion_profile, title=title.strip(),
                width=int(generated.get("width") or 0), height=int(generated.get("height") or 0),
                fps=int(generated.get("fps") or 0), ffmpeg_path=tools.get("ffmpeg", ""),
                ffprobe_path=tools.get("ffprobe", ""), timeout_seconds=timeout_seconds,
            )
            tools = {item.tool_id: item.executable for item in inventory.tools}
            qa_report = _inspect_product_video(
                rendered, ffprobe_path=tools.get("ffprobe"), ffmpeg_path=tools.get("ffmpeg"),
                expected_width=generated.get("width"), expected_height=generated.get("height"),
                expected_fps=generated.get("fps"), timeout_seconds=timeout_seconds,
            )
            quality_gate = _quality_gate_summary(qa_report)
            video_metadata = _finalize_video_metadata(generated, qa_report, rendered, motion=motion)
            if rendered.stat().st_size > _MAX_PRODUCT_BYTES:
                raise VideoProductError("generated MP4 exceeds the 2 GiB product limit")
            storyboard = {
                "schema_version": 1, "title": title.strip(), "production_mode": "local_ai_text_to_video",
                "final_output": {key: video_metadata[key] for key in ("duration_seconds", "frames", "width", "height", "fps", "has_audio", "bytes") if key in video_metadata},
                "ai_generation": "completed", "model": settings["model"],
                "prompt": prompt.strip(), "negative_prompt": negative_prompt,
                "settings": settings, "motion": motion, "scenes": [{
                    "order": 1, "prompt": prompt.strip(), "generation_status": "generated",
                    "prompt_id": generated.get("prompt_id"), "seed": seed,
                }],
            }
            readme = (
                f"# {title.strip()}\n\nMuse 本地 AI 视频产品。镜头由本机 ComfyUI + Wan 2.1 T2V 1.3B 生成，未调用云端视频 API。\n\n"
                f"- 模型：{settings['model']}\n- 画幅：{aspect_ratio}\n- 分辨率：{generated.get('width')}×{generated.get('height')}\n"
                f"- 帧率：{video_metadata.get('fps')} fps\n"
                f"- AI 原始镜头：约 {video_metadata.get('source_duration_seconds', generated.get('duration_seconds'))} 秒 / {video_metadata.get('source_frames', generated.get('frames'))} 帧\n"
                f"- Remotion 后成片：{video_metadata.get('duration_seconds')} 秒 / {video_metadata.get('frames')} 帧\n"
                f"- 采样步数：{steps}\n- Seed：{seed}\n- 成片：`video.mp4`\n\n"
                f"- Remotion 动效：{motion.get('profile')}；转场：{motion.get('transition', '无')}\n"
                f"- 技术质检：{quality_gate['status']}；产品状态仍为 testing。\n"
                "技术质检通过不代表内容、版权、披露或平台审核通过。\n"
                "本版本属于短片预览级原型；请人工检查运动连贯性、画面质量和内容后再发布。\n"
            ).encode("utf-8")
            (staging / "README.md").write_bytes(readme)
            (staging / "storyboard.json").write_bytes(_safe_json(storyboard))
            (staging / "qa-report.json").write_bytes(_safe_json(qa_report))
            with zipfile.ZipFile(staging / "product.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(staging / "README.md", "README.md")
                archive.write(staging / "storyboard.json", "storyboard.json")
                archive.write(staging / "qa-report.json", "qa-report.json")
                archive.write(rendered, "video.mp4")
            files = {name: _hash_file(staging / name) for name in ("README.md", "storyboard.json", "qa-report.json", "video.mp4", "product.zip")}
            video_metadata["output"] = "video.mp4"
            video_metadata["motion"] = motion
            manifest = {
                "schema_version": 1, "builder_version": self.BUILDER_VERSION,
                "product_kind": "video_creator", "initiative_id": identity, "version_id": version,
                "title": title.strip(), "status": "testing", "generated_at": datetime.now(timezone.utc).isoformat(),
                "input_sha256": fingerprint, "files": files, "video_metadata": video_metadata,
                "quality_gate": quality_gate,
                **({"recipe_provenance": {
                    "schema_version": 1,
                    "recipe_sha256": video_metadata["recipe_sha256"],
                    "relationship": "recipe_reference",
                }} if video_metadata.get("recipe_sha256") else {}),
                "source_assets": [], "audio_asset": None,
                "execution": {"arbitrary_code_executed": False, "network_access": False,
                              "local_tools_invoked": ["comfyui", "wan21"] + (["node", "remotion", "ffmpeg"] if motion_profile != "none" else []), "ai_generation": "completed"},
            }
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return self._publish_staging(
                staging, products_root, identity, version, title=title, prompt=prompt,
                creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def package_generated_clip(
        self, title: str, prompt: str, source_path: str | Path, *,
        negative_prompt: str = "", generation_metadata: dict[str, Any],
        scene_records: list[dict[str, Any]] | None = None,
        scene_asset_paths: list[str | Path] | None = None,
        narration_path: str | Path | None = None, narration_script: str | None = None,
        subtitle_path: str | Path | None = None, narration_asr: dict[str, Any] | None = None,
        narration_evidence_contract: dict[str, Any] | None = None,
        narration_prosody: dict[str, Any] | None = None,
        background_music_path: str | Path | None = None, motion_profile: str = "none",
        creator_initiative_id: str | None = None, evolution_cycle_id: str | None = None,
    ) -> dict[str, Any]:
        """Package a completed local AI MP4, optionally enforcing claim-grounded narration."""
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise ValueError("title must contain 1-160 characters")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 2000:
            raise ValueError("prompt must contain 1-2000 characters")
        if not isinstance(negative_prompt, str) or len(negative_prompt) > 1000:
            raise ValueError("negative_prompt must be at most 1000 characters")
        if not isinstance(generation_metadata, dict) or generation_metadata.get("status") != "completed":
            raise ValueError("generation_metadata must describe a completed AI render")
        if motion_profile not in {"none", "brand_intro_outro"}:
            raise ValueError("motion_profile must be none or brand_intro_outro")

        if scene_records is None:
            normalized_scenes = [{
                "order": 1, "prompt": prompt.strip(), "generation_status": "generated",
                "prompt_id": generation_metadata.get("prompt_id"), "seed": generation_metadata.get("seed"),
            }]
        else:
            if not isinstance(scene_records, list) or not 1 <= len(scene_records) <= 20:
                raise ValueError("scene_records must contain between 1 and 20 scenes")
            normalized_scenes = []
            for index, scene in enumerate(scene_records, start=1):
                if not isinstance(scene, dict) or set(scene) - {
                    "order", "prompt", "prompt_id", "seed", "generation_status", "source_file",
                    "duration_seconds", "frames", "width", "height", "fps",
                }:
                    raise ValueError("scene_records contains an invalid scene shape")
                order = scene.get("order", index)
                scene_prompt = scene.get("prompt")
                if (isinstance(order, bool) or not isinstance(order, int) or order != index
                        or not isinstance(scene_prompt, str) or not scene_prompt.strip() or len(scene_prompt) > 2000
                        or scene.get("generation_status", "generated") != "generated"):
                    raise ValueError("scene_records contains an invalid order, prompt or generation status")
                prompt_id, seed, source_file = scene.get("prompt_id"), scene.get("seed"), scene.get("source_file")
                if prompt_id is not None and (not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 128):
                    raise ValueError("scene_records contains an invalid prompt_id")
                if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1):
                    raise ValueError("scene_records contains an invalid seed")
                if source_file is not None and (not isinstance(source_file, str) or not source_file or Path(source_file).name != source_file):
                    raise ValueError("scene_records contains an invalid source_file")
                details = {key: scene[key] for key in ("duration_seconds", "frames", "width", "height", "fps") if key in scene}
                if "duration_seconds" in details and (
                    isinstance(details["duration_seconds"], bool) or not isinstance(details["duration_seconds"], (int, float))
                    or not math.isfinite(details["duration_seconds"]) or details["duration_seconds"] <= 0
                ):
                    raise ValueError("scene_records contains an invalid duration_seconds")
                for key in ("frames", "width", "height"):
                    if key in details and (isinstance(details[key], bool) or not isinstance(details[key], int) or details[key] <= 0):
                        raise ValueError(f"scene_records contains invalid {key}")
                if "fps" in details and (isinstance(details["fps"], bool) or not isinstance(details["fps"], (int, float)) or not math.isfinite(details["fps"]) or details["fps"] <= 0):
                    raise ValueError("scene_records contains invalid fps")
                normalized_scenes.append({
                    "order": order, "prompt": scene_prompt.strip(), "generation_status": "generated",
                    "prompt_id": prompt_id, "seed": seed,
                    **({"source_file": source_file} if source_file else {}), **details,
                })
            if len(_safe_json(normalized_scenes)) > 32 * 1024:
                raise ValueError("scene_records exceeds 32 KiB")
        scene_asset_sources: list[Path] = []
        scene_asset_facts: list[dict[str, Any]] = []
        if scene_asset_paths is not None:
            if not isinstance(scene_asset_paths, list) or len(scene_asset_paths) != len(normalized_scenes):
                raise ValueError("scene_asset_paths must match the number of normalized scenes")
            scene_asset_total = 0
            seen_scene_names: set[str] = set()
            for index, raw_path in enumerate(scene_asset_paths, start=1):
                path = Path(raw_path).expanduser()
                if (path.suffix.casefold() != ".mp4" or any(parent.is_symlink() for parent in (path, *path.parents))
                        or not path.is_file()):
                    raise VideoProductError(f"scene asset {index} must be a regular MP4 without symlink parents")
                path = path.resolve(strict=True)
                size = path.stat().st_size
                if size < 12:
                    raise VideoProductError(f"scene asset {index} is empty or invalid")
                scene_asset_total += size
                if scene_asset_total > _MAX_PRODUCT_BYTES:
                    raise VideoProductError("combined source scene assets exceed the 2 GiB product limit")
                if path.name in seen_scene_names:
                    raise VideoProductError("scene asset filenames must be unique")
                seen_scene_names.add(path.name)
                if normalized_scenes[index - 1].get("source_file") not in {None, path.name}:
                    raise VideoProductError("scene asset filename does not match its storyboard source_file")
                scene_asset_sources.append(path)
                scene_asset_facts.append({
                    "order": index, "name": path.name, "archive_path": f"scenes/{path.name}",
                    "bytes": size, "sha256": _hash_file(path),
                })

        source = Path(source_path).expanduser()
        if any(parent.is_symlink() for parent in (source, *source.parents)):
            raise VideoProductError("generated MP4 source must not contain symlinks")
        if source.suffix.casefold() != ".mp4" or not source.is_file():
            raise VideoProductError("generated clip source must be a regular MP4 file")
        source = source.resolve(strict=True)
        source_size = source.stat().st_size
        if scene_asset_sources and sum(fact["bytes"] for fact in scene_asset_facts) + source_size > _MAX_PRODUCT_BYTES:
            raise VideoProductError("combined scene sources and final MP4 exceed the 2 GiB product limit")
        if source_size < 12 or source_size > _MAX_PRODUCT_BYTES:
            raise VideoProductError("generated MP4 is empty, invalid or exceeds the 2 GiB product limit")
        with source.open("rb") as stream:
            header = stream.read(12)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise VideoProductError("generated clip source is not a valid MP4 container")
        source_sha256 = _hash_file(source)

        narration = Path(narration_path).expanduser() if narration_path is not None else None
        if narration_script is not None and (
            not isinstance(narration_script, str) or not narration_script.strip() or len(narration_script) > 20000
        ):
            raise ValueError("narration_script must contain 1-20000 characters when provided")
        if narration_evidence_contract is not None:
            contract = narration_evidence_contract
            if (not isinstance(contract, dict)
                    or contract.get("schema_version") != 1
                    or contract.get("contract") != "claim_grounded_narration"
                    or contract.get("publishable") is not True
                    or contract.get("status") != "ready"
                    or not isinstance(contract.get("segments"), list)
                    or not contract["segments"]):
                raise VideoProductError("claim-grounded narration evidence contract is blocked or invalid")
            if narration is None or narration_script is None:
                raise VideoProductError("claim-grounded narration requires matching narration audio and script")
            contract_text = []
            for segment in contract["segments"]:
                if (not isinstance(segment, dict) or not isinstance(segment.get("text"), str)
                        or not segment["text"].strip() or not isinstance(segment.get("factual"), bool)):
                    raise VideoProductError("claim-grounded narration contract contains an invalid segment")
                if segment["factual"]:
                    if (not isinstance(segment.get("claim_ids"), list) or not segment["claim_ids"]
                            or segment.get("gate_status") != "ready"):
                        raise VideoProductError("claim-grounded factual narration segment lacks verified Claim support")
                elif segment.get("gate_status") != "not_applicable":
                    raise VideoProductError("non-factual narration segment has an invalid gate state")
                contract_text.append("".join(segment["text"].split()))
            if "".join(contract_text) != "".join(narration_script.split()):
                raise VideoProductError("claim-grounded narration contract text does not match the packaged script")
            try:
                contract_bytes = _safe_json(contract)
            except (TypeError, ValueError):
                raise VideoProductError("claim-grounded narration contract is not valid bounded JSON") from None
            if len(contract_bytes) > 1024 * 1024:
                raise VideoProductError("claim-grounded narration contract exceeds 1 MiB")
            _revalidate_claim_grounded_contract(self.workspace, contract)
        if narration is not None:
            if (narration.suffix.casefold() not in AUDIO_EXTENSIONS
                    or any(parent.is_symlink() for parent in (narration, *narration.parents))
                    or not narration.is_file()):
                raise VideoProductError("narration_path must be a regular supported audio file without symlink parents")
            narration = narration.resolve(strict=True)
            if narration.stat().st_size <= 0 or narration.stat().st_size > _MAX_INPUT_BYTES:
                raise VideoProductError("narration audio is empty or exceeds the 8 GiB input limit")
        elif narration_script is not None:
            raise ValueError("narration_path is required when narration_script is provided")
        narration_fact = (
            {"name": narration.name, "bytes": narration.stat().st_size, "sha256": _hash_file(narration)}
            if narration else None
        )
        if narration_prosody is not None:
            runtime_prosody = narration_prosody.get("runtime") if isinstance(narration_prosody, dict) else None
            prosody_segments = narration_prosody.get("segments") if isinstance(narration_prosody, dict) else None
            pause_frames = runtime_prosody.get("inserted_pause_frames") if isinstance(runtime_prosody, dict) else None
            expected_applied = ["pause_after"] if isinstance(pause_frames, int) and not isinstance(pause_frames, bool) and pause_frames > 0 else []
            if (narration is None or narration_script is None
                    or narration_prosody.get("schema") != "muse.narration.prosody.runtime.v1"
                    or not isinstance(runtime_prosody, dict)
                    or runtime_prosody.get("audio_sha256") != narration_fact["sha256"]
                    or not isinstance(prosody_segments, list) or not 1 <= len(prosody_segments) <= 16
                    or runtime_prosody.get("segment_count") != len(prosody_segments)
                    or narration_prosody.get("backend_capabilities") != ["pause_after"]
                    or runtime_prosody.get("backend_capabilities") != ["pause_after"]
                    or runtime_prosody.get("applied_controls") != expected_applied
                    or not isinstance(pause_frames, int) or isinstance(pause_frames, bool) or pause_frames < 0
                    or narration_prosody.get("audio_effects_verified") is not False
                    or narration_prosody.get("waveform_pause_accounting_verified") is not True
                    or narration_prosody.get("waveform_pause_verified") is not (pause_frames > 0)
                    or narration_prosody.get("human_listening_review_required") is not True):
                raise VideoProductError("narration prosody evidence must bind the actual narration WAV and preserve review status")

            try:
                prosody_bytes = _safe_json(narration_prosody)
            except (TypeError, ValueError):
                raise VideoProductError("narration prosody evidence is not bounded JSON") from None
            if len(prosody_bytes) > 64 * 1024:
                raise VideoProductError("narration prosody evidence exceeds 64 KiB")

        background_music = Path(background_music_path).expanduser() if background_music_path is not None else None
        if background_music is not None:
            if narration is None:
                raise ValueError("background_music_path requires narration_path so the voice can drive ducking")
            if (background_music.suffix.casefold() not in AUDIO_EXTENSIONS
                    or any(parent.is_symlink() for parent in (background_music, *background_music.parents))
                    or not background_music.is_file()):
                raise VideoProductError("background_music_path must be a regular supported audio file without symlink parents")
            background_music = background_music.resolve(strict=True)
            if background_music.stat().st_size <= 0 or background_music.stat().st_size > _MAX_INPUT_BYTES:
                raise VideoProductError("background music is empty or exceeds the 8 GiB input limit")
        background_music_fact = (
            {"name": background_music.name, "bytes": background_music.stat().st_size,
             "sha256": _hash_file(background_music)} if background_music else None
        )
        background_music_member = (
            "music/background" + background_music.suffix.casefold() if background_music else None
        )
        subtitles = Path(subtitle_path).expanduser() if subtitle_path is not None else None
        if subtitles is not None:
            if (subtitles.suffix.casefold() != ".srt"
                    or any(parent.is_symlink() for parent in (subtitles, *subtitles.parents))
                    or not subtitles.is_file() or subtitles.stat().st_size <= 0
                    or subtitles.stat().st_size > 4 * 1024 * 1024):
                raise VideoProductError("subtitle_path must be a regular non-empty SRT file no larger than 4 MiB")
            subtitles = subtitles.resolve(strict=True)
        if subtitles is not None and narration is None:
            raise ValueError("narration_path is required when subtitle_path is provided")
        subtitle_fact = ({"name": "subtitles.srt", "bytes": subtitles.stat().st_size,
                          "sha256": _hash_file(subtitles)} if subtitles else None)
        asr_fact = None
        if narration_asr is not None:
            if subtitles is None or narration_fact is None or not isinstance(narration_asr, dict):
                raise ValueError("narration_asr requires narration audio and a subtitle file")
            from muse.cantonese_asr import MODEL_REPO, MODEL_REVISION
            comparison = narration_asr.get("comparison")
            asr_subtitles = narration_asr.get("subtitles")
            if (narration_asr.get("status") not in {"passed", "needs_review"}
                    or narration_asr.get("model_repo") != MODEL_REPO
                    or narration_asr.get("model_revision") != MODEL_REVISION
                    or narration_asr.get("audio_sha256") != narration_fact["sha256"]
                    or not isinstance(narration_asr.get("model_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", narration_asr["model_sha256"])
                    or not isinstance(narration_asr.get("transcript"), str)
                    or not isinstance(comparison, dict)
                    or comparison.get("status") != narration_asr.get("status")
                    or comparison.get("metric") != "character_error_rate"
                    or isinstance(comparison.get("cer"), bool)
                    or not isinstance(comparison.get("cer"), (int, float))
                    or not math.isfinite(comparison["cer"]) or not 0 <= comparison["cer"] <= 1
                    or not isinstance(asr_subtitles, dict)
                    or asr_subtitles.get("sha256") != subtitle_fact["sha256"]
                    or asr_subtitles.get("bytes") != subtitle_fact["bytes"]
                    or narration_asr.get("human_listening_review_required") is not True):
                raise ValueError("narration_asr provenance does not match the pinned model, audio and SRT assets")
            asr_fact = {key: narration_asr[key] for key in (
                "status", "engine", "model_repo", "model_revision", "model_sha256",
                "audio_sha256", "transcript", "segment_count", "subtitle_timing", "comparison",
                "human_listening_review_required",
            ) if key in narration_asr}
            if len(_safe_json(asr_fact)) > 64 * 1024:
                raise ValueError("narration_asr metadata exceeds 64 KiB")


        metadata_keys = {
            "status", "prompt_id", "model", "seed", "aspect_ratio", "fps",
            "duration_seconds", "frames", "width", "height", "steps", "bytes", "has_audio",
            "recipe_sha256",
        }
        metadata = {key: generation_metadata[key] for key in metadata_keys if key in generation_metadata}
        if "recipe_sha256" in metadata:
            metadata["recipe_sha256"] = _validate_recipe_sha256(metadata["recipe_sha256"])
        try:
            metadata_bytes = _safe_json(metadata)
        except (TypeError, ValueError):
            raise ValueError("generation_metadata must contain bounded JSON values") from None
        if len(metadata_bytes) > 16 * 1024:
            raise ValueError("generation_metadata exceeds 16 KiB")
        settings = {key: value for key, value in metadata.items() if key != "status"}
        identity = hashlib.sha256(("muse-video-ai:" + title.strip() + ":" + prompt.strip()).encode("utf-8")).hexdigest()[:32]
        fingerprint_data = {
            "builder_version": self.BUILDER_VERSION, "prompt": prompt.strip(),
            "negative_prompt": negative_prompt, "generation_metadata": metadata,
            "scene_records": normalized_scenes, "generated_clip_sha256": source_sha256,
            "narration_asset": narration_fact, "narration_script": narration_script,
            "narration_prosody": narration_prosody,

            "motion_profile": motion_profile, "subtitle_asset": subtitle_fact,
            "narration_asr": asr_fact, "background_music_asset": background_music_fact,
            "scene_assets": scene_asset_facts,
        }
        if narration_evidence_contract is not None:
            fingerprint_data["narration_evidence_contract"] = narration_evidence_contract
        fingerprint = hashlib.sha256(_safe_json(fingerprint_data)).hexdigest()
        version = fingerprint[:20]
        _, products_root = self._roots()
        initiative_root = products_root / identity
        target = initiative_root / version
        for path in (initiative_root, target):
            if path.is_symlink():
                raise VideoProductError("AI video product destination must not contain symlinks")
        if target.exists():
            result = self.verify_path(products_root, identity, version)
            return self._record_creator_artifact(
                result, title=title, prompt=prompt, creator_initiative_id=creator_initiative_id,
                evolution_cycle_id=evolution_cycle_id,
            )
        initiative_root.mkdir(parents=True, exist_ok=True)
        if initiative_root.is_symlink() or initiative_root.resolve().parent != products_root:
            raise VideoProductError("AI video product destination escapes product root")
        staging = Path(tempfile.mkdtemp(prefix=".wan-video-product-recovered-", dir=initiative_root))
        try:
            inventory = discover_media_tools()
            tools = {item.tool_id: item.executable for item in inventory.tools}
            ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
            narration_timeline = None
            scene_asset_copies: list[tuple[Path, str]] = []
            for path, fact in zip(scene_asset_sources, scene_asset_facts):
                copy_path = staging / fact["archive_path"]
                copy_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, copy_path)
                if _hash_file(path) != fact["sha256"] or _hash_file(copy_path) != fact["sha256"]:
                    raise VideoProductError("source scene MP4 changed while being packaged")
                scene_asset_copies.append((copy_path, fact["archive_path"]))
            background_music_mix = None
            if narration is not None:
                if not ffprobe:
                    raise VideoProductError("FFprobe is required to verify narration/video duration alignment")
                video_duration = _probe_media_duration_seconds(source, ffprobe, stream_type="video")
                narration_duration = _probe_media_duration_seconds(narration, ffprobe, stream_type="audio")
                narration_timeline = _validate_narration_timeline(video_duration, narration_duration)
                if background_music is not None:
                    from muse.video_audio_mix import build_bgm_envelope_expression, parse_srt_speech_windows
                    speech_windows = []
                    timing_source = "sidechain_only"
                    if subtitles is not None:
                        try:
                            srt_text = subtitles.read_text(encoding="utf-8-sig")
                            speech_windows = parse_srt_speech_windows(srt_text, video_duration)
                        except (OSError, UnicodeError, ValueError) as exc:
                            raise VideoProductError(
                                f"subtitle-timed BGM ducking could not validate SRT timing: {type(exc).__name__}"
                            ) from None
                        timing_source = "subtitle_srt"
                    fade_out_seconds = min(1.5, video_duration)
                    volume_expression = build_bgm_envelope_expression(
                        speech_windows, video_duration, base_gain=0.22, duck_gain=0.10,
                        attack_ms=80, release_ms=450, fade_out_seconds=fade_out_seconds,
                    )
                    background_music_mix = {
                        "method": "subtitle_timed_volume_plus_sidechaincompress" if subtitles is not None
                                  else "fade_out_volume_plus_sidechaincompress",
                        "speech_timing_source": timing_source,
                        "speech_window_count": len(speech_windows),
                        "base_gain": 0.22, "duck_gain": 0.10,
                        "speech_attack_ms": 80, "speech_release_ms": 450,
                        "fade_out_seconds": fade_out_seconds,
                        "volume_expression_sha256": hashlib.sha256(volume_expression.encode("ascii")).hexdigest(),
                        "sidechain_threshold": 0.025, "sidechain_ratio": 8,
                        "sidechain_attack_ms": 20, "sidechain_release_ms": 450,
                        "loudness_normalization": {"method": "ffmpeg_loudnorm", "target_lufs": -16,
                                                    "true_peak_db": -1.5, "lra": 11},
                    }
            base_rendered, rendered = staging / "base-video.mp4", staging / "video.mp4"
            shutil.copyfile(source, base_rendered)
            if _hash_file(source) != source_sha256 or _hash_file(base_rendered) != source_sha256:
                raise VideoProductError("generated MP4 changed while it was being packaged")
            if narration is not None:
                if not ffmpeg:
                    raise VideoProductError("FFmpeg is required to mux the narration track")
                narrated = staging / "narrated-video.mp4"
                mux_args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                            "-i", str(base_rendered), "-i", str(narration)]
                if background_music is not None:
                    mux_args.extend([
                        "-stream_loop", "-1", "-i", str(background_music),
                        "-filter_complex",
                        "[1:a]aresample=48000,apad,volume=2.0,asplit=2[voice_sc][voice_mix];"
                        f"[2:a]aresample=48000,volume='{volume_expression}':eval=frame[music];"
                        "[music][voice_sc]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=450[ducked];"
                        "[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=2[mixed];"
                        "[mixed]loudnorm=I=-16:TP=-1.5:LRA=11[aout]",
                        "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest",
                        "-movflags", "+faststart", str(narrated),
                    ])
                else:
                    mux_args.extend([
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", "-shortest",
                        "-movflags", "+faststart", str(narrated),
                    ])
                mux = subprocess.run(
                    mux_args, capture_output=True, text=True, timeout=900, check=False,
                )
                if mux.returncode != 0 or not narrated.is_file() or narrated.stat().st_size == 0:
                    raise VideoProductError("narration mux failed: " + str((mux.stderr or "")[-1000:]))
                base_rendered.unlink()
                narrated.replace(base_rendered)
                if _hash_file(narration) != narration_fact["sha256"]:
                    raise VideoProductError("narration audio changed during packaging")
                if background_music is not None and _hash_file(background_music) != background_music_fact["sha256"]:
                    raise VideoProductError("background music changed during packaging")
            width, height, fps = metadata.get("width"), metadata.get("height"), metadata.get("fps")
            if motion_profile != "none" and any(
                isinstance(value, bool) or not isinstance(value, int) for value in (width, height, fps)
            ):
                raise VideoProductError("motion packaging requires verified integer width, height and fps metadata")
            motion = self._apply_motion_profile(
                base_rendered, rendered, motion_profile=motion_profile, title=title.strip(),
                width=width or 0, height=height or 0, fps=fps or 0,
                ffmpeg_path=ffmpeg or "", ffprobe_path=ffprobe or "", timeout_seconds=900,
            )
            if _hash_file(source) != source_sha256:
                raise VideoProductError("generated source MP4 changed during motion packaging")
            qa_report = _inspect_product_video(
                rendered, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
                expected_width=metadata.get("width"), expected_height=metadata.get("height"),
                expected_fps=metadata.get("fps"),
                require_audio=(narration is not None or metadata.get("has_audio") is True),
            )
            quality_gate = _quality_gate_summary(qa_report)
            if narration_timeline is not None:
                qa_report["narration_timeline"] = narration_timeline
                qa_report.setdefault("checks", {})["narration_duration_coverage"] = {
                    "passed": True,
                    "detail": (f"narration={narration_timeline['narration_duration_seconds']:.3f}s; "
                               f"video={narration_timeline['video_duration_seconds']:.3f}s; "
                               f"tolerance={narration_timeline['tolerance_seconds']:.3f}s"),
                }
            video_metadata = _finalize_video_metadata(metadata, qa_report, rendered, motion=motion)
            storyboard = {
                "schema_version": 1, "title": title.strip(),
                "production_mode": "local_ai_text_to_video", "ai_generation": "completed",
                "final_output": {key: video_metadata[key] for key in (
                    "duration_seconds", "frames", "width", "height", "fps", "has_audio", "bytes"
                ) if key in video_metadata},
                "model": metadata.get("model", "local AI video model"),
                "prompt": prompt.strip(), "negative_prompt": negative_prompt,
                "settings": settings, "source_video_sha256": source_sha256, "motion": motion,
                "narration": ({
                    "script": narration_script, "audio": narration_fact, "language": "zh-HK",
                    **({"prosody": narration_prosody} if narration_prosody is not None else {}),
                    **({"timeline": narration_timeline} if narration_timeline is not None else {}),
                    "loudness_normalization": {"method": "ffmpeg_loudnorm", "target_lufs": -16, "true_peak_db": -1.5, "lra": 11},
                    **({"subtitles": subtitle_fact, "asr": asr_fact} if subtitles else {}),
                } if narration else None),
                **({"narration_evidence_contract": narration_evidence_contract}
                   if narration_evidence_contract is not None else {}),
                "background_music": ({
                    **background_music_fact, "archive_path": background_music_member,
                    "mix": {"method": "ffmpeg_sidechaincompress", "voice_gain": 2.0,
                            "music_gain": 0.22, "threshold": 0.025, "ratio": 8,
                            "attack_ms": 20, "release_ms": 450,
                            "loudness_normalization": {"method": "ffmpeg_loudnorm", "target_lufs": -16, "true_peak_db": -1.5, "lra": 11}},
                } if background_music_fact else None),
                **({"background_music_mix": background_music_mix} if background_music_mix else {}),
                "scenes": normalized_scenes,
                "scene_assets": scene_asset_facts,
            }
            readme = (
                f"# {title.strip()}\n\nMuse 本地 AI 视频产品。此包由已完成的本机生成 MP4 恢复封装。\n\n"
                f"- 模型：{metadata.get('model', '未记录')}\n- 画幅：{metadata.get('aspect_ratio', '未记录')}\n"
                f"- 分辨率：{metadata.get('width', '未知')}×{metadata.get('height', '未知')}\n"
                f"- 帧率：{video_metadata.get('fps', '未知')} fps\n"
                f"- 时长：{video_metadata.get('duration_seconds', '未知')} 秒\n"
                f"- 旁白：{'已加入音轨' if narration else '未提供音轨'}；字幕：{'已加入' if subtitles else '未提供'}。\n"
                "技术质检通过不代表内容、事实、版权/肖像、披露或平台审核通过。\n"
            ).encode("utf-8")
            (staging / "README.md").write_bytes(readme)
            (staging / "storyboard.json").write_bytes(_safe_json(storyboard))
            (staging / "qa-report.json").write_bytes(_safe_json(qa_report))
            with zipfile.ZipFile(staging / "product.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(staging / "README.md", "README.md")
                archive.write(staging / "storyboard.json", "storyboard.json")
                archive.write(staging / "qa-report.json", "qa-report.json")
                archive.write(rendered, "video.mp4")
                for copy_path, archive_path in scene_asset_copies:
                    archive.write(copy_path, archive_path)
                if narration is not None:
                    archive.write(narration, "narration/voiceover" + narration.suffix.casefold())
                    if narration_script:
                        script_path = staging / "narration-script.txt"
                        script_path.write_text(narration_script, encoding="utf-8")
                        archive.write(script_path, "narration/script.txt")
                if background_music is not None and background_music_member is not None:
                    archive.write(background_music, background_music_member)
                if subtitles is not None:
                    archive.write(subtitles, "subtitles.srt")
            file_names = ["README.md", "storyboard.json", "qa-report.json", "video.mp4", "product.zip"]

            if subtitles is not None:
                shutil.copyfile(subtitles, staging / "subtitles.srt")
                if _hash_file(staging / "subtitles.srt") != subtitle_fact["sha256"]:
                    raise VideoProductError("subtitle SRT changed while being packaged")
                file_names.append("subtitles.srt")
            files = {name: _hash_file(staging / name) for name in file_names}
            video_metadata["output"] = "video.mp4"
            video_metadata["motion"] = motion
            manifest = {
                "schema_version": 1, "builder_version": self.BUILDER_VERSION,
                "product_kind": "video_creator", "initiative_id": identity, "version_id": version,
                "title": title.strip(), "status": "testing", "generated_at": datetime.now(timezone.utc).isoformat(),
                "input_sha256": fingerprint, "files": files, "video_metadata": video_metadata,
                "quality_gate": quality_gate,
                "generated_clip": {"name": source.name, "bytes": source_size, "sha256": source_sha256},
                **({"recipe_provenance": {
                    "schema_version": 1,
                    "recipe_sha256": metadata["recipe_sha256"],
                    "relationship": "recipe_reference",
                }} if metadata.get("recipe_sha256") else {}),
                "scene_assets": scene_asset_facts,
                "subtitles": subtitle_fact, "narration_asr": asr_fact,
                "narration_timeline": narration_timeline,
                "background_music": ({
                    **background_music_fact, "archive_path": background_music_member,
                    "mix": {"method": "ffmpeg_sidechaincompress", "voice_gain": 2.0,
                            "music_gain": 0.22, "threshold": 0.025, "ratio": 8,
                            "attack_ms": 20, "release_ms": 450,
                            "loudness_normalization": {"method": "ffmpeg_loudnorm", "target_lufs": -16, "true_peak_db": -1.5, "lra": 11}},
                } if background_music_fact else None),
                "source_assets": [], "audio_asset": narration_fact,
                "narration": ({
                    "script": narration_script, "language": "zh-HK",
                    "loudness_normalization": {"method": "ffmpeg_loudnorm", "target_lufs": -16, "true_peak_db": -1.5, "lra": 11},
                    "audio_file": "narration/voiceover" + narration.suffix.casefold(),
                    **({"prosody": narration_prosody} if narration_prosody is not None else {}),
                    **({"subtitles_file": "subtitles.srt"} if subtitles else {}),
                    **({"asr_status": asr_fact["status"]} if asr_fact else {}),
                    **({"background_music_file": background_music_member} if background_music_member else {}),
                } if narration else None),
                **({"narration_evidence_contract": narration_evidence_contract}
                   if narration_evidence_contract is not None else {}),
                **({"background_music_mix": background_music_mix} if background_music_mix else {}),
                "execution": {
                    "local_tools_invoked": ["comfyui", "wan21"] + (["ffmpeg"] if narration else [])
                    + (["node", "remotion", "ffmpeg"] if motion_profile != "none" else []),
                    "ai_generation": "completed",
                },
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return self._publish_staging(
                staging, products_root, identity, version, title=title, prompt=prompt,
                creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)


    def package_generated_clip_with_cantonese_narration(
        self, title: str, prompt: str, source_path: str | Path, *,
        narration_script: str, model_dir: str | Path,
        narration_prosody_plan: Mapping[str, Any] | None = None,
        asr_model_dir: str | Path | None = None, **package_options: Any,
    ) -> dict[str, Any]:
        """Synthesize Cantonese narration and optionally verify it with local ASR subtitles.

        Structured plans preserve segment boundaries through the TTS runtime.
        Legacy text-only callers remain supported, but do not claim prosody control.
        """
        if not isinstance(narration_script, str) or not narration_script.strip() or len(narration_script) > 20_000:
            raise ValueError("narration_script must contain 1-20000 characters")
        if any(key in package_options for key in (
            "narration_path", "narration_script", "subtitle_path", "narration_asr", "narration_prosody",
        )):
            raise ValueError("narration and ASR assets are managed by the Cantonese narration wrapper")
        payload = (
            _compile_cantonese_narration_payload(narration_script, narration_prosody_plan)
            if narration_prosody_plan is not None else None
        )
        with tempfile.TemporaryDirectory(prefix=".muse-cantonese-", dir=self.workspace) as temporary_dir:
            audio_path = Path(temporary_dir) / "voiceover.wav"
            if payload is not None:
                from muse.cantonese_tts import synthesize_cantonese_segment_payload_with_runtime
                synthesis = synthesize_cantonese_segment_payload_with_runtime(
                    payload, audio_path, model_dir=model_dir,
                    packages_dir=self.workspace / ".muse-canto-tts-py314", quality="duration_filter",
                )
                narration_prosody = _build_cantonese_narration_prosody_record(
                    narration_script, narration_prosody_plan, synthesis,
                )
            else:
                from muse.cantonese_tts import synthesize_cantonese_segments_with_runtime
                synthesis = synthesize_cantonese_segments_with_runtime(
                    narration_script, audio_path, model_dir=model_dir,
                    packages_dir=self.workspace / ".muse-canto-tts-py314", quality="duration_filter",
                )
                narration_prosody = None
            asr_result = None
            subtitle_path = None
            if asr_model_dir is not None:
                from muse.cantonese_asr import transcribe_cantonese
                subtitle_path = Path(temporary_dir) / "subtitles.srt"
                asr_result = transcribe_cantonese(
                    audio_path, model_dir=asr_model_dir, output_srt=subtitle_path,
                    reference_text=narration_script,
                )
                if asr_result.get("status") != "passed":
                    comparison = asr_result.get("comparison") or {}
                    cer = comparison.get("cer")
                    raise VideoProductError(
                        f"Cantonese ASR CER gate failed (status={asr_result.get('status')}, cer={cer}); refusing to package narration"
                    )
            packaged = self.package_generated_clip(
                title, prompt, source_path, narration_path=audio_path,
                narration_script=narration_script, narration_prosody=narration_prosody,
                subtitle_path=subtitle_path, narration_asr=asr_result, **package_options,
            )
        packaged["narration_synthesis"] = {
            key: synthesis[key] for key in (
                "status", "engine", "model_repo", "model_revision", "license",
                "language", "sample_rate", "channels", "frames", "duration_seconds", "bytes",
                "segment_count", "inserted_pause_frames", "inserted_pause_seconds",
                "audio_sha256", "applied_controls", "backend_capabilities", "unsupported_controls",
            ) if key in synthesis
        }
        if narration_prosody is not None:
            packaged["narration_prosody"] = narration_prosody
        if asr_result is not None:
            packaged["narration_asr"] = asr_result
        return packaged



    def package_approved_subtitle_revision(
        self, initiative_id: str, version: str, review_document: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new immutable product version with a human-approved MP4 subtitle track.

        The existing product remains untouched. Technical QA and Creator publication
        readiness remain independent gates; subtitle approval alone never ships a product.
        """
        from muse.initiative_engine import InitiativeStore
        from muse.subtitle_review import parse_srt_cues, render_approved_subtitles

        verified = self.verify_path(self.workspace / "data" / "products", initiative_id, version)
        if not verified.get("validated") or not verified.get("subtitles") or not verified.get("narration_asr"):
            raise VideoProductError("approved subtitle replacement requires a verified narrated video product")
        if not isinstance(review_document, dict) or review_document.get("human_approved") is not True or review_document.get("review_status") != "approved":
            raise VideoProductError("explicit human subtitle approval is required")
        approved_by = review_document.get("approved_by")
        approval_timestamp = review_document.get("approval_timestamp")
        if not isinstance(approved_by, str) or not approved_by.strip() or not isinstance(approval_timestamp, str) or not approval_timestamp.strip():
            raise VideoProductError("approved subtitle review identity and timestamp are required")

        source_dir = (self.workspace / verified["relative_path"]).resolve(strict=True)
        if source_dir.is_symlink() or source_dir.parent.is_symlink():
            raise VideoProductError("source product path must not contain symlinks")
        manifest_path = source_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("subtitle_review") is not None:
            raise VideoProductError("this product version already has an approved subtitle revision")
        narration = manifest.get("narration")
        audio_fact = manifest.get("audio_asset")
        asr_fact = manifest.get("narration_asr")
        review_asr = review_document.get("asr")
        if (not isinstance(narration, dict) or not isinstance(audio_fact, dict)
                or not isinstance(asr_fact, dict) or not isinstance(review_asr, dict)
                or not isinstance(review_document.get("subtitle_file"), str)
                or Path(review_document.get("subtitle_file", "")).name != review_document.get("subtitle_file")
                or Path(review_document.get("subtitle_file", "")).suffix.casefold() != ".srt"
                or review_document.get("reference_script") != narration.get("script")
                or review_document.get("raw_asr_transcript") != asr_fact.get("transcript")
                or review_asr.get("audio_sha256") != audio_fact.get("sha256")
                or review_asr.get("audio_sha256") != asr_fact.get("audio_sha256")
                or review_asr.get("model_repo") != asr_fact.get("model_repo")
                or review_asr.get("model_revision") != asr_fact.get("model_revision")):
            raise VideoProductError("subtitle review does not match this product's narration, ASR model and audio")
        segments = review_document.get("segments")
        if (not isinstance(segments, list) or not segments
                or review_document.get("subtitle_cue_count") != len(segments)):
            raise VideoProductError("approved subtitle review cue count is invalid")
        approved_srt = render_approved_subtitles(review_document)
        cues = parse_srt_cues(approved_srt)
        if len(cues) != len(segments):
            raise VideoProductError("approved subtitle SRT cue count does not match the review")
        review_bytes = (json.dumps(review_document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        subtitle_bytes = approved_srt.encode("utf-8")
        review_sha256 = hashlib.sha256(review_bytes).hexdigest()
        subtitle_sha256 = hashlib.sha256(subtitle_bytes).hexdigest()

        # Resolve the exact existing Creator Initiative from its product_built ledger event.
        initiative_database = self.workspace / "data" / "initiatives" / "muse.sqlite3"
        creator_matches: list[tuple[str, str | None]] = []
        with InitiativeStore(initiative_database) as store:
            for candidate in store.list(limit=200):
                for event in store.list_events(initiative_id=candidate["id"], limit=200):
                    details = event.get("details", {})
                    artifact = details.get("artifact_metadata", {}) if isinstance(details, dict) else {}
                    if (event.get("event_type") == "product_built"
                            and details.get("relative_path") == verified["relative_path"].replace("\\", "/")
                            and artifact.get("product_id") == initiative_id
                            and artifact.get("version_id") == version):
                        creator_matches.append((candidate["id"], artifact.get("evolution_cycle_id")))
            if len(creator_matches) != 1:
                raise VideoProductError("could not resolve one exact Creator Initiative for this product version")
            creator_initiative_id, evolution_cycle_id = creator_matches[0]
            creator = next((item for item in store.list(limit=200) if item["id"] == creator_initiative_id), None)
            if creator is None or creator.get("status") in {"parked", "rejected", "shipped"}:
                raise VideoProductError("Creator Initiative does not permit a subtitle revision")

        inventory = discover_media_tools()
        tools = {item.tool_id: item.executable for item in inventory.tools}
        ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
        if not ffmpeg or not ffprobe:
            raise VideoProductError("FFmpeg and FFprobe are required to mux approved subtitles")
        products_root = self.workspace / "data" / "products"
        initiative_root = products_root / initiative_id
        if (products_root.is_symlink() or initiative_root.is_symlink()
                or initiative_root.resolve().parent != products_root.resolve()):
            raise VideoProductError("video product destination is unsafe")
        staging = Path(tempfile.mkdtemp(prefix=".approved-subtitles-", dir=initiative_root))
        try:
            for name in ("README.md", "storyboard.json", "qa-report.json", "subtitles.srt"):
                source = source_dir / name
                if source.is_symlink() or not source.is_file() or source.resolve().parent != source_dir:
                    raise VideoProductError("source product is missing a required safe artifact")
                shutil.copyfile(source, staging / name)
            source_video_copy = staging / "source-video.mp4"
            source_video = source_dir / "video.mp4"
            if source_video.is_symlink() or not source_video.is_file() or source_video.resolve().parent != source_dir:
                raise VideoProductError("source product MP4 is missing or unsafe")
            shutil.copyfile(source_video, source_video_copy)

            source_probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries",
                 "stream=codec_type,codec_name,width,height,avg_frame_rate",
                 "-of", "json", str(source_video_copy)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if source_probe.returncode != 0:
                raise VideoProductError("could not inspect the source MP4 before subtitle mux")
            try:
                source_streams = json.loads(source_probe.stdout).get("streams", [])
            except (json.JSONDecodeError, AttributeError) as exc:
                raise VideoProductError("source MP4 stream metadata is invalid") from exc
            source_video = next((item for item in source_streams if item.get("codec_type") == "video"), None)
            source_audio = [item for item in source_streams if item.get("codec_type") == "audio"]
            if not source_video or not source_audio:
                raise VideoProductError("source MP4 must contain video and narration audio streams")

            srt_path = staging / "approved-subtitles.srt"
            srt_path.write_bytes(subtitle_bytes)
            output_video = staging / "video.mp4"
            mux = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                 "-i", str(source_video_copy), "-i", str(srt_path),
                 "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
                 "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
                 "-metadata:s:s:0", "language=yue",
                 "-metadata:s:s:0", "title=粵語人工校對字幕",
                 "-movflags", "+faststart", str(output_video)],
                capture_output=True, text=True, timeout=900, check=False,
            )
            if mux.returncode != 0 or not output_video.is_file() or output_video.stat().st_size <= 12:
                raise VideoProductError("approved subtitle MP4 mux failed: " + str((mux.stderr or "")[-1000:]))
            # The temporary input copy is distinct from the mux output after FFmpeg succeeds.
            output_probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries",
                 "stream=codec_type,codec_name,width,height,avg_frame_rate:stream_tags=language,title",
                 "-of", "json", str(output_video)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if output_probe.returncode != 0:
                raise VideoProductError("could not inspect the remuxed MP4")
            try:
                output_streams = json.loads(output_probe.stdout).get("streams", [])
            except (json.JSONDecodeError, AttributeError) as exc:
                raise VideoProductError("remuxed MP4 stream metadata is invalid") from exc
            output_video_stream = next((item for item in output_streams if item.get("codec_type") == "video"), None)
            output_audio = [item for item in output_streams if item.get("codec_type") == "audio"]
            subtitle_streams = [item for item in output_streams if item.get("codec_type") == "subtitle"]
            if (not output_video_stream or not output_audio
                    or any(output_video_stream.get(key) != source_video.get(key)
                           for key in ("codec_name", "width", "height", "avg_frame_rate"))
                    or not any(item.get("codec_name") == "mov_text"
                               and item.get("tags", {}).get("language") == "yue"
                               for item in subtitle_streams)):
                raise VideoProductError("remuxed MP4 failed video/audio preservation or mov_text subtitle verification")
            source_video_copy.unlink()
            srt_path.unlink()

            qa_report = _inspect_product_video(
                output_video, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
                expected_width=source_video.get("width"), expected_height=source_video.get("height"),
                expected_fps=None, require_audio=True,
            )
            quality_gate = _quality_gate_summary(qa_report)
            video_metadata = _finalize_video_metadata(
                manifest.get("video_metadata", {}), qa_report, output_video,
            )
            subtitle_fact = {
                "name": "subtitles.srt", "bytes": len(subtitle_bytes), "sha256": subtitle_sha256,
                "cue_count": len(cues), "review_status": "approved",
            }
            review_record = {
                "schema_version": 1, "status": "approved", "human_approved": True,
                "approved_by": approved_by.strip(), "approval_timestamp": approval_timestamp,
                "review_sha256": review_sha256, "approved_srt_sha256": subtitle_sha256,
                "video_sha256": _hash_file(output_video), "muxed_subtitle_codec": "mov_text",
                "language": "yue",
            }
            (staging / "subtitles.srt").write_bytes(subtitle_bytes)
            (staging / "subtitle-review.json").write_bytes(review_bytes)
            (staging / "qa-report.json").write_bytes(_safe_json(qa_report))
            storyboard = json.loads((staging / "storyboard.json").read_text(encoding="utf-8"))
            storyboard_narration = storyboard.get("narration")
            if not isinstance(storyboard_narration, dict):
                raise VideoProductError("storyboard narration metadata is missing")
            storyboard_narration["subtitles"] = subtitle_fact
            storyboard_narration["subtitle_review"] = review_record
            (staging / "storyboard.json").write_bytes(_safe_json(storyboard))
            readme = (staging / "README.md").read_text(encoding="utf-8")
            readme += (
                "\n\n## 人工校对字幕\n\n"
                f"- 审核者：{approved_by.strip()}\n"
                f"- 审核时间：{approval_timestamp}\n"
                "- MP4 已封装可切换的粤语 mov_text 字幕轨；原视频与音频流均采用 stream copy。\n"
                "- 人工字幕批准不等于技术质检、版权/肖像、平台规则或正式发布审批。\n"
            )
            (staging / "README.md").write_text(readme, encoding="utf-8")

            archive_path = source_dir / "product.zip"
            if archive_path.is_symlink() or not archive_path.is_file():
                raise VideoProductError("source product ZIP is missing or unsafe")
            replaced = {"README.md", "storyboard.json", "qa-report.json", "video.mp4", "subtitles.srt", "subtitle-review.json"}
            with zipfile.ZipFile(archive_path, "r") as original, zipfile.ZipFile(
                staging / "product.zip", "w", compression=zipfile.ZIP_DEFLATED,
            ) as updated:
                for info in original.infolist():
                    if info.filename not in replaced:
                        updated.writestr(info, original.read(info.filename))
                for name in ("README.md", "storyboard.json", "qa-report.json", "video.mp4", "subtitles.srt", "subtitle-review.json"):
                    updated.write(staging / name, name)
            if (staging / "product.zip").stat().st_size > _MAX_PRODUCT_BYTES:
                raise VideoProductError("updated video product ZIP exceeds the 2 GiB limit")

            files = dict(manifest.get("files", {}))
            for name in ("README.md", "storyboard.json", "qa-report.json", "video.mp4", "subtitles.srt", "subtitle-review.json", "product.zip"):
                files[name] = _hash_file(staging / name)
            fingerprint = hashlib.sha256(_safe_json({
                "source_version": version, "source_manifest_sha256": verified["manifest_sha256"],
                "review_sha256": review_sha256, "approved_srt_sha256": subtitle_sha256,
                "video_sha256": files["video.mp4"],
            })).hexdigest()
            new_version = fingerprint[:20]
            reviewer_identity = review_document.get("reviewer_identity")
            evidence_refs = review_document.get("approval_evidence_refs")
            rationale = review_document.get("approval_rationale")
            if (not isinstance(reviewer_identity, dict)
                    or not all(isinstance(reviewer_identity.get(key), str) and reviewer_identity[key].strip()
                               for key in ("id", "role"))
                    or reviewer_identity.get("identity_assurance") != "self_asserted_local_identity"
                    or not isinstance(evidence_refs, list) or not evidence_refs
                    or not isinstance(rationale, str) or not rationale.strip()):
                raise VideoProductError("structured subtitle reviewer identity, rationale and evidence are required")
            from muse.video_publish_package import _json_bytes
            subtitle_release_review = {
                "schema_version": 1, "category": "subtitle_review", "decision": "approved",
                "reviewer": {"id": reviewer_identity["id"], "role": reviewer_identity["role"],
                             "identity_assurance": "self_asserted_local_identity"},
                "reviewed_at": approval_timestamp,
                "subject": {
                    "initiative_id": initiative_id, "version_id": new_version,
                    "video_sha256": files["video.mp4"],
                    "artifact_sha256": {
                        "subtitles.srt": files["subtitles.srt"],
                        "subtitle-review.json": files["subtitle-review.json"],
                    },
                },
                "evidence_refs": evidence_refs, "rationale": rationale,
            }
            subtitle_release_review["review_sha256"] = hashlib.sha256(
                _json_bytes(subtitle_release_review)
            ).hexdigest()
            human_reviews = dict(manifest.get("human_reviews", {})) if isinstance(manifest.get("human_reviews"), dict) else {}
            human_reviews["subtitle_review"] = subtitle_release_review
            manifest.update({
                "version_id": new_version, "status": "testing",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "input_sha256": fingerprint, "files": files,
                "video_metadata": video_metadata, "quality_gate": quality_gate,
                "subtitles": subtitle_fact, "subtitle_review": review_record,
                "human_reviews": human_reviews,
            })
            manifest["video_metadata"]["output"] = "video.mp4"
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            target = initiative_root / new_version
            if target.exists():
                existing = self.verify_path(products_root, initiative_id, new_version)
                shutil.rmtree(staging, ignore_errors=True)
                return self._record_creator_artifact(
                    existing, title=existing["title"], prompt="Human-approved Cantonese subtitle revision",
                    creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id,
                )
            staging.rename(target)
            result = self.verify_path(products_root, initiative_id, new_version)
            return self._record_creator_artifact(
                result, title=result["title"], prompt="Human-approved Cantonese subtitle revision",
                creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _publish_staging(
        self, staging: Path, products_root: Path, identity: str, version: str, *,
        title: str, prompt: str, creator_initiative_id: str | None,
        evolution_cycle_id: str | None,
    ) -> dict[str, Any]:
        """Publish one staged version, or safely reuse a concurrent valid winner."""
        target = Path(products_root) / identity / version
        try:
            staging.rename(target)
        except FileExistsError:
            if not target.exists():
                raise
        result = self.verify_path(products_root, identity, version)
        return self._record_creator_artifact(
            result, title=title, prompt=prompt,
            creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id,
        )

    def _record_creator_artifact(
        self, result: dict[str, Any], *, title: str, prompt: str,
        creator_initiative_id: str | None, evolution_cycle_id: str | None,
    ) -> dict[str, Any]:
        """Attach a verified video package to the single Creator Initiative Ledger."""
        from muse.initiative_engine import InitiativeStore

        if evolution_cycle_id is not None and not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id):
            raise ValueError("evolution_cycle_id must be a valid Muse Evolution Cycle ID")
        database = self.workspace / "data" / "initiatives" / "muse.sqlite3"
        with InitiativeStore(database) as store:
            if creator_initiative_id is None:
                item = store.propose(
                    f"{title.strip()}（视频 {result['initiative_id'][:8]}）",
                    kind="creative_output",
                    creator_value="把研究、灵感或用户创意转化为可播放、可下载、可复核的视频作品",
                    concept=(prompt.strip() or f"本地素材合成视频：{title.strip()}")[:3900],
                    first_step="生成 MP4、工程 ZIP 与可追溯素材清单，并完成完整性验证",
                    expected_output="MP4、工程 ZIP、manifest、素材来源和验证摘要",
                    inspiration_refs=[f"video-product:{result['initiative_id']}"],
                    capability_gap="研究知识、创作资产与演化经验尚未完全贯通",
                    autonomy_level="prototype",
                )
            else:
                item = next((entry for entry in store.list(limit=200) if entry["id"] == creator_initiative_id), None)
                if item is None:
                    raise KeyError(creator_initiative_id)
            if item.get("status") in {"parked", "rejected", "shipped"}:
                raise VideoProductError("creator initiative status does not permit new video artifacts")
            if item.get("status") != "testing":
                store.update(item["id"], status="testing", outcome="Video package verified locally; awaiting human review.")
            generation = result.get("video_metadata", {})
            generation_allowlist = (
                "model", "prompt_id", "seed", "steps", "fps", "width", "height",
                "frames", "duration_seconds", "aspect_ratio", "resolution_tier",
            )
            generation_summary = {key: generation[key] for key in generation_allowlist if key in generation}
            source_hashes = [
                {"name": str(item.get("name", ""))[:120], "sha256": item.get("sha256", "")}
                for item in result.get("source_assets", [])[:8] if isinstance(item, dict)
            ]
            artifact_metadata = {
                "mp4_sha256": result["mp4_sha256"],
                "manifest_sha256": result["manifest_sha256"],
                "storyboard_sha256": result["storyboard_sha256"],
                "readme_sha256": result["readme_sha256"],
                "source_asset_hashes": source_hashes,
                "generation_metadata": generation_summary,
                "validation": result["validation_summary"],
                "quality_gate": result.get("quality_gate"),
                **({"clip_provenance": {key: result["clip_provenance"].get(key) for key in ("source_sha256", "start_seconds", "end_seconds", "score", "analysis_mode", "captions_sha256")}} if isinstance(result.get("clip_provenance"), dict) else {}),
                **({"subtitle_review": result["subtitle_review"]} if result.get("subtitle_review") else {}),
                "product_id": result["initiative_id"],
                "version_id": result["version_id"],
                **({"recipe_sha256": result["recipe_sha256"]} if result.get("recipe_sha256") else {}),
            }
            if evolution_cycle_id:
                artifact_metadata["evolution_cycle_id"] = evolution_cycle_id
            event = store.record_product_artifact(
                item["id"], relative_path=result["relative_path"],
                package_sha256=result["package_sha256"], validated=result["validated"],
                artifact_metadata=artifact_metadata,
            )
            knowledge_linkage = {"status": "not_initialized", "linked_cycle_nodes": 0}
            knowledge_database = self.workspace / "data" / "research" / "knowledge.sqlite3"
            if knowledge_database.is_symlink():
                knowledge_linkage = {"status": "blocked_unsafe_path", "linked_cycle_nodes": 0}
            elif knowledge_database.is_file():
                try:
                    from muse.research_knowledge import ResearchKnowledgeStore

                    with ResearchKnowledgeStore(knowledge_database) as knowledge:
                        artifact_node = knowledge.upsert_node(
                            "source", f"视频创作资产：{title.strip()}",
                            summary="Muse 本地视频产品；MP4、工程 ZIP、manifest 与素材哈希均已通过产品完整性验证。",
                            external_ref=f"muse-video-product:{result['initiative_id']}:{result['version_id']}",
                            metadata={
                                "source_kind": "creative_artifact",
                                "artifact_format": "video/mp4+application/zip",
                                "creator_initiative_id": item["id"],
                                "product_id": result["initiative_id"],
                                "version_id": result["version_id"],
                                "relative_path": result["relative_path"],
                                "mp4_sha256": result["mp4_sha256"],
                                "package_sha256": result["package_sha256"],
                                "manifest_sha256": result["manifest_sha256"],
                                **({"recipe_sha256": result["recipe_sha256"]} if result.get("recipe_sha256") else {}),
                                "storyboard_sha256": result["storyboard_sha256"],
                                "readme_sha256": result["readme_sha256"],
                                "source_asset_hashes": source_hashes,
                                "generation_metadata": generation_summary,
                                "validation": result["validation_summary"],
                                "quality_gate": result.get("quality_gate"),
                                **({"subtitle_review": result["subtitle_review"]} if result.get("subtitle_review") else {}),
                                **({"evolution_cycle_id": evolution_cycle_id} if evolution_cycle_id else {}),
                            },
                            provenance=(result["relative_path"],),
                        )
                        knowledge.link_node_to_initiative(
                            store, item["id"], artifact_node["id"],
                            relationship="related", evolution_cycle_id=evolution_cycle_id,
                        )
                        linked_cycle_nodes = 0
                        already_linked_cycle_nodes = 0
                        truncated = False
                        if evolution_cycle_id:
                            cycle_nodes = knowledge.list_nodes_for_evolution_cycle(evolution_cycle_id, limit=500)
                            truncated = len(cycle_nodes) == 500
                            for node in cycle_nodes:
                                if node["id"] == artifact_node["id"]:
                                    continue
                                node_metadata = node.get("metadata", {})
                                linked_initiatives = node_metadata.get("creator_initiative_ids", [])
                                linked_cycles = node_metadata.get("evolution_cycle_ids", [])
                                if item["id"] in linked_initiatives and evolution_cycle_id in linked_cycles:
                                    already_linked_cycle_nodes += 1
                                else:
                                    knowledge.link_node_to_initiative(
                                        store, item["id"], node["id"],
                                        relationship="related", evolution_cycle_id=evolution_cycle_id,
                                    )
                                linked_cycle_nodes += 1
                        knowledge_linkage = {
                            "status": "linked_bounded" if truncated else "linked",
                            "artifact_node_id": artifact_node["id"],
                            "linked_cycle_nodes": linked_cycle_nodes,
                            "already_linked_cycle_nodes": already_linked_cycle_nodes,
                            "new_cycle_links": linked_cycle_nodes - already_linked_cycle_nodes,
                            "evolution_cycle_id": evolution_cycle_id,
                            "scan_limit": 500,
                        }
                except Exception as exc:
                    knowledge_linkage = {
                        "status": "failed", "linked_cycle_nodes": 0,
                        "error_type": type(exc).__name__,
                    }
        result["creator_initiative_id"] = item["id"]
        result["ledger_event_id"] = event["id"]
        result["knowledge_linkage"] = knowledge_linkage
        if evolution_cycle_id:
            result["evolution_cycle_id"] = evolution_cycle_id
        return result

    @staticmethod
    def verify_path(products_root: str | Path, initiative_id: str, version: str) -> dict[str, Any]:
        root = Path(products_root)
        if root.is_symlink() or root.parent.is_symlink():
            raise VideoProductError("video product root must not contain symlinks")
        if not re.fullmatch(r"[0-9a-f]{32}", initiative_id) or not re.fullmatch(r"[0-9a-f]{20}", version):
            raise ValueError("invalid video product identity")
        initiative_root = root / initiative_id
        directory = initiative_root / version
        if initiative_root.is_symlink() or directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != initiative_root.resolve():
            raise FileNotFoundError("video product not found")
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise VideoProductError("video product manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recipe_provenance = manifest.get("recipe_provenance")
        if recipe_provenance is not None and (
            not isinstance(recipe_provenance, dict)
            or recipe_provenance.get("schema_version") != 1
            or recipe_provenance.get("relationship") != "recipe_reference"
            or not isinstance(recipe_provenance.get("recipe_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", recipe_provenance["recipe_sha256"])
        ):
            raise VideoProductError("video recipe provenance is invalid")
        legacy_expected = {"README.md", "storyboard.json", "video.mp4", "product.zip"}
        qa_expected = legacy_expected | {"qa-report.json"}
        subtitle_expected = qa_expected | {"subtitles.srt"}
        reviewed_subtitle_expected = subtitle_expected | {"subtitle-review.json"}
        clip_expected = qa_expected | {"clip-captions.srt"}
        files = manifest.get("files")
        if (manifest.get("product_kind") != "video_creator" or manifest.get("initiative_id") != initiative_id
                or manifest.get("version_id") != version or not isinstance(files, dict)
                or set(files) not in (legacy_expected, qa_expected, subtitle_expected, reviewed_subtitle_expected, clip_expected)): 
            raise VideoProductError("video product manifest is invalid")
        scene_assets = manifest.get("scene_assets", [])
        if not isinstance(scene_assets, list) or len(scene_assets) > 20:
            raise VideoProductError("video scene asset manifest is invalid")
        scene_asset_paths: dict[str, Path] = {}
        for index, fact in enumerate(scene_assets, start=1):
            if not isinstance(fact, dict):
                raise VideoProductError("video scene asset metadata is invalid")
            name, archive_path = fact.get("name"), fact.get("archive_path")
            size, digest = fact.get("bytes"), fact.get("sha256")
            if (fact.get("order") != index or not isinstance(name, str) or Path(name).name != name
                    or not name.lower().endswith(".mp4") or archive_path != f"scenes/{name}"
                    or isinstance(size, bool) or not isinstance(size, int) or size <= 0
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or archive_path in scene_asset_paths):
                raise VideoProductError("video scene asset provenance is invalid")
            asset_path = directory / archive_path
            scenes_dir = directory / "scenes"
            if (scenes_dir.is_symlink() or asset_path.is_symlink() or not asset_path.is_file()
                    or not asset_path.resolve().is_relative_to(directory.resolve())
                    or asset_path.stat().st_size != size or _hash_file(asset_path) != digest):
                raise VideoProductError("video scene asset is missing, unsafe or has changed")
            scene_asset_paths[archive_path] = asset_path

        expected = set(files)
        resolved: dict[str, tuple[Path, bytes | None]] = {}
        for name, digest in files.items():
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
                raise VideoProductError("video product file is missing or unsafe")
            if path.stat().st_size > _MAX_PRODUCT_BYTES or _hash_file(path) != digest:
                raise VideoProductError("video product file integrity check failed")
            resolved[name] = (path, None if name in {"video.mp4", "product.zip"} else path.read_bytes())

        subtitle_fact = manifest.get("subtitles")
        narration_asr = manifest.get("narration_asr")
        background_music_fact = manifest.get("background_music")
        if background_music_fact is not None and not isinstance(background_music_fact, dict):
            raise VideoProductError("video background music manifest is invalid")
        if background_music_fact is not None and manifest.get("narration") is None:
            raise VideoProductError("video background music requires a narration manifest")
        if "subtitles.srt" in expected:
            subtitle_path = resolved["subtitles.srt"][0]
            if (not isinstance(subtitle_fact, dict) or subtitle_fact.get("name") != "subtitles.srt"
                    or subtitle_fact.get("bytes") != subtitle_path.stat().st_size
                    or subtitle_fact.get("sha256") != files["subtitles.srt"]):
                raise VideoProductError("video subtitle manifest does not match the SRT artifact")
        elif subtitle_fact is not None or narration_asr is not None:
            raise VideoProductError("video subtitle/ASR metadata exists without a subtitle artifact")
        clip_provenance = manifest.get("clip_provenance")
        if "clip-captions.srt" in expected:
            caption_path = resolved["clip-captions.srt"][0]
            if (not isinstance(clip_provenance, dict)
                    or clip_provenance.get("schema_version") != 1
                    or clip_provenance.get("captions_sha256") != files["clip-captions.srt"]
                    or caption_path.stat().st_size > 2 * 1024 * 1024):
                raise VideoProductError("highlight clip caption provenance is invalid")
        elif isinstance(clip_provenance, dict) and clip_provenance.get("captions_sha256") is not None:
            raise VideoProductError("highlight caption hash exists without its SRT artifact")
        if clip_provenance is not None:
            if (not isinstance(clip_provenance, dict)
                    or clip_provenance.get("schema_version") != 1
                    or not isinstance(clip_provenance.get("source_name"), str)
                    or Path(clip_provenance.get("source_name", "")).name != clip_provenance.get("source_name")
                    or not isinstance(clip_provenance.get("source_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", clip_provenance["source_sha256"])
                    or isinstance(clip_provenance.get("source_bytes"), bool)
                    or not isinstance(clip_provenance.get("source_bytes"), int)
                    or clip_provenance["source_bytes"] <= 0
                    or any(isinstance(clip_provenance.get(key), bool)
                           or not isinstance(clip_provenance.get(key), (int, float))
                           or not math.isfinite(clip_provenance[key])
                           for key in ("start_seconds", "end_seconds", "score"))
                    or clip_provenance["start_seconds"] < 0
                    or clip_provenance["end_seconds"] <= clip_provenance["start_seconds"]
                    or not 0 <= clip_provenance["score"] <= 1
                    or not isinstance(clip_provenance.get("analysis_mode"), str)
                    or not isinstance(clip_provenance.get("transcript"), str)
                    or len(clip_provenance["transcript"]) > 12000):
                raise VideoProductError("highlight clip provenance is invalid")
        elif "clip-captions.srt" in expected:
            raise VideoProductError("highlight captions require clip provenance")


        subtitle_review_fact = manifest.get("subtitle_review")
        if "subtitle-review.json" in expected:
            review_document = json.loads(resolved["subtitle-review.json"][0].read_text(encoding="utf-8"))
            if (not isinstance(review_document, dict)
                    or not isinstance(subtitle_review_fact, dict)
                    or subtitle_review_fact.get("schema_version") != 1
                    or subtitle_review_fact.get("status") != "approved"
                    or not isinstance(subtitle_review_fact.get("approved_by"), str)
                    or not subtitle_review_fact.get("approved_by", "").strip()
                    or not isinstance(subtitle_review_fact.get("approval_timestamp"), str)
                    or not subtitle_review_fact.get("approval_timestamp", "").strip()
                    or subtitle_review_fact.get("human_approved") is not True
                    or subtitle_review_fact.get("muxed_subtitle_codec") != "mov_text"
                    or subtitle_review_fact.get("language") != "yue"
                    or subtitle_review_fact.get("review_sha256") != files["subtitle-review.json"]
                    or subtitle_review_fact.get("approved_srt_sha256") != files.get("subtitles.srt")
                    or subtitle_review_fact.get("video_sha256") != files.get("video.mp4")
                    or review_document.get("human_approved") is not True
                    or review_document.get("review_status") != "approved"
                    or review_document.get("approved_by") != subtitle_review_fact.get("approved_by")
                    or review_document.get("approval_timestamp") != subtitle_review_fact.get("approval_timestamp")):
                raise VideoProductError("approved subtitle review provenance does not match the product artifacts")
            from muse.subtitle_review import render_approved_subtitles
            if render_approved_subtitles(review_document).encode("utf-8") != resolved["subtitles.srt"][0].read_bytes():
                raise VideoProductError("approved subtitle SRT does not match its human review document")
        elif subtitle_review_fact is not None:
            raise VideoProductError("subtitle review metadata exists without its review artifact")

        if narration_asr is not None:
            audio_fact = manifest.get("audio_asset")
            from muse.cantonese_asr import MODEL_REPO, MODEL_REVISION
            comparison = narration_asr.get("comparison") if isinstance(narration_asr, dict) else None
            if (not isinstance(audio_fact, dict)
                    or narration_asr.get("status") not in {"passed", "needs_review"}
                    or narration_asr.get("model_repo") != MODEL_REPO
                    or narration_asr.get("model_revision") != MODEL_REVISION
                    or narration_asr.get("audio_sha256") != audio_fact.get("sha256")
                    or not isinstance(narration_asr.get("model_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", narration_asr["model_sha256"])
                    or narration_asr.get("human_listening_review_required") is not True
                    or not isinstance(comparison, dict)
                    or comparison.get("status") != narration_asr.get("status")
                    or comparison.get("metric") != "character_error_rate"):
                raise VideoProductError("video narration ASR provenance is invalid")

        with resolved["video.mp4"][0].open("rb") as stream:
            header = stream.read(12)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise VideoProductError("video product is not a valid MP4 container")
        quality_gate = None
        if "qa-report.json" in resolved:
            try:
                qa_report = json.loads(resolved["qa-report.json"][0].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise VideoProductError("video technical QA report is invalid") from exc
            quality_gate = manifest.get("quality_gate")
            if (not isinstance(qa_report, dict)
                    or qa_report.get("schema_version") != 1
                    or qa_report.get("status") not in {"passed", "failed", "blocked"}
                    or qa_report.get("path") != "video.mp4"
                    or qa_report.get("artifact_sha256") != files["video.mp4"]
                    or not isinstance(quality_gate, dict)
                    or quality_gate.get("status") != qa_report.get("status")
                    or quality_gate.get("passed") is not (qa_report.get("status") == "passed")
                    or quality_gate.get("ready_to_publish") is not False
                    or quality_gate.get("report") != "qa-report.json"
                    or manifest.get("status") != "testing"):
                raise VideoProductError("video technical QA gate does not match its MP4 artifact")

        with zipfile.ZipFile(resolved["product.zip"][0]) as archive:
            names = archive.namelist()
            expected_members = expected - {"product.zip"}
            narration = manifest.get("narration")
            allowed_members = set(expected_members)
            allowed_members.update(scene_asset_paths)
            narration_member = None
            background_music_member = None
            if background_music_fact is not None:
                background_music_member = background_music_fact.get("archive_path")
                if (not isinstance(background_music_member, str)
                        or not background_music_member.startswith("music/background.")
                        or Path(background_music_member).name != background_music_member.split("/")[-1]
                        or not isinstance(background_music_fact.get("bytes"), int)
                        or isinstance(background_music_fact.get("bytes"), bool)
                        or background_music_fact["bytes"] <= 0
                        or not isinstance(background_music_fact.get("sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", background_music_fact["sha256"])):
                    raise VideoProductError("video background music provenance is invalid")
                allowed_members.add(background_music_member)

            if narration is not None:
                if not isinstance(narration, dict):
                    raise VideoProductError("video narration manifest is invalid")
                if narration.get("subtitles_file") != ("subtitles.srt" if subtitle_fact else None):
                    raise VideoProductError("video narration subtitle reference is invalid")
                if narration.get("background_music_file") != background_music_member:
                    raise VideoProductError("video narration background music reference is invalid")
                narration_member = narration.get("audio_file")
                if not isinstance(narration_member, str) or not narration_member.startswith("narration/voiceover"):
                    raise VideoProductError("video narration audio reference is invalid")
                allowed_members.add(narration_member)
                if narration.get("script"):
                    allowed_members.add("narration/script.txt")
                if narration_asr is not None and narration.get("asr_status") != narration_asr.get("status"):
                    raise VideoProductError("video narration ASR status does not match the manifest")
            elif subtitle_fact is not None or narration_asr is not None:
                raise VideoProductError("video subtitles/ASR require a narration manifest")
            elif background_music_member is not None:
                raise VideoProductError("video background music requires a narration manifest")

            if len(names) != len(allowed_members) or set(names) != allowed_members or archive.testzip() is not None:
                raise VideoProductError("video product ZIP structure is invalid")
            for name in expected_members:
                with archive.open(name) as zipped, resolved[name][0].open("rb") as original:
                    while True:
                        left, right = zipped.read(1024 * 1024), original.read(1024 * 1024)
                        if left != right:
                            raise VideoProductError("video product ZIP content mismatch")
                        if not left:
                            break
            if background_music_member:
                music_bytes = archive.read(background_music_member)
                if (len(music_bytes) != background_music_fact.get("bytes")
                        or hashlib.sha256(music_bytes).hexdigest() != background_music_fact.get("sha256")):
                    raise VideoProductError("video background music integrity check failed")
            for archive_path, asset_path in scene_asset_paths.items():
                scene_bytes = archive.read(archive_path)
                if (len(scene_bytes) != asset_path.stat().st_size
                        or hashlib.sha256(scene_bytes).hexdigest() != _hash_file(asset_path)):
                    raise VideoProductError("video scene asset ZIP integrity check failed")
            if narration_member:
                audio_fact = manifest.get("audio_asset")
                if not isinstance(audio_fact, dict):
                    raise VideoProductError("video narration audio metadata is missing")
                audio_bytes = archive.read(narration_member)
                if (len(audio_bytes) != audio_fact.get("bytes")
                        or hashlib.sha256(audio_bytes).hexdigest() != audio_fact.get("sha256")):
                    raise VideoProductError("video narration audio integrity check failed")
                if narration.get("script") and archive.read("narration/script.txt").decode("utf-8") != narration["script"]:
                    raise VideoProductError("video narration script integrity check failed")
        return {
            "relative_path": str(directory.relative_to(root.parent.parent)), "initiative_id": initiative_id,
            "version_id": version, "title": manifest["title"], "kind": "video_creator",
            "status": manifest.get("status", "testing"), "video_metadata": manifest.get("video_metadata", {}),
            "quality_gate": quality_gate, "source_assets": manifest.get("source_assets", []),
            "subtitles": subtitle_fact, "narration_asr": narration_asr,
            "clip_provenance": clip_provenance,
            "subtitle_review": subtitle_review_fact,
            "background_music": background_music_fact,
            "package_sha256": files["product.zip"], "mp4_sha256": files["video.mp4"],
            "manifest_sha256": _hash_file(manifest_path),
            "recipe_sha256": recipe_provenance.get("recipe_sha256") if recipe_provenance else None,
            "storyboard_sha256": files["storyboard.json"], "readme_sha256": files["README.md"],
            "validation_summary": {"validated": True, "checks": ["mp4_container", "sha256", "zip_crc", "zip_members", "zip_content_hashes"]},            "created": False, "validated": True,
        }

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Muse video product from local assets")
    parser.add_argument("source", help="folder key under data/video_sources")
    parser.add_argument("title")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--audio")
    parser.add_argument("--audio-mode", choices=("music", "narration"), default="music")
    parser.add_argument("--aspect-ratio", choices=("16:9", "9:16", "1:1"), default="16:9")
    parser.add_argument("--fps", type=int, choices=(24, 25, 30, 50, 60), default=30)
    parser.add_argument("--image-seconds", type=float, default=4.0)
    parser.add_argument("--motion-profile", choices=("none", "brand_intro_outro"), default="none")
    parser.add_argument("--encoder", choices=("auto", "amf", "software"), default="auto")
    args = parser.parse_args(argv)
    try:
        result = VideoProductBuilder(args.workspace).build_from_source(
            args.source, args.title, audio_name=args.audio, audio_mode=args.audio_mode,
            aspect_ratio=args.aspect_ratio, fps=args.fps, image_seconds=args.image_seconds,
            encoder=args.encoder, motion_profile=args.motion_profile,
        )
    except (OSError, ValueError, VideoProductError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "completed", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
