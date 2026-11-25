from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None

from muse.video_visual_quality import analyze_video_visual_quality


class VideoVisualQualityTests(unittest.TestCase):
    def test_rejects_invalid_sample_count(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"
            source.write_bytes(b"fixture")
            with self.assertRaises(ValueError):
                analyze_video_visual_quality(source, sample_count=2)

    def test_rejects_missing_source_before_optional_import(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.mp4"
            with self.assertRaises(ValueError):
                analyze_video_visual_quality(missing)

    @unittest.skipIf(cv2 is None or np is None, "optional OpenCV/NumPy runtime is unavailable")
    def test_reports_bounded_exposure_detail_and_temporal_metrics_for_real_local_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.avi"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 8, (96, 64))
            self.assertTrue(writer.isOpened())
            for index in range(12):
                frame = np.zeros((64, 96, 3), dtype=np.uint8)
                frame[10:54, 10 + index:42 + index] = (30, 180, 245)
                if index >= 6:
                    frame[:] = (245, 245, 245)
                writer.write(frame)
            writer.release()

            report = analyze_video_visual_quality(source, sample_count=8)

        self.assertEqual(report["status"], "observed")
        self.assertFalse(report["semantic_recognition"])
        self.assertGreaterEqual(report["sampled_frame_count"], 6)
        self.assertLessEqual(report["sampled_frame_count"], 8)
        self.assertEqual(report["temporal"]["pair_count"], report["sampled_frame_count"] - 1)
        self.assertTrue(any(item["code"] == "large_luminance_change" for item in report["signals"]))
        for frame in report["frames"]:
            self.assertGreaterEqual(frame["shadow_fraction"], 0.0)
            self.assertLessEqual(frame["shadow_fraction"], 1.0)
            self.assertGreaterEqual(frame["highlight_fraction"], 0.0)
            self.assertLessEqual(frame["highlight_fraction"], 1.0)
            self.assertGreaterEqual(frame["mean_saturation"], 0.0)
            self.assertLessEqual(frame["mean_saturation"], 255.0)


if __name__ == "__main__":
    unittest.main()
