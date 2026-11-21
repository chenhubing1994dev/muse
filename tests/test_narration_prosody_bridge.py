"""Tests for the structured narration to prosody bridge."""
from __future__ import annotations

import unittest

from muse.narration_generator import NarrationGenerator, NarrationStyle
from muse.narration_prosody_bridge import (
    build_cantonese_narration_prosody_plan,
    build_cantonese_tts_segments,
    build_narration_prosody_plan,
)
from muse.prosody_contract import ProsodyContractError


class NarrationProsodyBridgeTests(unittest.TestCase):

    def setUp(self):
        self.script = NarrationGenerator().generate(
            "Muse", "第一段內容。第二段內容。第三段內容。",
            "保持清晰、自然、克制", NarrationStyle.YUJIE_CANTO,
        )

    def test_maps_legacy_voice_profile_to_explicit_intent_without_claiming_effects(self):
        plan = build_narration_prosody_plan(self.script)
        self.assertEqual(plan["segment_count"], 3)
        first = plan["segments"][0]
        self.assertEqual(first["requested_intent"]["rate_scale"], 0.85)
        self.assertEqual(first["requested_intent"]["pitch_semitones"], -2.0)
        self.assertTrue(first["requested_intent"]["breath_hint"])
        self.assertEqual(first["application_status"], "metadata_only")
        self.assertEqual(
            first["unsupported_controls"],
            ["breath_hint", "emotion", "pitch_semitones", "rate_scale"],
        )
        self.assertFalse(plan["audio_effects_verified"])
        self.assertTrue(plan["requires_backend_and_audio_validation"])

    def test_only_explicit_backend_capabilities_mark_controls_as_supported(self):
        plan = build_narration_prosody_plan(
            self.script,
            backend_capabilities=("emotion", "rate", "pitch", "breath"),
        )
        first = plan["segments"][0]
        self.assertEqual(
            set(first["applied_controls"]),
            {"emotion", "rate_scale", "pitch_semitones", "breath_hint"},
        )
        self.assertEqual(first["unsupported_controls"], [])
        self.assertFalse(plan["audio_effects_verified"])

    def test_segment_voice_override_is_respected(self):
        script = {
            "style": "yujie_canto",
            "segments": [{
                "text": "獨立聲線意圖。",
                "emotion": "calm",
                "voice": {
                    "speed_scale": 1.0,
                    "pitch_semitones": 0,
                    "breath_hint": False,
                },
            }],
        }
        plan = build_narration_prosody_plan(script)
        intent = plan["segments"][0]["requested_intent"]
        self.assertEqual(intent["rate_scale"], 1.0)
        self.assertEqual(intent["pitch_semitones"], 0.0)
        self.assertFalse(intent["breath_hint"])

    def test_unknown_planner_emotion_fails_closed(self):
        script = {
            "style": "yujie_canto",
            "segments": [{"text": "測試內容。", "emotion": "made_up_emotion"}],
        }
        with self.assertRaises(ProsodyContractError):
            build_narration_prosody_plan(script)

    def test_invalid_script_shape_is_rejected(self):
        for value in (None, {}, {"segments": []}, {"segments": ["bad"]}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_narration_prosody_plan(value)

    def test_pause_intent_is_preserved_without_inventing_pause_values(self):
        script = {
            "style": "news_canto",
            "segments": [{
                "text": "第一句。",
                "emotion": "formal",
                "pause_before_ms": 100,
                "pause_after_ms": 420,
            }],
        }
        plan = build_narration_prosody_plan(script, backend_capabilities=("pause_after",))
        item = plan["segments"][0]
        self.assertEqual(item["requested_intent"]["pause_after_ms"], 420)
        self.assertEqual(item["applied_controls"], {"pause_after_ms": 420})
        self.assertEqual(
            item["unsupported_controls"],
            ["breath_hint", "emotion", "pause_before_ms", "pitch_semitones", "rate_scale"],
        )


    def test_canto_tts_profile_only_claims_trailing_pause_support(self):
        script = {
            "style": "news_canto",
            "segments": [{
                "text": "第一句。",
                "pause_before_ms": 150,
                "pause_after_ms": 350,
            }],
        }
        plan = build_cantonese_narration_prosody_plan(script)
        item = plan["segments"][0]
        self.assertEqual(plan["backend_capabilities"], ["pause_after"])
        self.assertEqual(item["applied_controls"], {"pause_after_ms": 350})
        self.assertIn("pause_before_ms", item["unsupported_controls"])
        self.assertFalse(plan["audio_effects_verified"])

    def test_builds_ordered_tts_payload_from_only_supported_applied_controls(self):
        script = {
            "style": "news_canto",
            "segments": [
                {"text": "第一句。", "pause_after_ms": 240, "pause_before_ms": 120},
                {"text": "第二句。", "pause_after_ms": 0},
            ],
        }
        plan = build_narration_prosody_plan(script, backend_capabilities=("pause_after",))
        payload = build_cantonese_tts_segments(plan)
        self.assertEqual(payload, [
            {"text": "第一句。", "pause_after_ms": 240},
            {"text": "第二句。", "pause_after_ms": 0},
        ])
        self.assertNotIn("pause_before_ms", payload[0])
        self.assertNotIn("emotion", payload[0])

    def test_tts_payload_rejects_unverified_audio_claim(self):
        plan = build_cantonese_narration_prosody_plan(self.script)
        plan["audio_effects_verified"] = True
        with self.assertRaisesRegex(ValueError, "must not claim verified audio effects"):
            build_cantonese_tts_segments(plan)

    def test_tts_payload_rejects_unverified_backend_capabilities(self):
        plan = build_cantonese_narration_prosody_plan(self.script)
        plan["backend_capabilities"] = ["pause_after", "pitch"]
        with self.assertRaisesRegex(ValueError, "exceed verified canto-tts-nano capabilities"):
            build_cantonese_tts_segments(plan)

    def test_tts_payload_rejects_legacy_bidirectional_pause_alias(self):
        plan = {
            "schema": "muse.narration.prosody.v1",
            "audio_effects_verified": False,
            "requires_backend_and_audio_validation": True,
            "backend_capabilities": ["segment_pause"],
            "segments": [{"text": "測試。", "applied_controls": {"pause_after_ms": 120}}],
        }
        with self.assertRaisesRegex(ValueError, "explicit pause_after"):
            build_cantonese_tts_segments(plan)

    def test_tts_payload_rejects_invalid_plan_and_pause(self):
        with self.assertRaises(ValueError):
            build_cantonese_tts_segments({"schema": "wrong", "segments": []})
        plan = {
            "schema": "muse.narration.prosody.v1",
            "segments": [{"text": "測試。", "applied_controls": {"pause_after_ms": True}}],
        }
        with self.assertRaises(ValueError):
            build_cantonese_tts_segments(plan)


if __name__ == "__main__":
    unittest.main()
