from __future__ import annotations

import unittest
from dataclasses import replace

from muse.video_prompt_composer import (
    PromptSourceRef,
    VideoPromptScene,
    VideoPromptSpec,
    compile_video_prompt,
    video_prompt_spec_from_dict,
)


def sample_bible() -> dict:
    return {
        "schema_version": 1,
        "title": "雨夜归途",
        "logline": "一名旅人在雨夜寻找归途。",
        "genre": "cinematic drama",
        "target_audience": "成人观众",
        "characters": [{
            "id": "traveler", "name": "旅人", "description": "黑发，深色长风衣，左眉有小疤",
            "canonical_asset_id": "asset_traveler",
            "outfits": [{"id": "coat", "description": "深色长风衣，银色扣子"}],
        }],
        "locations": [{"id": "old_street", "name": "旧街", "description": "湿润石板路与暖色路灯"}],
        "props": [{"id": "lantern", "name": "纸灯", "description": "暖黄色纸灯笼"}],
        "style_guide": {
            "visual_style": "写实电影摄影",
            "palette": ["靛蓝", "琥珀"],
            "lighting": "雨夜冷暖对比",
            "camera_language": "克制的手持跟拍",
            "audio_mood": "雨声与低沉弦乐",
            "dialogue_language": "粤语",
        },
        "continuity": {
            "rules": ["旅人左眉疤痕始终位于左侧", "纸灯保持暖黄色"],
            "scene_states": [{
                "scene_id": "opening",
                "location_id": "old_street",
                "character_states": [{
                    "character_id": "traveler", "outfit_id": "coat", "state_note": "右手提纸灯",
                }],
                "prop_states": [{"prop_id": "lantern", "state_note": "灯光稳定，不熄灭"}],
            }],
        },
    }


