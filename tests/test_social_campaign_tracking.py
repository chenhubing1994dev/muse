from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlsplit

from muse.social_campaign_tracking import (
    SocialTrackingError,
    build_social_tracking_manifest,
    build_tracking_url,
)


class SocialCampaignTrackingTests(unittest.TestCase):
    def test_builds_deterministic_platform_specific_utm_url(self):
        url = build_tracking_url(
            "https://example.test/muse?ref=profile",
            source="youtube_shorts",
            campaign="prometheus-launch",
            content="hero-cut-a",
        )
        parsed = urlsplit(url)
        self.assertEqual(parsed.netloc, "example.test")
        self.assertEqual(parse_qs(parsed.query), {
            "ref": ["profile"],
            "utm_source": ["youtube_shorts"],
            "utm_medium": ["social"],
            "utm_campaign": ["prometheus-launch"],
            "utm_content": ["hero-cut-a"],
        })

    def test_encodes_unicode_campaign_and_content(self):
        url = build_tracking_url(
            "https://example.test/muse",
            source="xiaohongshu",
            campaign="Muse 首发",
            content="粤语样片",
        )
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["utm_campaign"], ["Muse 首发"])
        self.assertEqual(params["utm_content"], ["粤语样片"])

    def test_manifest_is_draft_only_and_deduplicates_platforms(self):
        result = build_social_tracking_manifest(
            "https://example.test/muse",
            campaign="launch-01",
            platforms=["youtube_shorts", "tiktok", "youtube_shorts"],
            content_by_platform={"youtube_shorts": "hook-a", "tiktok": "hook-b"},
        )
        self.assertEqual([item["platform"] for item in result["platforms"]], ["youtube_shorts", "tiktok"])
        self.assertEqual(result["release_state"], "draft_only")
        self.assertFalse(result["can_publish"])
        self.assertFalse(result["auto_publish"])
        self.assertEqual(result["revenue_state"], "not_observed")

    def test_rejects_invalid_platform_container(self):
        with self.assertRaises(SocialTrackingError):
            build_social_tracking_manifest(
                "https://example.test/muse", campaign="launch", platforms=[["tiktok"]],
                content_by_platform={},
            )

    def test_manifest_requires_content_label_for_every_platform(self):
        with self.assertRaises(SocialTrackingError):
            build_social_tracking_manifest(
                "https://example.test/muse",
                campaign="launch",
                platforms=["tiktok"],
                content_by_platform={},
            )

    def test_rejects_non_http_or_credential_urls(self):
        for url in ("file:///tmp/a", "https://user:secret@example.test/a", "/relative/path"):
            with self.subTest(url=url), self.assertRaises(SocialTrackingError):
                build_tracking_url(url, source="tiktok", campaign="launch", content="v1")

    def test_rejects_existing_utm_to_avoid_silent_overwrite(self):
        with self.assertRaises(SocialTrackingError):
            build_tracking_url(
                "https://example.test/muse?utm_source=old",
                source="tiktok", campaign="launch", content="v1",
            )

    def test_rejects_fragment_and_invalid_platform_identifier(self):
        with self.assertRaises(SocialTrackingError):
            build_tracking_url(
                "https://example.test/muse#pricing",
                source="tiktok", campaign="launch", content="v1",
            )
        with self.assertRaises(SocialTrackingError):
            build_social_tracking_manifest(
                "https://example.test/muse",
                campaign="launch",
                platforms=["TikTok"],
                content_by_platform={"TikTok": "v1"},
            )

    def test_rejects_empty_or_control_character_tags(self):
        for value in ("", "  ", "bad\nvalue"):
            with self.subTest(value=value), self.assertRaises(SocialTrackingError):
                build_tracking_url(
                    "https://example.test/muse",
                    source="tiktok", campaign="launch", content=value,
                )


if __name__ == "__main__":
    unittest.main()
