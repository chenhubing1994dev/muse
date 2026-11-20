"""Build account-safe manual publishing packs from validated Muse video products."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from muse.video_product_builder import VideoProductBuilder, VideoProductError


DEFAULT_PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels", "bilibili")
VIRAL_MONETIZATION_PLATFORMS_9X16 = ("douyin", "wechat_channels", "youtube_shorts", "tiktok", "bilibili")
VIRAL_MONETIZATION_PLATFORMS_16X9 = ("youtube", "wechat_channels", "bilibili")
_PLATFORM_COPY: dict[str, dict[str, Any]] = {
    "youtube_shorts": {
        "display_name": "YouTube Shorts",
        "hashtags": ["#AI", "#事实核验", "#人工智能", "#知识科普"],
        "tags": ["AI", "事实核验", "生成式人工智能", "信息素养"],
    },
    "youtube": {
        "display_name": "YouTube (16:9 横屏主频道)",
        "hashtags": ["#AIWorkflow", "#FactChecking", "#AutonomousAgents", "#Productivity"],
        "tags": ["AI workflow", "autonomous research", "counter evidence", "content creation"],
    },
    "douyin": {
        "display_name": "抖音 (Douyin)",
        "hashtags": ["#人工智能", "#AI进化", "#认知升级", "#内容创作", "#思维模型"],
        "tags": ["人工智能", "AI工具", "自我进化", "内容创作", "思维升级"],
    },
    "wechat_channels": {
        "display_name": "微信视频号",
        "hashtags": ["#AI进化", "#知识管理", "#认知升级", "#自媒体干货", "#生产力"],
        "tags": ["AI工具", "知识管理", "自我提升", "视频创作"],
    },
    "tiktok": {
        "display_name": "TikTok",
        "hashtags": ["#AI", "#人工智能", "#事实查证", "#知识科普"],
        "tags": ["AI", "人工智能", "事实查证"],
    },
    "instagram_reels": {
        "display_name": "Instagram Reels",
        "hashtags": ["#AI", "#AIFacts", "#FactChecking", "#人工智能", "#知识科普"],
        "tags": ["AI", "fact checking", "media literacy"],
    },
    "bilibili": {
        "display_name": "哔哩哔哩",
        "hashtags": [],
        "tags": ["人工智能", "AI", "事实核验", "知识科普", "信息素养"],
    },
}


class VideoPublishPackageError(RuntimeError):
    """A manual publishing package could not be built safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoPublishPackageError(f"{label} is missing or invalid JSON") from exc
    if not isinstance(value, dict):
        raise VideoPublishPackageError(f"{label} must contain a JSON object")
    return value


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    root_real = root.resolve(strict=True)
    if candidate.is_symlink():
        raise VideoPublishPackageError("symlinks are not allowed in publish-package inputs")
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(root_real)
    except (OSError, ValueError) as exc:
        raise VideoPublishPackageError("an input path escapes the Muse workspace") from exc
    cursor = root_real
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise VideoPublishPackageError("symlinks are not allowed in publish-package inputs")

