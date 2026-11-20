"""Register rendered highlight clips as traceable Muse video products.

This adapter deliberately reuses VideoProductBuilder's versioned product format,
integrity verifier, Creator Initiative ledger and InitiativeStore video attempts.
It never publishes or changes release permissions.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from muse.video_product_builder import (
    VideoProductBuilder,
    VideoProductError,
    _hash_file,
    _inspect_product_video,
    _quality_gate_summary,
    _safe_json,
    _probe_media_duration_seconds,
)
from muse.media import discover_media_tools

_MAX_CLIP_BYTES = 2 * 1024 * 1024 * 1024


def register_highlight_clip(
    workspace: str | Path,
    source_video: str | Path,
    clip_video: str | Path,
    candidate: dict[str, Any],
    *,
    title: str,
    captions_srt: str | Path | None = None,
    creator_initiative_id: str | None = None,
    evolution_cycle_id: str | None = None,
    analysis_mode: str = "audio_only",
    transcript_engine: str | None = None,
    transcript: str = "",
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
) -> dict[str, Any]:
    """Package one already-rendered, human-selected clip into the canonical video library."""
    root = Path(workspace).resolve()
    source = Path(source_video).expanduser()
    clip = Path(clip_video).expanduser()
    if not root.is_dir():
        raise VideoProductError("workspace must be an existing directory")
    for path, label in ((source, "source video"), (clip, "rendered clip")):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{label} must be an existing regular local file")
    if source.resolve() == clip.resolve():
        raise ValueError("source video and rendered clip must be different files")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError("title must contain 1-160 characters")
    if not isinstance(candidate, dict):
        raise ValueError("candidate metadata must be an object")
    start, end, score = candidate.get("start"), candidate.get("end"), candidate.get("score")
    values = (start, end, score)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise ValueError("candidate start, end and score must be finite numbers")
    start, end, score = float(start), float(end), float(score)
    if start < 0 or end <= start or end - start < 3 or end - start > 600 or not 0 <= score <= 1:
        raise ValueError("candidate interval must be 3..600 seconds and score must be 0..1")
    if not isinstance(analysis_mode, str) or not analysis_mode or len(analysis_mode) > 80:
        raise ValueError("analysis_mode must be bounded non-empty text")
    if transcript_engine is not None and (not isinstance(transcript_engine, str) or len(transcript_engine) > 120):
        raise ValueError("transcript_engine must be bounded text")
    if not isinstance(transcript, str) or len(transcript) > 12000:
        raise ValueError("transcript must be at most 12000 characters")
    if source.stat().st_size <= 0 or clip.stat().st_size <= 0 or clip.stat().st_size > _MAX_CLIP_BYTES:
        raise ValueError("source and clip files must be non-empty; clip must be at most 2 GiB")
    caption = Path(captions_srt).expanduser() if captions_srt is not None else None
    if caption is not None and (caption.is_symlink() or not caption.is_file() or caption.suffix.casefold() != ".srt"):
        raise ValueError("captions_srt must be an existing regular SRT file")
    if caption is not None and caption.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("caption SRT exceeds the 2 MiB limit")

    source_fact = {"name": source.name[:120], "bytes": source.stat().st_size, "sha256": _hash_file(source)}
    clip_sha = _hash_file(clip)
    caption_sha = _hash_file(caption) if caption else None
    clip_provenance = {
        "schema_version": 1,
        "source_name": source_fact["name"],
        "source_sha256": source_fact["sha256"],
        "source_bytes": source_fact["bytes"],
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "score": round(score, 6),
        "analysis_mode": analysis_mode,
        "transcript_engine": transcript_engine,
        "transcript": transcript.strip(),
        "captions_sha256": caption_sha,
    }
    identity = hashlib.sha256(
        ("muse-highlight:" + source_fact["sha256"] + ":" + title.strip()).encode("utf-8")
    ).hexdigest()[:32]
    fingerprint = hashlib.sha256(_safe_json({
        "builder_version": VideoProductBuilder.BUILDER_VERSION,
        "clip_sha256": clip_sha,
        "clip_provenance": clip_provenance,
    })).hexdigest()
    version = fingerprint[:20]
    builder = VideoProductBuilder(root)
    _, products_root = builder._roots()
    initiative_root = products_root / identity
    target = initiative_root / version
    for path in (initiative_root, target):
        if path.is_symlink():
            raise VideoProductError("highlight product destination must not contain symlinks")
    if target.exists():
        result = builder.verify_path(products_root, identity, version)
        result = builder._record_creator_artifact(
            result, title=title.strip(), prompt="NeonClipping-inspired locally rendered highlight clip",
            creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id,
        )
    else:
        initiative_root.mkdir(parents=True, exist_ok=True)
        if initiative_root.resolve().parent != products_root.resolve():
            raise VideoProductError("highlight product destination escapes product root")
        inventory = discover_media_tools()
        tools = {item.tool_id: item.executable for item in inventory.tools}
        ffmpeg = ffmpeg_path or tools.get("ffmpeg")
        ffprobe = ffprobe_path or tools.get("ffprobe")
        if not ffmpeg or not ffprobe:
            raise VideoProductError("FFmpeg and FFprobe are required to register a highlight product")
        source_duration = _probe_media_duration_seconds(source, ffprobe, stream_type="video")
        if end > source_duration + 0.25:
            raise ValueError("candidate end time exceeds the source video duration")
        staging = Path(tempfile.mkdtemp(prefix=".highlight-product-", dir=initiative_root))
        try:
            shutil.copyfile(clip, staging / "video.mp4")
            if caption is not None:
                shutil.copyfile(caption, staging / "clip-captions.srt")
            if _hash_file(staging / "video.mp4") != clip_sha:
                raise VideoProductError("rendered clip changed while it was being packaged")
            if _hash_file(source) != source_fact["sha256"]:
                raise VideoProductError("source video changed while the highlight product was being packaged")
            if caption is not None and _hash_file(staging / "clip-captions.srt") != caption_sha:
                raise VideoProductError("caption SRT changed while it was being packaged")
            qa_report = _inspect_product_video(
                staging / "video.mp4", ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
                expected_width=1080, expected_height=1920, expected_fps=30,
                require_audio=True, timeout_seconds=900,
            )
            quality_gate = _quality_gate_summary(qa_report)
            if quality_gate["status"] != "passed":
                raise VideoProductError("highlight clip failed canonical Muse product QA")
            measured_duration = (qa_report.get("metadata") or {}).get("duration_seconds")
            if (isinstance(measured_duration, bool) or not isinstance(measured_duration, (int, float))
                    or not math.isfinite(measured_duration) or abs(float(measured_duration) - (end - start)) > 1.0):
                raise VideoProductError("rendered clip duration does not match its selected source interval")
            video_metadata = {
                key: value for key, value in (qa_report.get("metadata") or {}).items()
                if key in {"duration_seconds", "width", "height", "fps", "has_audio", "frames"}
            }
            video_metadata.update({
                "output": "video.mp4", "aspect_ratio": "9:16",
                "production_mode": "neonclipping_highlight_adapter",
                "source_start_seconds": round(start, 3),
                "source_end_seconds": round(end, 3),
            })
            storyboard = {
                "schema_version": 1, "title": title.strip(),
                "production_mode": "audio_first_highlight_clip",
                "ai_generation": "not_requested",
                "source_video": source_fact,
                "clip": clip_provenance,
                "final_output": video_metadata,
                "scenes": [],
            }
            (staging / "qa-report.json").write_text(
                json.dumps(qa_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            (staging / "storyboard.json").write_bytes(_safe_json(storyboard))
            readme = (
                f"# {title.strip()}\n\n"
                "Muse 本地高光切片产品。该版本来自用户本地源视频的候选区间，"
                "已经过 Muse 技术 QA；不代表内容、事实、版权或平台发布审批。\n\n"
                f"- 源文件：{source_fact['name']}\n"
                f"- 源 SHA-256：{source_fact['sha256']}\n"
                f"- 时间区间：{start:.3f}s – {end:.3f}s\n"
                f"- 高光评分：{score:.4f}\n"
                "- 状态：testing（等待人工内容/版权审核）\n"
            )
            (staging / "README.md").write_text(readme, encoding="utf-8")
            names = ["README.md", "storyboard.json", "qa-report.json", "video.mp4"]
            if caption is not None:
                names.append("clip-captions.srt")
            with zipfile.ZipFile(staging / "product.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in names:
                    archive.write(staging / name, name)
            files = {name: _hash_file(staging / name) for name in (*names, "product.zip")}
            manifest = {
                "schema_version": 1, "builder_version": VideoProductBuilder.BUILDER_VERSION,
                "product_kind": "video_creator", "initiative_id": identity, "version_id": version,
                "title": title.strip(), "status": "testing",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "input_sha256": fingerprint, "files": files,
                "video_metadata": video_metadata, "quality_gate": quality_gate,
                "source_assets": [source_fact], "audio_asset": None,
                "clip_provenance": clip_provenance,
                "execution": {
                    "arbitrary_code_executed": False, "network_access": False,
                    "local_tools_invoked": ["ffmpeg", "ffprobe"],
                    "ai_generation": "not_requested",
                },
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            result = builder._publish_staging(
                staging, products_root, identity, version,
                title=title.strip(), prompt="NeonClipping-inspired locally rendered highlight clip",
                creator_initiative_id=creator_initiative_id, evolution_cycle_id=evolution_cycle_id,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    from muse.initiative_engine import InitiativeStore
    database = root / "data" / "initiatives" / "muse.sqlite3"
    attempt_id = "clip-" + hashlib.sha256(
        (result["initiative_id"] + ":" + result["version_id"]).encode("ascii")
    ).hexdigest()[:32]
    creator_id = result["creator_initiative_id"]
    with InitiativeStore(database) as store:
        attempt = store.get_video_attempt(attempt_id)
        if attempt is None:
            attempt = store.start_video_attempt(
                attempt_id, mode="local_assets", initiative_id=creator_id,
            )
        if attempt["status"] == "queued":
            attempt = store.mark_video_attempt_running(attempt_id)
        attempt = store.complete_video_attempt(
            attempt_id, initiative_id=creator_id,
            product_id=result["initiative_id"], version_id=result["version_id"],
            mp4_sha256=result["mp4_sha256"], package_sha256=result["package_sha256"],
            manifest_sha256=result["manifest_sha256"],
        )
    result["video_attempt_id"] = attempt_id
    result["video_attempt_status"] = attempt["status"]
    result["clip_provenance"] = clip_provenance
    return result
