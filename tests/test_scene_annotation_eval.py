from __future__ import annotations

import unittest

from tools.evaluate_scene_annotations import evaluate


class SceneAnnotationEvaluationTests(unittest.TestCase):
    def test_empty_human_labels_are_not_reported_as_scored(self):
        result = evaluate([{"presence": "", "pred_presence": "present"}])
        self.assertEqual(result["status"], "not_scored")
        self.assertEqual(result["unlabelled_samples"], 1)
        self.assertIsNone(result["precision"])

    def test_presence_confusion_and_coverage_exclude_ambiguous(self):
        rows = [
            {"presence": "present", "pred_presence": "present", "x_min": "0.1", "y_min": "0.1", "x_max": "0.5", "y_max": "0.5", "pred_x_min": "0.1", "pred_y_min": "0.1", "pred_x_max": "0.5", "pred_y_max": "0.5"},
            {"presence": "absent", "pred_presence": "present"},
            {"presence": "present", "pred_presence": "absent"},
            {"presence": "ambiguous", "pred_presence": "present"},
            {"presence": "present", "pred_presence": ""},
            {"presence": "", "pred_presence": "present"},
        ]
        result = evaluate(rows)
        self.assertEqual(result["confusion"], {"true_positive": 1, "false_positive": 1, "true_negative": 0, "false_negative": 1})
        self.assertEqual(result["ambiguous_samples_excluded"], 1)
        self.assertEqual(result["prediction_missing_samples"], 1)
        self.assertEqual(result["unlabelled_samples"], 1)
        self.assertAlmostEqual(result["coverage"], 3 / 4)
        self.assertEqual(result["mean_iou"], 1.0)
        self.assertEqual(result["mean_normalized_center_error"], 0.0)

    def test_camera_motion_metrics_are_separate_and_require_both_labels(self):
        rows = [
            {"camera_motion": "static", "pred_camera_motion": "static"},
            {"camera_motion": "low", "pred_camera_motion": "medium"},
            {"camera_motion": "high", "pred_camera_motion": ""},
            {"camera_motion": "", "pred_camera_motion": "high"},
        ]
        result = evaluate(rows)["camera_motion"]
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["scored_samples"], 2)
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(result["prediction_missing_samples"], 1)
        self.assertEqual(result["unlabelled_samples"], 1)
        self.assertEqual(result["confusion"]["low"]["medium"], 1)

    def test_legacy_rows_without_camera_prediction_remain_not_scored(self):
        result = evaluate([{"presence": "", "camera_motion": "static"}])
        self.assertEqual(result["camera_motion"]["status"], "not_scored")
        self.assertEqual(result["camera_motion"]["unlabelled_samples"], 0)
        self.assertEqual(result["camera_motion"]["prediction_missing_samples"], 1)

    def test_partial_or_out_of_range_boxes_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([{"presence": "present", "pred_presence": "present", "x_min": "0.1"}])
        with self.assertRaises(ValueError):
            evaluate([{"presence": "present", "pred_presence": "present", "x_min": "-0.1", "y_min": "0", "x_max": "0.5", "y_max": "0.5"}])


if __name__ == "__main__":
    unittest.main()
