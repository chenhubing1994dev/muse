from __future__ import annotations

import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from muse.cantonese_tts import MODEL_REVISION, CantoneseTTSError, synthesize_cantonese
from muse import cantonese_tts
from muse.cantonese_tts import _synthesize_segments_with_engine, split_cantonese_narration

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
        self.assertEqual(result["applied_controls"], ["segment_pause"])
        self.assertEqual(len(frames), (480 + 12_000 + 480) * 2)
        self.assertEqual(frames[960:960 + 24], b"\x00" * 24)
        self.assertEqual(frames[-960:], b"\x01\x00" * 480)

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
