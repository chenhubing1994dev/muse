"""Phase 2 Cantonese narration pipeline tests (TDD, unittest)."""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
import wave
from pathlib import Path

from muse.narration_generator import (
    NarrationGenerator,
    NarrationStyle,
    apply_voice_style,
    synth_params,
    synthesize_cantonese_styled,
)

from muse.narration_generator import _distribute

from muse.narration_sync import NarrationSyncStore, wav_duration_seconds


RESEARCH = (
    "Muse 係一個本地智能體項目。"
    "佢結合長期記憶、研究進化同唯一 Commander 調度。"
    "資源感知並行引擎會根據實際硬件限制任務數量。"
    "所有敏感資料默認保留喺本機，唔會上傳雲端。"
    "插件通過適配器接入，避免形成第二套控制平面。"
    "每次升級都要有測試、版本記錄同回滾方案。"
)


def _segments(count: int = 3):
    return [
        {
            "index": i + 1,
            "text": f"粵語解說段落{i + 1}",
            "estimated_duration_seconds": 2.0,
            "emotion": "calm",
        }
        for i in range(count)
    ]


class NarrationGeneratorTests(unittest.TestCase):
    def test_distribute_preserves_source_order_and_balances_contiguous_buckets(self):
        items = ["第一句。", "第二句。", "第三句。", "第四句。", "第五句。"]
        buckets = _distribute(items, 3)
        self.assertEqual(buckets, [
            ["第一句。", "第二句。"],
            ["第三句。", "第四句。"],
            ["第五句。"],
        ])
        self.assertEqual([item for bucket in buckets for item in bucket], items)

    def test_distribute_does_not_duplicate_when_bucket_count_exceeds_items(self):
        items = ["第一句。", "第二句。"]
        buckets = _distribute(items, 4)
        self.assertEqual(buckets, [items[:1], items[1:], [], []])
        self.assertEqual([item for bucket in buckets for item in bucket], items)

    def test_distribute_handles_empty_items_and_rejects_invalid_count(self):
        self.assertEqual(_distribute([], 3), [[], [], []])
        for invalid in (0, -1, True, 1.5):
            with self.subTest(count=invalid):
                with self.assertRaises(ValueError):
                    _distribute(["內容"], invalid)


    def test_explicit_claim_ids_are_preserved_per_segment(self):
        claim_a = "a" * 32
        claim_b = "b" * 32
        result = NarrationGenerator().generate(
            "Muse 项目", RESEARCH, "可验证", NarrationStyle.YUJIE_CANTO,
            segments=3,
            claim_ids_by_segment=[[claim_a, claim_a], [], [claim_b]],
            factual_by_segment=[True, False, True],
        )
        self.assertEqual(result["segments"][0]["claim_ids"], [claim_a])
        self.assertEqual(result["segments"][1]["claim_ids"], [])
        self.assertFalse(result["segments"][1]["factual"])
        self.assertEqual(result["segments"][2]["claim_ids"], [claim_b])
        self.assertTrue(result["segments"][2]["factual"])

    def test_claim_mapping_rejects_wrong_count_and_invalid_ids(self):
        generator = NarrationGenerator()
        with self.assertRaises(ValueError):
            generator.generate(
                "Muse 项目", RESEARCH, "灵感", NarrationStyle.YUJIE_CANTO,
                segments=3, claim_ids_by_segment=[["a" * 32]],
            )
        with self.assertRaises(ValueError):
            generator.generate(
                "Muse 项目", RESEARCH, "灵感", NarrationStyle.YUJIE_CANTO,
                segments=3, claim_ids_by_segment=[["not-an-id"], [], []],
            )
        with self.assertRaises(ValueError):
            generator.generate(
                "Muse 项目", RESEARCH, "灵感", NarrationStyle.YUJIE_CANTO,
                segments=3, factual_by_segment=[True, 1, False],
            )

    def test_three_styles_produce_different_text(self):
        gen = NarrationGenerator()
        texts = {}
        for style in NarrationStyle:
            result = gen.generate("Muse 項目", RESEARCH, "冷靜知性", style)
            texts[style] = [s["text"] for s in result["segments"]]
            self.assertEqual(len(texts[style]), 3)
        self.assertNotEqual(texts[NarrationStyle.YUJIE_CANTO], texts[NarrationStyle.CUTE_CANTO])
        self.assertNotEqual(texts[NarrationStyle.CUTE_CANTO], texts[NarrationStyle.NEWS_CANTO])
        self.assertNotEqual(texts[NarrationStyle.YUJIE_CANTO], texts[NarrationStyle.NEWS_CANTO])

    def test_yujie_style_uses_mature_cantonese_markers(self):
        gen = NarrationGenerator()
        result = gen.generate("Muse 項目", RESEARCH, "知性", NarrationStyle.YUJIE_CANTO)
        joined = "".join(s["text"] for s in result["segments"])
        self.assertIn("係", joined)
        self.assertIn("㗎", joined)

    def test_cute_style_uses_playful_markers(self):
        gen = NarrationGenerator()
        result = gen.generate("Muse 項目", RESEARCH, "可愛", NarrationStyle.CUTE_CANTO)
        joined = "".join(s["text"] for s in result["segments"])
        self.assertIn("啦", joined)
        self.assertIn("✨", joined)

    def test_news_style_uses_formal_markers(self):
        gen = NarrationGenerator()
        result = gen.generate("Muse 項目", RESEARCH, "中立", NarrationStyle.NEWS_CANTO)
        joined = "".join(s["text"] for s in result["segments"])
        self.assertIn("本台", joined)
        self.assertNotIn("✨", joined)

    def test_segment_count_parameter_and_length_caps(self):
        gen = NarrationGenerator()
        for n in (3, 4, 6):
            result = gen.generate("Muse 項目", RESEARCH, "灵感", NarrationStyle.YUJIE_CANTO, segments=n)
            self.assertEqual(len(result["segments"]), n)
            self.assertTrue(all(len(s["text"]) <= 280 for s in result["segments"]))
            self.assertLessEqual(len(result["topic"]), 200)
        long_text = "內容。" * 5000
        result = gen.generate("主" * 1000, long_text, "感" * 1000, NarrationStyle.CUTE_CANTO)
        self.assertLessEqual(len(result["topic"]), 200)
        self.assertLessEqual(len(result["inspiration"]), 200)
        self.assertTrue(all(len(s["text"]) <= 280 for s in result["segments"]))

    def test_segment_structure_and_estimated_durations(self):
        result = NarrationGenerator().generate(
            "Muse", RESEARCH, "inspiration", NarrationStyle.YUJIE_CANTO, segments=4
        )
        self.assertEqual(set(result), {"topic", "inspiration", "style", "source", "segments"})
        self.assertEqual(result["source"], "deterministic_template")
        self.assertEqual(result["style"], "yujie_canto")
        for i, seg in enumerate(result["segments"]):
            self.assertEqual(set(seg), {"index", "text", "estimated_duration_seconds", "emotion"})
            self.assertEqual(seg["index"], i + 1)
            self.assertGreater(seg["estimated_duration_seconds"], 0)
            self.assertTrue(math.isfinite(seg["estimated_duration_seconds"]))
            self.assertIsInstance(seg["emotion"], str)

    def test_deterministic_without_planner(self):
        gen = NarrationGenerator()
        a = gen.generate("Muse", RESEARCH, "inspiration", NarrationStyle.YUJIE_CANTO)
        b = gen.generate("Muse", RESEARCH, "inspiration", NarrationStyle.YUJIE_CANTO)
        self.assertEqual(a, b)
        self.assertEqual(a["source"], "deterministic_template")

    def test_planner_output_is_used_when_valid(self):
        def planner(payload):
            return [
                {"text": "第一段：本地記憶系統。", "emotion": "calm"},
                {"text": "第二段：唯一 Commander 調度。", "emotion": "warm"},
                {"text": "第三段：資源感知並行執行。", "emotion": "bright"},
            ]

        result = NarrationGenerator(planner=planner).generate(
            "Muse", RESEARCH, "inspiration", NarrationStyle.YUJIE_CANTO
        )
        self.assertEqual(result["source"], "planner")
        self.assertIn("本地記憶", result["segments"][0]["text"])
        self.assertEqual(result["segments"][0]["emotion"], "calm")

    def test_invalid_planner_falls_back_to_deterministic(self):
        def bad_planner(_payload):
            return [{"text": "太短"}]

        result = NarrationGenerator(planner=bad_planner).generate(
            "Muse", RESEARCH, "inspiration", NarrationStyle.CUTE_CANTO
        )
        self.assertEqual(result["source"], "deterministic_template")
        self.assertGreaterEqual(len(result["segments"]), 3)

    def test_empty_inputs_are_rejected(self):
        gen = NarrationGenerator()
        with self.assertRaises(ValueError):
            gen.generate("", RESEARCH, "x", NarrationStyle.YUJIE_CANTO)
        with self.assertRaises(ValueError):
            gen.generate("topic", "   ", "x", NarrationStyle.YUJIE_CANTO)
        with self.assertRaises(ValueError):
            gen.generate("topic", RESEARCH, "x", "not-a-style")
        with self.assertRaises(ValueError):
            gen.generate("topic", RESEARCH, "x", NarrationStyle.YUJIE_CANTO, segments=2)


