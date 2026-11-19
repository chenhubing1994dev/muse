from __future__ import annotations

import math
import unittest

from muse.prosodic_boundary import snap_cut_to_verified_boundary


class ProsodicBoundaryTests(unittest.TestCase):
    def test_no_candidate_keeps_original_cut(self):
        result = snap_cut_to_verified_boundary(5.0, [])
        self.assertFalse(result["snapped"])
        self.assertEqual(result["cut_seconds"], 5.0)
        self.assertEqual(result["reason"], "no_verified_candidate_within_window")

    def test_pause_without_confirmed_semantic_boundary_is_rejected(self):
        result = snap_cut_to_verified_boundary(5.0, [{
            "time_seconds": 5.1,
            "semantic_boundary_confirmed": False,
            "acoustic_cues": ["pause"],
            "continuity_risk": "low",
        }])
        self.assertFalse(result["snapped"])
        self.assertEqual(result["cut_seconds"], 5.0)

    def test_pitch_reset_alone_is_not_enough_for_cantonese(self):
        result = snap_cut_to_verified_boundary(5.0, [{
            "time_seconds": 5.1,
            "semantic_boundary_confirmed": True,
            "acoustic_cues": ["pitch_reset"],
            "continuity_risk": "low",
        }])
        self.assertFalse(result["snapped"])

    def test_confirmed_boundary_with_pause_can_be_selected(self):
        result = snap_cut_to_verified_boundary(5.0, [{
            "time_seconds": 5.18,
            "semantic_boundary_confirmed": True,
            "acoustic_cues": ["pause"],
            "continuity_risk": "low",
        }])
        self.assertTrue(result["snapped"])
        self.assertAlmostEqual(result["cut_seconds"], 5.18)
        self.assertEqual(result["reason"], "verified_semantic_boundary")

    def test_confirmed_boundary_with_preboundary_lengthening_can_be_selected(self):
        result = snap_cut_to_verified_boundary(5.0, [{
            "time_seconds": 4.9,
            "semantic_boundary_confirmed": True,
            "acoustic_cues": ["preboundary_lengthening"],
            "continuity_risk": "medium",
        }])
        self.assertTrue(result["snapped"])
        self.assertAlmostEqual(result["cut_seconds"], 4.9)

    def test_high_visual_continuity_risk_blocks_snap(self):
        result = snap_cut_to_verified_boundary(5.0, [{
            "time_seconds": 5.05,
            "semantic_boundary_confirmed": True,
            "acoustic_cues": ["pause", "preboundary_lengthening"],
            "continuity_risk": "high",
        }])
        self.assertFalse(result["snapped"])
        self.assertEqual(result["cut_seconds"], 5.0)

    def test_candidate_outside_window_is_rejected(self):
        result = snap_cut_to_verified_boundary(5.0, [{
            "time_seconds": 5.61,
            "semantic_boundary_confirmed": True,
            "acoustic_cues": ["pause"],
            "continuity_risk": "low",
        }], window_seconds=0.6)
        self.assertFalse(result["snapped"])

    def test_nearest_verified_candidate_wins_without_composite_score(self):
        candidates = [
            {"time_seconds": 5.3, "semantic_boundary_confirmed": True,
             "acoustic_cues": ["pause", "preboundary_lengthening"], "continuity_risk": "low"},
            {"time_seconds": 4.95, "semantic_boundary_confirmed": True,
             "acoustic_cues": ["pause"], "continuity_risk": "low"},
        ]
        result = snap_cut_to_verified_boundary(5.0, candidates)
        self.assertTrue(result["snapped"])
        self.assertAlmostEqual(result["cut_seconds"], 4.95)

    def test_invalid_numeric_inputs_are_rejected(self):
        for value in (True, float("nan"), float("inf"), -0.1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    snap_cut_to_verified_boundary(value, [])
        with self.assertRaises(ValueError):
            snap_cut_to_verified_boundary(1.0, [], window_seconds=math.nan)

    def test_invalid_candidate_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            snap_cut_to_verified_boundary(1.0, [{
                "time_seconds": 1.0,
                "semantic_boundary_confirmed": "yes",
                "acoustic_cues": ["pause"],
                "continuity_risk": "low",
            }])

    def test_unhashable_continuity_risk_is_rejected_cleanly(self):
        with self.assertRaises(ValueError):
            snap_cut_to_verified_boundary(1.0, [{
                "time_seconds": 1.0,
                "semantic_boundary_confirmed": True,
                "acoustic_cues": ["pause"],
                "continuity_risk": [],
            }])

    def test_unknown_acoustic_cue_is_rejected(self):
        with self.assertRaises(ValueError):
            snap_cut_to_verified_boundary(1.0, [{
                "time_seconds": 1.0,
                "semantic_boundary_confirmed": True,
                "acoustic_cues": ["silence_only"],
                "continuity_risk": "low",
            }])

    def test_unrepresentably_large_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            snap_cut_to_verified_boundary(10**1000, [])

    def test_candidate_input_is_not_mutated(self):
        candidate = {
            "time_seconds": 2.0,
            "semantic_boundary_confirmed": True,
            "acoustic_cues": ["pause"],
            "continuity_risk": "low",
        }
        original = dict(candidate)
        snap_cut_to_verified_boundary(2.0, [candidate])
        self.assertEqual(candidate, original)


if __name__ == "__main__":
    unittest.main()
