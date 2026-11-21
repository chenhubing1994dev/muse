"""Integrity tests for fail-closed structured Cantonese narration payloads."""
from __future__ import annotations

import unittest

from muse.narration_generator import NarrationGenerator, NarrationStyle
from muse.narration_prosody_bridge import (
    build_cantonese_narration_prosody_plan,
    build_cantonese_tts_segments,
)


class NarrationProsodyBridgeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.script = NarrationGenerator().generate(
            "Muse",
            "第一段內容。第二段內容。第三段內容。",
            "保持清晰、自然、克制",
            NarrationStyle.NEWS_CANTO,
        )

    def test_rejects_segment_count_mismatch(self):
        plan = build_cantonese_narration_prosody_plan(self.script)
        plan["segment_count"] += 1
        with self.assertRaisesRegex(ValueError, "segment_count must match"):
            build_cantonese_tts_segments(plan)

    def test_rejects_boolean_segment_count(self):
        plan = build_cantonese_narration_prosody_plan(self.script)
        plan["segment_count"] = True
        with self.assertRaisesRegex(ValueError, "segment_count must match"):
            build_cantonese_tts_segments(plan)

    def test_rejects_claimed_application_without_backend_capability(self):
        plan = build_cantonese_narration_prosody_plan(self.script)
        first = plan["segments"][0]
        first["unsupported_controls"].remove("emotion")
        first["applied_controls"]["emotion"] = "formal"
        with self.assertRaisesRegex(ValueError, "control classifications are inconsistent"):
            build_cantonese_tts_segments(plan)

    def test_rejects_reordered_or_duplicated_segment_indexes(self):
        plan = build_cantonese_narration_prosody_plan(self.script)
        plan["segments"][1]["index"] = 0
        with self.assertRaisesRegex(ValueError, "contiguous segment order"):
            build_cantonese_tts_segments(plan)

    def test_rejects_forged_control_classification(self):
        plan = build_cantonese_narration_prosody_plan(self.script)
        first = plan["segments"][0]
        first["applied_controls"]["pitch_semitones"] = 0.0
        with self.assertRaisesRegex(ValueError, "control classifications are inconsistent"):
            build_cantonese_tts_segments(plan)

    def test_rejects_applied_pause_that_differs_from_requested_intent(self):
        plan = build_cantonese_narration_prosody_plan({
            "style": "news_canto",
            "segments": [{"text": "停頓測試。", "pause_after_ms": 300}],
        })
        plan["segments"][0]["applied_controls"]["pause_after_ms"] = 900
        with self.assertRaisesRegex(ValueError, "applied pause must match requested intent"):
            build_cantonese_tts_segments(plan)

    def test_rejects_unsorted_or_duplicate_capabilities(self):
        plan = build_cantonese_narration_prosody_plan(self.script)
        plan["backend_capabilities"] = ["pause_after", "pause_after"]
        with self.assertRaisesRegex(ValueError, "unique and sorted"):
            build_cantonese_tts_segments(plan)


if __name__ == "__main__":
    unittest.main()
