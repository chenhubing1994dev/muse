"""Build versioned Muse video products from user-provided local assets.

This is an asset-assembly product builder, not a prompt-to-video generator. It
reuses the existing FFmpeg factory and never installs models or calls cloud APIs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import math
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from muse.media import discover_media_tools
from muse.video_factory import AUDIO_EXTENSIONS, assemble_video_from_folder, discover_video_assets
from muse.video_qa import inspect_video_file

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


def _safe_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


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

    BUILDER_VERSION = 7
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
        inventory = discover_media_tools()
        tools = {item.tool_id: item.executable for item in inventory.tools}
        ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
        if not ffmpeg or not ffprobe:
            raise VideoProductError("FFmpeg and FFprobe must both be installed")
        source_facts = [{"name": path.name, "bytes": path.stat().st_size, "sha256": _hash_file(path)} for path in assets]
        audio_fact = ({"name": audio.name, "bytes": audio.stat().st_size, "sha256": _hash_file(audio)} if audio else None)
        identity = hashlib.sha256(("muse-video:" + source_key + ":" + title.strip()).encode("utf-8")).hexdigest()[:32]
        settings = {"aspect_ratio": aspect_ratio, "fps": fps, "image_seconds": float(image_seconds), "encoder": encoder, "audio_mode": audio_mode, "motion_profile": motion_profile}
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
                image_seconds=float(image_seconds),
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
            (staging / "qa-report.json").write_bytes(_safe_json(qa_report))
            storyboard = {
                "schema_version": 1, "title": title.strip(), "production_mode": "local_asset_assembly",
                "final_output": {key: result[key] for key in ("duration_seconds", "frames", "width", "height", "fps", "has_audio", "bytes") if key in result},
                "ai_generation": "not_requested", "source_folder": source_key, "settings": settings,
                "motion": motion,
                "scenes": [{"order": index, "source_asset": path.name, "source_sha256": fact["sha256"],
                            "generation_status": "imported_asset"} for index, (path, fact) in enumerate(zip(assets, source_facts), start=1)],
            }
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

    def build_from_prompt(
        self, title: str, prompt: str, *, negative_prompt: str = "",
        aspect_ratio: str = "16:9", duration_seconds: float = 2.0,
        steps: int = 8, seed: int | None = None, timeout_seconds: int = 1800,
        motion_profile: str = "none",
        creator_initiative_id: str | None = None, evolution_cycle_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate one local Wan 2.1 clip and package it as a versioned Muse product."""
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
        from muse.wan_video import generate_wan21_t2v

        settings = {
            "model": "Wan 2.1 T2V 1.3B", "aspect_ratio": aspect_ratio,
            "duration_seconds": float(duration_seconds), "steps": steps, "seed": seed,
            "fps": 12, "resolution_tier": "preview_512", "negative_prompt": negative_prompt,
            "vae_decode_mode": "tiled_temporal_8", "motion_profile": motion_profile,
        }
        identity = hashlib.sha256(("muse-video-ai:" + title.strip() + ":" + prompt.strip()).encode("utf-8")).hexdigest()[:32]
        fingerprint = hashlib.sha256(_safe_json({"builder_version": self.BUILDER_VERSION, "prompt": prompt.strip(), "settings": settings, "motion_profile": motion_profile})).hexdigest()
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
        initiative_root.mkdir(parents=True, exist_ok=True)
        if initiative_root.is_symlink() or initiative_root.resolve().parent != products_root:
            raise VideoProductError("AI video product destination escapes product root")
        staging = Path(tempfile.mkdtemp(prefix=".wan-video-product-", dir=initiative_root))
        try:
            base_rendered = staging / "base-video.mp4"
            rendered = staging / "video.mp4"
            generated = generate_wan21_t2v(
                prompt.strip(), base_rendered, negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio, duration_seconds=float(duration_seconds),
                steps=steps, seed=seed, timeout_seconds=timeout_seconds,
                resource_db=self.workspace / "data" / "gpu-resources.sqlite3",
                workspace=self.workspace,
            )
            if generated.get("status") != "completed" or not base_rendered.is_file():
                raise VideoProductError("local Wan generation did not return a completed MP4")
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
        narration_path: str | Path | None = None, narration_script: str | None = None,
        subtitle_path: str | Path | None = None, narration_asr: dict[str, Any] | None = None,
        background_music_path: str | Path | None = None, motion_profile: str = "none",
        creator_initiative_id: str | None = None, evolution_cycle_id: str | None = None,
    ) -> dict[str, Any]:
        """Package a completed local AI MP4, optionally adding a verified narration track."""
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
                if not isinstance(scene, dict) or set(scene) - {"order", "prompt", "prompt_id", "seed", "generation_status", "source_file"}:
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
                normalized_scenes.append({
                    "order": order, "prompt": scene_prompt.strip(), "generation_status": "generated",
                    "prompt_id": prompt_id, "seed": seed, **({"source_file": source_file} if source_file else {}),
                })
            if len(_safe_json(normalized_scenes)) > 32 * 1024:
                raise ValueError("scene_records exceeds 32 KiB")

        source = Path(source_path).expanduser()
        if any(parent.is_symlink() for parent in (source, *source.parents)):
            raise VideoProductError("generated MP4 source must not contain symlinks")
        if source.suffix.casefold() != ".mp4" or not source.is_file():
            raise VideoProductError("generated clip source must be a regular MP4 file")
        source = source.resolve(strict=True)
        source_size = source.stat().st_size
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
                "audio_sha256", "transcript", "segment_count", "comparison",
                "human_listening_review_required",
            ) if key in narration_asr}
            if len(_safe_json(asr_fact)) > 64 * 1024:
                raise ValueError("narration_asr metadata exceeds 64 KiB")


        metadata_keys = {
            "status", "prompt_id", "model", "seed", "aspect_ratio", "fps",
            "duration_seconds", "frames", "width", "height", "steps", "bytes", "has_audio",
        }
        metadata = {key: generation_metadata[key] for key in metadata_keys if key in generation_metadata}
        try:
            metadata_bytes = _safe_json(metadata)
        except (TypeError, ValueError):
            raise ValueError("generation_metadata must contain bounded JSON values") from None
        if len(metadata_bytes) > 16 * 1024:
            raise ValueError("generation_metadata exceeds 16 KiB")
        settings = {key: value for key, value in metadata.items() if key != "status"}
        identity = hashlib.sha256(("muse-video-ai:" + title.strip() + ":" + prompt.strip()).encode("utf-8")).hexdigest()[:32]
        fingerprint = hashlib.sha256(_safe_json({
            "builder_version": self.BUILDER_VERSION, "prompt": prompt.strip(),
            "negative_prompt": negative_prompt, "generation_metadata": metadata,
            "scene_records": normalized_scenes, "generated_clip_sha256": source_sha256,
            "narration_asset": narration_fact, "narration_script": narration_script,
            "motion_profile": motion_profile, "subtitle_asset": subtitle_fact,
            "narration_asr": asr_fact, "background_music_asset": background_music_fact,
        })).hexdigest()
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
                        "[1:a]aresample=48000,apad,asplit=2[voice_sc][voice_mix];"
                        "[2:a]aresample=48000,volume=0.22[music];"
                        "[music][voice_sc]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=450[ducked];"
                        "[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                        "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest",
                        "-movflags", "+faststart", str(narrated),
                    ])
                else:
                    mux_args.extend([
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-af", "apad", "-shortest",
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
                    **({"subtitles": subtitle_fact, "asr": asr_fact} if subtitles else {}),
                } if narration else None),
                "background_music": ({
                    **background_music_fact, "archive_path": background_music_member,
                    "mix": {"method": "ffmpeg_sidechaincompress", "music_gain": 0.22,
                            "threshold": 0.025, "ratio": 8, "attack_ms": 20, "release_ms": 450},
                } if background_music_fact else None),
                "scenes": normalized_scenes,
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
                "subtitles": subtitle_fact, "narration_asr": asr_fact,
                "background_music": ({
                    **background_music_fact, "archive_path": background_music_member,
                    "mix": {"method": "ffmpeg_sidechaincompress", "music_gain": 0.22,
                            "threshold": 0.025, "ratio": 8, "attack_ms": 20, "release_ms": 450},
                } if background_music_fact else None),
                "source_assets": [], "audio_asset": narration_fact,
                "narration": ({
                    "script": narration_script, "language": "zh-HK",
                    "audio_file": "narration/voiceover" + narration.suffix.casefold(),
                    **({"subtitles_file": "subtitles.srt"} if subtitles else {}),
                    **({"asr_status": asr_fact["status"]} if asr_fact else {}),
                    **({"background_music_file": background_music_member} if background_music_member else {}),
                } if narration else None),
                "execution": {
                    "arbitrary_code_executed": False, "network_access": False,
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
        asr_model_dir: str | Path | None = None, **package_options: Any,
    ) -> dict[str, Any]:
        """Synthesize Cantonese narration and optionally verify it with local ASR subtitles.

        Both models must already be provisioned locally. ASR is opt-in until the
        pinned model and voice have passed transcript and human listening review.
        """
        if not isinstance(narration_script, str) or not narration_script.strip() or len(narration_script) > 20_000:
            raise ValueError("narration_script must contain 1-20000 characters")
        if any(key in package_options for key in ("narration_path", "narration_script", "subtitle_path", "narration_asr")):
            raise ValueError("narration and ASR assets are managed by the Cantonese narration wrapper")
        from muse.cantonese_tts import synthesize_cantonese

        with tempfile.TemporaryDirectory(prefix=".muse-cantonese-", dir=self.workspace) as temporary_dir:
            audio_path = Path(temporary_dir) / "voiceover.wav"
            synthesis = synthesize_cantonese(
                narration_script, audio_path, model_dir=model_dir, quality="duration_filter",
            )
            asr_result = None
            subtitle_path = None
            if asr_model_dir is not None:
                from muse.cantonese_asr import transcribe_cantonese
                subtitle_path = Path(temporary_dir) / "subtitles.srt"
                asr_result = transcribe_cantonese(
                    audio_path, model_dir=asr_model_dir, output_srt=subtitle_path,
                    reference_text=narration_script,
                )
            packaged = self.package_generated_clip(
                title, prompt, source_path, narration_path=audio_path,
                narration_script=narration_script, subtitle_path=subtitle_path,
                narration_asr=asr_result, **package_options,
            )
        packaged["narration_synthesis"] = {
            key: synthesis[key] for key in (
                "status", "engine", "model_repo", "model_revision", "license",
                "language", "sample_rate", "channels", "frames", "duration_seconds", "bytes",
            ) if key in synthesis
        }
        if asr_result is not None:
            packaged["narration_asr"] = asr_result
        return packaged

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
                "product_id": result["initiative_id"],
                "version_id": result["version_id"],
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
                                "storyboard_sha256": result["storyboard_sha256"],
                                "readme_sha256": result["readme_sha256"],
                                "source_asset_hashes": source_hashes,
                                "generation_metadata": generation_summary,
                                "validation": result["validation_summary"],
                                "quality_gate": result.get("quality_gate"),
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
        legacy_expected = {"README.md", "storyboard.json", "video.mp4", "product.zip"}
        qa_expected = legacy_expected | {"qa-report.json"}
        subtitle_expected = qa_expected | {"subtitles.srt"}
        files = manifest.get("files")
        if (manifest.get("product_kind") != "video_creator" or manifest.get("initiative_id") != initiative_id
                or manifest.get("version_id") != version or not isinstance(files, dict)
                or set(files) not in (legacy_expected, qa_expected, subtitle_expected)):
            raise VideoProductError("video product manifest is invalid")

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
            "background_music": background_music_fact,
            "package_sha256": files["product.zip"], "mp4_sha256": files["video.mp4"],
            "manifest_sha256": _hash_file(manifest_path),
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
