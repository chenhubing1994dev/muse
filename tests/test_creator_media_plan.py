from __future__ import annotations

import unittest

from muse.creator_media_plan import build_creator_media_plan
from muse.media_search import MediaSearchBlocked


class FakeSearchEngine:
    def __init__(self, assets=None, error=None):
        self.assets = list(assets or [])
        self.error = error
        self.calls = []

    def search_images(self, query, style, **kwargs):
        self.calls.append((query, style, kwargs))
        if self.error is not None:
            raise self.error
        return list(self.assets)


def asset(url, *, title="宇宙星云", license="CC0", width=1920, height=1080):
    return {
        "url": url,
        "title": title,
        "snippet": "宇宙 星云 科技",
        "source": "test-search",
        "source_ref": "source-record-1",
        "license": license,
        "width": width,
        "height": height,
    }


def segments():
    return [
        {
            "text": "第一幕：探索宇宙星云",
            "visual": "宇宙星云",
            "keywords": ["宇宙", "星云"],
            "topic": "天文探索",
            "start": 0,
            "end": 2,
        },
        {
            "text": "第二幕：科技如何观察宇宙",
            "visual": "科技观测设备",
            "keywords": ["科技", "观测"],
            "topic": "天文探索",
            "start": 2,
            "end": 4,
        },
    ]


