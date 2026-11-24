from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from muse.social_content_kit import (
    SocialContentKitError,
    build_social_content_kit,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SocialContentKitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / "publish-pack"
        (self.package / "platform-drafts").mkdir(parents=True)
        files = {
            "video.mp4": b"fake-mp4-bytes-for-test",
            "generation.json": None,
            "release-quality-report.json": None,
            "platform-drafts/youtube_shorts.json": None,
            "platform-drafts/tiktok.json": None,
            "platform-drafts/instagram_reels.json": None,
            "platform-drafts/bilibili.json": None,
        }
        (self.package / "video.mp4").write_bytes(files["video.mp4"])
        (self.package / "generation.json").write_text(json.dumps({
            "status": "completed",
            "mp4": {"sha256": sha(self.package / "video.mp4"), "duration_seconds": 24.5},
            "voiceover": {"status": "not_used"},
        }), encoding="utf-8")
        (self.package / "release-quality-report.json").write_text(json.dumps({
            "can_publish": False,
            "overall_status": "blocked_pending_human_review",
            "blocking_items": ["visual_and_model_rights_review", "human_editorial_review"],
        }), encoding="utf-8")
        draft = {
            "release_state": "draft_only",
            "publish_api_called": False,
            "title": "别把 AI 的自信，当成事实",
            "title_variants": ["AI 讲得自信，就一定正确？"],
            "caption": "拆主张、查原文、找反证。",
            "description": "依据随包来源说明；编辑建议不代表来源机构认证。",
            "hashtags": ["#AI", "#事实核验"],
            "tags": ["AI", "事实核验"],
            "source_citations": [{"title": "NIST AI 600-1"}],
            "compliance_review": {"visual_and_model_rights": {"status": "review_required"}},
        }
        for platform in ("youtube_shorts", "tiktok", "instagram_reels", "bilibili"): 
            (self.package / "platform-drafts" / f"{platform}.json").write_text(
                json.dumps(draft, ensure_ascii=False), encoding="utf-8",
            )
        for name in ("video.mp4", "generation.json", "release-quality-report.json",
                     "platform-drafts/youtube_shorts.json", "platform-drafts/tiktok.json",
                     "platform-drafts/instagram_reels.json", "platform-drafts/bilibili.json"): 
            files[name] = sha(self.package / name)
        self.manifest = {
            "package_type": "muse_manual_platform_publish_pack",
            "release_state": "draft_only",
            "can_publish": False,
            "initiative_id": "a" * 32,
            "version_id": "b" * 32,
            "evolution_cycle_id": "cycle-test",
            "source_video_sha256": sha(self.package / "video.mp4"),
            "release_quality_status": "blocked_pending_human_review",
            "platforms": ["youtube_shorts", "tiktok", "instagram_reels", "bilibili"],
            "files": files,
        }
        (self.package / "manifest.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8",
        )
        with zipfile.ZipFile(self.package / "publish-package.zip", "w") as archive:
            for name in (*files, "manifest.json"):
                archive.write(self.package / name, name)

    def tearDown(self):
        self.temp.cleanup()

    def test_builds_five_platform_draft_kit_and_carries_release_blockers(self):
        result = build_social_content_kit(
            self.root, "publish-pack", campaign="muse-launch-01",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["platforms"], [
            "youtube_shorts", "tiktok", "instagram_reels", "bilibili", "xiaohongshu",
        ])
        self.assertFalse(result["can_publish"])
        self.assertEqual(result["release_state"], "draft_only")
        kit_path = self.root / result["kit_path"]
        kit = json.loads(kit_path.read_text(encoding="utf-8"))
        self.assertEqual(kit["revenue_state"], "not_observed")
        self.assertEqual(len(kit["release_blockers"]), 2)
        xhs = next(item for item in kit["platforms"] if item["platform"] == "xiaohongshu")
        self.assertEqual(xhs["copy_source"], "derived_from_existing_platform_draft")
        self.assertIn("3步查证法", xhs["title"])
        self.assertIn("收藏这份清单", xhs["caption"])
        self.assertEqual(kit["source_publish_package"]["voiceover_status"], "not_used")
        self.assertTrue(xhs["subtitle_safe_area"]["manual_verification_required"])
        self.assertIsNone(xhs["duration_profile"]["official_duration_limit_seconds"])
        self.assertEqual(xhs["schedule"]["status"], "not_scheduled")
        with zipfile.ZipFile(self.root / result["package_path"]) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("social-content-kit.json", archive.namelist())
            self.assertIn("social-content-kit.md", archive.namelist())
        markdown = (self.root / result["markdown_path"]).read_text(encoding="utf-8")
        self.assertIn("小红书", markdown)
        self.assertIn("发布前阻塞项", markdown)
        self.assertIn("旁白状态：not_used", markdown)
        self.assertIn("\n## 小红书\n", markdown)

    def test_tracking_requires_owner_confirmed_destination_and_stays_draft_only(self):
        with self.assertRaisesRegex(SocialContentKitError, "confirm ownership"):
            build_social_content_kit(
                self.root, "publish-pack", campaign="launch",
                destination_url="https://example.test/muse",
            )
        result = build_social_content_kit(
            self.root, "publish-pack", campaign="launch",
            destination_url="https://example.test/muse",
            destination_url_owner_confirmed=True,
        )
        kit = json.loads((self.root / result["kit_path"]).read_text(encoding="utf-8"))
        self.assertEqual(kit["tracking_state"], "draft_links_created")
        self.assertFalse(kit["tracking_manifest"]["can_publish"])
        self.assertEqual(kit["revenue_state"], "not_observed")
        self.assertEqual(len(kit["tracking_manifest"]["platforms"]), 5)

    def test_rejects_tampered_source_file_hash(self):
        (self.package / "video.mp4").write_bytes(b"tampered")
        with self.assertRaisesRegex(SocialContentKitError, "hash mismatch"):
            build_social_content_kit(self.root, "publish-pack", campaign="launch")

    def test_rejects_publishable_or_non_draft_source_package(self):
        self.manifest["can_publish"] = True
        (self.package / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(SocialContentKitError, "draft_only"):
            build_social_content_kit(self.root, "publish-pack", campaign="launch")

    def test_schedule_requires_timezone_and_rejects_unselected_platform(self):
        with self.assertRaisesRegex(SocialContentKitError, "include a timezone"):
            build_social_content_kit(
                self.root, "publish-pack", campaign="launch",
                schedule_by_platform={"tiktok": "2026-11-25T19:00:00"},
            )
        with self.assertRaisesRegex(SocialContentKitError, "unselected platform"):
            build_social_content_kit(
                self.root, "publish-pack", campaign="launch",
                platforms=["youtube_shorts", "tiktok"],
                schedule_by_platform={"instagram_reels": "2026-11-25T19:00:00-05:00"},
            )

    def test_rejects_unknown_platform(self):
        with self.assertRaisesRegex(SocialContentKitError, "unsupported"):
            build_social_content_kit(
                self.root, "publish-pack", campaign="launch", platforms=["unknown"],
            )

    def test_refuses_to_overwrite_existing_kit_directory(self):
        target = self.package / "social-content-kit-v1"
        target.mkdir()
        with self.assertRaises(FileExistsError):
            build_social_content_kit(self.root, "publish-pack", campaign="launch")


if __name__ == "__main__":
    unittest.main()
