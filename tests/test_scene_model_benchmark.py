from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from tools.evaluate_scene_model_benchmark import compare_models


class SceneModelBenchmarkTests(unittest.TestCase):
    def write_csv(self, path, rows, fields):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_models_are_aligned_by_id_and_missing_predictions_reduce_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            annotations = [
                {"sample_id": "a", "presence": "present", "x_min": ".1", "y_min": ".1", "x_max": ".5", "y_max": ".5"},
                {"sample_id": "b", "presence": "absent"},
                {"sample_id": "c", "presence": "ambiguous"},
            ]
            fields = ["sample_id", "pred_presence", "pred_x_min", "pred_y_min", "pred_x_max", "pred_y_max", "pred_source"]
            first = root / "opencv.csv"
            second = root / "candidate.csv"
            self.write_csv(first, [
                {"sample_id": "a", "pred_presence": "present", "pred_x_min": ".1", "pred_y_min": ".1", "pred_x_max": ".5", "pred_y_max": ".5", "pred_source": "opencv"},
                {"sample_id": "b", "pred_presence": "absent", "pred_source": "opencv"},
            ], fields)
            self.write_csv(second, [{"sample_id": "a", "pred_presence": "absent", "pred_source": "candidate"}], fields)
            result = compare_models(annotations, {"OpenCV": first, "Candidate": second})
            self.assertEqual(result["status"], "scored")
            self.assertEqual(result["models"]["OpenCV"]["confusion"]["true_positive"], 1)
            self.assertEqual(result["models"]["OpenCV"]["coverage"], 1.0)
            self.assertEqual(result["models"]["OpenCV"]["mean_iou"], 1.0)
            self.assertEqual(result["models"]["Candidate"]["missing_prediction_rows"], 2)
            self.assertEqual(result["models"]["Candidate"]["coverage"], .5)

    def test_duplicate_and_unknown_prediction_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pred.csv"
            fields = ["sample_id", "pred_presence"]
            self.write_csv(path, [{"sample_id": "x", "pred_presence": "present"}, {"sample_id": "x", "pred_presence": "absent"}], fields)
            with self.assertRaises(ValueError):
                compare_models([{"sample_id": "x", "presence": "present"}], {"bad": path})
            self.write_csv(path, [{"sample_id": "unknown", "pred_presence": "present"}], fields)
            with self.assertRaises(ValueError):
                compare_models([{"sample_id": "x", "presence": "present"}], {"bad": path})

    def test_empty_prediction_set_is_not_scored(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "empty.csv"
            self.write_csv(path, [], ["sample_id", "pred_presence"])
            result = compare_models([{"sample_id": "x", "presence": "present"}], {"empty": path})
            self.assertEqual(result["models"]["empty"]["status"], "not_scored")
            self.assertEqual(result["models"]["empty"]["coverage"], 0.0)


    def test_prediction_confidence_and_boxes_are_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pred.csv"
            fields = ["sample_id", "pred_presence", "pred_confidence", "pred_x_min", "pred_y_min", "pred_x_max", "pred_y_max"]
            self.write_csv(path, [{"sample_id": "x", "pred_presence": "present", "pred_confidence": "1.2"}], fields)
            with self.assertRaises(ValueError):
                compare_models([{"sample_id": "x", "presence": "present"}], {"bad": path})
            self.write_csv(path, [{"sample_id": "x", "pred_presence": "present", "pred_x_min": ".8", "pred_y_min": ".1", "pred_x_max": ".2", "pred_y_max": ".9"}], fields)
            with self.assertRaises(ValueError):
                compare_models([{"sample_id": "x", "presence": "present"}], {"bad": path})

    def test_template_contains_all_sample_ids_and_refuses_overwrite(self):
        from tools.evaluate_scene_model_benchmark import create_prediction_template
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            annotations = root / "annotations.csv"
            output = root / "predictions" / "model.csv"
            self.write_csv(annotations, [{"sample_id": "a"}, {"sample_id": "b"}], ["sample_id"])
            self.assertEqual(create_prediction_template(annotations, output), 2)
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["sample_id"] for row in rows], ["a", "b"])
            with self.assertRaises(FileExistsError):
                create_prediction_template(annotations, output)


if __name__ == "__main__":
    unittest.main()