class VideoPromptComposerTests(unittest.TestCase):
    def make_spec(self) -> VideoPromptSpec:
        return VideoPromptSpec(
            title="雨夜归途",
            creative_intent="以电影感镜头讲述旅人在雨夜寻找归途。",
            aspect_ratio="9:16",
            platform="短视频",
            visual_style="写实电影摄影，真实雨滴与自然运动",
            camera_language="手持跟拍，轻微呼吸式对焦",
            lighting="冷色环境光与暖色纸灯形成对比",
            audio_mood="雨声、脚步声与克制弦乐",
            scenes=(
                VideoPromptScene(
                    scene_id="opening", action="旅人提着纸灯走过湿润石板路",
                    duration_seconds=3, beat="建立地点与人物",
                    narration="雨还未停，归途仍远。", music_cue="低音弦乐渐入",
                ),
                VideoPromptScene(
                    scene_id="closeup", action="镜头靠近旅人的侧脸，纸灯映亮眉间",
                    duration_seconds=2, beat="情绪转折",
                    camera="缓慢推近，浅景深", depends_on=("opening",),
                    sound_effects="近处雨滴落在布料上",
                ),
            ),
            source_refs=(
                PromptSourceRef(
                    source_id="x-post-01", author="创作者甲",
                    source_url="https://x.com/example/status/123",
                    license="CC-BY-4.0", completeness="excerpt",
                    category="cinematic", method_tags=("shot-beats", "continuity"),
                    summary="按镜头节拍组织叙事并保持角色一致性",
                    captured_at="2026-11-21",
                ),
            ),
            model_family="Gemini",
        )

    def test_compiles_master_clip_timeline_and_existing_storyboard(self) -> None:
        result = compile_video_prompt(self.make_spec(), sample_bible())
        storyboard = result["storyboard"]
        self.assertEqual(storyboard["aspect_ratio"], "9:16")
        self.assertEqual([item["id"] for item in storyboard["scenes"]], ["opening", "closeup"])
        self.assertIn("角色锁定 traveler", result["master_prompt"])
        self.assertIn("指定服装", storyboard["scenes"][0]["prompt"])
        self.assertIn("角色一致性锁定", storyboard["scenes"][1]["prompt"])
        self.assertIn("道具一致性锁定", storyboard["scenes"][1]["prompt"])
        self.assertEqual(result["clip_manifest"][1]["start_seconds"], 3.0)
        self.assertEqual(result["clip_manifest"][1]["end_seconds"], 5.0)
        self.assertEqual(result["continuity_validation"]["continuity_scene_count"], 1)

    def test_compiles_tempo_lock_and_beat_grid_into_master_clips_and_audio_plan(self) -> None:
        spec = replace(
            self.make_spec(), tempo_bpm=120, beat_offset_seconds=0.0,
            beat_alignment_tolerance_ms=80, align_scene_cuts_to_beats=True,
        )
        result = compile_video_prompt(spec)
        first = result["clip_manifest"][0]["beat_alignment"]
        second = result["clip_manifest"][1]["beat_alignment"]

        self.assertIn("节奏锁定：120 BPM", result["master_prompt"])
        self.assertIn("节拍网格锁定", result["storyboard"]["scenes"][0]["prompt"])
        self.assertEqual(first["end_beat_index"], 6)
        self.assertTrue(first["end_on_beat"])
        self.assertEqual(second["end_beat_index"], 10)
        self.assertEqual(result["audio_plan"]["tempo_bpm"], 120)
        self.assertTrue(result["audio_plan"]["align_scene_cuts_to_beats"])

    def test_rejects_required_scene_cut_off_the_beat_grid(self) -> None:
        original = self.make_spec()
        scenes = (replace(original.scenes[0], duration_seconds=1.3), original.scenes[1])
        spec = replace(
            original, scenes=scenes, tempo_bpm=120,
            align_scene_cuts_to_beats=True, beat_alignment_tolerance_ms=80,
        )
        with self.assertRaisesRegex(ValueError, "not aligned to the beat grid"):
            compile_video_prompt(spec)

    def test_beat_alignment_requires_tempo_and_rejects_boolean_bpm(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires tempo_bpm"):
            replace(self.make_spec(), align_scene_cuts_to_beats=True)
        with self.assertRaisesRegex(ValueError, "tempo_bpm"):
            replace(self.make_spec(), tempo_bpm=True)

    def test_phone_realism_capture_profile_is_repeated_in_master_and_each_clip(self) -> None:
        result = compile_video_prompt(replace(self.make_spec(), capture_profile="phone_realism"))
        self.assertIn("手机纪实质感锁定", result["master_prompt"])
        for scene in result["storyboard"]["scenes"]:
            self.assertIn("手持微抖", scene["prompt"])
            self.assertIn("HDR", scene["prompt"])
        self.assertEqual(result["model_guidance"]["capture_profile"], "phone_realism")

    def test_rejects_unknown_capture_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "capture_profile"):
            replace(self.make_spec(), capture_profile="unreal_camera")

    def test_audio_is_a_separate_cantonese_sidecar(self) -> None:
        result = compile_video_prompt(self.make_spec(), sample_bible())
        self.assertEqual(result["audio_plan"]["narration_language"], "粤语")
        self.assertEqual(result["audio_plan"]["cues"][0]["narration"], "雨还未停，归途仍远。")
        self.assertIn("不假设当前视频模型支持", result["audio_plan"]["note"])

    def test_source_ledger_keeps_excerpt_provenance_without_prompt_copies(self) -> None:
        result = compile_video_prompt(self.make_spec())
        source = result["source_ledger"][0]
        self.assertEqual(source["completeness"], "excerpt")
        self.assertEqual(source["license"], "CC-BY-4.0")
        self.assertEqual(source["source_url"], "https://x.com/example/status/123")
        self.assertEqual(source["captured_at"], "2026-11-21")
        self.assertNotIn("prompt_text", source)
        self.assertNotIn("raw_prompt", source)

    def test_unknown_model_capabilities_are_not_guessed(self) -> None:
        result = compile_video_prompt(self.make_spec())
        self.assertEqual(result["model_guidance"]["family"], "google")
        self.assertEqual(result["model_guidance"]["provider_parameters_applied"], [])

    def test_rejects_malformed_typed_fields_with_value_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "method_tags"):
            PromptSourceRef(
                "s1", "author", "https://example.com/p", "CC-BY-4.0", "full", "cinematic",
                method_tags=(123,),
            )
        with self.assertRaisesRegex(ValueError, "captured_at"):
            PromptSourceRef(
                "s1", "author", "https://example.com/p", "CC-BY-4.0", "full", "cinematic",
                captured_at=None,
            )
        with self.assertRaisesRegex(ValueError, "depends_on"):
            VideoPromptScene("one", "动作", 1, depends_on=(None,))
        with self.assertRaisesRegex(ValueError, "scenes"):
            VideoPromptSpec("title", "intent", scenes=("not-a-scene",))
        with self.assertRaisesRegex(ValueError, "source_refs"):
            VideoPromptSpec("title", "intent", scenes=(VideoPromptScene("one", "动作", 1),),
                            source_refs=("not-a-source",))
        with self.assertRaisesRegex(ValueError, "fps"):
            VideoPromptSpec("title", "intent", scenes=(VideoPromptScene("one", "动作", 1),), fps=24.0)

    def test_json_loader_rejects_non_string_nested_labels(self) -> None:
        import json
        from dataclasses import asdict

        payload = json.loads(json.dumps(asdict(self.make_spec()), ensure_ascii=False))
        payload["scenes"][0]["depends_on"] = [None]
        with self.assertRaisesRegex(ValueError, "depends_on"):
            video_prompt_spec_from_dict(payload)

        payload = json.loads(json.dumps(asdict(self.make_spec()), ensure_ascii=False))
        payload["source_refs"][0]["method_tags"] = [123]
        with self.assertRaisesRegex(ValueError, "method_tags"):
            video_prompt_spec_from_dict(payload)

    def test_rejects_invalid_source_url_and_incomplete_label(self) -> None:
        with self.assertRaises(ValueError):
            PromptSourceRef("s1", "author", "https://user:pass@example.com/p", "CC-BY-4.0", "full", "cinematic")
        with self.assertRaisesRegex(ValueError, "completeness"):
            PromptSourceRef("s1", "author", "https://example.com", "CC-BY-4.0", [], "cinematic")
        with self.assertRaisesRegex(ValueError, "aspect_ratio"):
            VideoPromptSpec("title", "intent", scenes=(VideoPromptScene("one", "动作", 1),),
                            aspect_ratio=[])
        with self.assertRaises(ValueError):
            PromptSourceRef("s1", "author", "http://example.com", "CC-BY-4.0", "full", "cinematic")
        with self.assertRaises(ValueError):
            PromptSourceRef("s1", "author", "https://example.com/p", "CC-BY-4.0", "partial", "cinematic")

    def test_rejects_overlong_compiled_clip_prompt(self) -> None:
        spec = VideoPromptSpec(
            title="长提示测试", creative_intent="目" * 1200,
            scenes=(VideoPromptScene(scene_id="one", action="动作" * 500, duration_seconds=1),),
        )
        with self.assertRaisesRegex(ValueError, "exceeds 2000"):
            compile_video_prompt(spec)

    def test_rejects_unknown_story_bible_references(self) -> None:
        bible = sample_bible()
        bible["continuity"]["scene_states"][0]["location_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown location"):
            compile_video_prompt(self.make_spec(), bible)

    def test_rejects_unknown_scene_dependencies(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown dependencies"):
            VideoPromptSpec(
                title="依赖测试", creative_intent="测试",
                scenes=(VideoPromptScene(
                    scene_id="one", action="动作", duration_seconds=1,
                    depends_on=("missing",),
                ),),
            )

    def test_json_loader_builds_typed_spec_and_rejects_unknown_fields(self) -> None:
        import json
        from dataclasses import asdict

        payload = json.loads(json.dumps(asdict(self.make_spec()), ensure_ascii=False))
        loaded = video_prompt_spec_from_dict(payload)
        self.assertEqual(loaded.title, "雨夜归途")
        self.assertEqual(loaded.scenes[1].depends_on, ("opening",))
        self.assertEqual(loaded.source_refs[0].method_tags, ("shot-beats", "continuity"))
        invalid = json.loads(json.dumps(asdict(self.make_spec()), ensure_ascii=False))
        invalid["surprise"] = "must not be silently ignored"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            video_prompt_spec_from_dict(invalid)

    def test_video_factory_compose_cli_previews_existing_commander_plan(self) -> None:
        import json
        import tempfile
        from contextlib import redirect_stdout
        from dataclasses import asdict
        from io import StringIO
        from pathlib import Path
        from muse.video_factory import main

        with tempfile.TemporaryDirectory() as directory:
            spec_path = Path(directory) / "video-prompt.json"
            bible_path = Path(directory) / "story-bible.json"
            spec_path.write_text(json.dumps(asdict(self.make_spec()), ensure_ascii=False), encoding="utf-8")
            bible_path.write_text(json.dumps(sample_bible(), ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["compose", str(spec_path), "--story-bible", str(bible_path)])
            result = json.loads(output.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "composed")
        self.assertEqual(result["storyboard"]["aspect_ratio"], "9:16")
        self.assertEqual(result["continuity_validation"]["continuity_scene_count"], 1)
        self.assertEqual(result["commander_plan_preview"]["source"], "deterministic_video_storyboard")
        self.assertEqual(result["execution"], "not_started")
        self.assertEqual(result["commander_submission"], "not_submitted")


if __name__ == "__main__":
    unittest.main()
