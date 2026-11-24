"""Create an offline, rights-aware social content kit from an existing Muse publish pack.

This is a copy/scheduling/attribution draft adapter. It does not publish, log in,
contact customers, fetch analytics, or assert that revenue was earned.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from muse.social_campaign_tracking import build_social_tracking_manifest


class SocialContentKitError(ValueError):
    """Raised when a source publish pack or social draft is unsafe or incomplete."""


DEFAULT_PLATFORMS = (
    "youtube_shorts",
    "tiktok",
    "instagram_reels",
    "bilibili",
    "xiaohongshu",
)
_SUPPORTED_PLATFORMS = {
    "youtube_shorts", "youtube", "tiktok", "instagram_reels",
    "bilibili", "douyin", "wechat_channels", "xiaohongshu",
}
_PLATFORM_CTA = {
    "youtube_shorts": "订阅并留言：你会先核查哪一项？",
    "youtube": "在评论区分享你遇过的 AI 信息核验难题。",
    "tiktok": "留言分享你遇过的 AI 说法，我们一起查来源。",
    "instagram_reels": "先收藏这份三步清单，下次核查时再打开。",
    "bilibili": "收藏本期清单，并在评论区补充你的查证方法。",
    "douyin": "评论说说你会怎样核实这条信息。",
    "wechat_channels": "转发给需要核验信息的朋友，并补充你的经验。",
    "xiaohongshu": "收藏这份查证清单，遇到类似说法时逐项核对。",
}
_PLATFORM_NAMES = {
    "youtube_shorts": "YouTube Shorts",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram_reels": "Instagram Reels",
    "bilibili": "哔哩哔哩",
    "douyin": "抖音",
    "wechat_channels": "微信视频号",
    "xiaohongshu": "小红书",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_file(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise SocialContentKitError(f"{label} path is missing")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or any(":" in part for part in pure.parts):
        raise SocialContentKitError(f"{label} path is not a safe relative path")
    candidate = root.joinpath(*pure.parts)
    cursor = root
    try:
        for part in pure.parts:
            cursor = cursor / part
            info = cursor.lstat()
            if cursor.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400):
                raise SocialContentKitError(f"{label} path contains a symlink or reparse point")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except SocialContentKitError:
        raise
    except (OSError, ValueError) as exc:
        raise SocialContentKitError(f"{label} is missing or escapes its package") from exc
    if not resolved.is_file():
        raise SocialContentKitError(f"{label} is not a regular file")
    return resolved

def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SocialContentKitError(f"{label} is missing or invalid JSON") from exc
    if not isinstance(value, dict):
        raise SocialContentKitError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _verify_source_package(workspace: Path, package_relative: str) -> dict[str, Any]:
    if not isinstance(package_relative, str) or not package_relative.strip():
        raise SocialContentKitError("publish_package_dir must be a relative directory path")
    package_dir = _safe_relative_file(workspace, package_relative + "/manifest.json", "publish package manifest").parent
    manifest = _load_object(package_dir / "manifest.json", "publish package manifest")
    if manifest.get("package_type") != "muse_manual_platform_publish_pack":
        raise SocialContentKitError("source is not a Muse manual platform publish package")
    if manifest.get("release_state") != "draft_only" or manifest.get("can_publish") is not False:
        raise SocialContentKitError("source publish package must remain draft_only and non-publishable")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise SocialContentKitError("source package manifest has no file hash map")
    for relative, expected in files.items():
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise SocialContentKitError("source package contains an invalid file digest")
        path = _safe_relative_file(package_dir, relative, "source package file")
        if _sha256(path) != expected:
            raise SocialContentKitError(f"source package file hash mismatch: {relative}")
    release_report = _load_object(
        _safe_relative_file(package_dir, "release-quality-report.json", "release quality report"),
        "release quality report",
    )
    if release_report.get("can_publish") is not False:
        raise SocialContentKitError("release quality report must explicitly block publishing")
    generation = _load_object(
        _safe_relative_file(package_dir, "generation.json", "generation metadata"),
        "generation metadata",
    )
    source_video_sha = manifest.get("source_video_sha256")
    if not isinstance(source_video_sha, str) or re.fullmatch(r"[0-9a-f]{64}", source_video_sha) is None:
        raise SocialContentKitError("source video digest is missing or invalid")
    video_hash = files.get("video.mp4")
    if video_hash != source_video_sha:
        raise SocialContentKitError("source video digest does not match the publish package file map")
    mp4 = generation.get("mp4")
    if not isinstance(mp4, dict) or mp4.get("sha256") != source_video_sha:
        raise SocialContentKitError("generation metadata does not bind the source video digest")
    duration = mp4.get("duration_seconds")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration)) or duration <= 0):
        raise SocialContentKitError("source video duration is missing or invalid")
    zip_path = _safe_relative_file(package_dir, "publish-package.zip", "publish package ZIP")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if archive.testzip() is not None:
                raise SocialContentKitError("source publish package ZIP failed CRC validation")
            expected_members = set(files) | {"manifest.json"}
            if set(archive.namelist()) != expected_members:
                raise SocialContentKitError("source publish package ZIP contents do not match its manifest")
            for relative, expected in files.items():
                if hashlib.sha256(archive.read(relative)).hexdigest() != expected:
                    raise SocialContentKitError(f"source publish package ZIP hash mismatch: {relative}")
            embedded_manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
            if embedded_manifest != manifest:
                raise SocialContentKitError("source publish package ZIP manifest does not match the on-disk manifest")
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise SocialContentKitError("source publish package ZIP is missing or invalid") from exc
    package_platforms = manifest.get("platforms")
    if not isinstance(package_platforms, list) or not package_platforms:
        raise SocialContentKitError("source publish package has no platform list")
    drafts: dict[str, dict[str, Any]] = {}
    for platform in package_platforms:
        if not isinstance(platform, str) or platform not in _SUPPORTED_PLATFORMS:
            raise SocialContentKitError("source package contains an unsupported platform identifier")
        draft_path = _safe_relative_file(
            package_dir, f"platform-drafts/{platform}.json", f"{platform} platform draft",
        )
        draft = _load_object(draft_path, f"{platform} platform draft")
        if draft.get("release_state") != "draft_only" or draft.get("publish_api_called") is not False:
            raise SocialContentKitError(f"{platform} source draft is not safely marked draft_only")
        drafts[platform] = draft
    return {
        "package_dir": package_dir,
        "manifest": manifest,
        "release_report": release_report,
        "generation": generation,
        "drafts": drafts,
        "duration_seconds": float(duration),
        "zip_sha256": _sha256(zip_path),
        "video_sha256": source_video_sha,
    }


def _schedule_entry(platform: str, schedule_by_platform: Mapping[str, str]) -> dict[str, Any]:
    value = schedule_by_platform.get(platform)
    if value is None:
        return {
            "status": "not_scheduled",
            "publish_at": None,
            "note": "No publishing time was supplied; choose a time using the account's own audience analytics.",
        }
    if not isinstance(value, str) or len(value) > 64:
        raise SocialContentKitError(f"schedule for {platform} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SocialContentKitError(f"schedule for {platform} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SocialContentKitError(f"schedule for {platform} must include a timezone")
    return {"status": "draft_schedule_only", "publish_at": parsed.isoformat(), "note": "Not submitted to the platform."}


def build_social_content_kit(
    workspace: str | Path,
    publish_package_dir: str,
    *,
    campaign: str,
    platforms: Sequence[str] = DEFAULT_PLATFORMS,
    destination_url: str | None = None,
    destination_url_owner_confirmed: bool = False,
    content_id_by_platform: Mapping[str, str] | None = None,
    schedule_by_platform: Mapping[str, str] | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Build copy, CTA, conservative caption guidance, schedule and UTM drafts.

    The source package must already be integrity-verified. This function only
    writes a separate metadata ZIP; it never copies or alters the video master.
    """
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise SocialContentKitError("workspace must be an existing directory")
    if (not isinstance(campaign, str) or not campaign.strip() or len(campaign) > 120
            or any(ord(char) < 32 for char in campaign)):
        raise SocialContentKitError("campaign must contain 1-120 printable characters")
    if isinstance(platforms, (str, bytes)) or not isinstance(platforms, Sequence):
        raise SocialContentKitError("platforms must be a sequence of identifiers")
    platform_values = list(platforms)
    if any(not isinstance(item, str) or item not in _SUPPORTED_PLATFORMS for item in platform_values):
        raise SocialContentKitError("platforms contains an unsupported or empty platform selection")
    selected = tuple(dict.fromkeys(platform_values))
    if not selected:
        raise SocialContentKitError("at least one platform is required")
    if not isinstance(schedule_by_platform, Mapping) and schedule_by_platform is not None:
        raise SocialContentKitError("schedule_by_platform must be a mapping")
    if not isinstance(content_id_by_platform, Mapping) and content_id_by_platform is not None:
        raise SocialContentKitError("content_id_by_platform must be a mapping")
    if set(schedule_by_platform or {}) - set(selected):
        raise SocialContentKitError("schedule_by_platform contains an unselected platform")
    if set(content_id_by_platform or {}) - set(selected):
        raise SocialContentKitError("content_id_by_platform contains an unselected platform")
    if not isinstance(destination_url_owner_confirmed, bool):
        raise SocialContentKitError("destination_url_owner_confirmed must be a boolean")
    if destination_url is not None and not destination_url_owner_confirmed:
        raise SocialContentKitError("confirm ownership/control of the destination URL before creating tracking links")
    source = _verify_source_package(root, publish_package_dir)
    drafts = source["drafts"]
    base = drafts.get("youtube_shorts") or drafts.get("tiktok") or next(iter(drafts.values()))
    tracking = None
    if destination_url is not None:
        content_labels = {
            platform: (content_id_by_platform or {}).get(platform, f"{campaign.strip()}-{platform}-v1")
            for platform in selected
        }
        tracking = build_social_tracking_manifest(
            destination_url,
            campaign=campaign.strip(),
            platforms=selected,
            content_by_platform=content_labels,
        )

    tracking_by_platform = {
        item["platform"]: item for item in (tracking or {}).get("platforms", [])
    }
    release_report = source["release_report"]
    blockers = release_report.get("blocking_items")
    if not isinstance(blockers, list):
        blockers = ["release_blocker_evidence_unavailable"]

    platform_items = []
    for platform in selected:
        draft = drafts.get(platform)
        derived = draft is None
        if derived and platform != "xiaohongshu":
            raise SocialContentKitError(
                f"{platform} has no source platform draft; add it to the publish package first"
            )
        chosen = draft or base
        title = chosen.get("title") if isinstance(chosen.get("title"), str) else None
        title_variants = chosen.get("title_variants", [])
        if not isinstance(title_variants, list):
            title_variants = []
        caption = chosen.get("caption") if isinstance(chosen.get("caption"), str) else None
        description = chosen.get("description") if isinstance(chosen.get("description"), str) else None
        hashtags = chosen.get("hashtags", [])
        if not isinstance(hashtags, list):
            hashtags = []
        tags = chosen.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        if platform == "xiaohongshu" and derived:
            title = "AI说得越肯定，就越可信吗？3步查证法"
            title_variants = [title, "AI回答很自信？先做这3步核验"]
            caption = "AI回答得很自信，不等于它说的就是真的。遇到重要信息，先做三步：拆开具体主张、查原始来源、主动找反面证据。收藏这份清单，下次核验时照着做。"
            description = (description or "").rstrip() + "\n\n收藏这份查证清单，逐项核对。NIST 原文不为本片背书；三步查证法属于编辑建议。"
            hashtags = ["#人工智能", "#AI工具", "#信息核验", "#知识科普", "#数字素养"]
            tags = ["人工智能", "AI工具", "信息核验", "数字素养"]
        platform_items.append({
            "platform": platform,
            "platform_name": _PLATFORM_NAMES[platform],
            "copy_source": "derived_from_existing_platform_draft" if derived else "existing_publish_package",
            "title": title,
            "title_variants": title_variants,
            "caption": caption,
            "description": description,
            "hashtags": list(hashtags),
            "tags": list(tags),
            "cta": _PLATFORM_CTA[platform],
            "cover_prompt": (
                f"为《{str(title or '本片主题')[:100]}》制作竖屏封面；"
                "只使用本片真实画面和已核实文案，不添加虚构数据、认证标志或不存在的产品界面。"
            ),
            "subtitle_safe_area": {
                "guidance": "关键字幕放在画面中部安全区域，避开顶部/底部界面遮挡和右侧交互按钮；发布前在目标平台官方 App 预览。",
                "heuristic_only": True,
                "official_platform_spec": None,
                "manual_verification_required": True,
            },
            "duration_profile": {
                "master_duration_seconds": source["duration_seconds"],
                "official_duration_limit_seconds": None,
                "status": "master_duration_observed_official_limit_unverified",
                "manual_account_specific_check_required": True,
            },
            "schedule": _schedule_entry(platform, schedule_by_platform or {}),
            "tracking": tracking_by_platform.get(platform),
            "source_citations": chosen.get("source_citations", []),
            "compliance_review": chosen.get("compliance_review", {}),
            "source_release_state": "draft_only",
            "publish_api_called": False,
        })

    kit = {
        "schema_version": 1,
        "package_type": "muse_social_content_kit",
        "campaign": campaign.strip(),
        "release_state": "draft_only",
        "can_publish": False,
        "auto_publish": False,
        "publish_api_called": False,
        "account_authorization": "not_connected",
        "source_publish_package": {
            "path": str(Path(publish_package_dir).as_posix()),
            "sha256": source["zip_sha256"],
            "source_video_sha256": source["video_sha256"],
            "voiceover_status": (
                source["generation"].get("voiceover", {}).get("status")
                if isinstance(source["generation"].get("voiceover"), dict) else "unknown"
            ),
            "initiative_id": source["manifest"].get("initiative_id"),
            "version_id": source["manifest"].get("version_id"),
            "evolution_cycle_id": source["manifest"].get("evolution_cycle_id"),
            "release_quality_status": source["manifest"].get("release_quality_status"),
        },
        "release_blockers": blockers,
        "platforms": platform_items,
        "tracking_state": "draft_links_created" if tracking is not None else "awaiting_owner_controlled_destination_url",
        "tracking_manifest": tracking,
        "analytics_state": "not_imported",
        "revenue_state": "not_observed",
        "settlement_state": "not_observed",
        "note": "This kit is a manual-operation draft. Views/clicks are not revenue; only verified orders and settlements count as realized income.",
    }

    target_relative = output_dir or f"{publish_package_dir.rstrip('/')}/social-content-kit-v1"
    if not isinstance(target_relative, str) or not target_relative.strip():
        raise SocialContentKitError("output_dir must be a non-empty relative directory path")
    target_pure = PurePosixPath(target_relative.replace("\\", "/"))
    if target_pure.is_absolute() or not target_pure.parts or ".." in target_pure.parts or any(":" in part for part in target_pure.parts):
        raise SocialContentKitError("output_dir must be a safe workspace-relative directory")
    target = root.joinpath(*target_pure.parts)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite social content kit: {target_relative}")
    cursor = root
    for part in target_pure.parts[:-1]:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            info = cursor.lstat()
            if cursor.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400) or not cursor.is_dir():
                raise SocialContentKitError("output path contains a symlink, reparse point or non-directory")
        else:
            cursor.mkdir()
    parent = cursor
    staging = Path(tempfile.mkdtemp(prefix=".social-content-kit-", dir=parent))
    try:
        _write_json(staging / "social-content-kit.json", kit)
        readme = (
            "# Muse Social Content Kit\n\n"
            f"- Campaign: {campaign.strip()}\n"
            f"- Platforms: {', '.join(selected)}\n"
            f"- Source video SHA-256: {source['video_sha256']}\n"
            f"- Voiceover status: {kit['source_publish_package']['voiceover_status']}\n"
            f"- Release state: draft_only; publishing is blocked pending human review.\n"
            f"- Release blockers carried forward: {len(blockers)}\n"
            "- Human-readable, copy-ready platform drafts are in social-content-kit.md.\n"
            "- No account login, upload, publish API, customer contact, advertising or payment action was performed.\n"
            "- Official duration limits and current in-app safe areas are not asserted; verify them for each account before posting.\n"
            "- Analytics, orders and settled revenue were not imported; revenue_state=not_observed.\n"
        )
        (staging / "README.md").write_text(readme, encoding="utf-8")
        markdown_lines = [
            "# Muse 社交平台发布草稿", "",
            f"> Campaign: {campaign.strip()} | 状态：draft_only；不可直接发布。", "",
            f"- 源视频 SHA-256：{source['video_sha256']}",
            f"- 旁白状态：{kit['source_publish_package']['voiceover_status']}",
            f"- 发布阻塞项：{len(blockers)} 项；必须逐项人工处理。", "",
        ]
        for item in platform_items:
            markdown_lines.extend([
                f"## {item['platform_name']}", "",
                "**标题**", str(item.get("title") or "待补充"), "",
                "**正文/短文案**", str(item.get("caption") or "待补充"), "",
                "**完整简介与来源**", str(item.get("description") or "待补充"), "",
                "**标签**", " ".join(str(tag) for tag in item.get("hashtags", [])), "",
                "**行动引导**", str(item.get("cta") or ""), "",
                "**排期**", str(item.get("schedule", {}).get("publish_at") or "未安排；按账号数据人工选择"), "",
                "**画面与时长检查**",
                f"母版 {item['duration_profile']['master_duration_seconds']} 秒；官方上限未核实。",
                item["subtitle_safe_area"]["guidance"], "",
                "**发布状态**", "草稿；账号未授权；未调用发布 API。", "",
            ])
        markdown_lines.extend(["## 发布前阻塞项", ""])
        markdown_lines.extend(f"- {str(blocker)}" for blocker in blockers)
        (staging / "social-content-kit.md").write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")
        file_hashes = {
            path.name: _sha256(path)
            for path in (staging / "social-content-kit.json", staging / "social-content-kit.md", staging / "README.md")
        }
        manifest = {
            "schema_version": 1,
            "package_type": "muse_social_content_kit",
            "release_state": "draft_only",
            "can_publish": False,
            "source_publish_package_sha256": source["zip_sha256"],
            "source_video_sha256": source["video_sha256"],
            "files": file_hashes,
        }
        _write_json(staging / "manifest.json", manifest)
        bundle = staging / "social-content-kit.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ("social-content-kit.json", "social-content-kit.md", "README.md", "manifest.json"):
                archive.write(staging / name, name)
        with zipfile.ZipFile(bundle) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != {
                "social-content-kit.json", "social-content-kit.md", "README.md", "manifest.json",
            }:
                raise SocialContentKitError("social content kit ZIP failed integrity validation")
        bundle_sha = _sha256(bundle)
        staging.rename(target)
        return {
            "status": "completed",
            "output_dir": target.relative_to(root).as_posix(),
            "kit_path": (target / "social-content-kit.json").relative_to(root).as_posix(),
            "markdown_path": (target / "social-content-kit.md").relative_to(root).as_posix(),
            "package_path": (target / "social-content-kit.zip").relative_to(root).as_posix(),
            "package_sha256": bundle_sha,
            "platforms": list(selected),
            "release_state": "draft_only",
            "can_publish": False,
            "release_blocker_count": len(blockers),
            "tracking_state": kit["tracking_state"],
            "revenue_state": "not_observed",
        }
    except Exception:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        raise
