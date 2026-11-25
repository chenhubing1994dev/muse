import unittest

from scripts.build_cantonese_pilot_v7_audio_qa import _qa_warning_counts


class CantonesePilotV7QASummaryTests(unittest.TestCase):
    def test_counts_authoritative_technical_and_frozen_frame_warnings(self):
        technical = {
            "video-9x16.mp4": {"warnings": [
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
            ]},            "video-16x9.mp4": {"warnings": [
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
                {"code": "frozen_frames"},
            ]},
        }
        self.assertEqual(_qa_warning_counts(technical), (12, 12))
    def test_empty_optical_flow_diagnostics_do_not_hide_technical_warnings(self):
        technical = {"vertical.mp4": {"warnings": [{"code": "frozen_frames"}]}}
        optical_flow_diagnostics = {"vertical.mp4": {"observations": []}}
        self.assertEqual(_qa_warning_counts(technical), (1, 1))
        self.assertNotIn("warnings", optical_flow_diagnostics["vertical.mp4"])

    def test_missing_or_malformed_warning_lists_are_ignored_safely(self):
        self.assertEqual(_qa_warning_counts({
            "a": {"warnings": None}, "b": None, "c": {"warnings": ["bad", {}]}
        }), (0, 0))


if __name__ == "__main__":
    unittest.main()
