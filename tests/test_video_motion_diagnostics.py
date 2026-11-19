from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.video_motion_diagnostics import inspect_video_motion


class VideoMotionDiagnosticsTests(unittest.TestCase):
    def test_rejects_non_mp4_and_invalid_frame_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mov"
            path.write_bytes(b"x")
            with self.assertRaises(ValueError):
                inspect_video_motion(path)
            path = Path(folder) / "clip.mp4"
            path.write_bytes(b"x")
            with self.assertRaises(ValueError):
                inspect_video_motion(path, max_frames=1)

    def test_rejects_missing_or_symlink_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.mp4"
            with self.assertRaises(FileNotFoundError):
                inspect_video_motion(path)
            target = Path(folder) / "target.mp4"
            target.write_bytes(b"x")
            link = Path(folder) / "link.mp4"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises(FileNotFoundError):
                inspect_video_motion(link)

    def test_optional_opencv_dependency_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            path.write_bytes(b"fixture")
            with patch.dict("sys.modules", {"numpy": None}):
                result = inspect_video_motion(path)
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["reason"].startswith("opencv_dependency_unavailable:"))

    def test_reports_metrics_without_inventing_pass_threshold(self):
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("optional NumPy dependency is not installed")
        class Capture:
            def __init__(self, _path):
                self.index = 0
            def isOpened(self):
                return True
            def get(self, _key):
                return 3
            def read(self):
                if self.index >= 3:
                    return False, None
                frame = np.zeros((16, 16, 3), dtype=np.uint8)
                frame[:, self.index: self.index + 2] = 255
                self.index += 1
                return True, frame
            def release(self):
                pass

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            path.write_bytes(b"fixture")
            with patch("cv2.VideoCapture", Capture):
                result = inspect_video_motion(path)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["sampled_frames"], 3)
        self.assertEqual(result["sampled_pairs"], 2)
        self.assertIn("median_mean_flow_px", result["summary"])
        self.assertIn("no universal motion pass threshold", result["summary"]["note"])
        self.assertEqual(len(result["pairs"]), 2)


if __name__ == "__main__":
    unittest.main()