class VoiceStyleTests(unittest.TestCase):
    def test_yujie_voice_profile(self):
        params = synth_params(NarrationStyle.YUJIE_CANTO)
        self.assertEqual(params, {
            "speed_scale": 0.85,
            "pitch_semitones": -2,
            "breath_hint": True,
        })

    def test_other_profiles_differ(self):
        yujie = synth_params(NarrationStyle.YUJIE_CANTO)
        cute = synth_params(NarrationStyle.CUTE_CANTO)
        news = synth_params(NarrationStyle.NEWS_CANTO)
        self.assertNotEqual(yujie, cute)
        self.assertNotEqual(cute, news)
        self.assertTrue(cute["speed_scale"] > yujie["speed_scale"])
        self.assertTrue(news["speed_scale"] >= cute["speed_scale"])
        self.assertFalse(news["breath_hint"])

    def test_apply_voice_style_is_pure_and_adds_voice_metadata(self):
        segment = {"index": 1, "text": "你好", "estimated_duration_seconds": 2.0, "emotion": "calm"}
        result = apply_voice_style(segment, NarrationStyle.YUJIE_CANTO)
        self.assertEqual(result["voice"], synth_params(NarrationStyle.YUJIE_CANTO))
        self.assertEqual(result["text"], segment["text"])
        self.assertEqual(set(segment), {"index", "text", "estimated_duration_seconds", "emotion"})

    def test_apply_voice_style_rejects_bad_segment(self):
        with self.assertRaises(ValueError):
            apply_voice_style({"text": ""}, NarrationStyle.YUJIE_CANTO)

    def test_synthesize_wrapper_enriches_existing_adapter_result(self):
        captured = {}

        def fake_synthesize(text, output_path, **kwargs):
            captured["text"] = text
            captured["output_path"] = str(output_path)
            captured["kwargs"] = kwargs
            return {"status": "completed", "duration_seconds": 1.5}

        result = synthesize_cantonese_styled(
            "粵語測試", "out.wav", model_dir="models/fake",
            style=NarrationStyle.YUJIE_CANTO, synthesize_fn=fake_synthesize,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["voice_style"], synth_params(NarrationStyle.YUJIE_CANTO))
        self.assertEqual(result["style"], "yujie_canto")
        self.assertEqual(captured["text"], "粵語測試")
        self.assertEqual(captured["kwargs"]["model_dir"], "models/fake")
        self.assertNotIn("style", captured["kwargs"])
        self.assertNotIn("synthesize_fn", captured["kwargs"])

    def test_synthesize_wrapper_does_not_mutate_input(self):
        original = {"status": "completed"}
        result = synthesize_cantonese_styled(
            "test", "o.wav", model_dir="m", synthesize_fn=lambda *a, **k: original
        )
        self.assertNotIn("voice_style", original)
        self.assertIn("voice_style", result)


