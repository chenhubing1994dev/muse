from __future__ import annotations

import sys
import json

import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from muse.cantonese_tts import MODEL_REVISION, CantoneseTTSError, synthesize_cantonese
from muse import cantonese_tts
from muse.cantonese_tts import _synthesize_segments_with_engine, split_cantonese_narration
from muse.cantonese_tts import synthesize_cantonese_segment_payload_with_runtime


class CantoneseTTSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "model"
        self.model.mkdir()
        (self.model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
        self.output = self.root / "voice.wav"

    def tearDown(self):
        self.temp.cleanup()

    def test_python314_helper_suppresses_windows_console_and_keeps_structured_output(self):
        completed = __import__("subprocess").CompletedProcess(
            args=["python.exe"], returncode=0,
            stdout=cantonese_tts._RESULT_MARKER + '{"status":"ok"}\n', stderr="",
        )
        with patch("muse.cantonese_tts._python314_command", return_value=["python.exe"]), patch(
            "muse.cantonese_tts._child_environment", return_value={}
        ), patch("muse.cantonese_tts.no_console_window_kwargs", return_value={"creationflags": 123}), patch(
            "muse.cantonese_tts.subprocess.run", return_value=completed
        ) as run:
            result = cantonese_tts._run_python314_cli(["synthesize"], timeout=30)
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(run.call_args.kwargs["creationflags"], 123)

    def _wav(self, path):
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(48_000)
            audio.writeframes(b"\x00\x00\x00\x00" * 480)

    def test_synthesizes_with_pinned_local_model_and_validates_wav(self):
        observed = {}
        self_outer = self

        class FakeTTS:
            def __init__(self, *, checkpoint, backend):
                observed["checkpoint"] = checkpoint
                observed["backend"] = backend

            def synthesize(self, text, path, **kwargs):
                observed["text"] = text
                observed["kwargs"] = kwargs
                self_outer._wav(Path(path))

        with patch.dict(sys.modules, {"canto_tts": types.SimpleNamespace(CantoTTS=FakeTTS)}):
            result = synthesize_cantonese("  大家好。  ", self.output, model_dir=self.model)

        self.assertEqual(observed["backend"], "onnx")
        self.assertEqual(observed["checkpoint"], str(self.model.resolve()))
        self.assertEqual(observed["text"], "大家好。")
        self.assertEqual(observed["kwargs"]["quality"], "duration_filter")
        self.assertEqual(result["model_revision"], MODEL_REVISION)
        self.assertEqual(result["sample_rate"], 48_000)
        self.assertEqual(result["channels"], 2)
        self.assertTrue(self.output.is_file())

    def test_splitter_preserves_punctuation_and_assigns_real_pause_intent(self):
        segments = split_cantonese_narration("你好，世界。下一句！")
        self.assertEqual([item["text"] for item in segments], ["你好，", "世界。", "下一句！"])
        self.assertEqual([item["pause_after_ms"] for item in segments], [160, 320, 320])

    def test_segmented_engine_inserts_exact_pcm_silence_between_clips(self):
        class FakeTTS:
            def synthesize(self, text, path, **kwargs):
                with wave.open(path, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(48_000)
                    audio.writeframes(b"\x01\x00" * 480)

        result = _synthesize_segments_with_engine(
            [{"text": "第一句。", "pause_after_ms": 250}, {"text": "第二句。", "pause_after_ms": 0}],
            self.output, engine=FakeTTS(), quality="single",
        )
        with wave.open(str(self.output), "rb") as audio:
            frames = audio.readframes(audio.getnframes())
        self.assertEqual(result["inserted_pause_frames"], 12_000)
        self.assertEqual(result["applied_controls"], ["pause_after"])
        self.assertEqual(result["backend_capabilities"], ["pause_after"])
        self.assertEqual(result["segment_metadata"][0]["inserted_pause_frames"], 12_000)
        self.assertEqual(len(frames), (480 + 12_000 + 480) * 2)
        self.assertEqual(frames[960:960 + 24], b"\x00" * 24)
        self.assertEqual(frames[-960:], b"\x01\x00" * 480)

    def test_structured_segment_runtime_preserves_boundaries_and_reports_actual_pause(self):
        self_outer = self

        class FakeTTS:
            def __init__(self, *, checkpoint, backend):
                self.checkpoint = checkpoint
                self.backend = backend

            def synthesize(self, text, path, **kwargs):
                self_outer._wav(Path(path))

        segments = [
            {"text": "第一句。", "pause_after_ms": 250},
            {"text": "第二句。"},
        ]
        with (
            patch("muse.cantonese_tts.sys.version_info", (3, 14)),
            patch("muse.cantonese_tts._validate_runtime_paths", return_value=(self.root, self.model)),
            patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}),
            patch("muse.cantonese_tts._load_canto_engine", return_value=FakeTTS),
        ):
            result = synthesize_cantonese_segment_payload_with_runtime(
                segments, self.output, model_dir=self.model, packages_dir=self.root, quality="single",
            )

        self.assertEqual(result["segment_count"], 2)
        self.assertEqual([item["pause_after_ms"] for item in result["segment_metadata"]], [250, 0])
        self.assertEqual(result["inserted_pause_frames"], 12_000)
        self.assertEqual(result["applied_controls"], ["pause_after"])
        self.assertEqual(result["backend_capabilities"], ["pause_after"])
        self.assertIn("emotion", result["unsupported_controls"])
        self.assertIn("pause_before_ms", result["unsupported_controls"])
        self.assertEqual(result["audio_sha256"], cantonese_tts._sha256_file(self.output))
        self.assertEqual(result["frames"], 12_960)
        self.assertTrue(self.output.is_file())

    def test_structured_segment_runtime_transports_ordered_payload_to_python314_child(self):
        import json
        observed = {}

        class FakeTTS:
            def synthesize(self, text, path, **kwargs):
                with wave.open(path, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(48_000)
                    audio.writeframes(bytes([1, 0]) * 480)

        def fake_child(arguments, *, timeout):
            segments_path = Path(arguments[arguments.index("--segments-file") + 1])
            output_path = Path(arguments[arguments.index("--output") + 1])
            observed["segments"] = json.loads(segments_path.read_text(encoding="utf-8"))
            return _synthesize_segments_with_engine(
                observed["segments"], output_path, engine=FakeTTS(), quality="single",
            )

        segments = [
            {"text": "甲。", "pause_after_ms": 80},
            {"text": "乙。", "pause_after_ms": 0},
            {"text": "丙。"},
        ]
        with (
            patch("muse.cantonese_tts.sys.version_info", (3, 11)),
            patch("muse.cantonese_tts._validate_runtime_paths", return_value=(self.root, self.model)),
            patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}),
            patch("muse.cantonese_tts._run_python314_cli", side_effect=fake_child),
        ):
            result = synthesize_cantonese_segment_payload_with_runtime(
                segments, self.output, model_dir=self.model, packages_dir=self.root, quality="single",
            )

        self.assertEqual(observed["segments"], segments)
        self.assertEqual(result["segment_count"], 3)
        self.assertEqual(result["inserted_pause_frames"], 3_840)
        self.assertEqual(result["output"], str(self.output))
        self.assertEqual(result["audio_sha256"], cantonese_tts._sha256_file(self.output))

    def test_structured_segment_runtime_rejects_unimplemented_controls_and_invalid_pause(self):
        invalid_payloads = [
            [{"text": "你好", "emotion": "warm"}],
            [{"text": "你好", "pause_before_ms": 120}],
            [{"text": "你好", "pause_after_ms": True}],
            [{"text": "你好", "pause_after_ms": 3001}],
            [{"text": "段落"} for _ in range(17)],
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    synthesize_cantonese_segment_payload_with_runtime(
                        payload, self.output, model_dir=self.model, packages_dir=self.root,
                    )
        self.assertFalse(self.output.exists())

    def test_segmented_engine_rejects_mismatched_clip_formats_without_delivery(self):
        class FakeTTS:
            calls = 0
            def synthesize(inner, text, path, **kwargs):
                inner.calls += 1
                with wave.open(path, "wb") as audio:
                    audio.setnchannels(1 if inner.calls == 1 else 2)
                    audio.setsampwidth(2)
                    audio.setframerate(48_000)
                    audio.writeframes(b"\x00\x00" * 480 * (1 if inner.calls == 1 else 2))

        with self.assertRaisesRegex(CantoneseTTSError, "inconsistent WAV formats"):
            _synthesize_segments_with_engine(
                [{"text": "第一句。"}, {"text": "第二句。"}], self.output,
                engine=FakeTTS(), quality="single",
            )
        self.assertFalse(self.output.exists())

    def test_missing_model_fails_before_import_or_download(self):
        with self.assertRaises(CantoneseTTSError):
            synthesize_cantonese("你好", self.output, model_dir=self.root / "missing")
        self.assertFalse(self.output.exists())

    def test_invalid_text_and_output_extension_are_rejected(self):
        with self.assertRaises(ValueError):
            synthesize_cantonese("  ", self.output, model_dir=self.model)
        with self.assertRaises(ValueError):
            synthesize_cantonese("你好", self.root / "voice.mp3", model_dir=self.model)

    def test_wrong_sample_rate_is_rejected(self):
        class FakeTTS:
            def __init__(self, **kwargs):
                pass

            def synthesize(self, text, path, **kwargs):
                with wave.open(path, "wb") as audio:
                    audio.setnchannels(2)
                    audio.setsampwidth(2)
                    audio.setframerate(44_100)
                    audio.writeframes(b"\x00\x00\x00\x00" * 441)

        with patch.dict(sys.modules, {"canto_tts": types.SimpleNamespace(CantoTTS=FakeTTS)}):
            with self.assertRaises(CantoneseTTSError):
                synthesize_cantonese("你好", self.output, model_dir=self.model)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
