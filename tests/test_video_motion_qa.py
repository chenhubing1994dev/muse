from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import inspect_video_motion as qa


class VideoMotionQATests(unittest.TestCase):
    def test_rejects_missing_video(self):
        with self.assertRaises(FileNotFoundError):
            qa.inspect_video(Path("missing-file.mp4"))

    def test_rejects_invalid_thresholds(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.mp4"
            path.write_bytes(b"x")
            with self.assertRaises(ValueError):
                qa.inspect_video(path, motion_floor=-1)
            with self.assertRaises(ValueError):
                qa.inspect_video(path, freeze_seconds=0)

    @patch.object(qa, "_require_tools", return_value=("ffmpeg", "ffprobe"))
    @patch.object(qa, "_run")
    def test_reports_technical_pass_but_flags_short_low_motion_clip(self, run, tools):
        run.side_effect = [
            qa.subprocess.CompletedProcess([], 0, json.dumps({
                "format": {"duration": "1.0625", "size": "29557"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "width": 256,
                             "height": 256, "r_frame_rate": "16/1", "nb_frames": "17"}],
            }), ""),
            qa.subprocess.CompletedProcess([], 0, "", ""),
            qa.subprocess.CompletedProcess([], 0, "", "".join(
                f"lavfi.signalstats.YAVG={value}\n" for value in [0.2, 0.4, 0.6]
            )),
            qa.subprocess.CompletedProcess([], 0, "", ""),
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.mp4"
            path.write_bytes(b"mp4")
            result = qa.inspect_video(path)
        self.assertEqual(result["status"], "technical_pass")
        self.assertTrue(result["decode"]["ok"])
        self.assertEqual(result["video"]["frame_count"], 17)
        self.assertAlmostEqual(result["motion_signal"]["mean_yavg"], 0.4)
        self.assertIn("low_frame_difference_signal", result["warnings"])
        self.assertIn("very_short_clip_not_suitable_for_motion_quality_judgement", result["warnings"])
        self.assertEqual(result["freeze"]["events"], [])

    @patch.object(qa, "_require_tools", return_value=("ffmpeg", "ffprobe"))
    @patch.object(qa, "_run")
    def test_freeze_event_forces_technical_review(self, run, tools):
        run.side_effect = [
            qa.subprocess.CompletedProcess([], 0, json.dumps({
                "format": {"duration": "4", "size": "100"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "width": 640,
                             "height": 360, "r_frame_rate": "24/1", "nb_frames": "96"}],
            }), ""),
            qa.subprocess.CompletedProcess([], 0, "", ""),
            qa.subprocess.CompletedProcess([], 0, "", "lavfi.signalstats.YAVG=4.2\n"),
            qa.subprocess.CompletedProcess([], 0, "", "lavfi.freezedetect.freeze_start=1.0\nlavfi.freezedetect.freeze_duration=0.5\n"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.mp4"
            path.write_bytes(b"mp4")
            result = qa.inspect_video(path)
        self.assertEqual(result["status"], "technical_review")
        self.assertIn("freeze_events_detected", result["warnings"])
        self.assertEqual(len(result["freeze"]["events"]), 2)


if __name__ == "__main__":
    unittest.main()