class NarrationSyncTests(unittest.TestCase):
    def test_align_segments_normalizes_and_sums_to_duration(self):
        segments = [
            {"index": 1, "text": "一", "estimated_duration_seconds": 2, "emotion": "calm"},
            {"index": 2, "text": "二", "estimated_duration_seconds": 3, "emotion": "warm"},
            {"index": 3, "text": "三", "estimated_duration_seconds": 5, "emotion": "bright"},
        ]
        timeline = NarrationSyncStore.align_segments(segments, 20.0)
        self.assertEqual(len(timeline), 3)
        self.assertAlmostEqual(sum(s["duration_seconds"] for s in timeline), 20.0, places=6)
        self.assertEqual(timeline[0]["start_seconds"], 0.0)
        self.assertAlmostEqual(timeline[-1]["end_seconds"], 20.0, places=6)
        for prev, nxt in zip(timeline, timeline[1:]):
            self.assertAlmostEqual(prev["end_seconds"], nxt["start_seconds"], places=6)
        for seg in timeline:
            self.assertEqual(set(seg), {"index", "text", "start_seconds", "end_seconds", "duration_seconds", "emotion"})

    def test_align_segments_rejects_invalid_input(self):
        with self.assertRaises(ValueError):
            NarrationSyncStore.align_segments([], 10.0)
        with self.assertRaises(ValueError):
            NarrationSyncStore.align_segments(_segments(), 0.0)
        with self.assertRaises(ValueError):
            NarrationSyncStore.align_segments(_segments(), -1.0)

    def test_generate_srt_format(self):
        timeline = NarrationSyncStore.align_segments(_segments(), 6.0)
        srt = NarrationSyncStore.generate_srt(timeline)
        self.assertIn("1\n00:00:00,000 --> 00:00:02,000", srt)
        self.assertIn("2\n00:00:02,000 --> 00:00:04,000", srt)
        self.assertIn("3\n00:00:04,000 --> 00:00:06,000", srt)
        self.assertIn("粵語解說段落1", srt)

    def test_generate_ass_uses_hong_kong_style(self):
        timeline = NarrationSyncStore.align_segments(_segments(), 6.0)
        ass = NarrationSyncStore.generate_ass(timeline, style="hk-yellow-white")
        self.assertIn("[Script Info]", ass)
        self.assertIn("[V4+ Styles]", ass)
        self.assertIn("Style: HKCanto", ass)
        self.assertIn("&H0000FFFF&", ass)
        self.assertIn("&H00FFFFFF&", ass)
        self.assertIn("Dialogue:", ass)
        self.assertIn("00:00:02.00", ass)
        self.assertIn("粵語解說段落1", ass)
        with self.assertRaises(ValueError):
            NarrationSyncStore.generate_ass(timeline, style="unknown")

    def test_mouth_cues_frame_range(self):
        timeline = NarrationSyncStore.align_segments(_segments(2), 1.0)
        cues = NarrationSyncStore.mouth_cues(timeline, fps=10)
        self.assertEqual(len(cues), 10)
        self.assertEqual([c["frame"] for c in cues], list(range(10)))
        self.assertTrue(all(0.0 <= c["mouth_open"] <= 1.0 for c in cues))
        self.assertEqual(cues[-1]["frame"], 9)

    def test_mouth_cues_rejects_invalid_fps(self):
        timeline = NarrationSyncStore.align_segments(_segments(), 6.0)
        with self.assertRaises(ValueError):
            NarrationSyncStore.mouth_cues(timeline, fps=0)
        with self.assertRaises(ValueError):
            NarrationSyncStore.mouth_cues(timeline, fps=-10)

    def test_wav_duration_uses_standard_wave_module(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tone.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(8000)
                w.writeframes(b"\x00\x00" * 8000)
            self.assertAlmostEqual(wav_duration_seconds(path), 1.0, places=6)

    def test_sqlite_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "narration.sqlite3"
            segments = _segments(3)
            timeline = NarrationSyncStore.align_segments(segments, 6.0)
            audio = Path(d) / "audio.wav"
            audio.write_bytes(b"RIFFfake")
            srt = Path(d) / "narration.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhello", encoding="utf-8")
            with NarrationSyncStore(db) as store:
                run = store.save_run(
                    topic="Muse 項目",
                    style="yujie_canto",
                    segments=segments,
                    audio_path=str(audio),
                    timeline=timeline,
                    subtitle_path=str(srt),
                )
                run_id = run["id"]
            with NarrationSyncStore(db) as store:
                loaded = store.get_run(run_id)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["topic"], "Muse 項目")
                self.assertEqual(loaded["style"], "yujie_canto")
                self.assertEqual(loaded["segments"], segments)
                self.assertEqual(loaded["timeline"], timeline)
                self.assertEqual(loaded["audio_path"], str(audio))
                self.assertEqual(loaded["subtitle_path"], str(srt))
                self.assertEqual(len(store.list_runs()), 1)
                self.assertEqual(store.list_runs()[0]["id"], run_id)

    def test_evidence_contract_roundtrip(self):
        contract = {
            "schema_version": 1,
            "contract": "claim_grounded_narration",
            "publishable": False,
            "status": "blocked",
            "segments": [{
                "index": 1, "text": "事实句待审核。", "claim_ids": ["a" * 32],
                "gate_status": "blocked", "blocking_reasons": ["no_verified_support"],
            }],
            "policy": {"requires_reviewed_support": True},
        }
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "narration.sqlite3"
            with NarrationSyncStore(db) as store:
                saved = store.save_run(
                    topic="Claim narration", style="yujie_canto", segments=_segments(),
                    evidence_contract=contract,
                )
                self.assertEqual(saved["evidence_contract"], contract)
                loaded = store.get_run(saved["id"])
                self.assertEqual(loaded["evidence_contract"], contract)
                self.assertEqual(store.list_runs()[0]["evidence_contract"], contract)

    def test_evidence_contract_rejects_invalid_schema(self):
        with tempfile.TemporaryDirectory() as d:
            with NarrationSyncStore(Path(d) / "narration.sqlite3") as store:
                with self.assertRaises(ValueError):
                    store.save_run(
                        topic="Claim narration", style="yujie_canto", segments=_segments(),
                        evidence_contract={"publishable": True},
                    )

    def test_legacy_narration_database_migrates_evidence_contract_column(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "legacy.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute("""
                CREATE TABLE narration_runs(
                    id TEXT PRIMARY KEY, topic TEXT NOT NULL, style TEXT NOT NULL,
                    segments_json TEXT NOT NULL, audio_path TEXT, timeline_json TEXT NOT NULL,
                    subtitle_path TEXT, created_at TEXT NOT NULL
                )
            """)
            connection.commit()
            connection.close()
            with NarrationSyncStore(db) as store:
                columns = {row["name"] for row in store.connection.execute("PRAGMA table_info(narration_runs)")}
                self.assertIn("evidence_contract_json", columns)
                saved = store.save_run(topic="Legacy", style="yujie_canto", segments=_segments())
                self.assertIsNone(saved["evidence_contract"])

    def test_save_run_rejects_empty_topic(self):
        with tempfile.TemporaryDirectory() as d:
            with NarrationSyncStore(Path(d) / "n.sqlite3") as store:
                with self.assertRaises(ValueError):
                    store.save_run("", "yujie_canto", _segments())

    def test_metadata_does_not_leak_paths_from_segments(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "n.sqlite3"
            with NarrationSyncStore(db) as store:
                run = store.save_run(
                    topic="topic", style="news_canto",
                    segments=_segments(), audio_path=None, timeline=None, subtitle_path=None,
                )
            self.assertNotIn("C:\\", json.dumps(run, ensure_ascii=False))
            self.assertNotIn(os.sep, run["topic"])


if __name__ == "__main__":
    unittest.main()