def _project_file(root: Path, raw: str, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise VideoPublishPackageError(f"{label} path is missing")
    normalized = raw.strip().replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts or any(":" in part for part in pure.parts):
        raise VideoPublishPackageError(f"{label} path is not a safe workspace-relative path")
    lexical = root.joinpath(*pure.parts)
    _reject_symlink_components(root, lexical)
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise VideoPublishPackageError(f"{label} is missing or escapes the workspace") from exc
    if not resolved.is_file():
        raise VideoPublishPackageError(f"{label} is not a regular file")
    return resolved


def _workspace_directory(root: Path, value: str | Path, label: str) -> Path:
    raw = Path(value)
    lexical = raw if raw.is_absolute() else root / raw
    lexical = Path(lexical.absolute())
    _reject_symlink_components(root, lexical)
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise VideoPublishPackageError(f"{label} is missing or escapes the workspace") from exc
    if not resolved.is_dir():
        raise VideoPublishPackageError(f"{label} is not a directory")
    return resolved


def _platform_draft(
    platform: str,
    *,
    title: str,
    source_notes: dict[str, Any],
    metadata: dict[str, Any],
    voiceover: str,
    audio_description: str,
    master_aspect_ratio: str = "9:16",
    storyboard: dict[str, Any] | None = None,
    visual_provenance: str | None = None,
) -> dict[str, Any]:
    profile = _PLATFORM_COPY[platform]
    custom_copy = (
        (storyboard.get("platform_copy") or {}).get(platform)
        if isinstance(storyboard, dict) and isinstance(storyboard.get("platform_copy"), dict)
        else None
    )
    doi, pdf = source_notes.get("doi"), source_notes.get("pdf")
    sections = source_notes.get("relevant_sections", {})
    source_title = str(source_notes.get("title") or "Source details in source-notes.json")
    publisher = str(source_notes.get("publisher") or "Not recorded")
    published_at = str(source_notes.get("published_at") or "Not recorded")
    section_labels = list(sections.keys()) if isinstance(sections, dict) else []
    hook = (
        str(custom_copy["caption"])
        if isinstance(custom_copy, dict) and custom_copy.get("caption")
        else "AI 说得很自信，不代表它一定正确。记住三步：拆开主张、查原始来源、主动寻找反证。"
    )
    description = (
        str(custom_copy["description"])
        if isinstance(custom_copy, dict) and custom_copy.get("description")
        else (
            f"{hook}\n\n"
            f"资料来源：{source_title}；{publisher}；发布日期：{published_at}。\n"
            f"参考章节/行动：{', '.join(section_labels) if section_labels else '详见随包 source-notes.json'}。\n"
            f"原始资料：{doi or '请查看随包 source-notes.json'}\n"
            f"PDF：{pdf or '请查看随包 source-notes.json'}\n\n"
            f"内容边界：{source_notes.get('editorial_boundary', '本片的编辑建议不应被误认为来源机构的认证或逐字结论')}\n"
            "本片为知识分享，不构成对所有 AI 系统准确率的统计结论。"
        )
    )
    pinned_comment = (
        str(custom_copy["pinned_comment"])
        if isinstance(custom_copy, dict) and custom_copy.get("pinned_comment")
        else (
            "资料来源与章节见简介及随包 source-notes.json。三步查证法是本片的实用编辑建议；"
            "主动寻找反证不是来源文件的逐字引用，也不代表来源机构认证了本片。"
        )
    )
    hashtags = list(custom_copy["hashtags"]) if isinstance(custom_copy, dict) and isinstance(custom_copy.get("hashtags"), list) else profile["hashtags"]
    tags = list(custom_copy["tags"]) if isinstance(custom_copy, dict) and isinstance(custom_copy.get("tags"), list) else profile["tags"]
    title_variants = list(custom_copy["title_variants"]) if isinstance(custom_copy, dict) and isinstance(custom_copy.get("title_variants"), list) else [title]
    raw_citations = source_notes.get("citations")
    if isinstance(raw_citations, list) and raw_citations:
        citations = raw_citations[:8]
    else:
        citations = [{"title": source_title, "publisher": publisher, "doi": doi, "pdf": pdf}]
    technical = {
        "master_aspect_ratio": master_aspect_ratio,
        "width": metadata.get("width"),
        "height": metadata.get("height"),
        "fps": metadata.get("fps"),
        "duration_seconds": metadata.get("duration_seconds"),
        "video_codec": metadata.get("codec"),
        "audio_codec": metadata.get("audio_codec"),
        "audio_sample_rate": metadata.get("audio_sample_rate"),
        "profile_version": 2 if master_aspect_ratio == "16:9" or platform in {"youtube", "douyin", "wechat_channels"} else 1,
        "technical_profile_status": "manual_uploader_and_account_specific_check_required",
        "note": "This is a metadata handoff profile, not a certification of current platform upload limits or account eligibility.",
    }
    return {
        "schema_version": 1,
        "profile_version": 1,
        "platform_key": platform,
        "platform_name": profile["display_name"],
        "release_state": "draft_only",
        "title": title,
        "title_variants": title_variants,
        "caption": hook,
        "description": description,
        "hashtags": hashtags,
        "tags": tags,
        "suggested_pinned_comment": pinned_comment,
        "source_citations": citations,
        "assets": {"video": "video.mp4", "cover": "cover.jpg", "subtitles": "subtitles.srt"},
        "technical_profile": technical,
        "production_provenance": {
            "visual_source": visual_provenance or "Local Wan 2.1 generated footage, re-edited with FFmpeg",
            "voiceover": voiceover,
            "audio": audio_description,
        },
        "compliance_review": {
            "ai_generated_visuals_and_platform_label": {
                "status": "review_required",
                "note": "Synthetic voiceover or programmatic/AI media is present; select the applicable platform AI-content disclosure label before publishing.",
            },
            "factual_claims_and_context": {"status": "editorial_review_required"},
            "visual_and_model_rights": {"status": "review_required"},
            "privacy_and_person_release": {"status": "review_required"},
            "music_and_audio_rights": {"status": "review_required", "note": audio_description},
            "sponsorship_affiliate_and_product_placement": {"status": "confirm_before_upload"},
            "community_rules_and_upload_specifications": {"status": "manual_review_required"},
            "account_authorization": {"status": "not_connected"},
        },
        "platform_video_id": None,
        "published_url": None,
        "publish_api_called": False,
    }


def _release_quality_report(
    *, product_manifest: dict[str, Any], qa_warnings: list[dict[str, Any]],
    subtitles: Path, video: Path,
) -> dict[str, Any]:
    """Summarize evidence without promoting missing human reviews to approvals."""
    review = product_manifest.get("subtitle_review")
    subtitle_status = "not_recorded"
    if isinstance(review, dict):
        approved_by = review.get("approved_by")
        approved_at = review.get("approval_timestamp")
        evidence_matches = (
            review.get("schema_version") == 1
            and review.get("status") == "approved"
            and review.get("human_approved") is True
            and isinstance(approved_by, str) and bool(approved_by.strip())
            and isinstance(approved_at, str) and bool(approved_at.strip())
            and review.get("approved_srt_sha256") == _sha256(subtitles)
            and review.get("video_sha256") == _sha256(video)
            and isinstance(review.get("review_sha256"), str)
            and len(review["review_sha256"]) == 64
        )
        subtitle_status = "verified_human_approval" if evidence_matches else "evidence_mismatch_or_incomplete"

    checks = {
        "technical_quality": {"status": "passed", "evidence": "qa-report.json"},
        "visual_anomaly_review": {
            "status": "pending_human_review" if qa_warnings else "heuristics_only_review_required",
            "warning_count": len(qa_warnings),
            "warning_codes": sorted({item["code"] for item in qa_warnings if isinstance(item.get("code"), str)}),
        },
        "subtitle_review": {"status": subtitle_status, "evidence": "muse-product-manifest.json:subtitle_review"},
        "editorial_and_fact_review": {"status": "pending_human_review"},
        "visual_and_model_rights_review": {"status": "pending_human_review"},
        "privacy_and_person_release_review": {"status": "pending_human_review"},
        "music_and_audio_rights_review": {"status": "pending_human_review"},
        "ai_generated_content_disclosure": {"status": "pending_platform_specific_review"},
        "sponsorship_affiliate_and_product_placement": {"status": "confirmation_required"},
        "platform_upload_specifications": {"status": "manual_account_specific_check_required"},
        "account_authorization": {"status": "not_connected"},
    }
    blockers = [name for name, item in checks.items() if item["status"] != "passed"]
    return {
        "schema_version": 1,
        "overall_status": "blocked_pending_human_review" if blockers else "ready_for_separate_release_authorization",
        "can_publish": False,
        "technical_qa_is_not_release_approval": True,
        "checks": checks,
        "blocking_items": blockers,
        "publish_api_called": False,
    }


def build_publish_package(
    workspace: str | Path,
    source_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    platforms: Sequence[str] = DEFAULT_PLATFORMS,
) -> dict[str, Any]:
    """Create a verifiable manual handoff package; never logs in or publishes."""
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise VideoPublishPackageError("workspace must be an existing directory")
    source = _workspace_directory(root, source_dir, "source directory")
    selected = tuple(dict.fromkeys(platforms))
    if not selected or any(item not in _PLATFORM_COPY for item in selected):
        raise VideoPublishPackageError("platforms must be selected from the supported metadata profiles")

    generation_path = _project_file(root, (source / "generation.json").relative_to(root).as_posix(), "generation metadata")
    storyboard_path = _project_file(root, (source / "storyboard.json").relative_to(root).as_posix(), "storyboard")
    source_notes_path = _project_file(root, (source / "source-notes.json").relative_to(root).as_posix(), "source notes")
    generation = _load_object(generation_path, "generation.json")
    storyboard = _load_object(storyboard_path, "storyboard.json")
    source_notes = _load_object(source_notes_path, "source-notes.json")
    if generation.get("status") != "completed":
        raise VideoPublishPackageError("generation status is not completed")

    mp4_info = generation.get("mp4")
    cover_info = generation.get("cover")
    product_info = generation.get("product")
    if not all(isinstance(item, dict) for item in (mp4_info, cover_info, product_info)):
        raise VideoPublishPackageError("generation metadata lacks MP4, cover or product provenance")
    video = _project_file(root, mp4_info.get("path"), "master MP4")
    cover = _project_file(root, cover_info.get("path"), "cover")
    subtitles = _project_file(root, (source / "subtitles.srt").relative_to(root).as_posix(), "subtitles")
    readme = _project_file(root, (source / "README.md").relative_to(root).as_posix(), "README")

    if not subtitles.stat().st_size:
        raise VideoPublishPackageError("subtitles file is empty")
    if not isinstance(mp4_info.get("sha256"), str) or _sha256(video) != mp4_info["sha256"]:
        raise VideoPublishPackageError("master MP4 SHA-256 does not match generation metadata")
    if mp4_info.get("bytes") is not None and int(mp4_info["bytes"]) != video.stat().st_size:
        raise VideoPublishPackageError("master MP4 byte count does not match generation metadata")
    if cover.stat().st_size == 0:
        raise VideoPublishPackageError("cover file is empty")
    if not isinstance(cover_info.get("sha256"), str) or _sha256(cover) != cover_info["sha256"]:
        raise VideoPublishPackageError("cover SHA-256 does not match generation metadata")
    try:
        width, height = int(mp4_info["width"]), int(mp4_info["height"])
        fps = float(mp4_info["fps"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoPublishPackageError("master MP4 dimensions or frame rate are missing") from exc
    if height > width and abs((width / height) - (9 / 16)) <= 0.02:
        master_aspect_ratio = "9:16"
    elif width > height and abs((width / height) - (16 / 9)) <= 0.02:
        master_aspect_ratio = "16:9"
    else:
        raise VideoPublishPackageError("publish-pack profile currently accepts portrait 9:16 or landscape 16:9 masters only")
    if mp4_info.get("full_decode") != "passed":
        raise VideoPublishPackageError("master MP4 has no successful full-decode evidence")

    initiative_id, version_id = product_info.get("initiative_id"), product_info.get("version_id")
    if not isinstance(initiative_id, str) or not isinstance(version_id, str):
        raise VideoPublishPackageError("product identity is missing")
    verified = VideoProductBuilder.verify_path(root / "data" / "products", initiative_id, version_id)
    gate = verified.get("quality_gate")
    if (not verified.get("validated") or verified.get("status") != "testing"
            or not isinstance(gate, dict) or gate.get("status") != "passed"
            or gate.get("ready_to_publish") is not False
            or verified.get("mp4_sha256") != mp4_info["sha256"]):
        raise VideoPublishPackageError("only an integrity-verified MP4 with a passed technical QA gate can enter this draft pack")
    product_dir = _workspace_directory(root, product_info.get("relative_path"), "Muse product directory")
    qa_report = _project_file(root, (product_dir / "qa-report.json").relative_to(root).as_posix(), "QA report")
    product_zip = _project_file(root, (product_dir / "product.zip").relative_to(root).as_posix(), "Muse product ZIP")
    product_manifest = _project_file(root, (product_dir / "manifest.json").relative_to(root).as_posix(), "Muse product manifest")
    qa_document = _load_object(qa_report, "QA report")
    product_manifest_document = _load_object(product_manifest, "Muse product manifest")
    raw_warnings = qa_document.get("warnings", [])
    if not isinstance(raw_warnings, list):
        raise VideoPublishPackageError("QA report warnings must be a list")
    qa_warnings = [item for item in raw_warnings if isinstance(item, dict)]
    qa_warning_codes = sorted({item["code"] for item in qa_warnings if isinstance(item.get("code"), str)})
    release_quality_report = _release_quality_report(
        product_manifest=product_manifest_document, qa_warnings=qa_warnings,
        subtitles=subtitles, video=video,
    )

    profile = product_info.get("video_metadata", {})
    if isinstance(profile, dict) and profile.get("aspect_ratio") not in (None, master_aspect_ratio):
        raise VideoPublishPackageError(f"publish-pack profile requires a {master_aspect_ratio} product manifest")
    title = str(generation.get("title") or storyboard.get("title") or product_info.get("title") or "").strip()
    if not title:
        raise VideoPublishPackageError("video title is missing")
    voiceover = str(storyboard.get("voiceover") or "not recorded")
    audio_description = str(storyboard.get("audio") or "audio provenance not recorded")
    visual_provenance = str(generation.get("visual_provenance") or storyboard.get("production_mode") or "") or None
    drafts = {
        platform: _platform_draft(
            platform, title=title, source_notes=source_notes, metadata=mp4_info,
            voiceover=voiceover, audio_description=audio_description,
            master_aspect_ratio=master_aspect_ratio, storyboard=storyboard,
            visual_provenance=visual_provenance,
        )
        for platform in selected
    }

    target = Path(output_dir) if output_dir is not None else source / "publish-package-v1"
    target_lexical = target if target.is_absolute() else root / target
    if target_lexical.is_symlink():
        raise VideoPublishPackageError("output directory cannot be a symlink")
    target_lexical = target_lexical.resolve(strict=False)
    try:
        target_lexical.relative_to(root)
    except ValueError as exc:
        raise VideoPublishPackageError("output directory must remain inside the Muse workspace") from exc
    _reject_symlink_components(root, target_lexical.parent)
    if target_lexical.exists() or target_lexical.is_symlink():
        raise FileExistsError(f"refusing to overwrite publish package: {target_lexical}")
    target_lexical.parent.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(prefix=".video-publish-pack-", dir=target_lexical.parent))
    try:
        (staging / "platform-drafts").mkdir()
        copies = [
            (video, "video.mp4"), (cover, "cover.jpg"), (subtitles, "subtitles.srt"),
            (storyboard_path, "storyboard.json"), (source_notes_path, "source-notes.json"),
            (generation_path, "generation.json"), (readme, "production-readme.md"),
            (qa_report, "qa-report.json"), (product_zip, "muse-product.zip"),
            (product_manifest, "muse-product-manifest.json"),
        ]
        for src, name in copies:
            shutil.copyfile(src, staging / name)
        for platform, draft in drafts.items():
            (staging / "platform-drafts" / f"{platform}.json").write_bytes(_json_bytes(draft))

        release_checklist = {
            "schema_version": 1,
            "release_state": "draft_only",
            "can_publish": False,
            "technical_quality": {"status": "passed", "report": "qa-report.json"},
            "release_quality_report": "release-quality-report.json",
            "visual_anomaly_review": {
                "status": "required",
                "warning_count": len(qa_warnings),
                "warning_codes": qa_warning_codes,
                "warnings": qa_warnings,
                "note": "Black, frozen-frame and silence detections are heuristic; review intentional dark cards, pauses and static shots.",
            },
            "editorial_and_fact_review": "pending",
            "visual_and_model_rights_review": "pending",
            "privacy_and_person_release_review": "pending",
            "ai_generated_content_disclosure": "pending_platform_specific_review",
            "sponsorship_affiliate_and_product_placement": "must_be_confirmed",
            "platform_upload_specifications": "manual_account_specific_check_required",
            "account_authorization": "not_connected",
            "official_publish_api": "not_called",
            "real_platform_metrics_and_revenue": "not_available_until_authorized_data_import",
        }
        (staging / "release-checklist.json").write_bytes(_json_bytes(release_checklist))
        (staging / "release-quality-report.json").write_bytes(_json_bytes(release_quality_report))
        readme_text = (
            f"# {title}\n\n"
            f"- 画幅与技术指标：{master_aspect_ratio}（{width}×{height}, {fps:g} FPS）；技术 QA：passed；Muse 产品状态：testing。\n"
            f"- 目标平台草稿：{', '.join(selected)}。\n"            "- 视频、封面、字幕、分镜、来源说明和 Muse 产品 ZIP 均随包提供。\n"
            "- 各平台可直接复制的标题备选、标签、置顶评论与简介，见 `platform-drafts/<platform>.json`。\n"
            "- 发布前必须复核事实、来源、视觉/模型权利、隐私、AI 合成披露、商业关系披露及平台上传规格。\n"
            "- 未登录平台账号，未调用发布 API，未生成平台视频 ID 或发布链接。\n"
        )
        (staging / "README.md").write_text(readme_text, encoding="utf-8")

        file_hashes = {
            path.relative_to(staging).as_posix(): _sha256(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema_version": 1,
            "package_type": "muse_manual_platform_publish_pack",
            "profile_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "release_state": "draft_only",
            "can_publish": False,
            "initiative_id": initiative_id,
            "version_id": version_id,
            "evolution_cycle_id": generation.get("evolution_cycle_id"),
            "source_video_sha256": mp4_info["sha256"],
            "muse_product_zip_sha256": _sha256(product_zip),
            "release_quality_report": release_quality_report,
            "release_quality_status": release_quality_report["overall_status"],            "platforms": list(selected),
            "files": file_hashes,
        }
        (staging / "manifest.json").write_bytes(_json_bytes(manifest))
        bundle = staging / "publish-package.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file() and path != bundle:
                    archive.write(path, path.relative_to(staging).as_posix())
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            expected = set(file_hashes) | {"manifest.json"}
            if len(names) != len(expected) or set(names) != expected or archive.testzip() is not None:
                raise VideoPublishPackageError("publish package ZIP validation failed")
        bundle_hash = _sha256(bundle)
        staging.rename(target_lexical)
        final_bundle = target_lexical / "publish-package.zip"
        return {
            "status": "completed",
            "release_state": "draft_only",
            "can_publish": False,
            "output_dir": target_lexical.relative_to(root).as_posix(),
            "package_path": final_bundle.relative_to(root).as_posix(),
            "package_bytes": final_bundle.stat().st_size,
            "package_sha256": bundle_hash,
            "platforms": list(selected),
            "files": len(file_hashes) + 1,
            "technical_qa": "passed",
            "review_warning_count": len(qa_warnings),
            "review_warning_codes": qa_warning_codes,
            "release_quality_status": release_quality_report["overall_status"],
            "release_blocker_count": len(release_quality_report["blocking_items"]),
            "official_publish_api_called": False,
            "account_authorization": "not_connected",
        }
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Muse manual multi-platform video publish package")
    parser.add_argument("source_dir", help="existing production directory containing generation.json")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output")
    parser.add_argument("--platforms", nargs="+", choices=tuple(_PLATFORM_COPY), default=list(DEFAULT_PLATFORMS))
    args = parser.parse_args(argv)
    try:
        result = build_publish_package(args.workspace, args.source_dir, output_dir=args.output, platforms=args.platforms)
    except (OSError, ValueError, VideoProductError, VideoPublishPackageError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
