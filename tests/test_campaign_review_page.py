from __future__ import annotations

import hashlib
import json
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "ui-prototype"
PAGE = UI_ROOT / "campaign-review.html"
MANIFEST_PATH = UI_ROOT / "assets" / "campaign-review" / "manifest.json"


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.video_sources: list[str] = []
        self.local_links: list[str] = []
        self.external_assets: list[str] = []
        self.has_autoplay = False

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "source" and values.get("src"):
            self.video_sources.append(values["src"])
        if tag == "link" and values.get("href"):
            self.local_links.append(values["href"])
        for attribute in ("src", "href"):
            value = values.get(attribute, "")
            if value.startswith(("http://", "https://", "//")):
                self.external_assets.append(value)
        if tag == "video" and "autoplay" in values:
            self.has_autoplay = True


class CampaignReviewPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.parser = _AssetParser()
        cls.parser.feed(cls.html)

    def test_page_references_exactly_three_local_video_assets(self) -> None:
        expected = {
            "./assets/campaign-review/episode-01.mp4",
            "./assets/campaign-review/episode-02.mp4",
            "./assets/campaign-review/episode-03.mp4",
        }
        self.assertEqual(set(self.parser.video_sources), expected)
        self.assertEqual(len(self.parser.video_sources), 3)
        for relative in self.parser.video_sources:
            target = (UI_ROOT / relative).resolve(strict=True)
            self.assertTrue(target.is_relative_to(UI_ROOT.resolve()))
            self.assertTrue(target.is_file())

    def test_preview_video_sha256_matches_manifest(self) -> None:
        self.assertEqual(len(self.manifest["episodes"]), 3)
        for episode in self.manifest["episodes"]:
            target = (UI_ROOT / episode["local_preview"]).resolve(strict=True)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(digest, episode["sha256"], episode["title"])

    def test_all_episodes_remain_internal_review_only(self) -> None:
        self.assertEqual(self.manifest["status"], "internal_review_only")
        self.assertFalse(self.manifest["public_posting_authorized"])
        self.assertFalse(self.manifest["public_deployment_authorized"])
        self.assertFalse(self.manifest["payments_enabled"])
        for episode in self.manifest["episodes"]:
            self.assertEqual(episode["rights_status"], "editorial_review_required")
            self.assertFalse(episode["commercial_release_authorized"])
            self.assertFalse(episode["audio_stream"])
            self.assertEqual(episode["voiceover"], "not_used")

    def test_page_discloses_review_and_silent_master_status(self) -> None:
        self.assertIn("INTERNAL REVIEW ONLY", self.html)
        self.assertIn("不可公开发布", self.html)
        self.assertIn("没有旁白或音轨", self.html)
        self.assertIn("editorial_review_required", self.html)
        self.assertIn("NO AUDIO", self.html)

    def test_page_uses_no_external_assets_or_autoplay(self) -> None:
        self.assertFalse(self.parser.external_assets)
        self.assertFalse(self.parser.has_autoplay)
        self.assertIn("./campaign-review.css", self.parser.local_links)
        self.assertNotIn("<form", self.html.lower())


if __name__ == "__main__":
    unittest.main()