class CreatorMediaPlanTests(unittest.TestCase):
    def test_builds_timeline_and_never_promotes_provider_license_to_commercial_rights(self):
        engine = FakeSearchEngine([asset("https://assets.example.test/nebula.jpg")])

        result = build_creator_media_plan(
            engine, query="宇宙星云", style="cosmic", narration_segments=segments(),
        )

        self.assertEqual(result["status"], "needs_rights_review")
        self.assertFalse(result["commercial_use_allowed"])
        self.assertFalse(result["publishable"])
        self.assertFalse(result["auto_publish"])
        self.assertFalse(result["render_dispatched"])
        self.assertTrue(result["rights"]["rights_review_required"])
        self.assertEqual(result["rights"]["unreviewed_asset_count"], 1)
        self.assertEqual(result["assets"][0]["license"], "CC0")
        self.assertEqual(result["assets"][0]["license_status"], "unreviewed")
        self.assertFalse(result["assets"][0]["commercial_use_allowed"])
        self.assertEqual(result["timeline"]["compositionId"], "MuseSceneTimeline")
        self.assertEqual(len(result["timeline"]["props"]["scenes"]), 2)
        first_scene = result["timeline"]["props"]["scenes"][0]
        self.assertTrue(first_scene["rights_review_required"])
        self.assertFalse(first_scene["commercial_use_allowed"])
        self.assertEqual(first_scene["source_ref"], "source-record-1")
        self.assertEqual(first_scene["asset_match"]["status"], "keyword_matched")
        self.assertEqual(result["provenance"]["accepted_count"], 1)
        self.assertEqual(len(engine.calls), 1)

    def test_returns_explicit_original_visual_fallback_when_no_candidate_passes_filters(self):
        engine = FakeSearchEngine([
            asset("https://assets.example.test/small.jpg", width=320, height=180),
            asset("https://assets.example.test/wide.jpg", width=1920, height=1200),
        ])

        result = build_creator_media_plan(
            engine, query="宇宙星云", style="cosmic", narration_segments=segments(),
        )

        self.assertEqual(result["status"], "needs_original_visuals")
        self.assertIsNone(result["timeline"])
        self.assertTrue(result["fallback_required"])
        self.assertEqual(result["assets"], [])
        self.assertTrue(all(
            scene["asset_match"]["status"] == "fallback_no_assets"
            for scene in result["scene_matches"]
        ))
        self.assertFalse(result["commercial_use_allowed"])

    def test_vertical_creator_brief_passes_explicit_portrait_ratio(self):
        portrait = asset(
            "https://assets.example.test/portrait.jpg", width=1080, height=1920,
        )
        engine = FakeSearchEngine([portrait])

        result = build_creator_media_plan(
            engine,
            query="宇宙星云",
            style="cosmic",
            narration_segments=segments(),
            min_width=720,
            min_height=1280,
            target_ratio=9 / 16,
        )

        self.assertEqual(result["status"], "needs_rights_review")
        self.assertAlmostEqual(engine.calls[0][2]["target_ratio"], 9 / 16)
        self.assertEqual(result["assets"][0]["url"], portrait["url"])

    def test_partial_scene_bundle_does_not_shift_asset_to_another_narration_scene(self):
        from unittest.mock import patch

        first = asset("https://assets.example.test/nebula.jpg")
        bundle = {
            "assets": [first],
            "scenes": [
                {
                    "segment_index": 0,
                    "asset": first,
                    "asset_match": {"status": "keyword_matched"},
                },
                {
                    "segment_index": 1,
                    "asset": None,
                    "asset_match": {"status": "fallback_no_assets"},
                },
            ],
            "provenance": {"candidate_count": 1, "accepted_count": 1},
        }
        engine = FakeSearchEngine()
        with patch("muse.creator_media_plan.build_scene_asset_bundle", return_value=bundle):
            result = build_creator_media_plan(
                engine, query="宇宙星云", style="cosmic", narration_segments=segments(),
            )

        self.assertEqual(result["status"], "needs_original_visuals")
        self.assertIsNone(result["timeline"])
        self.assertEqual([item["segment_index"] for item in result["scene_matches"]], [0, 1])
        self.assertEqual(
            [item["status"] for item in result["scene_matches"]],
            ["rights_review_required", "fallback_no_assets"],
        )
        self.assertEqual(result["rights"]["missing_scene_count"], 1)

    def test_provider_failure_is_a_structured_block_not_a_false_empty_success(self):
        engine = FakeSearchEngine(error=MediaSearchBlocked("provider secret detail"))

        result = build_creator_media_plan(
            engine, query="宇宙星云", style="cosmic", narration_segments=segments(),
        )

        self.assertEqual(result["status"], "blocked_search_provider")
        self.assertEqual(result["error_code"], "media_search_unavailable")
        self.assertNotIn("provider secret detail", str(result))
        self.assertIsNone(result["timeline"])
        self.assertFalse(result["publishable"])

    def test_derives_timeline_intervals_from_narration_duration(self):
        engine = FakeSearchEngine([asset("https://assets.example.test/nebula.jpg")])
        narration = [
            {"text": "第一句", "estimated_duration_seconds": 1.5, "visual": "星云"},
            {"text": "第二句", "estimated_duration_seconds": 2.0, "visual": "宇宙"},
        ]

        result = build_creator_media_plan(
            engine, query="宇宙", style="cosmic", narration_segments=narration,
        )

        scenes = result["timeline"]["props"]["scenes"]
        self.assertEqual(scenes[0]["startFrame"], 0)
        self.assertEqual(scenes[0]["durationInFrames"], 45)
        self.assertEqual(scenes[1]["startFrame"], 45)
        self.assertEqual(scenes[1]["durationInFrames"], 60)

    def test_rejects_invalid_narration_before_search(self):
        engine = FakeSearchEngine([asset("https://assets.example.test/nebula.jpg")])

        with self.assertRaisesRegex(ValueError, "narration segment"):
            build_creator_media_plan(
                engine,
                query="宇宙",
                style="cosmic",
                narration_segments=[{"text": "缺少时间或时长"}],
            )

        self.assertEqual(engine.calls, [])

    def test_rejects_invalid_fps(self):
        engine = FakeSearchEngine([asset("https://assets.example.test/nebula.jpg")])

        with self.assertRaisesRegex(ValueError, "fps"):
            build_creator_media_plan(
                engine, query="宇宙", style="cosmic",
                narration_segments=segments(), fps=0,
            )


if __name__ == "__main__":
    unittest.main()
