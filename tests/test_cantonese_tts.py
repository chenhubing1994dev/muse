from __future__ import annotations

import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from muse.cantonese_tts import MODEL_REVISION, CantoneseTTSError, synthesize_cantonese


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
