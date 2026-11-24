from __future__ import annotations

import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.cantonese_asr import (
    MODEL_REVISION,
    CantoneseASRError,
    _write_srt,
    compare_cantonese_transcript,
    transcribe_cantonese,
)


class CantoneseASRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "model"
        self.cts = self.model / "cts"
        self.cts.mkdir(parents=True)
        (self.cts / "config.json").write_text("{}", encoding="utf-8")
        (self.cts / "vocabulary.json").write_text("{}", encoding="utf-8")
        (self.cts / "preprocessor_config.json").write_text("{}", encoding="utf-8")
        self.audio = self.root / "voice.wav"
        self.audio.write_bytes(b"RIFF" + b"audio-fixture")

    def tearDown(self):
        self.temp.cleanup()

    def _pinned_model(self):
        model_file = self.cts / "model.bin"
        model_file.write_bytes(b"pinned-ct2-model-fixture")
        return model_file

    def test_missing_or_partial_pinned_model_is_rejected_before_import(self):
        with patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaisesRegex(CantoneseASRError, "pinned Whisper model"):
                transcribe_cantonese(self.audio, model_dir=self.model)

    def test_transcription_uses_local_pinned_model_and_writes_audio_timed_srt(self):
        model_file = self._pinned_model()
        observed = {}

        class FakeWhisperModel:
            def __init__(self, model_path, **kwargs):
                observed["model_path"] = model_path
                observed["model_kwargs"] = kwargs

            def transcribe(self, audio_path, **kwargs):
                observed["audio_path"] = audio_path
                observed["transcribe_kwargs"] = kwargs
                segments = [
                    SimpleNamespace(start=0.125, end=1.25, text=" 大家好。", avg_logprob=-0.1, no_speech_prob=0.01),
                    SimpleNamespace(start=1.5, end=2.875, text="今日開始。", avg_logprob=-0.2, no_speech_prob=0.02),
                ]
                return iter(segments), SimpleNamespace(language="zh", language_probability=0.99)

        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
        srt = self.root / "captions.srt"
        with patch.dict(sys.modules, {"faster_whisper": fake_module}), \
             patch("muse.cantonese_asr._MODEL_BYTES", model_file.stat().st_size), \
             patch("muse.cantonese_asr._MODEL_SHA256", hashlib.sha256(model_file.read_bytes()).hexdigest()):
            result = transcribe_cantonese(
                self.audio, model_dir=self.model, output_srt=srt,
                reference_text="大家好，今日開始。", device="cpu", compute_type="int8",
            )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["model_revision"], MODEL_REVISION)
        self.assertEqual(result["language"], "zh")
        self.assertEqual(result["segment_count"], 2)
        self.assertEqual(observed["model_kwargs"]["device"], "cpu")
        self.assertEqual(observed["model_kwargs"]["compute_type"], "int8")
        self.assertTrue(observed["transcribe_kwargs"]["word_timestamps"])
        self.assertFalse(observed["transcribe_kwargs"]["vad_filter"])
        self.assertTrue(observed["transcribe_kwargs"]["condition_on_previous_text"])
        self.assertEqual(observed["transcribe_kwargs"]["temperature"], 0.0)
        self.assertIn("00:00:00,125 --> 00:00:01,250", srt.read_text(encoding="utf-8"))
        self.assertIn("00:00:01,500 --> 00:00:02,875", srt.read_text(encoding="utf-8"))

    def test_word_level_srt_uses_timestamped_readable_chunks(self):
        srt = self.root / "word-captions.srt"
        result = _write_srt(srt, [{
            "start": 0.0, "end": 3.2, "text": "大家好。今日開始。",
            "words": [
                {"start": 0.0, "end": 0.55, "text": "大家"},
                {"start": 0.55, "end": 0.55, "text": "好"},
                {"start": 0.55, "end": 1.0, "text": "。"},
                {"start": 1.2, "end": 2.0, "text": "今日"},
                {"start": 2.0, "end": 3.2, "text": "開始。"},
            ],
        }])
        content = srt.read_text(encoding="utf-8-sig")
        self.assertEqual(result["cue_count"], 2)
        self.assertEqual(result["timing_mode"], "word")
        self.assertIn("00:00:00,000 --> 00:00:01,000", content)
        self.assertIn("00:00:01,200 --> 00:00:03,200", content)
        self.assertIn("大家好。", content)
        self.assertIn("今日開始。", content)

    def test_zero_duration_word_at_next_onset_is_merged_without_fabricated_timing(self):
        srt = self.root / "zero-duration-captions.srt"
        result = _write_srt(srt, [{
            "start": 0.0, "end": 2.0, "text": "第三主動查證。",
            "words": [
                {"start": 0.0, "end": 0.0, "text": "第三"},
                {"start": 0.0, "end": 0.5, "text": "主動"},
                {"start": 0.5, "end": 1.0, "text": "查證。"},
            ],
        }])
        content = srt.read_text(encoding="utf-8-sig")
        self.assertEqual(result["timing_mode"], "word")
        self.assertIn("第三主動", content)
        self.assertIn("00:00:00,000 --> 00:00:01,000", content)

    def test_word_level_srt_falls_back_to_real_segment_bounds_for_bad_word_alignment(self):
        srt = self.root / "fallback-captions.srt"
        result = _write_srt(srt, [{
            "start": 0.25, "end": 2.5, "text": "完整保留片段文字。",
            "words": [{"start": None, "end": 0.5, "text": "完整"}],
        }])
        content = srt.read_text(encoding="utf-8-sig")
        self.assertEqual(result["timing_mode"], "segment")
        self.assertEqual(result["segment_fallback_count"], 1)
        self.assertIn("00:00:00,250 --> 00:00:02,500", content)
        self.assertIn("完整保留片段文字。", content)

    def test_segment_srt_rejects_invalid_segment_timestamps(self):
        srt = self.root / "bad-segment-captions.srt"
        with self.assertRaisesRegex(CantoneseASRError, "segment has invalid"):
            _write_srt(srt, [{
                "start": 1.0, "end": 0.5, "text": "測試",
                "words": [{"start": None, "end": None, "text": "測試"}],
            }])

    def test_decoder_options_must_be_booleans(self):
        model_file = self._pinned_model()
        with patch.multiple("muse.cantonese_asr", _MODEL_BYTES=model_file.stat().st_size, _MODEL_SHA256=hashlib.sha256(model_file.read_bytes()).hexdigest()):
            with self.assertRaisesRegex(ValueError, "must be booleans"):
                transcribe_cantonese(self.audio, model_dir=self.model, vad_filter=1)

    def test_transcript_comparison_ignores_punctuation_and_flags_low_match(self):
        good = compare_cantonese_transcript("大家好，今日開始！", "大家好今日開始")
        poor = compare_cantonese_transcript("大家好，今日開始！", "完全唔同內容")
        self.assertEqual(good["cer"], 0.0)
        self.assertEqual(good["status"], "passed")
        self.assertGreater(poor["cer"], 0.2)
        self.assertEqual(poor["status"], "needs_review")

    def test_transcript_comparison_rejects_identically_corrupted_unicode(self):
        corrupted = "人工智能\ufffdб"
        result = compare_cantonese_transcript(corrupted, corrupted)
        self.assertEqual(result["cer"], 0.0)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["encoding_integrity"], "failed")
        self.assertIn("replacement_character", result["encoding_issues"])
        self.assertIn("unexpected_letter_script", result["encoding_issues"])


if __name__ == "__main__":
    unittest.main()
