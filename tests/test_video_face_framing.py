from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.video_face_framing import (
    build_dynamic_crop_expression,
    detect_clip_face_center,
    stabilize_face_keyframes,
)


class FakeFrame:
    shape = (80, 100, 3)

    def __init__(self, index):
        self.index = index


class FakeCapture:
    def __init__(self, frame_count=7):
        self.frame_count = frame_count
        self.position = 0
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == 3:  # CAP_PROP_FRAME_COUNT
            return float(self.frame_count)
        if prop == 5:  # CAP_PROP_FPS
            return 10.0
        return 0.0

    def set(self, prop, value):
        self.position = int(value)
        return True

    def read(self):
        return True, FakeFrame(self.position)

    def release(self):
        self.released = True


class FakeCascade:
    def __init__(self, faces_by_frame):
        self.faces_by_frame = faces_by_frame

    def empty(self):
        return False

    def detectMultiScale(self, frame, **kwargs):
        return self.faces_by_frame.get(frame.index, [])


class VideoFaceFramingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"video")
        self.cascade_dir = self.root / "cascades"
        self.cascade_dir.mkdir()
        (self.cascade_dir / "haarcascade_frontalface_default.xml").write_text("fixture", encoding="utf-8")
        self.capture = FakeCapture()

    def tearDown(self):
        self.temp.cleanup()

    def _fake_cv2(self, faces_by_frame):
        cascade = FakeCascade(faces_by_frame)
        return SimpleNamespace(
            data=SimpleNamespace(haarcascades=str(self.cascade_dir) + "/"),
            CascadeClassifier=lambda path: cascade,
            VideoCapture=lambda path: self.capture,
            CAP_PROP_FRAME_COUNT=3,
            CAP_PROP_POS_FRAMES=4,
            CAP_PROP_FPS=5,
            COLOR_BGR2GRAY=6,
            INTER_AREA=7,
            resize=lambda frame, size, interpolation=None: frame,
            cvtColor=lambda frame, code: frame,
        )

    def test_detects_stable_clip_level_face_center_from_sampled_frames(self):
        fake_cv2 = self._fake_cv2({
            0: [(10, 10, 20, 20)],
            3: [(30, 10, 20, 20)],
            6: [(50, 10, 20, 20)],
        })
        with patch.dict(sys.modules, {"cv2": fake_cv2}):
            result = detect_clip_face_center(self.source, 0, 0.6, max_samples=3)

        self.assertEqual(result["status"], "detected")
        self.assertAlmostEqual(result["face_center_x"], 0.4)
        self.assertEqual(result["detected_samples"], 3)
        self.assertEqual(result["sampled_frames"], 3)
        self.assertFalse(result["identity_recognition"])
        self.assertTrue(self.capture.released)
        self.assertEqual(result["framing_mode"], "sampled_face_keyframes")
        self.assertEqual([item["time_seconds"] for item in result["face_keyframes"]], [0.0, 0.3, 0.6])
        self.assertEqual([item["center_x"] for item in result["face_keyframes"]], [0.2, 0.4, 0.6])
        self.assertEqual([item["center_x"] for item in result["stabilized_face_keyframes"]], [0.2, 0.35, 0.5375])
        self.assertTrue(result["crop_stabilization"]["applied"])

    def test_lucas_kanade_bridges_a_single_short_haar_miss(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is part of the optional video-diagnostics extra")
        fake_cv2 = self._fake_cv2({
            0: [(10, 10, 20, 20)],
            6: [(50, 10, 20, 20)],
        })
        points = np.array([[[14, 14]], [[18, 14]], [[14, 18]], [[18, 18]]], dtype=np.float32)
        fake_cv2.goodFeaturesToTrack = lambda image, **kwargs: points.copy()

        def fake_lk(previous, current, source_points, _initial, **kwargs):
            shift = np.array([[[5.0, 0.0]]], dtype=np.float32)
            moved = source_points + shift if previous.index < current.index else source_points - shift
            return moved, np.ones((len(source_points), 1), dtype=np.uint8), np.zeros((len(source_points), 1), dtype=np.float32)

        fake_cv2.calcOpticalFlowPyrLK = fake_lk
        with patch.dict(sys.modules, {"cv2": fake_cv2}):
            result = detect_clip_face_center(self.source, 0, 0.6, max_samples=3)

        self.assertEqual(result["status"], "detected")
        self.assertEqual(result["flow_tracked_samples"], 1)
        self.assertEqual(result["tracking_mode"], "haar_plus_bounded_sparse_lucas_kanade")
        self.assertEqual([item["source"] for item in result["face_keyframes"]], ["haar", "lucas_kanade", "haar"])
        self.assertAlmostEqual(result["face_keyframes"][1]["center_x"], 0.25)

    def test_builds_bounded_time_varying_crop_expression(self):
        expression = build_dynamic_crop_expression([
            {"time_seconds": 0.0, "center_x": 0.25},
            {"time_seconds": 2.0, "center_x": 0.75},
        ], duration_seconds=4.0)
        self.assertIn("t", expression)
        self.assertIn("iw*(if(", expression)
        self.assertIn("0.250000+", expression)
        self.assertIn("0.750000", expression)
        self.assertIn(",", expression)

    def test_crop_stabilization_caps_speed_and_smooths_reversals(self):
        keyframes = [
            {"time_seconds": 0.0, "center_x": 0.2, "source": "haar"},
            {"time_seconds": 1.0, "center_x": 0.95, "source": "lucas_kanade"},
            {"time_seconds": 2.0, "center_x": 0.2, "source": "haar"},
        ]
        stabilized, report = stabilize_face_keyframes(
            keyframes, max_speed_per_second=0.4, smoothing_alpha=0.5,
        )
        centers = [item["center_x"] for item in stabilized]
        self.assertEqual(centers, [0.2, 0.4, 0.3])
        self.assertEqual([item["source"] for item in stabilized], ["haar", "lucas_kanade", "haar"])
        self.assertEqual(report["clamped_keyframes"], 1)
        self.assertLessEqual(report["max_output_speed_per_second"], 0.4)

    def test_crop_stabilization_rejects_invalid_limits_and_keyframes(self):
        valid = [
            {"time_seconds": 0.0, "center_x": 0.2},
            {"time_seconds": 1.0, "center_x": 0.8},
        ]
        with self.assertRaisesRegex(ValueError, "positive"):
            stabilize_face_keyframes(valid, max_speed_per_second=0)
        with self.assertRaisesRegex(ValueError, "smoothing_alpha"):
            stabilize_face_keyframes(valid, smoothing_alpha=1.5)
        with self.assertRaisesRegex(ValueError, "increasing"):
            stabilize_face_keyframes([
                {"time_seconds": 0.5, "center_x": 0.2},
                {"time_seconds": 0.5, "center_x": 0.8},
            ])

    def test_lucas_kanade_rejects_forward_backward_inconsistency(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is part of the optional video-diagnostics extra")
        from muse.video_face_framing import _track_face_center_with_lk

        fake_cv2 = SimpleNamespace()
        points = np.array([[[14, 14]], [[18, 14]], [[14, 18]], [[18, 18]]], dtype=np.float32)
        fake_cv2.goodFeaturesToTrack = lambda image, **kwargs: points.copy()

        def inconsistent_lk(previous, current, source_points, _initial, **kwargs):
            moved = source_points + 5.0
            if previous is not current:
                moved = moved + 5.0
            return moved, np.ones((len(source_points), 1), dtype=np.uint8), np.zeros((len(source_points), 1), dtype=np.float32)

        fake_cv2.calcOpticalFlowPyrLK = inconsistent_lk
        result = _track_face_center_with_lk(
            fake_cv2, FakeFrame(0), FakeFrame(1), (10, 10, 20, 20),
        )
        self.assertIsNone(result)

    @unittest.skipUnless(
        importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None,
        "OpenCV and NumPy are optional video-diagnostics dependencies",
    )
    def test_real_lucas_kanade_tracks_a_synthetic_translated_face_patch(self):
        import cv2
        import numpy as np
        from muse.video_face_framing import _track_face_center_with_lk

        rng = np.random.default_rng(42)
        previous = np.zeros((160, 240), dtype=np.uint8)
        patch = rng.integers(20, 240, size=(48, 48), dtype=np.uint8)
        previous[40:88, 50:98] = patch
        current = np.zeros_like(previous)
        current[40:88, 58:106] = patch

        result = _track_face_center_with_lk(cv2, previous, current, (50, 40, 48, 48))

        self.assertIsNotNone(result)
        self.assertGreaterEqual(result["tracked_points"], 4)
        self.assertGreaterEqual(result["inlier_ratio"], 0.35)
        self.assertAlmostEqual(result["center_x"], 82 / 240, delta=0.03)

    def test_dynamic_crop_expression_rejects_bad_keyframes(self):
        with self.assertRaisesRegex(ValueError, "at least two"):
            build_dynamic_crop_expression([{"time_seconds": 0, "center_x": 0.5}], duration_seconds=3)
        with self.assertRaisesRegex(ValueError, "increasing"):
            build_dynamic_crop_expression([
                {"time_seconds": 1, "center_x": 0.5},
                {"time_seconds": 1, "center_x": 0.6},
            ], duration_seconds=3)
        with self.assertRaisesRegex(ValueError, "duration"):
            build_dynamic_crop_expression([
                {"time_seconds": 0, "center_x": 0.5},
                {"time_seconds": 4, "center_x": 0.6},
            ], duration_seconds=3)

    def test_no_face_is_explicit_and_does_not_invent_a_crop_center(self):
        with patch.dict(sys.modules, {"cv2": self._fake_cv2({})}):
            result = detect_clip_face_center(self.source, 0, 0.6, max_samples=3)
        self.assertEqual(result["status"], "no_face")
        self.assertIsNone(result["face_center_x"])
        self.assertEqual(result["detected_samples"], 0)

    def test_single_frame_false_positive_is_rejected_as_low_confidence(self):
        fake_cv2 = self._fake_cv2({3: [(30, 10, 20, 20)]})
        with patch.dict(sys.modules, {"cv2": fake_cv2}):
            result = detect_clip_face_center(self.source, 0, 0.6, max_samples=3)
        self.assertEqual(result["status"], "low_confidence")
        self.assertIsNone(result["face_center_x"])
        self.assertEqual(result["detected_samples"], 1)
        self.assertAlmostEqual(result["detection_coverage"], 1 / 3, delta=1e-6)

    def test_missing_opencv_is_a_safe_unavailable_result(self):
        with patch.dict(sys.modules, {"cv2": None}):
            result = detect_clip_face_center(self.source, 0, 0.6)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["face_center_x"])

    def test_invalid_interval_is_rejected_before_video_decode(self):
        with self.assertRaisesRegex(ValueError, "interval"):
            detect_clip_face_center(self.source, 2, 1)


if __name__ == "__main__":
    unittest.main()
