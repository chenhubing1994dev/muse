from __future__ import annotations

import copy
import unittest

from muse.story_bible import validate_story_bible, validate_story_continuity
from tests.test_video_workflow import sample_storyboard


def sample_story_bible():
    return {
        "schema_version": 1,
        "title": "山海奇旅",
        "logline": "一名年轻旅人寻找失落的海上城邦。",
        "genre": "mythic adventure",
        "target_audience": "喜欢东方奇幻与电影感短片的观众",
        "characters": [
            {
                "id": "traveler",
                "name": "旅人",
                "description": "二十多岁，黑发，穿深蓝色旅行长袍。",
                "canonical_asset_id": "asset_traveler_ref",
                "outfits": [
                    {"id": "travel_cloak", "description": "深蓝色长袍与旧皮革肩带"}
                ],
            }
        ],
        "locations": [
            {
                "id": "sea_gate",
                "name": "海上古城门",
                "description": "被薄雾包围的巨大石门，立于潮汐之间。",
                "reference_asset_id": "asset_sea_gate",
            }
        ],
        "props": [
            {"id": "brass_compass", "name": "黄铜罗盘", "description": "边缘磨损的旧罗盘。"}
        ],
        "style_guide": {
            "visual_style": "cinematic mythic realism",
            "palette": ["deep teal", "aged gold", "mist grey"],
            "lighting": "soft volumetric dawn light",
            "camera_language": "slow deliberate camera movement",
            "audio_mood": "mysterious orchestral ambience",
            "dialogue_language": "yue-Hant",
        },
        "continuity": {
            "rules": ["旅人的黑发与深蓝长袍在所有镜头保持一致。"],
            "scene_states": [
                {
                    "scene_id": "opening",
                    "location_id": "sea_gate",
                    "character_states": [
                        {"character_id": "traveler", "outfit_id": "travel_cloak", "state_note": "衣袍被海风轻轻吹动"}
                    ],
                    "prop_states": [
                        {"prop_id": "brass_compass", "state_note": "罗盘握在旅人右手"}
                    ],
                }
            ],
        },
    }


class StoryBibleTests(unittest.TestCase):
    def test_validates_and_normalizes_story_bible(self):
        result = validate_story_bible(sample_story_bible())
        self.assertEqual(result["title"], "山海奇旅")
        self.assertEqual(result["characters"][0]["id"], "traveler")
        self.assertEqual(result["style_guide"]["dialogue_language"], "yue-Hant")
        self.assertEqual(len(result["continuity"]["scene_states"]), 1)

    def test_rejects_unknown_fields_and_unsupported_schema(self):
        for field, value in (("api_key", "secret"), ("schema_version", 2)):
            payload = sample_story_bible()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_story_bible(payload)

    def test_rejects_duplicate_or_unsafe_asset_ids(self):
        payload = sample_story_bible()
        payload["characters"].append(copy.deepcopy(payload["characters"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_story_bible(payload)
        payload = sample_story_bible()
        payload["locations"][0]["id"] = "../escape"
        with self.assertRaises(ValueError):
            validate_story_bible(payload)

    def test_rejects_unbounded_or_invalid_nested_data(self):
        payload = sample_story_bible()
        payload["style_guide"]["palette"] = ["x"] * 9
        with self.assertRaises(ValueError):
            validate_story_bible(payload)
        payload = sample_story_bible()
        payload["characters"][0]["description"] = "x" * 1001
        with self.assertRaises(ValueError):
            validate_story_bible(payload)

    def test_rejects_unknown_asset_references_in_continuity(self):
        payload = sample_story_bible()
        payload["continuity"]["scene_states"][0]["character_states"][0]["character_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown character"):
            validate_story_bible(payload)

    def test_cross_validates_continuity_against_storyboard(self):
        bible = sample_story_bible()
        storyboard = sample_storyboard()
        result = validate_story_continuity(bible, storyboard)
        self.assertEqual(result["scene_count"], 3)
        self.assertEqual(result["continuity_scene_count"], 1)

    def test_rejects_continuity_for_nonexistent_storyboard_scene(self):
        bible = sample_story_bible()
        bible["continuity"]["scene_states"][0]["scene_id"] = "not_in_storyboard"
        with self.assertRaisesRegex(ValueError, "unknown storyboard scene"):
            validate_story_continuity(bible, sample_storyboard())


    def test_video_plan_cli_accepts_story_bible_and_reports_continuity(self):
        import json
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from muse.video_factory import main

        with tempfile.TemporaryDirectory() as directory:
            storyboard_path = Path(directory) / "storyboard.json"
            bible_path = Path(directory) / "story-bible.json"
            storyboard_path.write_text(json.dumps(sample_storyboard(), ensure_ascii=False), encoding="utf-8")
            bible_path.write_text(json.dumps(sample_story_bible(), ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["plan", str(storyboard_path), "--story-bible", str(bible_path)])
            result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["story_bible"]["character_count"], 1)
        self.assertEqual(result["story_bible"]["continuity_scene_count"], 1)
        self.assertEqual(result["execution"], "not_started")

    def test_video_plan_cli_blocks_story_bible_scene_mismatch(self):
        import json
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from muse.video_factory import main

        bible = sample_story_bible()
        bible["continuity"]["scene_states"][0]["scene_id"] = "unknown_scene"
        with tempfile.TemporaryDirectory() as directory:
            storyboard_path = Path(directory) / "storyboard.json"
            bible_path = Path(directory) / "story-bible.json"
            storyboard_path.write_text(json.dumps(sample_storyboard(), ensure_ascii=False), encoding="utf-8")
            bible_path.write_text(json.dumps(bible, ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["plan", str(storyboard_path), "--story-bible", str(bible_path)])
            result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "invalid_story_package")
        self.assertIn("unknown storyboard scene", result["error"])

if __name__ == "__main__":
    unittest.main()
