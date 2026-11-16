from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.video_product_builder import VideoProductBuilder, VideoProductError, _quality_gate_summary


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

    def test_builds_ai_generated_video_product_from_local_wan_engine(self):
        def fake_generate(prompt, output, **kwargs):
            output.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return {"status": "completed", "prompt_id": "prompt-123", "model": "Wan 2.1 T2V 1.3B",                    "seed": kwargs["seed"], "aspect_ratio": kwargs["aspect_ratio"], "fps": 12,
                    "duration_seconds": 2.0, "frames": 25, "width": 512, "height": 288,
                    "steps": kwargs["steps"], "bytes": output.stat().st_size, "output": str(output)}
        with patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate):
            result = self.builder.build_from_prompt("AI 测试短片", "一只狐狸穿过竹林", seed=123)
        self.assertTrue(result["validated"])
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["execution"]["ai_generation"], "completed")
        self.assertEqual(storyboard["production_mode"], "local_ai_text_to_video")
        self.assertEqual(storyboard["prompt"], "一只狐狸穿过竹林")
        self.assertEqual(manifest["source_assets"], [])
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"README.md", "storyboard.json", "qa-report.json", "video.mp4"})


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
            self.assertEqual(set(archive.namelist()), {"README.md", "storyboard.json", "qa-report.json", "video.mp4"})
            self.assertIsNone(archive.testzip())

    def test_cantonese_narration_wrapper_synthesizes_then_packages_and_cleans_temporary_audio(self):
        source = self.root / "recovered-wan.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        observed = {}

        def fake_synthesize(text, output, *, model_dir, quality):
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
            patch("muse.cantonese_tts.synthesize_cantonese", side_effect=fake_synthesize),
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

    def test_cantonese_narration_wrapper_can_run_local_asr_and_package_subtitles(self):
        import hashlib
        from muse.cantonese_asr import MODEL_REPO, MODEL_REVISION
        source = self.root / "asr-wrapper-source.mp4"
        source.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        observed = {}
        def fake_synthesize(text, output, *, model_dir, quality):
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
            patch("muse.cantonese_tts.synthesize_cantonese", side_effect=fake_synthesize),
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
        recovered = self.builder._publish_staging(
            staging, products_root, first["initiative_id"], first["version_id"],
            title="并发发布短片", prompt="晨雾中的纸鹤",
            creator_initiative_id=first["creator_initiative_id"], evolution_cycle_id=None,
        )
        self.assertEqual(recovered["package_sha256"], first["package_sha256"])
        self.assertEqual(recovered["ledger_event_id"], first["ledger_event_id"])
        self.assertTrue(staging.is_dir())

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
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertIn("qa-report.json", archive.namelist())
            self.assertIsNone(archive.testzip())
        self.assertEqual(result["quality_gate"]["status"], "passed")
        report_path = product_dir / "qa-report.json"
        report_path.write_text('{"tampered": true}', encoding="utf-8")
        with self.assertRaisesRegex(VideoProductError, "integrity"):
            self.builder.verify_path(self.root / "data" / "products", result["initiative_id"], result["version_id"])


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


        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.subprocess.run", side_effect=fake_mux), \
             patch("muse.video_product_builder.inspect_video_file", return_value=qa, create=True) as inspect:
            result = self.builder.package_generated_clip(
                "有旁白的短片", "AI 資訊查證", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
                narration_path=voice, narration_script=script,
            )

        inspect.assert_called_once()
        self.assertTrue(inspect.call_args.kwargs["require_audio"])
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["narration"]["language"], "zh-HK")
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
                           "duration_seconds": 2.0, "audio_codecs": ["aac"]}}
        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.subprocess.run", side_effect=fake_mux), \
             patch("muse.video_product_builder.inspect_video_file", return_value=qa):
            result = self.builder.package_generated_clip(
                "旁白压低背景音乐", "城市夜景旁白", source,
                generation_metadata={"status": "completed", "width": 512, "height": 288, "fps": 12},
                narration_path=narration, narration_script="大家好，欢迎来到城市夜景。",
                background_music_path=music,
            )

        command = observed["command"]
        self.assertIn("-filter_complex", command)
        self.assertIn("sidechaincompress", command[command.index("-filter_complex") + 1])
        product_dir = self.root / result["relative_path"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["background_music"]["sha256"], hashlib.sha256(music.read_bytes()).hexdigest())
        self.assertEqual(manifest["narration"]["background_music_file"], "music/background.mp3")
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            self.assertIn("music/background.mp3", archive.namelist())
            self.assertEqual(archive.read("music/background.mp3"), music.read_bytes())
        verified = self.builder.verify_path(self.root / "data" / "products", result["initiative_id"], result["version_id"])
        self.assertTrue(verified["validated"])

if __name__ == "__main__":
    unittest.main()
