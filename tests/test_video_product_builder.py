from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.initiative_engine import InitiativeStore
from muse.video_product_builder import (
    VideoProductBuilder, VideoProductError, _default_scene_motion_directives, _quality_gate_summary,
    _resolve_recipe_sha256, _probe_video_stream_metadata,
)


class VideoProductBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "data" / "video_sources" / "demo"
        source.mkdir(parents=True)
        (source / "01.png").write_bytes(b"image fixture")
        (source / "02.mp4").write_bytes(b"video fixture")
        (source / "voice.wav").write_bytes(b"audio fixture")
        self.builder = VideoProductBuilder(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _inventory(self):
        return SimpleNamespace(tools=(SimpleNamespace(tool_id="ffmpeg", executable="ffmpeg"),
                                      SimpleNamespace(tool_id="ffprobe", executable="ffprobe")),
                               hardware_encoders=("h264_amf",))

    def _render(self, source, output, **kwargs):
        output.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00fixture")
        return {"status": "completed", "output": str(output), "scene_count": 2,
                "duration_seconds": 8.0, "width": 1920, "height": 1080, "fps": 30.0,
                "has_audio": kwargs.get("audio_path") is not None, "encoder": "h264_amf", "bytes": output.stat().st_size,
                "audio_mode": kwargs.get("audio_mode")}

    def test_ffprobe_video_stream_metadata_parses_rational_rate_and_decoded_frame_count(self):
        output = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"streams": [{
                "codec_type": "video", "width": 512, "height": 288,
                "avg_frame_rate": "24000/1001", "r_frame_rate": "24/1",
                "nb_read_frames": "13", "nb_frames": "13",
            }]}),
            stderr="",
        )
        with patch("muse.video_product_builder.subprocess.run", return_value=output) as run:
            metadata = _probe_video_stream_metadata(self.root / "fixture.mp4", "ffprobe")

        self.assertEqual(metadata[:2], (512, 288))
        self.assertAlmostEqual(metadata[2], 24000 / 1001, places=8)
        self.assertEqual(metadata[3], 13)
        self.assertIn("-count_frames", run.call_args.args[0])

    def test_checkpoint_callback_is_blocked_when_ffprobe_stream_metadata_disagrees(self):
        staging = self.root / "data" / "video_attempt_staging" / ("e" * 32)
        staging.mkdir(parents=True)
        callbacks = []

        def fake_generate(_prompt, output_path, **_kwargs):
            output_path.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return {
                "status": "completed", "prompt_id": "wan-mismatch", "model": "Wan 2.1 T2V 1.3B",
                "width": 512, "height": 288, "fps": 12.0, "frames": 13,
            }

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", return_value=1.0),
            patch("muse.video_product_builder._probe_video_stream_metadata", return_value=(640, 288, 12.0, 13)),
            patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate),
        ):
            with self.assertRaisesRegex(VideoProductError, "FFprobe stream metadata"):
                self.builder.build_from_scenes(
                    "FFprobe mismatch",
                    [{"prompt": "第一镜", "duration_seconds": 1, "seed": 1},
                     {"prompt": "第二镜", "duration_seconds": 1, "seed": 2}],
                    staging_directory=staging,
                    resume_scene_metadata={},
                    reusable_step_ids=(),
                    scene_checkpoint_callback=lambda *args: callbacks.append(args),
                )

        self.assertEqual(callbacks, [])
        self.assertTrue((staging / "shot_01.mp4").is_file())

    def test_final_video_metadata_reports_post_production_duration_and_preserves_source_facts(self):
        from muse.video_product_builder import _finalize_video_metadata

        output = self.root / "final.mp4"
        output.write_bytes(b"final mp4 fixture")
        metadata = _finalize_video_metadata(
            {"duration_seconds": 1.0, "frames": 13, "fps": 12, "width": 512, "height": 288,
             "model": "Wan 2.1 T2V 1.3B", "bytes": 100},
            {"metadata": {"duration_seconds": 4.416667, "fps": 12.0, "width": 512,
                          "height": 288, "audio_codecs": [], "file_bytes": output.stat().st_size}},
            output,
            motion={"source_duration_seconds": 1.083333},
        )
        self.assertAlmostEqual(metadata["source_duration_seconds"], 1.083333)
        self.assertEqual(metadata["source_frames"], 13)
        self.assertAlmostEqual(metadata["duration_seconds"], 4.416667)
        self.assertEqual(metadata["frames"], 53)
        self.assertEqual(metadata["bytes"], output.stat().st_size)
        self.assertFalse(metadata["has_audio"])

    def test_builds_versioned_video_product_and_verifies_zip_and_mp4(self):
        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render):
            result = self.builder.build_from_source("demo", "测试短片", audio_name="voice.wav", audio_mode="narration")
        self.assertTrue(result["validated"])
        self.assertEqual(result["kind"], "video_creator")
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["execution"]["ai_generation"], "not_requested")
        self.assertEqual(manifest["video_metadata"]["audio_mode"], "narration")
        self.assertEqual(json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))["settings"]["audio_mode"], "narration")
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"README.md", "storyboard.json", "qa-report.json", "video.mp4"})
        again = self.builder.verify_path(self.root / "data" / "products", result["initiative_id"], result["version_id"])
        self.assertTrue(again["validated"])

    def test_local_asset_narration_routes_through_canto_tts_asr_and_remotion_package(self):
        motion = {
            "status": "completed", "profile": "brand_intro_outro",
            "transition": "fade", "duration_seconds": 9.8, "width": 1920, "height": 1080,
            "fps": 30.0, "frames": 294, "bytes": 4096,
        }
        def apply_motion(_base, output, **_kwargs):
            output.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00motion")
            return motion
        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render) as assemble,
            patch("muse.video_product_builder._probe_media_duration_seconds", return_value=9.8),
            patch.object(self.builder, "_apply_motion_profile", side_effect=apply_motion),
            patch.object(
                self.builder, "package_generated_clip_with_cantonese_narration",
                return_value={"validated": True, "kind": "video_creator", "production_mode": "local_asset_assembly"},
            ) as package,
        ):
            result = self.builder.build_from_source(
                "demo", "本地粤语旁白测试", narration_script="你好，欢迎使用 Muse 本地粤语配音流程。",
                audio_mode="music", motion_profile="brand_intro_outro",
            )
        self.assertTrue(result["validated"])
        self.assertEqual(assemble.call_args.kwargs["audio_path"], None)
        self.assertEqual(package.call_count, 1)
        args, kwargs = package.call_args
        self.assertEqual(args[1], "Local asset assembly from data/video_sources/demo; source SHA-256 recorded.")
        self.assertEqual(kwargs["production_mode"], "local_asset_assembly")
        self.assertEqual(kwargs["ai_generation"], "not_requested")
        self.assertEqual(kwargs["motion_profile"], "none")
        self.assertEqual(kwargs["pre_rendered_motion"]["profile"], "brand_intro_outro")
        self.assertEqual(len(kwargs["source_assets"]), 2)
        self.assertEqual(kwargs["generation_metadata"]["width"], 1920)
        self.assertEqual(kwargs["generation_metadata"]["height"], 1080)
        self.assertTrue(str(kwargs["asr_model_dir"]).casefold().endswith(r"\models\whisper-small-cantonese"))
        self.assertTrue(str(kwargs["model_dir"]).casefold().endswith(r"\data\models\canto-tts-nano"))
    def test_scene_timeline_metadata_is_persisted_and_drives_qa_mapping(self):
        qa_report = {
            "status": "passed",
            "warnings": [{"code": "frozen_frames", "start_seconds": 3.8, "duration_seconds": 0.4}],
            "metadata": {"duration_seconds": 7.0, "width": 1920, "height": 1080,
                         "fps": 30.0, "audio_codecs": ["aac"]},
        }
        motion = {
            "status": "completed",
            "profile": "brand_scene_transitions",
            "scene_timeline": {
                "scene_timings": [
                    {"timeline_start_seconds": 0.0, "timeline_end_seconds": 4.0,
                     "duration_seconds": 4.0, "transition_in_seconds": 0.0,
                     "transition_out_seconds": 0.4},
                    {"timeline_start_seconds": 3.6, "timeline_end_seconds": 7.0,
                     "duration_seconds": 3.4, "transition_in_seconds": 0.4,
                     "transition_out_seconds": 0.0},
                ],
            },
        }
        def apply_motion(base_video, output_video, **kwargs):
            output_video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00fixture")
            return motion

        def inspect_product(path, **kwargs):
            report = dict(qa_report)
            report.update({
                "schema_version": 1, "path": "video.mp4",
                "artifact_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
            })
            return report

        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render), \
             patch("muse.video_product_builder._inspect_product_video", side_effect=inspect_product), \
             patch.object(self.builder, "_apply_motion_profile", side_effect=apply_motion):
            result = self.builder.build_from_source(
                "demo", "带转场时间轴的测试短片", motion_profile="brand_scene_transitions",
            )
        product_dir = self.root / result["relative_path"]
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))
        report = json.loads((product_dir / "qa-report.json").read_text(encoding="utf-8"))
        self.assertEqual(storyboard["scenes"][1]["timeline_start_seconds"], 3.6)
        self.assertEqual(storyboard["scenes"][1]["timeline_end_seconds"], 7.0)
        matches = report["storyboard_mapping"][0]["storyboard_matches"]
        self.assertEqual([item["scene_id"] for item in matches], ["shot-01", "shot-02"])
        self.assertEqual([item["source_file"] for item in matches], ["01.png", "02.mp4"])
        self.assertAlmostEqual(matches[0]["overlap_seconds"], 0.2)
        self.assertAlmostEqual(matches[1]["overlap_seconds"], 0.4)
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            packaged_report = json.loads(archive.read("qa-report.json"))
        self.assertEqual(packaged_report["storyboard_mapping"], report["storyboard_mapping"])
        directives = storyboard["settings"]["shot_directives"]
        self.assertEqual([item["camera_move"] for item in directives], ["push_in", "drift_left"])
        self.assertEqual([item["camera_move"] for item in storyboard["scenes"]], ["push_in", "drift_left"])


    def test_default_scene_motion_directives_cycle_deterministically(self):
        directives = _default_scene_motion_directives(7)
        self.assertEqual(
            [item["camera_move"] for item in directives],
            ["push_in", "drift_left", "pull_out", "rise", "drift_right", "push_in", "drift_left"],
        )
        self.assertTrue(all(item["vfx"] == "none" for item in directives))

    def test_brand_scene_transitions_routes_all_source_scenes_through_remotion(self):
        source = self.root / "data" / "video_sources" / "demo"
        base = self.root / "base.mp4"
        base.write_bytes(b"base fixture")
        output = self.root / "final.mp4"
        observed = {}

        class FakeAdapter:
            def __init__(self, workspace, **kwargs):
                observed["workspace"] = workspace

            def render_scene_timeline(self, scenes, audio_source, timeline, **kwargs):
                observed["scenes"] = list(scenes)
                observed["audio_source"] = audio_source
                observed["timeline_kwargs"] = kwargs
                timeline.write_bytes(b"timeline fixture")
                return {"status": "completed", "transition": "alternating_crossfade_wipe",
                        "transition_seconds": 0.4, "scene_count": len(scenes)}

            def render(self, motion_source, target, **kwargs):
                observed["motion_source"] = motion_source
                target.write_bytes(b"final fixture")
                return {"status": "completed", "profile": "brand_intro_outro",
                        "transition": "crossfade", "transition_seconds": 0.45,
                        "renderer": "remotion"}

        with patch("muse.remotion_motion.RemotionMotionAdapter", FakeAdapter):
            result = self.builder._apply_motion_profile(
                base, output, motion_profile="brand_scene_transitions", title="Muse",
                width=1920, height=1080, fps=30, ffmpeg_path="ffmpeg",
                ffprobe_path="ffprobe", timeout_seconds=60, audio_mode="music",
                scene_assets=[source / "01.png", source / "02.mp4"], image_seconds=3.0,
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["transition"], "alternating_crossfade_wipe_plus_bookend_crossfade")
        self.assertEqual(result["scene_count"], 2)
        self.assertEqual(result["scene_transition_seconds"], 0.4)
        self.assertEqual(observed["scenes"], [source / "01.png", source / "02.mp4"])
        self.assertEqual(
            [item["camera_move"] for item in observed["timeline_kwargs"]["shot_directives"]],
            ["push_in", "drift_left"],
        )

        self.assertEqual(observed["motion_source"], base.parent / "scene-timeline.mp4")
        self.assertTrue(output.is_file())

    def test_rejects_source_traversal_and_symlink_assets(self):
        with self.assertRaises(ValueError):
            self.builder.build_from_source("../outside", "bad")
        source = self.root / "data" / "video_sources" / "demo"
        outside = self.root / "outside.png"
        outside.write_bytes(b"outside")
        (source / "linked.png").symlink_to(outside)
        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()):
            with self.assertRaises((VideoProductError, ValueError)):
                self.builder.build_from_source("demo", "bad")

    def test_refuses_missing_ffmpeg_and_failed_render(self):
        empty = SimpleNamespace(tools=(), hardware_encoders=())
        with patch("muse.video_product_builder.discover_media_tools", return_value=empty):
            with self.assertRaisesRegex(VideoProductError, "FFmpeg"):
                self.builder.build_from_source("demo", "blocked")
        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", return_value={"status": "failed", "error": "fixture"}):
            with self.assertRaisesRegex(VideoProductError, "assembly failed"):
                self.builder.build_from_source("demo", "failed")

    def test_rejects_source_changes_during_render(self):
        def mutate_source(source, output, **kwargs):
            result = self._render(source, output, **kwargs)
            (source / "01.png").write_bytes(b"changed while rendering")
            return result
        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=mutate_source):
            with self.assertRaisesRegex(VideoProductError, "changed during rendering"):
                self.builder.build_from_source("demo", "race")

    def test_rejects_modified_product_and_invalid_mp4_signature(self):
        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render):
            result = self.builder.build_from_source("demo", "tamper")
        product_dir = self.root / result["relative_path"]
        (product_dir / "video.mp4").write_bytes(b"not an mp4")
        with self.assertRaisesRegex(VideoProductError, "integrity"):
            self.builder.verify_path(self.root / "data" / "products", result["initiative_id"], result["version_id"])

    def test_prompt_generation_guard_is_forwarded_inside_the_live_wan_gpu_lease(self):
        lease = SimpleNamespace(
            lease_id="wan-lease-prompt", owner_id="wan-owner-prompt",
            coordination={"status": "ready"}, assert_valid=lambda: None,
        )
        guarded = []

        def guard(received_lease):
            guarded.append(received_lease)

        def fake_generate(_prompt, _output_path, **kwargs):
            kwargs["admission_guard"](lease)
            raise RuntimeError("stop after lease-bound admission callback")

        with patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate):
            with self.assertRaisesRegex(RuntimeError, "lease-bound admission callback"):
                self.builder.build_from_prompt(
                    "租约内授权测试", "一只狐狸穿过竹林", seed=123,
                    generation_guard=guard,
                )

        self.assertEqual(guarded, [lease])

    def test_builds_ai_generated_video_product_from_local_wan_engine(self):
        def fake_generate(prompt, output, **kwargs):
            output.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            self.assertEqual(kwargs["duration_seconds"], 5.0)
            self.assertEqual(kwargs["steps"], 20)
            return {"status": "completed", "prompt_id": "prompt-123", "model": "Wan 2.1 T2V 1.3B",
                    "seed": kwargs["seed"], "aspect_ratio": kwargs["aspect_ratio"], "fps": 16,
                    "duration_seconds": 5.0, "frames": 81, "width": 832, "height": 480,
                    "steps": kwargs["steps"], "bytes": output.stat().st_size, "output": str(output)}
        recipe_sha256 = "a" * 64
        with patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate):
            result = self.builder.build_from_prompt(
                "AI 测试短片", "一只狐狸穿过竹林", seed=123, recipe_sha256=recipe_sha256,
            )
        self.assertTrue(result["validated"])
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["execution"]["ai_generation"], "completed")
        self.assertEqual(storyboard["settings"]["fps"], 16)
        self.assertEqual(storyboard["settings"]["resolution_tier"], "wan21_480p_master")
        self.assertEqual(storyboard["settings"]["quality_profile"], "wan21_t2v_1.3b_480p_quality_first")
        self.assertEqual(storyboard["production_mode"], "local_ai_text_to_video")
        self.assertEqual(storyboard["prompt"], "一只狐狸穿过竹林")
        self.assertEqual(manifest["source_assets"], [])
        self.assertEqual(manifest["recipe_provenance"]["recipe_sha256"], recipe_sha256)
        self.assertEqual(result["recipe_sha256"], recipe_sha256)
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"README.md", "storyboard.json", "qa-report.json", "visual-quality-report.json", "video.mp4"})


    def test_recipe_sha256_resolves_from_creator_brief_without_loading_external_services(self):
        recipe_sha256 = "b" * 64
        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            initiative = store.propose(
                "Recipe-linked creator", kind="creative_output",
                creator_value="Preserve approved recipe provenance",
                concept=json.dumps({"creator_brief": {"video": {"recipe_sha256": recipe_sha256}}}),
                first_step="Build one verified preview", expected_output="A recipe-linked video manifest",
            )
        self.assertEqual(_resolve_recipe_sha256(self.root, initiative["id"], None), recipe_sha256)
        self.assertEqual(_resolve_recipe_sha256(self.root, initiative["id"], recipe_sha256), recipe_sha256)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            _resolve_recipe_sha256(self.root, initiative["id"], "invalid")
        with self.assertRaisesRegex(ValueError, "does not match the creator brief"):
            _resolve_recipe_sha256(self.root, initiative["id"], "c" * 64)
        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            store.start_video_attempt(
                "recipe-linked-attempt", mode="prompt_to_video",
                initiative_id=initiative["id"], recipe_sha256=recipe_sha256,
            )
        self.assertEqual(_resolve_recipe_sha256(self.root, initiative["id"], None), recipe_sha256)
        with self.assertRaisesRegex(ValueError, "active video attempt"):
            _resolve_recipe_sha256(self.root, initiative["id"], "c" * 64)

    def test_packages_recovered_wan_clip_with_ai_provenance_and_verified_zip(self):
        source = self.root / "recovered-wan.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        metadata = {
            "status": "completed", "prompt_id": "wan-real-123", "model": "Wan 2.1 T2V 1.3B",
            "seed": 20261111, "aspect_ratio": "9:16", "fps": 12,
            "duration_seconds": 2.083, "frames": 25, "width": 288, "height": 512,
            "steps": 8, "bytes": source.stat().st_size,
        }
        scene_records = [
            {"order": 1, "prompt": "纸鹤停在古桥", "prompt_id": "shot-1", "seed": 11, "source_file": "shot-01.mp4"},
            {"order": 2, "prompt": "纸鹤飞向星图", "prompt_id": "shot-2", "seed": 12, "source_file": "shot-02.mp4"},
        ]
        result = self.builder.package_generated_clip(
            "恢复的 Wan 短片", "一只纸鹤化作天空之城", source,
            generation_metadata=metadata, scene_records=scene_records,
        )
        self.assertTrue(result["validated"])
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["execution"]["ai_generation"], "completed")
        self.assertEqual(manifest["video_metadata"]["prompt_id"], "wan-real-123")
        self.assertEqual(storyboard["production_mode"], "local_ai_text_to_video")
        self.assertEqual(storyboard["source_video_sha256"], manifest["generated_clip"]["sha256"])
        self.assertEqual(len(storyboard["scenes"]), 2)
        self.assertEqual(storyboard["scenes"][1]["prompt_id"], "shot-2")
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"README.md", "storyboard.json", "qa-report.json", "visual-quality-report.json", "video.mp4"})
            self.assertIsNone(archive.testzip())

    def test_narration_duration_extends_wan_scenes_in_4n_plus_1_frame_steps(self):
        from muse.video_product_builder import _allocate_wan_scene_durations

        scenes = [
            {"order": 1, "prompt": "镜头一", "duration_seconds": 1.0, "seed": 1},
            {"order": 2, "prompt": "镜头二", "duration_seconds": 1.0, "seed": 2},
        ]
        allocated = _allocate_wan_scene_durations(scenes, 3.0)
        self.assertGreater(sum(item["duration_seconds"] for item in allocated), 3.0 - 1e-9)
        self.assertTrue(all(1.0 <= item["duration_seconds"] <= 5.0 for item in allocated))
        self.assertEqual([item["seed"] for item in allocated], [1, 2])
        self.assertEqual(sum(round(item["duration_seconds"] * 16) + 1 for item in allocated), 50)

    def test_build_from_scenes_synthesizes_once_and_plans_voiceover_before_wan(self):
        calls = {"tts": 0, "wan": [], "package": None}

        def fake_synthesize(text, output, *, model_dir, packages_dir, quality):
            calls["tts"] += 1
            output.write_bytes(b"RIFF" + b"x" * 64)
            return {
                "status": "completed", "engine": "canto-tts-onnx", "model_repo": "typangaa/canto-tts-nano",
                "model_revision": "pinned", "license": "Apache-2.0", "language": "zh-HK",
                "sample_rate": 48000, "channels": 1, "frames": 144000, "duration_seconds": 3.0,
                "bytes": output.stat().st_size,
            }

        def fake_generate(prompt, output_path, **kwargs):
            calls["wan"].append((prompt, kwargs["duration_seconds"]))
            output_path.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            frames = round(kwargs["duration_seconds"] * 16) + 1
            return {
                "status": "completed", "prompt_id": f"wan-{len(calls['wan'])}", "seed": kwargs["seed"],
                "width": 832, "height": 480, "fps": 16, "frames": frames,
            }

        def fake_concat(args, **kwargs):
            Path(args[-1]).write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return SimpleNamespace(returncode=0, stderr="")

        def fake_package(title, prompt, clip, **kwargs):
            calls["package"] = kwargs
            self.assertTrue(kwargs["narration_path"].is_file())
            self.assertEqual(kwargs["narration_script"], "呢段粵語旁白需要三秒。")
            return {"validated": True, "relative_path": "data/products/narrated-scenes"}

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}),
            patch("muse.cantonese_tts.synthesize_cantonese_segments_with_runtime", side_effect=fake_synthesize),
            patch("muse.video_product_builder._probe_media_duration_seconds",
                  side_effect=[3.0, 1.75, 1.4166667, 3.1666667, 3.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_concat),
            patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate),
            patch.object(self.builder, "package_generated_clip", side_effect=fake_package),
        ):
            result = self.builder.build_from_scenes(
                "旁白驅動分鏡",
                [{"prompt": "第一鏡", "duration_seconds": 1, "seed": 11},
                 {"prompt": "第二鏡", "duration_seconds": 1, "seed": 22}],
                narration_script="呢段粵語旁白需要三秒。",
                narration_model_dir=self.root / "tts-model",
            )

        self.assertEqual(calls["tts"], 1)
        self.assertEqual(len(calls["wan"]), 2)
        self.assertGreaterEqual(sum(duration for _, duration in calls["wan"]), 3.0)
        self.assertEqual(result["narration_synthesis"]["language"], "zh-HK")
        self.assertEqual(result["scene_generation"]["duration_planning"], "cantonese_narration_frame_quantized")
        self.assertEqual(calls["package"]["scene_asset_paths"].__len__(), 2)

    def test_overlong_cantonese_narration_fails_before_any_wan_generation(self):
        def fake_synthesize(text, output, **kwargs):
            output.write_bytes(b"RIFF" + b"x" * 64)
            return {"status": "completed", "duration_seconds": 11.0}

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}),
            patch("muse.cantonese_tts.synthesize_cantonese_segments_with_runtime", side_effect=fake_synthesize),
            patch("muse.video_product_builder._probe_media_duration_seconds", return_value=11.0),
            patch("muse.wan_video.generate_wan21_t2v") as generate,
        ):
            with self.assertRaisesRegex(VideoProductError, "exceeds the maximum duration"):
                self.builder.build_from_scenes(
                    "旁白超长",
                    [{"prompt": "第一镜", "duration_seconds": 1, "seed": 1},
                     {"prompt": "第二镜", "duration_seconds": 1, "seed": 2}],
                    narration_script="一段超长旁白。",
                    narration_model_dir=self.root / "tts-model",
                )
        generate.assert_not_called()

    def test_build_from_scenes_generates_independent_shots_and_packages_one_concatenated_product(self):
        generated_calls = []

        def fake_generate(prompt, output_path, **kwargs):
            output_path.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            generated_calls.append((prompt, kwargs["seed"], kwargs["duration_seconds"], kwargs["negative_prompt"]))
            return {
                "status": "completed", "prompt_id": f"wan-{len(generated_calls)}",
                "model": "Wan 2.1 T2V 1.3B", "seed": kwargs["seed"],
                "width": 512, "height": 288, "fps": 12, "frames": 13,
                "duration_seconds": kwargs["duration_seconds"], "bytes": output_path.stat().st_size,
            }

        def fake_concat(args, **kwargs):
            Path(args[-1]).write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return SimpleNamespace(returncode=0, stderr="")

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[1.0, 2.0, 3.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_concat) as concat,
            patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate),
            patch.object(self.builder, "package_generated_clip", return_value={"validated": True}) as package,
        ):
            result = self.builder.build_from_scenes("多镜头测试", [
                {"prompt": "清晨城市全景", "duration_seconds": 1, "seed": 101,
                 "negative_prompt": "避免额外人物"},
                {"prompt": "镜头推进到街道", "duration_seconds": 2, "seed": 202,
                 "negative_prompt": "避免字幕"},
            ], negative_prompt="避免画面文字")

        self.assertTrue(result["validated"])
        self.assertEqual(generated_calls, [
            ("清晨城市全景", 101, 1.0, "避免画面文字\n避免额外人物"),
            ("镜头推进到街道", 202, 2.0, "避免画面文字\n避免字幕"),
        ])
        self.assertEqual(concat.call_count, 1)
        self.assertIn("concat", concat.call_args.args[0])
        self.assertIn(str("-i"), concat.call_args.args[0])
        self.assertEqual(package.call_count, 1)
        self.assertEqual(package.call_args.kwargs["generation_metadata"]["duration_seconds"], 3.0)
        self.assertEqual(len(package.call_args.kwargs["scene_asset_paths"]), 2)
        self.assertEqual([scene["seed"] for scene in package.call_args.kwargs["scene_records"]], [101, 202])
        self.assertEqual([scene["duration_seconds"] for scene in package.call_args.kwargs["scene_records"]], [1.0, 2.0])
        self.assertEqual(
            [scene["negative_prompt"] for scene in package.call_args.kwargs["scene_records"]],
            ["避免画面文字\n避免额外人物", "避免画面文字\n避免字幕"],
        )

    def test_build_from_scenes_rejects_combined_negative_prompt_over_limit_before_wan_dispatch(self):
        with patch("muse.wan_video.generate_wan21_t2v") as generate:
            with self.assertRaisesRegex(ValueError, "combined negative_prompt exceeds 1000 characters"):
                self.builder.build_from_scenes(
                    "过长负面提示词", [
                        {"prompt": "第一镜", "duration_seconds": 1, "seed": 1, "negative_prompt": "s" * 401},
                        {"prompt": "第二镜", "duration_seconds": 1, "seed": 2},
                    ], negative_prompt="g" * 600,
                )
        generate.assert_not_called()

    def test_build_from_scenes_resumes_verified_shot_and_keeps_attempt_staging(self):
        attempt_id = "a" * 32
        attempt_staging = self.root / "data" / "video_attempt_staging" / attempt_id
        old_epoch_staging = attempt_staging / "epoch_1"
        staging = attempt_staging / "epoch_2"
        old_epoch_staging.mkdir(parents=True)
        staging.mkdir()
        first_shot = old_epoch_staging / "shot_01.mp4"
        first_shot.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        generation = {
            "status": "completed", "prompt_id": "wan-reused", "model": "Wan 2.1 T2V 1.3B",
            "width": 512, "height": 288, "fps": 12.0, "frames": 13,
        }
        generated_calls = []
        checkpoint_calls = []

        def fake_generate(prompt, output_path, **kwargs):
            generated_calls.append(prompt)
            output_path.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return {
                "status": "completed", "prompt_id": "wan-new", "model": "Wan 2.1 T2V 1.3B",
                "width": 512, "height": 288, "fps": 12, "frames": 13,
            }

        def fake_concat(args, **kwargs):
            Path(args[-1]).write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return SimpleNamespace(returncode=0, stderr="")

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[1.0, 2.0, 3.0]),
            patch("muse.video_product_builder._probe_video_stream_metadata", return_value=(512, 288, 12.0, 13)),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_concat),
            patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate) as generate,
            patch.object(self.builder, "package_generated_clip", return_value={"validated": True}),
        ):
            result = self.builder.build_from_scenes(
                "断点续作测试",
                [{"prompt": "第一镜", "duration_seconds": 1, "seed": 11},
                 {"prompt": "第二镜", "duration_seconds": 2, "seed": 22}],
                staging_directory=staging,
                resume_scene_metadata={
                    "shot_001": {
                        "artifact_relative_path": first_shot.relative_to(self.root).as_posix(),
                        "generation": generation,
                    },
                },
                reusable_step_ids=("shot_001",),
                scene_checkpoint_callback=lambda *args: checkpoint_calls.append(args),
            )

        self.assertEqual(generated_calls, ["第二镜"])
        generate.assert_called_once()
        self.assertEqual(len(checkpoint_calls), 1)
        self.assertEqual(checkpoint_calls[0][0], "shot_002")
        self.assertTrue(checkpoint_calls[0][1].is_file())
        self.assertEqual(checkpoint_calls[0][2]["status"], "completed")
        scenes = result["scene_generation"]["scenes"]
        self.assertEqual([item["generation_status"] for item in scenes], ["reused", "generated"])
        self.assertTrue(staging.is_dir())
        self.assertTrue(old_epoch_staging.is_dir())
        self.assertTrue(first_shot.is_file())
        self.assertTrue((staging / "shot_02.mp4").is_file())
        self.assertTrue((staging / "combined.mp4").is_file())

    def test_scene_generation_guard_stops_before_dispatching_the_next_shot(self):
        attempt_id = "c" * 32
        staging = self.root / "data" / "video_attempt_staging" / attempt_id
        staging.mkdir(parents=True)
        generated = []
        checkpoints = []
        guarded = []

        lease = SimpleNamespace(
            lease_id="wan-lease-scenes", owner_id="wan-owner-scenes",
            coordination={"status": "ready"}, assert_valid=lambda: None,
        )

        def fake_generate(prompt, output_path, **kwargs):
            kwargs["admission_guard"](lease)
            generated.append(prompt)
            output_path.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return {
                "status": "completed", "prompt_id": "guarded-shot", "model": "Wan 2.1 T2V 1.3B",
                "width": 512, "height": 288, "fps": 12, "frames": 13,
            }

        def guard(step_id, scene=None, lease_context=None):
            self.assertIs(lease_context, lease)
            guarded.append(step_id)
            if step_id == "shot_002":
                raise PermissionError("Commander approval revoked")

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", return_value=1.0),
            patch("muse.video_product_builder._probe_video_stream_metadata", return_value=(512, 288, 12.0, 13)),
            patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate),
        ):
            with self.assertRaisesRegex(PermissionError, "approval revoked"):
                self.builder.build_from_scenes(
                    "审批撤销测试",
                    [{"prompt": "第一镜", "duration_seconds": 1, "seed": 1},
                     {"prompt": "第二镜", "duration_seconds": 1, "seed": 2}],
                    staging_directory=staging,
                    resume_scene_metadata={},
                    reusable_step_ids=(),
                    scene_checkpoint_callback=lambda *args: checkpoints.append(args),
                    scene_generation_guard=guard,
                )

        self.assertEqual(guarded, ["shot_001", "shot_002"])
        self.assertEqual(generated, ["第一镜"])
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0][0], "shot_001")

    def test_build_from_scenes_preserves_completed_checkpoint_when_later_scene_fails(self):
        attempt_id = "b" * 32
        staging = self.root / "data" / "video_attempt_staging" / attempt_id
        staging.mkdir(parents=True)
        checkpoint_calls = []
        generated = 0

        def fake_generate(prompt, output_path, **kwargs):
            nonlocal generated
            generated += 1
            if generated == 2:
                return {"status": "failed"}
            output_path.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return {
                "status": "completed", "prompt_id": "wan-first", "model": "Wan 2.1 T2V 1.3B",
                "width": 512, "height": 288, "fps": 12, "frames": 13,
            }

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", return_value=1.0),
            patch("muse.video_product_builder._probe_video_stream_metadata", return_value=(512, 288, 12.0, 13)),
            patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate),
            patch.object(self.builder, "package_generated_clip") as package,
        ):
            with self.assertRaisesRegex(VideoProductError, "scene 2.*partial delivery"):
                self.builder.build_from_scenes(
                    "失败后保留检查点",
                    [{"prompt": "第一镜", "duration_seconds": 1, "seed": 1},
                     {"prompt": "第二镜", "duration_seconds": 1, "seed": 2}],
                    staging_directory=staging,
                    resume_scene_metadata={},
                    reusable_step_ids=(),
                    scene_checkpoint_callback=lambda *args: checkpoint_calls.append(args),
                )

        package.assert_not_called()
        self.assertEqual(len(checkpoint_calls), 1)
        self.assertEqual(checkpoint_calls[0][0], "shot_001")
        self.assertTrue((staging / "shot_01.mp4").is_file())
        self.assertTrue(staging.is_dir())

    def test_build_from_scenes_rejects_checkpoint_artifact_from_another_attempt(self):
        attempt_id = "c" * 32
        other_attempt_id = "d" * 32
        staging = self.root / "data" / "video_attempt_staging" / attempt_id
        other_staging = self.root / "data" / "video_attempt_staging" / other_attempt_id
        staging.mkdir(parents=True)
        other_staging.mkdir(parents=True)
        foreign_shot = other_staging / "shot_01.mp4"
        foreign_shot.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.wan_video.generate_wan21_t2v") as generate,
        ):
            with self.assertRaisesRegex(VideoProductError, "outside this Attempt staging"):
                self.builder.build_from_scenes(
                    "跨 Attempt 检查",
                    [{"prompt": "第一镜", "duration_seconds": 1, "seed": 1},
                     {"prompt": "第二镜", "duration_seconds": 1, "seed": 2}],
                    staging_directory=staging,
                    resume_scene_metadata={
                        "shot_001": {
                            "artifact_relative_path": foreign_shot.relative_to(self.root).as_posix(),
                            "generation": {
                                "status": "completed", "width": 512, "height": 288,
                                "fps": 12.0, "frames": 13,
                            },
                        },
                    },
                    reusable_step_ids=("shot_001",),
                    scene_checkpoint_callback=lambda *_args: None,
                )
        generate.assert_not_called()

    def test_build_from_scenes_stops_on_failed_shot_without_packaging_partial_video(self):
        calls = 0

        def fake_generate(prompt, output_path, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                return {"status": "failed"}
            output_path.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return {"status": "completed", "width": 512, "height": 288, "fps": 12,
                    "frames": 13, "prompt_id": "wan-1", "seed": kwargs["seed"]}

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", return_value=1.0),
            patch("muse.video_product_builder._probe_video_stream_metadata", return_value=(512, 288, 12.0, 13)),
            patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate),
            patch.object(self.builder, "package_generated_clip") as package,
        ):
            with self.assertRaisesRegex(VideoProductError, "scene 2.*partial delivery"):
                self.builder.build_from_scenes("不完整镜头", [
                    {"prompt": "镜头一", "duration_seconds": 1, "seed": 1},
                    {"prompt": "镜头二", "duration_seconds": 1, "seed": 2},
                ])
        package.assert_not_called()

    def test_build_from_scenes_enforces_total_preview_duration_limit(self):
        scenes = [{"prompt": f"镜头 {index}", "duration_seconds": 5, "seed": index} for index in range(13)]
        with self.assertRaisesRegex(ValueError, "must not exceed 60 seconds"):
            self.builder.build_from_scenes("超长预览", scenes)

    def test_package_generated_clip_archives_and_hashes_individual_scene_sources(self):
        source = self.root / "combined.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        shots = []
        scene_records = []
        for index, prompt in enumerate(("第一镜", "第二镜"), start=1):
            shot = self.root / f"shot_{index:02d}.mp4"
            shot.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265") + bytes([index]))
            shots.append(shot)
            scene_records.append({
                "order": index, "prompt": prompt, "generation_status": "generated",
                "seed": index, "source_file": shot.name, "duration_seconds": 1.0,
                "frames": 13, "width": 512, "height": 288, "fps": 12.0,
            })
        final_metadata = {
            "duration_seconds": 2.0, "frames": 26, "width": 512, "height": 288,
            "fps": 12, "has_audio": False, "bytes": source.stat().st_size,
        }
        qa_report = {
            "schema_version": 1, "status": "passed", "path": "video.mp4",
            "artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._inspect_product_video", return_value=qa_report),
            patch("muse.video_product_builder._quality_gate_summary", return_value={"status": "passed", "passed": True, "ready_to_publish": False, "report": "qa-report.json"}),
            patch("muse.video_product_builder._finalize_video_metadata", return_value=final_metadata),
        ):
            result = self.builder.package_generated_clip(
                "保留分镜素材", "多镜头测试", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12, "recipe_sha256": "d" * 64},
                scene_records=scene_records, scene_asset_paths=shots,
            )
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertIn("scenes/shot_01.mp4", archive.namelist())
            self.assertIn("scenes/shot_02.mp4", archive.namelist())
            self.assertEqual(archive.read("scenes/shot_01.mp4"), shots[0].read_bytes())
        self.assertEqual(len(manifest["scene_assets"]), 2)
        self.assertEqual(manifest["recipe_provenance"]["recipe_sha256"], "d" * 64)
        self.assertEqual(result["recipe_sha256"], "d" * 64)
        self.assertEqual(manifest["scene_assets"][0]["sha256"], hashlib.sha256(shots[0].read_bytes()).hexdigest())
    def test_narration_timeline_rejects_audio_that_would_be_truncated(self):
        source = self.root / "recovered-wan.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))

        narration = self.root / "voice.wav"
        narration.write_bytes(b"RIFF" + b"x" * 64)
        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[1.083333, 17.36]),
        ):
            with self.assertRaisesRegex(VideoProductError, "refusing to truncate voiceover"):
                self.builder.package_generated_clip(
                    "时长不匹配", "测试镜头", source,
                    generation_metadata={"status": "completed", "duration_seconds": 1.083333},
                    narration_path=narration, narration_script="一段较长的粤语旁白。",
                )

    def test_narration_timeline_allows_shorter_audio_and_records_alignment(self):
        from muse.video_product_builder import _validate_narration_timeline

        result = _validate_narration_timeline(12.0, 10.5)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["video_duration_seconds"], 12.0)
        self.assertEqual(result["narration_duration_seconds"], 10.5)
        self.assertEqual(result["policy"], "narration_must_fit_video_no_silent_truncation")




    def test_cantonese_narration_wrapper_synthesizes_then_packages_and_cleans_temporary_audio(self):

        source = self.root / "recovered-wan.mp4"

        observed = {}

        def fake_synthesize(text, output, *, model_dir, packages_dir, quality):
            observed["text"] = text
            observed["model_dir"] = model_dir
            observed["quality"] = quality
            output.write_bytes(b"RIFF" + b"x" * 48)
            observed["temporary_audio"] = output
            return {
                "status": "completed", "engine": "canto-tts-onnx",
                "model_repo": "typangaa/canto-tts-nano", "model_revision": "pinned",
                "license": "Apache-2.0", "language": "zh-HK", "sample_rate": 48000,
                "channels": 1, "frames": 48000, "duration_seconds": 1.0, "bytes": output.stat().st_size,
                "output": str(output),
            }

        def fake_package(title, prompt, clip, **kwargs):
            observed["package_kwargs"] = kwargs
            self.assertTrue(kwargs["narration_path"].is_file())
            self.assertEqual(kwargs["narration_script"], "多謝晒，我哋開始啦。")
            return {"validated": True, "relative_path": "data/products/fake"}

        with (
            patch("muse.cantonese_tts.synthesize_cantonese_segments_with_runtime", side_effect=fake_synthesize),
            patch.object(self.builder, "package_generated_clip", side_effect=fake_package),
        ):
            result = self.builder.package_generated_clip_with_cantonese_narration(
                "粤语旁白测试", "城市夜景", source, narration_script="多謝晒，我哋開始啦。",
                model_dir=self.root / "model", generation_metadata={"status": "completed"},
            )
        self.assertTrue(result["validated"])
        self.assertEqual(result["narration_synthesis"]["language"], "zh-HK")
        self.assertEqual(result["narration_synthesis"]["sample_rate"], 48000)
        self.assertFalse(observed["temporary_audio"].exists())

    def test_cantonese_narration_wrapper_preserves_structured_segments_and_prosody_evidence(self):
        import wave
        from muse.cantonese_tts import _synthesize_segments_with_engine
        from muse.narration_prosody_bridge import build_cantonese_narration_prosody_plan

        source = self.root / "structured-narration-source.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        generated = {
            "style": "yujie_canto",
            "segments": [
                {"text": "第一段。", "emotion": "calm", "voice": {
                    "speed_scale": 1.0, "pitch_semitones": 0.0, "breath_hint": True,
                }, "pause_after_ms": 200},
                {"text": "第二段。", "emotion": "warm", "voice": {
                    "speed_scale": 0.9, "pitch_semitones": 1.0, "breath_hint": False,
                }},
                {"text": "第三段。", "emotion": "calm", "voice": {
                    "speed_scale": 1.1, "pitch_semitones": -1.0, "breath_hint": True,
                }},
            ],
        }
        plan = build_cantonese_narration_prosody_plan(generated)
        from muse.narration_prosody_bridge import build_cantonese_tts_segments
        payload = build_cantonese_tts_segments(plan)
        script = "\n".join(item["text"] for item in payload)
        observed = {}

        class FakeTTS:
            def synthesize(self, text, path, **kwargs):
                with wave.open(path, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(48_000)
                    audio.writeframes(bytes([0, 0]) * 480)

        def fake_synthesize(segments, output, *, model_dir, packages_dir, quality):
            observed["segments"] = segments
            result = _synthesize_segments_with_engine(
                segments, output, engine=FakeTTS(), quality="duration_filter",
            )
            observed["temporary_audio"] = output
            return result

        def fake_package(title, prompt, clip, **kwargs):
            observed["package_kwargs"] = kwargs
            self.assertTrue(kwargs["narration_path"].is_file())
            return {"validated": True, "relative_path": "data/products/structured-narration"}

        with (
            patch("muse.cantonese_tts.synthesize_cantonese_segment_payload_with_runtime", side_effect=fake_synthesize),
            patch.object(self.builder, "package_generated_clip", side_effect=fake_package),
        ):
            result = self.builder.package_generated_clip_with_cantonese_narration(
                "结构化粤语旁白", "source-grounded prompt", source,
                narration_script=script, narration_prosody_plan=plan,
                model_dir=self.root / "model", generation_metadata={"status": "completed"},
            )

        record = observed["package_kwargs"]["narration_prosody"]
        self.assertEqual(observed["segments"], payload)
        self.assertEqual(observed["package_kwargs"]["narration_script"], script)
        self.assertEqual(record["schema"], "muse.narration.prosody.runtime.v1")
        self.assertFalse(record["audio_effects_verified"])
        self.assertTrue(record["waveform_pause_accounting_verified"])
        self.assertTrue(record["waveform_pause_verified"])
        self.assertTrue(record["human_listening_review_required"])
        self.assertIn("emotion", record["segments"][0]["unsupported_controls"])
        self.assertIn("rate_scale", record["segments"][0]["unsupported_controls"])
        self.assertEqual(record["runtime"]["audio_sha256"], result["narration_synthesis"]["audio_sha256"])
        self.assertEqual(record["runtime"]["applied_controls"], ["pause_after"])
        self.assertEqual(result["narration_synthesis"]["segment_count"], 3)
        self.assertFalse(observed["temporary_audio"].exists())

    def test_package_generated_clip_rejects_prosody_evidence_bound_to_another_audio(self):
        audio = self.root / "narration-unbound.wav"
        audio.write_bytes(b"RIFF" + b"audio-fixture")
        source = self.root / "data" / "video_sources" / "demo" / "02.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        record = {
            "schema": "muse.narration.prosody.runtime.v1",
            "backend": "canto-tts-nano",
            "backend_capabilities": ["pause_after"],
            "audio_effects_verified": False,
            "waveform_pause_accounting_verified": True,
            "waveform_pause_verified": False,
            "human_listening_review_required": True,
            "segments": [{"index": 0}],
            "runtime": {
                "audio_sha256": "0" * 64,
                "segment_count": 1,
                "inserted_pause_frames": 0,
                "backend_capabilities": ["pause_after"],
                "applied_controls": [],
            },
        }
        with self.assertRaisesRegex(VideoProductError, "bind the actual narration WAV"):
            self.builder.package_generated_clip(
                "未绑定旁白", "source-grounded prompt", source,
                generation_metadata={"status": "completed"},
                narration_path=audio, narration_script="测试旁白。",
                narration_prosody=record,
            )

    def test_prompt_builder_preflight_failure_prevents_wan_render(self):
        with (
            patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", side_effect=RuntimeError("TTS runtime unavailable")),
            patch("muse.wan_video.generate_wan21_t2v") as generate,
        ):
            with self.assertRaisesRegex(RuntimeError, "TTS runtime unavailable"):
                self.builder.build_from_prompt(
                    "Preflight gate", "source-grounded prompt", steps=8,
                    narration_script="呢段係測試。", narration_model_dir=self.root / "model",
                )
        generate.assert_not_called()

    def test_prompt_builder_routes_narration_to_existing_cantonese_product_packager(self):
        captured = {}

        def fake_preflight(packages, model):
            captured["preflight"] = True
            return {"status": "ready"}

        def fake_generate(prompt, output, **kwargs):
            self.assertTrue(captured.get("preflight"))
            output.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            captured["prompt"] = prompt
            return {
                "status": "completed", "output": str(output), "prompt_id": "prompt-1",
                "model": "Wan 2.1 T2V 1.3B", "seed": 7, "aspect_ratio": "9:16",
                "fps": 12, "duration_seconds": 2.0, "frames": 24,
                "width": 512, "height": 288, "steps": 8,
                "bytes": output.stat().st_size, "has_audio": False,
            }

        packaged = {"validated": True, "relative_path": "data/products/cantonese-test",
                    "narration_synthesis": {"status": "completed", "language": "zh-HK"}}

        def fake_package(*args, **kwargs):
            captured["source_exists_during_packaging"] = args[2].is_file()
            captured["package_args"] = args
            captured["package_kwargs"] = kwargs
            return packaged

        with (
            patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", side_effect=fake_preflight),
            patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate),
            patch.object(self.builder, "package_generated_clip_with_cantonese_narration", side_effect=fake_package) as package,
        ):
            result = self.builder.build_from_prompt(
                "粵語旁白整合", "source-grounded prompt", aspect_ratio="9:16", steps=8,
                narration_script="係㗎，呢個係一個仍待驗證嘅試點。",
                narration_model_dir=self.root / "data" / "models" / "canto-tts-nano",
            )
        self.assertTrue(result["validated"])
        package.assert_called_once()
        args = captured["package_args"]
        kwargs = captured["package_kwargs"]
        self.assertEqual(args[0], "粵語旁白整合")
        self.assertTrue(captured["source_exists_during_packaging"])
        self.assertEqual(kwargs["narration_script"], "係㗎，呢個係一個仍待驗證嘅試點。")
        self.assertEqual(kwargs["model_dir"], self.root / "data" / "models" / "canto-tts-nano")
        self.assertEqual(kwargs["generation_metadata"]["prompt_id"], "prompt-1")

    def test_cantonese_narration_wrapper_can_run_local_asr_and_package_subtitles(self):
        import hashlib
        from muse.cantonese_asr import MODEL_REPO, MODEL_REVISION
        source = self.root / "asr-wrapper-source.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        observed = {}
        def fake_synthesize(text, output, *, model_dir, packages_dir, quality):
            output.write_bytes(b"RIFF" + b"voice-fixture")
            observed["audio"] = output
            return {"status": "completed", "engine": "canto-tts-onnx", "model_repo": "typangaa/canto-tts-nano",
                    "model_revision": "pinned", "license": "Apache-2.0", "language": "zh-HK",
                    "sample_rate": 48000, "channels": 2, "frames": 48000, "duration_seconds": 1.0, "bytes": output.stat().st_size}
        def fake_asr(audio_path, *, model_dir, output_srt, reference_text):
            output_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n大家好。\n", encoding="utf-8")
            observed["subtitle"] = output_srt
            observed["reference"] = reference_text
            return {"status": "passed", "engine": "faster-whisper-local", "model_repo": MODEL_REPO,
                    "model_revision": MODEL_REVISION, "model_sha256": "a" * 64,
                    "audio_sha256": hashlib.sha256(audio_path.read_bytes()).hexdigest(),
                    "transcript": "大家好。", "segment_count": 1,
                    "comparison": {"status": "passed", "metric": "character_error_rate", "cer": 0.0, "max_cer": 0.18},
                    "subtitles": {"bytes": output_srt.stat().st_size, "sha256": hashlib.sha256(output_srt.read_bytes()).hexdigest(), "cue_count": 1},
                    "human_listening_review_required": True}
        def fake_package(title, prompt, clip, **kwargs):
            observed["package_kwargs"] = kwargs
            self.assertTrue(kwargs["narration_path"].is_file())
            self.assertTrue(kwargs["subtitle_path"].is_file())
            self.assertEqual(kwargs["narration_asr"]["model_revision"], MODEL_REVISION)
            return {"validated": True, "relative_path": "data/products/asr-wrapper"}
        with (
            patch("muse.cantonese_tts.synthesize_cantonese_segments_with_runtime", side_effect=fake_synthesize),
            patch("muse.cantonese_asr.transcribe_cantonese", side_effect=fake_asr),
            patch.object(self.builder, "package_generated_clip", side_effect=fake_package),
        ):
            result = self.builder.package_generated_clip_with_cantonese_narration(
                "ASR 字幕流程", "城市夜景", source, narration_script="大家好。",
                model_dir=self.root / "tts-model", asr_model_dir=self.root / "asr-model",
                generation_metadata={"status": "completed"},
            )
        self.assertTrue(result["validated"])
        self.assertEqual(result["narration_asr"]["comparison"]["cer"], 0.0)
        self.assertEqual(observed["reference"], "大家好。")
        self.assertFalse(observed["audio"].exists())
        self.assertFalse(observed["subtitle"].exists())

    def test_cantonese_asr_failure_blocks_product_packaging(self):
        source = self.root / "asr-failure-source.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))

        def fake_synthesize(text, output, *, model_dir, packages_dir, quality):
            output.write_bytes(b"RIFFvoice-fixture")
            return {"status": "completed", "engine": "canto-tts-onnx",
                    "model_repo": "typangaa/canto-tts-nano", "model_revision": "pinned",
                    "license": "Apache-2.0", "language": "zh-HK", "sample_rate": 48000,
                    "channels": 1, "frames": 48000, "duration_seconds": 1.0, "bytes": output.stat().st_size}

        def fake_asr(audio_path, *, model_dir, output_srt, reference_text):
            output_srt.write_text("1\\n00:00:00,000 --> 00:00:01,000\\n錯誤辨識\\n", encoding="utf-8")
            return {"status": "needs_review", "comparison": {"status": "needs_review", "cer": 0.42}}

        with (
            patch("muse.cantonese_tts.synthesize_cantonese_segments_with_runtime", side_effect=fake_synthesize),
            patch("muse.cantonese_asr.transcribe_cantonese", side_effect=fake_asr),
            patch.object(self.builder, "package_generated_clip") as package,
        ):
            with self.assertRaisesRegex(VideoProductError, "CER gate failed"):
                self.builder.package_generated_clip_with_cantonese_narration(
                    "ASR gate", "source verification", source, narration_script="大家好。",
                    model_dir=self.root / "tts-model", asr_model_dir=self.root / "asr-model",
                    generation_metadata={"status": "completed"},
                )
        package.assert_not_called()

    def test_recovered_clip_packaging_rejects_invalid_scene_records(self):
        source = self.root / "recovered-wan.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        with self.assertRaisesRegex(ValueError, "scene_records"):
            self.builder.package_generated_clip(
                "坏分镜", "星河", source, generation_metadata={"status": "completed"},
                scene_records=[{"order": 2, "prompt": "wrong order"}],
            )

    def test_recovered_clip_packaging_is_idempotent_and_rejects_invalid_mp4(self):
        source = self.root / "recovered-wan.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        metadata = {"status": "completed", "model": "Wan 2.1 T2V 1.3B", "seed": 7}
        first = self.builder.package_generated_clip("恢复短片", "星河", source, generation_metadata=metadata)
        second = self.builder.package_generated_clip("恢复短片", "星河", source, generation_metadata=metadata)
        self.assertEqual(first["initiative_id"], second["initiative_id"])
        self.assertEqual(first["knowledge_linkage"]["status"], "not_initialized")
        self.assertFalse((self.root / "data" / "research" / "knowledge.sqlite3").exists())
        self.assertEqual(first["version_id"], second["version_id"])
        invalid = self.root / "invalid.mp4"
        invalid.write_bytes(b"not an mp4")
        with self.assertRaisesRegex(VideoProductError, "MP4"):
            self.builder.package_generated_clip("无效短片", "星河", invalid, generation_metadata=metadata)

    def test_recovered_clip_packaging_rejects_symlink_source(self):
        source = self.root / "real.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        linked = self.root / "linked.mp4"
        linked.symlink_to(source)
        with self.assertRaisesRegex(VideoProductError, "symlink"):
            self.builder.package_generated_clip("链接短片", "星河", linked, generation_metadata={"status": "completed"})

    def test_verified_video_product_is_auto_linked_to_creator_ledger(self):
        from muse.initiative_engine import InitiativeStore

        source = self.root / "ledger-video.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        cycle_id = "cycle_" + "d" * 32
        result = self.builder.package_generated_clip(
            "账本联动短片", "星河与古城", source,
            generation_metadata={"status": "completed", "model": "Wan 2.1", "seed": 42},
            evolution_cycle_id=cycle_id,
        )
        self.assertTrue(result["validated"])
        self.assertEqual(len(result["mp4_sha256"]), 64)
        self.assertEqual(len(result["manifest_sha256"]), 64)
        database = self.root / "data" / "initiatives" / "muse.sqlite3"
        with InitiativeStore(database) as initiatives:
            events = initiatives.list_events(initiative_id=result["creator_initiative_id"])
            product_events = [event for event in events if event["event_type"] == "product_built"]
            self.assertEqual(len(product_events), 1)
            metadata = product_events[0]["details"]["artifact_metadata"]
            self.assertEqual(metadata["mp4_sha256"], result["mp4_sha256"])
            self.assertEqual(metadata["manifest_sha256"], result["manifest_sha256"])
            self.assertEqual(metadata["evolution_cycle_id"], cycle_id)
            self.assertEqual(product_events[0]["details"]["validated"], True)

    def test_verified_video_product_links_cycle_knowledge_into_creator_initiative(self):
        import sqlite3
        from contextlib import closing
        from muse.research_knowledge import ResearchKnowledgeStore

        knowledge_db = self.root / "data" / "research" / "knowledge.sqlite3"
        cycle_id = "cycle_" + "e" * 32
        with ResearchKnowledgeStore(knowledge_db) as knowledge:
            cycle_nodes = []
            for node_type in ("source", "idea", "experiment", "claim", "decision", "failure"):
                node = knowledge.upsert_node(
                    node_type, f"{node_type} for this cycle",
                    external_ref=f"cycle-fixture:{cycle_id}:{node_type}",
                    metadata={"evolution_cycle_id": cycle_id},
                )
                cycle_nodes.append(node["id"])
            unrelated = knowledge.upsert_node(
                "idea", "Unrelated cycle idea", external_ref="cycle-fixture:other:idea",
                metadata={"evolution_cycle_id": "cycle_" + "f" * 32},
            )

        source = self.root / "cycle-video.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        first = self.builder.package_generated_clip(
            "研究融合短片", "把研究灵感变成影像", source,
            generation_metadata={"status": "completed", "model": "Wan 2.1", "seed": 84},
            evolution_cycle_id=cycle_id,
        )
        self.assertEqual(first["knowledge_linkage"]["status"], "linked")
        self.assertEqual(first["knowledge_linkage"]["linked_cycle_nodes"], len(cycle_nodes))
        with closing(sqlite3.connect(self.root / "data" / "initiatives" / "muse.sqlite3")) as connection:
            rows = connection.execute(
                "SELECT knowledge_node_id,evolution_cycle_id FROM initiative_knowledge_links WHERE initiative_id=?",
                (first["creator_initiative_id"],),
            ).fetchall()
        linked_ids = {row[0] for row in rows}
        self.assertTrue(set(cycle_nodes).issubset(linked_ids))
        self.assertNotIn(unrelated["id"], linked_ids)
        self.assertTrue(all(row[1] == cycle_id for row in rows))

        with ResearchKnowledgeStore(knowledge_db) as knowledge:
            artifact = next(
                node for node in knowledge.list_nodes(limit=500)
                if node.get("external_ref") == f"muse-video-product:{first['initiative_id']}:{first['version_id']}"
            )
            self.assertEqual(artifact["metadata"]["source_kind"], "creative_artifact")
            self.assertEqual(artifact["metadata"]["mp4_sha256"], first["mp4_sha256"])
            self.assertEqual(artifact["metadata"]["package_sha256"], first["package_sha256"])
            self.assertIn(first["creator_initiative_id"], artifact["metadata"]["creator_initiative_ids"])

        second = self.builder.package_generated_clip(
            "研究融合短片", "把研究灵感变成影像", source,
            generation_metadata={"status": "completed", "model": "Wan 2.1", "seed": 84},
            evolution_cycle_id=cycle_id,
        )
        self.assertEqual(second["knowledge_linkage"]["status"], "linked")
        with closing(sqlite3.connect(self.root / "data" / "initiatives" / "muse.sqlite3")) as connection:
            count = connection.execute(
                "SELECT count(*) FROM initiative_knowledge_links WHERE initiative_id=?",
                (first["creator_initiative_id"],),
            ).fetchone()[0]
        self.assertEqual(count, len(cycle_nodes) + 1)

    def test_video_product_without_cycle_links_only_its_artifact_node(self):
        import sqlite3
        from contextlib import closing
        from muse.research_knowledge import ResearchKnowledgeStore

        knowledge_db = self.root / "data" / "research" / "knowledge.sqlite3"
        with ResearchKnowledgeStore(knowledge_db) as knowledge:
            unrelated = knowledge.upsert_node(
                "idea", "An unscoped research idea", external_ref="unscoped-cycle-fixture",
                metadata={"evolution_cycle_id": "cycle_" + "a" * 32},
            )
        source = self.root / "unscoped-video.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        result = self.builder.package_generated_clip(
            "无 Cycle 短片", "只记录作品，不猜测研究关联", source,
            generation_metadata={"status": "completed", "model": "Wan 2.1", "seed": 85},
        )
        self.assertEqual(result["knowledge_linkage"]["status"], "linked")
        self.assertEqual(result["knowledge_linkage"]["linked_cycle_nodes"], 0)
        with closing(sqlite3.connect(self.root / "data" / "initiatives" / "muse.sqlite3")) as connection:
            rows = connection.execute(
                "SELECT knowledge_node_id,node_type,evolution_cycle_id FROM initiative_knowledge_links WHERE initiative_id=?",
                (result["creator_initiative_id"],),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "source")
        self.assertEqual(rows[0][2], "")
        self.assertNotEqual(rows[0][0], unrelated["id"])

    def test_concurrent_publish_reuses_existing_verified_video_version(self):
        source = self.root / "race.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        first = self.builder.package_generated_clip(
            "并发发布短片", "晨雾中的纸鹤", source,
            generation_metadata={"status": "completed"},
        )
        products_root = self.root / "data" / "products"
        target_parent = products_root / first["initiative_id"]
        staging = target_parent / ".race-loser"
        staging.mkdir()
        with patch.object(InitiativeStore,"list",return_value=[]):
            recovered = self.builder._publish_staging(
                staging, products_root, first["initiative_id"], first["version_id"],
                title="并发发布短片", prompt="晨雾中的纸鹤",
                creator_initiative_id=first["creator_initiative_id"], evolution_cycle_id=None,
            )
        self.assertEqual(recovered["package_sha256"], first["package_sha256"])
        self.assertEqual(recovered["ledger_event_id"], first["ledger_event_id"])
        self.assertTrue(staging.is_dir())

    def test_visual_quality_report_is_hash_verified_in_ai_product_package(self):
        source = self.root / "visual-report.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        qa = {
            "status": "passed", "path": "video.mp4", "missing_tools": [],
            "checks": {"full_decode": {"passed": True, "detail": "full decode completed"}},
            "metadata": {"width": 512, "height": 288, "fps": 12},
        }
        with patch("muse.video_product_builder.inspect_video_file", return_value=qa, create=True):
            result = self.builder.package_generated_clip(
                "视觉诊断包", "一只纸鹤飞过星空", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
            )
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        report_path = product_dir / "visual-quality-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["visual_quality"]["status"], "unavailable")
        self.assertEqual(manifest["visual_quality"]["sha256"], hashlib.sha256(report_path.read_bytes()).hexdigest())
        self.assertEqual(report["status"], "unavailable")
        self.assertFalse(report["semantic_recognition"])
        self.assertEqual(result["visual_quality"]["report"], "visual-quality-report.json")
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertEqual(archive.read("visual-quality-report.json"), report_path.read_bytes())
        report_path.write_text(report_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(VideoProductError, "integrity"):
            self.builder.verify_path(self.root / "data" / "products", result["initiative_id"], result["version_id"])

    def test_packaged_clip_contains_technical_qa_report_and_stays_in_testing(self):
        import hashlib

        source = self.root / "qa-passed.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        qa = {
            "status": "passed", "path": str(source), "missing_tools": [],
            "checks": {"full_decode": {"passed": True, "detail": "full decode completed"}},
            "metadata": {"width": 512, "height": 288, "fps": 12},
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        with patch("muse.video_product_builder.inspect_video_file", return_value=qa, create=True):
            result = self.builder.package_generated_clip(
                "QA 通过仍待审核", "一只纸鹤飞过星空", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
            )

        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((product_dir / "qa-report.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "testing")
        self.assertEqual(manifest["quality_gate"]["status"], "passed")
        self.assertFalse(manifest["quality_gate"]["ready_to_publish"])
        self.assertEqual(report["status"], "passed")
        self.assertEqual(manifest["visual_quality"]["status"], "unavailable")
        self.assertEqual(manifest["visual_quality"]["review_required"], True)
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertIn("qa-report.json", archive.namelist())
            self.assertIn("visual-quality-report.json", archive.namelist())
            self.assertIsNone(archive.testzip())
        self.assertEqual(result["quality_gate"]["status"], "passed")
        report_path = product_dir / "qa-report.json"
        report_path.write_text('{"tampered": true}', encoding="utf-8")
        with self.assertRaisesRegex(VideoProductError, "integrity"):
            self.builder.verify_path(self.root / "data" / "products", result["initiative_id"], result["version_id"])


    def test_blocked_claim_grounded_narration_cannot_be_packaged(self):
        from muse.narration_evidence_contract import build_narration_evidence_contract

        source = self.root / "blocked-evidence.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        voice = self.root / "blocked-voice.wav"
        voice.write_bytes(b"RIFF voice fixture")
        script = "未审核来源不能作为事实旁白。"
        claim_id = "a" * 32
        contract = build_narration_evidence_contract(
            [{"text": script, "claim_ids": [claim_id]}], {claim_id: []}
        )
        with self.assertRaisesRegex(VideoProductError, "blocked or invalid"):
            self.builder.package_generated_clip(
                "证据门禁测试", "拒绝无证据旁白", source,
                generation_metadata={"status": "completed"},
                narration_path=voice, narration_script=script,
                narration_evidence_contract=contract,
            )

    def test_forged_ready_claim_grounded_contract_is_rejected_against_live_research_store(self):
        from muse.narration_evidence_contract import build_narration_evidence_contract
        from muse.research_knowledge import ResearchKnowledgeStore

        source = self.root / "grounded-evidence.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        voice = self.root / "grounded-voice.wav"
        voice.write_bytes(b"RIFF voice fixture")
        script = "每句事实旁白都要有可核验的来源。"
        claim_id = "a" * 32
        evidence = {
            "evidence_id": "b" * 32, "verdict": "supports",
            "citation_anchor_verified": True, "source_current": True,
            "source_ref": "https://example.org/verified",
        }
        forged_contract = build_narration_evidence_contract(
            [{"index": 1, "text": script, "claim_ids": [claim_id]}],
            {claim_id: [evidence]},
        )
        with ResearchKnowledgeStore(self.root / "data" / "research" / "knowledge.sqlite3"):
            pass
        with self.assertRaisesRegex(VideoProductError, "does not match live ResearchKnowledgeStore"):
            self.builder.package_generated_clip(
                "伪造放行测试", "Claim-grounded narration", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
                narration_path=voice, narration_script=script,
                narration_evidence_contract=forged_contract,
            )


    def test_claim_grounded_contract_must_match_packaged_script(self):
        from muse.narration_evidence_contract import build_narration_evidence_contract

        source = self.root / "mismatched-evidence.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        voice = self.root / "mismatched-voice.wav"
        voice.write_bytes(b"RIFF voice fixture")
        claim_id = "a" * 32
        evidence = {
            "evidence_id": "b" * 32, "verdict": "supports",
            "citation_anchor_verified": True, "source_current": True,
            "source_ref": "https://example.org/verified",
        }
        contract = build_narration_evidence_contract(
            [{"text": "经证据审核的旁白句。", "claim_ids": [claim_id]}],
            {claim_id: [evidence]},
        )
        with self.assertRaisesRegex(VideoProductError, "does not match"):
            self.builder.package_generated_clip(
                "脚本不匹配", "拒绝旁白与证据契约不一致", source,
                generation_metadata={"status": "completed"},
                narration_path=voice, narration_script="另一句没有绑定的旁白。",
                narration_evidence_contract=contract,
            )

    def test_recovered_clip_can_embed_and_package_a_narration_track(self):
        source = self.root / "narrated-source.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        voice = self.root / "voiceover.wav"
        voice.write_bytes(b"RIFF" + b"narration-fixture")
        script = "大家好，今集用三步方法查證 AI 資訊。"
        qa = {
            "status": "passed", "path": "video.mp4", "missing_tools": [],
            "checks": {"full_decode": {"passed": True, "detail": "full decode completed"}},
            "metadata": {"width": 512, "height": 288, "fps": 12, "audio_codecs": ["aac"]},
        }

        def fake_mux(command, **kwargs):
            Path(command[-1]).write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return SimpleNamespace(returncode=0, stderr="", stdout="")


        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[10.0, 5.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_mux) as run_mock,
            patch("muse.video_product_builder.inspect_video_file", return_value=qa, create=True) as inspect,
        ):
            result = self.builder.package_generated_clip(
                "有旁白的短片", "AI 資訊查證", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
                narration_path=voice, narration_script=script,
            )

        inspect.assert_called_once()
        self.assertTrue(inspect.call_args.kwargs["require_audio"])
        mux_command = run_mock.call_args.args[0]
        self.assertIn("-af", mux_command)
        self.assertEqual(mux_command[mux_command.index("-af") + 1], "loudnorm=I=-16:TP=-1.5:LRA=11,apad")
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["narration"]["language"], "zh-HK")
        self.assertEqual(manifest["narration"]["loudness_normalization"], {
            "method": "ffmpeg_loudnorm", "target_lufs": -16, "true_peak_db": -1.5, "lra": 11,
        })
        self.assertEqual(manifest["audio_asset"]["sha256"], __import__("hashlib").sha256(voice.read_bytes()).hexdigest())
        self.assertEqual(storyboard["narration"]["script"], script)
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertIn("narration/voiceover.wav", archive.namelist())
            self.assertIn("narration/script.txt", archive.namelist())
            self.assertIn("video.mp4", archive.namelist())
            self.assertIsNone(archive.testzip())

    def test_packaged_cantonese_asr_subtitles_are_hash_verified_and_preserved(self):
        import hashlib
        source = self.root / "asr-source.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        voice = self.root / "asr-voice.wav"
        voice.write_bytes(b"RIFF" + b"voice-fixture")
        subtitles = self.root / "captions.srt"
        subtitles.write_text("1\n00:00:00,100 --> 00:00:01,200\n大家好。\n", encoding="utf-8")
        script = "大家好。"
        subtitle_hash = hashlib.sha256(subtitles.read_bytes()).hexdigest()
        audio_hash = hashlib.sha256(voice.read_bytes()).hexdigest()
        asr = {
            "status": "passed", "engine": "faster-whisper-local",
            "model_repo": "alvanlii/whisper-small-cantonese",
            "model_revision": "b03a4e5d482f06858ce10ebbabebabec9f447a0d",
            "model_sha256": "a" * 64, "audio_sha256": audio_hash,
            "transcript": "大家好。", "segment_count": 1,
            "comparison": {"status": "passed", "metric": "character_error_rate", "cer": 0.0, "max_cer": 0.18},
            "subtitles": {"bytes": subtitles.stat().st_size, "sha256": subtitle_hash, "cue_count": 1},
            "human_listening_review_required": True,
        }
        qa = {
            "status": "passed", "path": "video.mp4", "missing_tools": [],
            "checks": {"full_decode": {"passed": True, "detail": "full decode completed"}},
            "metadata": {"width": 512, "height": 288, "fps": 12, "audio_codecs": ["aac"]},
        }
        def fake_mux(command, **kwargs):
            Path(command[-1]).write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
             patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[10.0, 5.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_mux),
            patch("muse.video_product_builder.inspect_video_file", return_value=qa, create=True),
        ):
            result = self.builder.package_generated_clip(
                "带粤语字幕的短片", "城市夜景", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
                narration_path=voice, narration_script=script,
                subtitle_path=subtitles, narration_asr=asr,
            )
        self.assertTrue(result["validated"])
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["narration_asr"]["comparison"]["cer"], 0.0)
        self.assertTrue(manifest["narration_asr"]["human_listening_review_required"])
        self.assertEqual(manifest["subtitles"]["sha256"], subtitle_hash)
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertIn("subtitles.srt", archive.namelist())
            self.assertEqual(archive.read("subtitles.srt"), subtitles.read_bytes())
            self.assertIsNone(archive.testzip())

    def test_approved_subtitle_revision_creates_immutable_version_and_keeps_creator_testing(self):
        import hashlib
        from muse.initiative_engine import InitiativeStore
        from muse.subtitle_review import apply_human_subtitle_review, build_subtitle_review, parse_srt_cues

        source = self.root / "approved-review-source.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        voice = self.root / "approved-review-voice.wav"
        voice.write_bytes(b"RIFF approved review voice fixture")
        subtitles = self.root / "approved-review-source.srt"
        subtitles.write_text(
            "1\n00:00:00,100 --> 00:00:01,200\n大家好。\n", encoding="utf-8",
        )
        audio_hash = hashlib.sha256(voice.read_bytes()).hexdigest()
        subtitle_hash = hashlib.sha256(subtitles.read_bytes()).hexdigest()
        asr = {
            "status": "passed", "engine": "faster-whisper-local",
            "model_repo": "alvanlii/whisper-small-cantonese",
            "model_revision": "b03a4e5d482f06858ce10ebbabebabec9f447a0d",
            "model_sha256": "a" * 64, "audio_sha256": audio_hash,
            "transcript": "大家好。", "segment_count": 1,
            "segments": [{"start": 0.1, "end": 1.2, "text": "大家好。", "avg_logprob": -0.1, "no_speech_prob": 0.01}],
            "comparison": {"status": "passed", "metric": "character_error_rate", "cer": 0.0, "max_cer": 0.18},
            "subtitles": {"bytes": subtitles.stat().st_size, "sha256": subtitle_hash, "cue_count": 1},
            "human_listening_review_required": True,
        }
        qa = {
            "schema_version": 1, "status": "passed", "path": "video.mp4", "missing_tools": [], "warnings": [],
            "checks": {"full_decode": {"passed": True, "detail": "full decode completed"}},
            "metadata": {"width": 512, "height": 288, "fps": 12.0, "duration_seconds": 2.0,
                         "audio_codecs": ["aac"], "file_bytes": 64},
        }

        def fake_run(command, **kwargs):
            if command[0] == "ffmpeg":
                Path(command[-1]).write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            if command[0] == "ffprobe":
                if str(command[-1]).endswith("source-video.mp4"):
                    streams = [
                        {"codec_type": "video", "codec_name": "h264", "width": 512, "height": 288, "avg_frame_rate": "12/1"},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ]
                else:
                    streams = [
                        {"codec_type": "video", "codec_name": "h264", "width": 512, "height": 288, "avg_frame_rate": "12/1"},
                        {"codec_type": "audio", "codec_name": "aac"},
                        {"codec_type": "subtitle", "codec_name": "mov_text", "tags": {"language": "yue", "title": "粵語人工校對字幕"}},
                    ]
                return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"streams": streams}))
            raise AssertionError(f"unexpected subprocess: {command}")

        def fake_qa(path, **kwargs):
            return {
                **qa, "artifact_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            }

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
             patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[10.0, 5.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_run),
            patch("muse.video_product_builder._inspect_product_video", side_effect=fake_qa),
        ):
            original = self.builder.package_generated_clip(
                "人工校對字幕版本", "測試可追溯字幕", source,
                generation_metadata={"status": "completed", "model": "fixture", "width": 512,
                                     "height": 288, "fps": 12, "duration_seconds": 2.0},
                narration_path=voice, narration_script="大家好。",
                subtitle_path=subtitles, narration_asr=asr,
            )

        review = build_subtitle_review(
            "大家好。", asr, subtitle_cues=parse_srt_cues(subtitles.read_text(encoding="utf-8")),
        )
        approved = apply_human_subtitle_review(
            review, reviewed_segment_indexes=[1],
            corrections=[{"segment_index": 1, "corrected_text": "大家好，歡迎收聽。"}],
            reviewer="human-editor", approve=True, reviewer_id="local-editor-1",
            reviewer_role="subtitle_editor", rationale="Checked against narration audio.",
            evidence_refs=["subtitle-review.json#segments", "narration.wav#sha256"],
        )
        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_run) as run_mock,
            patch("muse.video_product_builder._inspect_product_video", side_effect=fake_qa),
        ):
            with patch.object(InitiativeStore,"list",return_value=[]):
                revised = self.builder.package_approved_subtitle_revision(
                    original["initiative_id"], original["version_id"], approved,
                )

        self.assertTrue(revised["validated"])
        self.assertNotEqual(revised["version_id"], original["version_id"])
        self.assertEqual(revised["status"], "testing")
        self.assertTrue(revised["subtitle_review"]["human_approved"])
        self.assertEqual(revised["subtitle_review"]["approved_by"], "human-editor")
        self.assertFalse(revised["quality_gate"]["ready_to_publish"])
        command = next(call.args[0] for call in run_mock.call_args_list if call.args[0][0] == "ffmpeg")
        self.assertIn("mov_text", command)
        self.assertIn("language=yue", command)
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertEqual(command[command.index("-c:a") + 1], "copy")

        original_verified = self.builder.verify_path(
            self.root / "data" / "products", original["initiative_id"], original["version_id"],
        )
        self.assertTrue(original_verified["validated"])
        self.assertIsNone(original_verified["subtitle_review"])
        revised_dir = self.root / revised["relative_path"]
        revised_manifest = json.loads((revised_dir / "manifest.json").read_text(encoding="utf-8"))
        from muse.video_publish_package import _review_record_status
        release_review = revised_manifest["human_reviews"]["subtitle_review"]
        self.assertEqual(release_review["reviewer"]["identity_assurance"], "self_asserted_local_identity")
        self.assertEqual(release_review["subject"]["version_id"], revised["version_id"])
        self.assertEqual(
            _review_record_status(
                release_review, category="subtitle_review", initiative_id=revised["initiative_id"],
                version_id=revised["version_id"], video_sha256=release_review["subject"]["video_sha256"],
                required_artifacts=release_review["subject"]["artifact_sha256"],
            ),
            "verified_human_review_digest_scope_bound",
        )
        self.assertIn("大家好，歡迎收聽。", (revised_dir / "subtitles.srt").read_text(encoding="utf-8"))
        self.assertFalse((revised_dir / "source-video.mp4").exists())
        self.assertFalse((revised_dir / "approved-subtitles.srt").exists())
        from muse.status_api import build_products
        dashboard_row = next(
            item for item in build_products(self.root / "data" / "products")
            if item["version_id"] == revised["version_id"]
        )
        self.assertEqual(dashboard_row["subtitle_review"]["status"], "approved")
        self.assertTrue(dashboard_row["subtitle_review"]["human_approved"])
        self.assertFalse(dashboard_row["quality_gate"]["ready_to_publish"])
        with zipfile.ZipFile(revised_dir / "product.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("subtitle-review.json", archive.namelist())
            self.assertIn("大家好，歡迎收聽。", archive.read("subtitles.srt").decode("utf-8"))

        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            initiative = next(item for item in store.list(limit=200) if item["id"] == revised["creator_initiative_id"])
            self.assertEqual(initiative["status"], "testing")
            events = store.list_events(initiative_id=initiative["id"], limit=200)
            revision_event = next(
                event for event in events
                if event["event_type"] == "product_built"
                and event["details"].get("artifact_metadata", {}).get("version_id") == revised["version_id"]
            )
            self.assertTrue(revision_event["details"]["artifact_metadata"]["subtitle_review"]["human_approved"])

    def test_approved_subtitle_revision_rejects_pending_human_review(self):
        import hashlib
        from muse.subtitle_review import build_subtitle_review, parse_srt_cues
        from muse.video_product_builder import VideoProductError

        source = self.root / "pending-review-source.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        voice = self.root / "pending-review-voice.wav"
        voice.write_bytes(b"RIFF pending review voice")
        subtitles = self.root / "pending-review.srt"
        subtitles.write_text("1\n00:00:00,100 --> 00:00:01,200\n大家好。\n", encoding="utf-8")
        audio_hash = hashlib.sha256(voice.read_bytes()).hexdigest()
        subtitle_hash = hashlib.sha256(subtitles.read_bytes()).hexdigest()
        asr = {
            "status": "passed", "engine": "faster-whisper-local",
            "model_repo": "alvanlii/whisper-small-cantonese",
            "model_revision": "b03a4e5d482f06858ce10ebbabebabec9f447a0d",
            "model_sha256": "b" * 64, "audio_sha256": audio_hash,
            "transcript": "大家好。", "segment_count": 1,
            "segments": [{"start": 0.1, "end": 1.2, "text": "大家好。"}],
            "comparison": {"status": "passed", "metric": "character_error_rate", "cer": 0.0, "max_cer": 0.18},
            "subtitles": {"bytes": subtitles.stat().st_size, "sha256": subtitle_hash, "cue_count": 1},
            "human_listening_review_required": True,
        }
        qa = {"status": "passed", "path": "video.mp4", "schema_version": 1, "missing_tools": [],
              "checks": {"full_decode": {"passed": True}}, "metadata": {}}
        def fake_run(command, **kwargs):
            Path(command[-1]).write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        def fake_qa(path, **kwargs):
            return {**qa, "artifact_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
             patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[10.0, 5.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_run),
            patch("muse.video_product_builder._inspect_product_video", side_effect=fake_qa),
        ):
            original = self.builder.package_generated_clip(
                "待人工审核", "不得跳过审核", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
                narration_path=voice, narration_script="大家好。",
                subtitle_path=subtitles, narration_asr=asr,
            )
        pending = build_subtitle_review("大家好。", asr, subtitle_cues=parse_srt_cues(subtitles.read_text(encoding="utf-8")))
        with self.assertRaisesRegex(VideoProductError, "explicit human subtitle approval"):
            self.builder.package_approved_subtitle_revision(
                original["initiative_id"], original["version_id"], pending,
            )

    def test_failed_technical_qa_is_recorded_but_never_publish_ready(self):
        source = self.root / "qa-failed.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        qa = {
            "status": "failed", "path": str(source), "missing_tools": [],
            "checks": {"full_decode": {"passed": False, "detail": "decode error"}},
            "metadata": {}, "sha256": None,
        }
        with patch("muse.video_product_builder.inspect_video_file", return_value=qa, create=True):
            result = self.builder.package_generated_clip(
                "QA 失败短片", "镜头存在解码问题", source,
                generation_metadata={"status": "completed"},
            )

        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "testing")
        self.assertEqual(manifest["quality_gate"]["status"], "failed")
        self.assertFalse(manifest["quality_gate"]["ready_to_publish"])
        self.assertEqual(result["quality_gate"]["status"], "failed")

    def test_verify_path_accepts_legacy_video_product_without_qa_report(self):
        import hashlib

        products_root = self.root / "data" / "products"
        initiative_id, version_id = "a" * 32, "b" * 20
        product_dir = products_root / initiative_id / version_id
        product_dir.mkdir(parents=True)
        payloads = {
            "README.md": b"# legacy video product\\n",
            "storyboard.json": b"{}\\n",
            "video.mp4": bytes.fromhex("000000186674797069736f6d66697874757265"),
        }
        for name, payload in payloads.items():
            (product_dir / name).write_bytes(payload)
        with zipfile.ZipFile(product_dir / "product.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in payloads.items():
                archive.writestr(name, payload)
        files = {
            name: hashlib.sha256((product_dir / name).read_bytes()).hexdigest()
            for name in ("README.md", "storyboard.json", "video.mp4", "product.zip")
        }
        manifest = {
            "schema_version": 1, "builder_version": 1, "product_kind": "video_creator",
            "initiative_id": initiative_id, "version_id": version_id, "title": "legacy fixture",
            "status": "testing", "files": files, "video_metadata": {},
        }
        (product_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        result = self.builder.verify_path(products_root, initiative_id, version_id)
        self.assertTrue(result["validated"])
        self.assertIsNone(result["quality_gate"])

    def test_quality_gate_summary_surfaces_review_warnings_without_publish_readiness(self):
        summary = _quality_gate_summary({
            "status": "passed",
            "warnings": [
                {"code": "sustained_black"},
                {"code": "sustained_silence"},
                {"code": "sustained_black"},
            ],
        })
        self.assertEqual(summary["review_warning_count"], 3)
        self.assertEqual(summary["review_warning_codes"], ["sustained_black", "sustained_silence"])
        self.assertTrue(summary["warning_review_required"])
        self.assertFalse(summary["ready_to_publish"])

    def test_voice_aware_background_music_is_sidechain_ducked_and_hash_verified(self):
        import hashlib
        source = self.root / "voice-aware-source.mp4"
        narration = self.root / "voiceover.wav"
        music = self.root / "background.mp3"
        subtitles = self.root / "subtitles.srt"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        narration.write_bytes(b"RIFF voice fixture")
        music.write_bytes(b"ID3 music fixture")
        subtitles.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n大家好\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n欢迎收看\n",
            encoding="utf-8",
        )
        observed = {}

        def fake_mux(args, **kwargs):
            observed["command"] = list(args)
            Path(args[-1]).write_bytes(source.read_bytes())
            return SimpleNamespace(returncode=0, stderr="")

        qa = {"status": "passed", "missing_tools": [],
              "checks": {"full_decode": {"passed": True, "detail": "decoded"}},
              "metadata": {"width": 512, "height": 288, "fps": 12.0,
                           "duration_seconds": 2.0, "audio_codecs": ["aac"]}}
        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[10.0, 5.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_mux),
            patch("muse.video_product_builder.inspect_video_file", return_value=qa),
        ):
            result = self.builder.package_generated_clip(
                "旁白压低背景音乐", "城市夜景旁白", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
                narration_path=narration, narration_script="大家好，欢迎来到城市夜景。",
                subtitle_path=subtitles, background_music_path=music,
            )

        command = observed["command"]
        self.assertIn("-filter_complex", command)
        filter_graph = command[command.index("-filter_complex") + 1]
        self.assertIn("sidechaincompress", filter_graph)
        self.assertIn("eval=frame", filter_graph)
        self.assertIn("if(between(t,0.92,2.45)", filter_graph)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11", filter_graph)
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["background_music"]["sha256"], hashlib.sha256(music.read_bytes()).hexdigest())
        self.assertEqual(manifest["background_music_mix"]["speech_timing_source"], "subtitle_srt")
        self.assertEqual(manifest["background_music_mix"]["speech_window_count"], 2)
        self.assertEqual(manifest["background_music_mix"]["fade_out_seconds"], 1.5)
        self.assertEqual(manifest["narration"]["background_music_file"], "music/background.mp3")
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertIn("music/background.mp3", archive.namelist())
            self.assertEqual(archive.read("music/background.mp3"), music.read_bytes())
        verified = self.builder.verify_path(self.root / "data" / "products", result["initiative_id"], result["version_id"])
        self.assertTrue(verified["validated"])

    def test_voice_aware_background_music_without_subtitles_uses_sidechain_and_fade_out(self):
        source = self.root / "voice-aware-no-srt-source.mp4"
        narration = self.root / "voiceover-no-srt.wav"
        music = self.root / "background-no-srt.mp3"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        narration.write_bytes(b"RIFF voice fixture")
        music.write_bytes(b"ID3 music fixture")
        observed = {}

        def fake_mux(args, **kwargs):
            observed["command"] = list(args)
            Path(args[-1]).write_bytes(source.read_bytes())
            return SimpleNamespace(returncode=0, stderr="")

        qa = {"status": "passed", "missing_tools": [],
              "checks": {"full_decode": {"passed": True, "detail": "decoded"}},
              "metadata": {"width": 512, "height": 288, "fps": 12.0,
                           "duration_seconds": 10.0, "audio_codecs": ["aac"]}}
        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[10.0, 5.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=fake_mux),
            patch("muse.video_product_builder.inspect_video_file", return_value=qa),
        ):
            result = self.builder.package_generated_clip(
                "无字幕旁白压低背景音乐", "城市夜景旁白", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
                narration_path=narration, narration_script="大家好，欢迎来到城市夜景。",
                background_music_path=music,
            )

        command = observed["command"]
        filter_graph = command[command.index("-filter_complex") + 1]
        self.assertIn("sidechaincompress", filter_graph)
        self.assertIn("eval=frame", filter_graph)
        self.assertIn("if(lt(t,8.5),1,max(0,(10-t)/1.5))", filter_graph)
        self.assertNotIn("if(between(t,", filter_graph)
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        mix = manifest["background_music_mix"]
        self.assertEqual(mix["method"], "fade_out_volume_plus_sidechaincompress")
        self.assertEqual(mix["speech_timing_source"], "sidechain_only")
        self.assertEqual(mix["speech_window_count"], 0)
        self.assertEqual(mix["fade_out_seconds"], 1.5)

    def test_content_aware_scene_motion_uses_explicit_composition_metadata(self):
        from muse import video_product_builder as module

        metadata = [
            {"subject_center_x": 0.2, "subject_center_y": 0.5, "subject_scale": 0.2,
             "negative_space_left": 0.1, "negative_space_right": 0.8,
             "negative_space_top": 0.2, "negative_space_bottom": 0.2, "motion_level": 0.1},
            {"subject_center_x": 0.8, "subject_center_y": 0.5, "subject_scale": 0.8,
             "negative_space_left": 0.8, "negative_space_right": 0.1,
             "negative_space_top": 0.2, "negative_space_bottom": 0.2, "motion_level": 0.1},
        ]
        directives = module._content_aware_scene_motion_directives(metadata)
        self.assertEqual([item["camera_move"] for item in directives], ["push_in", "pull_out"])
        self.assertTrue(all(item["vfx"] == "none" for item in directives))
        self.assertTrue(all(item["strategy"] == "explicit_composition_v1" for item in directives))

    def test_content_aware_scene_motion_uses_negative_space_for_lateral_drift(self):
        from muse import video_product_builder as module

        metadata = [{"subject_center_x": 0.5, "subject_center_y": 0.5, "subject_scale": 0.4,
                     "negative_space_left": 0.1, "negative_space_right": 0.8,
                     "negative_space_top": 0.2, "negative_space_bottom": 0.2, "motion_level": 0.1}]
        directives = module._content_aware_scene_motion_directives(metadata)
        self.assertEqual(directives[0]["camera_move"], "drift_right")

    def test_content_aware_scene_motion_falls_back_on_invalid_or_missing_metadata(self):
        from muse import video_product_builder as module

        fallback = _default_scene_motion_directives(2)
        invalid_inputs = [None, [], [{}], [{"subject_center_x": True}],
                          [{"subject_center_x": float("nan")}],
                          [{"subject_center_x": 0.5, "subject_center_y": 0.5,
                            "subject_scale": 0.4, "negative_space_left": 0.2,
                            "negative_space_right": 0.2, "negative_space_top": 0.2,
                            "negative_space_bottom": 0.2}],
                          [{"subject_center_x": 1.1, "subject_center_y": 0.5,
                            "subject_scale": 0.4, "negative_space_left": 0.2,
                            "negative_space_right": 0.2, "negative_space_top": 0.2,
                            "negative_space_bottom": 0.2, "motion_level": 0.1}]]
        for metadata in invalid_inputs:
            with self.subTest(metadata=metadata):
                self.assertEqual(module._content_aware_scene_motion_directives(metadata, scene_count=2), fallback)

    def test_content_aware_scene_motion_avoids_camera_motion_for_highly_dynamic_source(self):
        from muse import video_product_builder as module

        metadata = [{"subject_center_x": 0.5, "subject_center_y": 0.5, "subject_scale": 0.3,
                     "negative_space_left": 0.5, "negative_space_right": 0.5,
                     "negative_space_top": 0.5, "negative_space_bottom": 0.5, "motion_level": 0.9}]
        self.assertEqual(module._content_aware_scene_motion_directives(metadata)[0]["camera_move"], "none")

    def test_build_from_source_persists_validated_content_aware_composition(self):
        metadata = [
            {"subject_center_x": 0.2, "subject_center_y": 0.5, "subject_scale": 0.2,
             "negative_space_left": 0.1, "negative_space_right": 0.8,
             "negative_space_top": 0.2, "negative_space_bottom": 0.2, "motion_level": 0.1},
            {"subject_center_x": 0.8, "subject_center_y": 0.5, "subject_scale": 0.8,
             "negative_space_left": 0.8, "negative_space_right": 0.1,
             "negative_space_top": 0.2, "negative_space_bottom": 0.2, "motion_level": 0.1},
        ]
        motion = {"status": "completed", "profile": "brand_scene_transitions",
                  "scene_timeline": {"scene_timings": [
                      {"timeline_start_seconds": 0.0, "timeline_end_seconds": 4.0, "duration_seconds": 4.0},
                      {"timeline_start_seconds": 3.6, "timeline_end_seconds": 7.0, "duration_seconds": 3.4},
                  ]}}

        def apply_motion(base_video, output_video, **kwargs):
            output_video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00fixture")
            return motion

        def inspect_product(path, **kwargs):
            return {"status": "passed", "schema_version": 1, "path": "video.mp4", "warnings": [], "metadata": {
                "duration_seconds": 7.0, "width": 1920, "height": 1080,
                "fps": 30.0, "audio_codecs": []},
                "artifact_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest()}


        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render), \
             patch("muse.video_product_builder._inspect_product_video", side_effect=inspect_product), \
             patch.object(self.builder, "_apply_motion_profile", side_effect=apply_motion):
            result = self.builder.build_from_source(
                "demo", "内容感知运镜产品", motion_profile="brand_scene_transitions",
                scene_analysis_metadata=metadata,
            )

        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))

        self.assertEqual(len(manifest["input_sha256"]), 64)
        self.assertEqual([item["camera_move"] for item in storyboard["settings"]["shot_directives"]], ["push_in", "pull_out"])
        self.assertEqual(storyboard["scenes"][0]["scene_analysis"]["subject_scale"], 0.2)
        self.assertEqual(storyboard["scenes"][1]["metadata_source"], "caller_supplied")

    def test_build_from_source_uses_optional_local_visual_analysis_when_metadata_is_omitted(self):
        metadata = [
            {"subject_center_x": 0.2, "subject_center_y": 0.5, "subject_scale": 0.2,
             "negative_space_left": 0.1, "negative_space_right": 0.8,
             "negative_space_top": 0.2, "negative_space_bottom": 0.2, "motion_level": 0.1},
            {"subject_center_x": 0.8, "subject_center_y": 0.5, "subject_scale": 0.8,
             "negative_space_left": 0.8, "negative_space_right": 0.1,
             "negative_space_top": 0.2, "negative_space_bottom": 0.2, "motion_level": 0.1},
        ]
        motion = {"status": "completed", "profile": "brand_scene_transitions",
                  "scene_timeline": {"scene_timings": [
                      {"timeline_start_seconds": 0.0, "timeline_end_seconds": 4.0, "duration_seconds": 4.0},
                      {"timeline_start_seconds": 3.6, "timeline_end_seconds": 7.0, "duration_seconds": 3.4},
                  ]}}

        def apply_motion(base_video, output_video, **kwargs):
            output_video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00fixture")
            return motion

        def inspect_product(path, **kwargs):
            return {"status": "passed", "schema_version": 1, "path": "video.mp4", "warnings": [],
                    "metadata": {"duration_seconds": 7.0, "width": 1920, "height": 1080,
                                 "fps": 30.0, "audio_codecs": []},
                    "artifact_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest()}

        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render), \
             patch("muse.video_product_builder._inspect_product_video", side_effect=inspect_product), \
             patch("muse.scene_analysis.analyze_scene_assets",
                   return_value={"status": "completed", "source": "opencv_visual_heuristic_v1", "scenes": metadata}),             patch.object(self.builder, "_apply_motion_profile", side_effect=apply_motion):
            result = self.builder.build_from_source(
                "demo", "本地视觉分析运镜产品", motion_profile="brand_scene_transitions",
            )

        storyboard = json.loads((self.root / result["relative_path"] / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(storyboard["settings"]["shot_directives"][0]["metadata_source"], "opencv_visual_heuristic_v1")
        self.assertEqual(storyboard["scenes"][1]["scene_analysis"]["subject_scale"], 0.8)

    def test_partial_local_visual_analysis_persists_motion_only_diagnostics_and_uses_safe_default_moves(self):
        diagnostics = [
            {"scene_index": 1, "asset_name": "scene-01.mp4", "sampled_frame_count": 3,
             "motion_sample_pairs": 2, "motion_level": 0.02, "motion_status": "completed",
             "composition_status": "unavailable", "reason": "composition_estimation_failed"},
            {"scene_index": 2, "asset_name": "scene-02.mp4", "sampled_frame_count": 3,
             "motion_sample_pairs": 2, "motion_level": 0.31, "motion_status": "completed",
             "composition_status": "unavailable", "reason": "composition_estimation_failed"},
        ]
        motion = {"status": "completed", "profile": "brand_scene_transitions",
                  "scene_timeline": {"scene_timings": [
                      {"timeline_start_seconds": 0.0, "timeline_end_seconds": 4.0, "duration_seconds": 4.0},
                      {"timeline_start_seconds": 3.6, "timeline_end_seconds": 7.0, "duration_seconds": 3.4},
                  ]}}

        def apply_motion(base_video, output_video, **kwargs):
            output_video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00fixture")
            return motion

        def inspect_product(path, **kwargs):
            return {"status": "passed", "schema_version": 1, "path": "video.mp4", "warnings": [],
                    "metadata": {"duration_seconds": 7.0, "width": 1920, "height": 1080,
                                 "fps": 30.0, "audio_codecs": []},
                    "artifact_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest()}

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render),
            patch("muse.video_product_builder._inspect_product_video", side_effect=inspect_product),
            patch("muse.scene_analysis.analyze_scene_assets", return_value={
                "status": "partial", "source": "opencv_visual_heuristic_v1",
                "semantic_recognition": False, "reason": "composition_estimation_failed",
                "motion_diagnostics": diagnostics,
            }),
            patch.object(self.builder, "_apply_motion_profile", side_effect=apply_motion),
        ):            result = self.builder.build_from_source(
                "demo", "低置信度视觉分析安全回退", motion_profile="brand_scene_transitions",
            )

        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))

        self.assertEqual(
            [item["camera_move"] for item in storyboard["settings"]["shot_directives"]],
            [item["camera_move"] for item in _default_scene_motion_directives(2)],
        )
        self.assertEqual(storyboard["settings"]["scene_analysis"]["status"], "partial")
        self.assertEqual(storyboard["settings"]["scene_analysis"]["motion_diagnostics"], diagnostics)

if __name__ == "__main__":
    unittest.main()
