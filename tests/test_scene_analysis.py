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

from muse.scene_analysis import analyze_scene_assets


@unittest.skipIf(cv2 is None or np is None, "optional OpenCV/NumPy runtime is unavailable")
class SceneAnalysisTests(unittest.TestCase):
    def test_local_image_analysis_returns_bounded_non_semantic_composition_features(self):
        with tempfile.TemporaryDirectory() as directory:
            image = np.zeros((180, 320, 3), dtype=np.uint8)
            image[35:145, 35:130] = (20, 180, 245)
            path = Path(directory) / "composition.png"
            self.assertTrue(cv2.imwrite(str(path), image))

            result = analyze_scene_assets([path])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source"], "opencv_visual_heuristic_v1")
        self.assertFalse(result["semantic_recognition"])
        self.assertEqual(len(result["scenes"]), 1)
        scene = result["scenes"][0]
        self.assertEqual(set(scene), {
            "subject_center_x", "subject_center_y", "subject_scale",
            "negative_space_left", "negative_space_right",
            "negative_space_top", "negative_space_bottom", "motion_level",
        })
        for value in scene.values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
        self.assertEqual(scene["motion_level"], 0.0)

    def test_frame_filling_texture_is_rejected_but_motion_diagnostics_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            rng = np.random.default_rng(17)
            path = Path(directory) / "texture.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 8, (320, 180))
            self.assertTrue(writer.isOpened())
            for _ in range(3):
                writer.write(rng.integers(0, 256, size=(180, 320, 3), dtype=np.uint8))
            writer.release()
            result = analyze_scene_assets([path])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reason"], "composition_estimation_failed")
        self.assertNotIn("scenes", result)
        self.assertEqual(len(result["motion_diagnostics"]), 1)
        diagnostic = result["motion_diagnostics"][0]
        self.assertEqual(diagnostic["composition_status"], "unavailable")
        self.assertEqual(diagnostic["motion_status"], "completed")
        self.assertGreater(diagnostic["motion_level"], 0.0)
        self.assertEqual(diagnostic["motion_sample_pairs"], 2)

    def test_unreadable_asset_returns_explicit_unavailable_without_fabricated_features(self):
        with tempfile.TemporaryDirectory() as directory:
            result = analyze_scene_assets([Path(directory) / "missing.png"])
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("scenes", result)


if __name__ == "__main__":
    unittest.main()
