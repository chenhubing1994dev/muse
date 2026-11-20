from __future__ import annotations

import unittest

from muse.video_audio_mix import (
    build_bgm_envelope_expression,
    normalize_speech_windows,
    parse_srt_speech_windows,
)


class VideoAudioMixTests(unittest.TestCase):
    def test_srt_parser_uses_only_timing_lines_and_preserves_windows(self):
        srt = (
            "1\n00:00:00,500 --> 00:00:01,200\nHello; do not treat this as filter text\n\n"
            "2\n00:00:01,400 --> 00:00:02,000 X1:10\nMuse\n"
        )
        self.assertEqual(
            parse_srt_speech_windows(srt, 3.0),
            [(0.5, 1.2), (1.4, 2.0)],
        )

    def test_overlapping_and_nearby_windows_are_merged(self):
        windows = normalize_speech_windows(
            [(1.0, 2.0), (1.8, 2.4), (2.45, 3.0), (5.0, 5.5)],
            8.0,
            merge_gap_seconds=0.1,
        )
        self.assertEqual(windows, [(1.0, 3.0), (5.0, 5.5)])

    def test_expression_has_smooth_attack_hold_release_and_no_windows_fall_back(self):
        expression = build_bgm_envelope_expression(
            [(1.0, 2.0), (2.2, 3.0)],
            5.0,
            base_gain=0.22,
            duck_gain=0.10,
            attack_ms=80,
            release_ms=450,
        )
        self.assertEqual(expression.count("between("), 1)
        self.assertIn("0.08", expression)
        self.assertIn("0.45", expression)
        self.assertIn("0.22", expression)
        self.assertIn("0.1", expression)
        no_speech = build_bgm_envelope_expression([], 5.0, fade_out_seconds=0)
        self.assertEqual(no_speech, "0.22")
        self.assertIn("if(lt(t,3.5),1,max(0,(5-t)/1.5))", build_bgm_envelope_expression([], 5.0))

    def test_fade_out_window_is_bounded_by_media_duration(self):
        with self.assertRaisesRegex(ValueError, "fade_out_seconds"):
            build_bgm_envelope_expression([], 2.0, fade_out_seconds=3.0)

    def test_rejects_malformed_or_out_of_range_srt_timing(self):
        with self.assertRaisesRegex(ValueError, "malformed"):
            parse_srt_speech_windows("1\nnot-a-time --> 00:00:01,000\ntext", 2.0)
        with self.assertRaisesRegex(ValueError, "outside"):
            parse_srt_speech_windows("1\n00:00:01,000 --> 00:00:03,000\ntext", 2.0)

    def test_empty_srt_still_validates_cue_limit(self):
        with self.assertRaisesRegex(ValueError, "max_cues"):
            parse_srt_speech_windows("no cue", 5.0, max_cues=0)

    def test_rejects_unsafe_windows_and_gain_parameters(self):
        with self.assertRaises(ValueError):
            normalize_speech_windows([(0, float("nan"))], 5.0)
        with self.assertRaises(ValueError):
            normalize_speech_windows([(4, 6)], 5.0)
        with self.assertRaises(ValueError):
            build_bgm_envelope_expression([(0, 1)], 5.0, duck_gain=0.5, base_gain=0.2)
        with self.assertRaises(ValueError):
            build_bgm_envelope_expression([(0, 1)], 5.0, attack_ms=5000)


if __name__ == "__main__":
    unittest.main()
