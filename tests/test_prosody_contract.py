from __future__ import annotations

import unittest

from muse.prosody_contract import ProsodyContractError, build_prosody_plan


class ProsodyContractTests(unittest.TestCase):
    def test_metadata_only_when_backend_declares_no_controls(self):
        plan = build_prosody_plan([{
            "text": "慢慢講，唔使急。",
            "emotion": "calm",
            "rate_scale": 0.85,
            "pitch_semitones": -2,
            "breath_hint": True,
            "pause_after_ms": 450,
        }])
        segment = plan["segments"][0]
        self.assertEqual(segment["application_status"], "metadata_only")
        self.assertEqual(segment["applied_controls"], {})
        self.assertEqual(
            set(segment["unsupported_controls"]),
            {"emotion", "rate_scale", "pitch_semitones", "breath_hint", "pause_after_ms"},
        )
        self.assertFalse(plan["audio_effects_verified"])

    def test_only_declared_capabilities_are_marked_applied(self):
        plan = build_prosody_plan(
            [{"text": "呢個結果值得留意。", "emotion": "thoughtful",
              "rate_scale": 0.9, "pitch_semitones": -1.5,
              "pause_before_ms": 200}],
            backend_capabilities={"emotion", "rate", "segment_pause"},
        )
        segment = plan["segments"][0]
        self.assertEqual(segment["application_status"], "partially_supported")
        self.assertEqual(
            set(segment["applied_controls"]),
            {"emotion", "rate_scale", "pause_before_ms"},
        )
        self.assertEqual(
            set(segment["unsupported_controls"]),
            {"pitch_semitones"},
        )

    def test_all_declared_controls_are_reported_as_supported(self):
        capabilities = {"emotion", "rate", "pitch", "breath", "pause_before", "pause_after"}
        plan = build_prosody_plan(
            [{"text": "Hello.", "emotion": "confident", "rate_scale": 1.05,
              "pitch_semitones": 1, "breath_hint": True,
              "pause_before_ms": 100, "pause_after_ms": 250}],
            backend_capabilities=capabilities,
        )
        segment = plan["segments"][0]
        self.assertEqual(segment["application_status"], "fully_supported")
        self.assertEqual(segment["unsupported_controls"], [])
        self.assertEqual(len(segment["applied_controls"]), 6)

    def test_pause_capabilities_are_direction_specific(self):
        plan = build_prosody_plan(
            [{"text": "前後停頓。", "pause_before_ms": 120, "pause_after_ms": 420}],
            backend_capabilities={"pause_after"},
        )
        segment = plan["segments"][0]
        self.assertEqual(segment["applied_controls"], {"pause_after_ms": 420})
        self.assertEqual(segment["unsupported_controls"], ["pause_before_ms"])
        self.assertEqual(segment["application_status"], "partially_supported")

    def test_preserves_segment_order_text_and_emotion(self):
        plan = build_prosody_plan([
            {"text": "第一句。", "emotion": "calm"},
            {"text": "第二句！", "emotion": "excited"},
        ])
        self.assertEqual([s["index"] for s in plan["segments"]], [0, 1])
        self.assertEqual([s["text"] for s in plan["segments"]], ["第一句。", "第二句！"])
        self.assertEqual(
            [s["requested_intent"]["emotion"] for s in plan["segments"]],
            ["calm", "excited"],
        )

    def test_rejects_empty_or_too_many_segments(self):
        with self.assertRaises(ProsodyContractError):
            build_prosody_plan([])
        with self.assertRaises(ProsodyContractError):
            build_prosody_plan([{"text": "x"}] * 17)

    def test_rejects_unknown_fields_and_emotions(self):
        with self.assertRaises(ProsodyContractError):
            build_prosody_plan([{"text": "x", "voice_clone": True}])
        with self.assertRaises(ProsodyContractError):
            build_prosody_plan([{"text": "x", "emotion": "magic"}])

    def test_rejects_out_of_range_or_wrong_type_controls(self):
        for payload in (
            {"text": "x", "rate_scale": 0.2},
            {"text": "x", "pitch_semitones": 9},
            {"text": "x", "pause_after_ms": -1},
            {"text": "x", "pause_before_ms": True},
            {"text": "x", "breath_hint": "yes"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ProsodyContractError):
                build_prosody_plan([payload])

    def test_rejects_invalid_capability_declarations(self):
        with self.assertRaises(ProsodyContractError):
            build_prosody_plan([{"text": "x"}], backend_capabilities="rate")
        with self.assertRaises(ProsodyContractError):
            build_prosody_plan([{"text": "x"}], backend_capabilities={"pretend_emotion"})
        with self.assertRaises(ProsodyContractError):
            build_prosody_plan([{"text": "x"}], backend_capabilities={object()})

    def test_rejects_non_mapping_segments(self):
        with self.assertRaises(ProsodyContractError):
            build_prosody_plan(["plain text"])

    def test_default_controls_are_explicit_and_bounded(self):
        plan = build_prosody_plan([{"text": "默认旁白。"}])
        intent = plan["segments"][0]["requested_intent"]
        self.assertEqual(intent["emotion"], "neutral")
        self.assertEqual(intent["rate_scale"], 1.0)
        self.assertEqual(intent["pitch_semitones"], 0.0)
        self.assertFalse(intent["breath_hint"])
        self.assertEqual(intent["pause_before_ms"], 0)
        self.assertEqual(intent["pause_after_ms"], 0)
        self.assertEqual(plan["segments"][0]["requested_controls"], [])
        self.assertEqual(plan["segments"][0]["application_status"], "no_controls_requested")


if __name__ == "__main__":
    unittest.main()
