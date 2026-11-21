from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from muse.video_prompt_studio import compose_video_studio_package, main


def sample_spec() -> dict:
    return {
        "title": "雨夜归途",
        "creative_intent": "以电影感镜头讲述旅人在雨夜寻找归途。",
        "aspect_ratio": "9:16",
        "visual_style": "写实电影摄影",
        "narration_language": "粤语",
        "scenes": [
            {"scene_id": "opening", "action": "旅人提着纸灯走过湿润石板路", "duration_seconds": 3},
            {"scene_id": "ending", "action": "旅人走向远处暖色灯光", "duration_seconds": 4,
             "depends_on": ["opening"]},
        ],
    }


class VideoPromptStudioAdapterTests(unittest.TestCase):
    def test_composer_output_flows_into_existing_profile_preview(self):
        result = compose_video_studio_package(sample_spec(), "animated_explainer")

        self.assertEqual(result["status"], "preview_only")
        self.assertEqual(result["title"], "雨夜归途")
        self.assertEqual(result["composition"]["storyboard"]["aspect_ratio"], "9:16")
        self.assertEqual(
            [scene["id"] for scene in result["composition"]["storyboard"]["scenes"]],
            ["opening", "ending"],
        )
        self.assertEqual(result["video_studio"]["pipeline"]["pipeline_id"], "animated_explainer")
        self.assertIn(
            "shot_opening",
            [step["id"] for step in result["video_studio"]["commander_plan"]["steps"]],
        )
        self.assertEqual(result["execution"], "not_started")
        self.assertEqual(result["commander_submission"], "not_submitted")
        self.assertFalse(result["checkpoint_persistence_available"])
        self.assertFalse(result["execution_eligible"])

    def test_story_bible_continuity_and_audio_sidecar_are_preserved(self):
        bible = {
            "schema_version": 1,
            "title": "雨夜归途",
            "logline": "旅人在雨夜寻找归途。",
            "genre": "drama",
            "target_audience": "通用",
            "characters": [{
                "id": "traveler", "name": "旅人", "description": "黑发，深色风衣",
                "canonical_asset_id": "traveler_ref",
                "outfits": [{"id": "coat", "description": "深色风衣"}],
            }],
            "locations": [{"id": "old_street", "name": "旧街", "description": "湿润石板路"}],
            "props": [{"id": "lantern", "name": "纸灯", "description": "暖黄色纸灯"}],
            "style_guide": {
                "visual_style": "写实电影摄影", "palette": ["靛蓝", "琥珀"],
                "lighting": "雨夜冷暖对比", "camera_language": "手持跟拍",
                "audio_mood": "雨声与弦乐", "dialogue_language": "粤语",
            },
            "continuity": {
                "rules": ["纸灯保持暖黄色"],
                "scene_states": [{
                    "scene_id": "opening", "location_id": "old_street",
                    "character_states": [{
                        "character_id": "traveler", "outfit_id": "coat", "state_note": "右手提灯",
                    }],
                    "prop_states": [{"prop_id": "lantern", "state_note": "灯光稳定"}],
                }],
            },
        }
        spec = sample_spec()
        spec["scenes"][0]["narration"] = "雨还未停，归途仍远。"
        result = compose_video_studio_package(spec, "documentary_montage", bible)

        self.assertEqual(result["composition"]["continuity_validation"]["continuity_scene_count"], 1)
        self.assertEqual(result["composition"]["audio_plan"]["cues"][0]["narration"], "雨还未停，归途仍远。")
        self.assertIn("角色锁定 traveler", result["composition"]["master_prompt"])
        self.assertEqual(result["video_studio"]["pipeline"]["pipeline_id"], "documentary_montage")

    def test_cli_compose_outputs_one_combined_plan_only_package(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "director.json"
            source.write_text(json.dumps(sample_spec(), ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["compose", str(source), "--pipeline", "product_ad"]), 0)

        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "preview_only")
        self.assertEqual(result["video_studio"]["pipeline"]["pipeline_id"], "product_ad")
        self.assertEqual(result["video_studio"]["commander_batching"]["status"],
                         "preview_only_worker_integration_pending")
        self.assertEqual(result["commander_submission"], "not_submitted")

    def test_adapter_rejects_unknown_profile_before_any_dispatch(self):
        with self.assertRaisesRegex(ValueError, "unknown video pipeline"):
            compose_video_studio_package(sample_spec(), "unknown_profile")


if __name__ == "__main__":
    unittest.main()
