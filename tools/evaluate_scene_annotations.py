"""Evaluate scene predictions against independently supplied human annotations."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def _number(row: dict[str, str], key: str) -> float | None:
    value = (row.get(key) or "").strip()
    if not value:
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{key} must be finite and within [0, 1]")
    return number


def _box(row: dict[str, str], prefix: str = "") -> tuple[float, float, float, float] | None:
    values = [_number(row, f"{prefix}{key}") for key in ("x_min", "y_min", "x_max", "y_max")]
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError(f"incomplete {prefix or 'ground-truth '}bounding box")
    x0, y0, x1, y1 = values
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"invalid {prefix or 'ground-truth '}bounding box")
    return x0, y0, x1, y1  # type: ignore[return-value]


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)


MOTION_LEVELS = ("static", "low", "medium", "high")


def _evaluate_camera_motion(rows: list[dict[str, str]]) -> dict[str, Any]:
    confusion = {truth: {prediction: 0 for prediction in MOTION_LEVELS} for truth in MOTION_LEVELS}
    unlabelled = missing_prediction = invalid = 0
    for row in rows:
        truth = (row.get("camera_motion") or "").strip().lower()
        prediction = (row.get("pred_camera_motion") or "").strip().lower()
        if not truth:
            unlabelled += 1
            continue
        if truth not in MOTION_LEVELS:
            invalid += 1
            continue
        if not prediction:
            missing_prediction += 1
            continue
        if prediction not in MOTION_LEVELS:
            invalid += 1
            continue
        confusion[truth][prediction] += 1
    scored = sum(sum(values.values()) for values in confusion.values())
    correct = sum(confusion[level][level] for level in MOTION_LEVELS)
    return {
        "status": "scored" if scored else "not_scored",
        "scored_samples": scored,
        "unlabelled_samples": unlabelled,
        "prediction_missing_samples": missing_prediction,
        "invalid_samples": invalid,
        "accuracy": correct / scored if scored else None,
        "confusion": confusion,
        "note": "Exact four-class agreement only; ordinal distance is not inferred.",
    }


def evaluate(rows: list[dict[str, str]]) -> dict[str, Any]:
    tp = fp = tn = fn = ambiguous = unlabelled = pred_missing = 0
    ious: list[float] = []
    center_errors: list[float] = []
    for row in rows:
        truth = (row.get("presence") or "").strip().lower()
        prediction = (row.get("pred_presence") or "").strip().lower()
        if truth not in {"present", "absent", "ambiguous"}:
            unlabelled += 1
            continue
        if truth == "ambiguous":
            ambiguous += 1
            continue
        if prediction not in {"present", "absent"}:
            pred_missing += 1
            continue
        if truth == "present" and prediction == "present":
            tp += 1
            gt_box, pred_box = _box(row), _box(row, "pred_")
            if gt_box and pred_box:
                ious.append(_iou(gt_box, pred_box))
                gt_center = ((gt_box[0] + gt_box[2]) / 2, (gt_box[1] + gt_box[3]) / 2)
                pred_center = ((pred_box[0] + pred_box[2]) / 2, (pred_box[1] + pred_box[3]) / 2)
                center_errors.append(math.dist(gt_center, pred_center))
        elif truth == "absent" and prediction == "absent":
            tn += 1
        elif truth == "absent" and prediction == "present":
            fp += 1
        else:
            fn += 1
    scored = tp + fp + tn + fn
    return {
        "camera_motion": _evaluate_camera_motion(rows),
        "status": "scored" if scored else "not_scored",
        "total_samples": len(rows), "scored_samples": scored,
        "ambiguous_samples_excluded": ambiguous, "unlabelled_samples": unlabelled,
        "prediction_missing_samples": pred_missing,
        "coverage": scored / (len(rows) - ambiguous - unlabelled) if len(rows) > ambiguous + unlabelled else None,
        "confusion": {"true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn},
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "bbox_scored_samples": len(ious),
        "mean_iou": sum(ious) / len(ious) if ious else None,
        "mean_normalized_center_error": sum(center_errors) / len(center_errors) if center_errors else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotations", type=Path, nargs="?", default=Path("data/annotation/b160/annotations.csv"))
    args = parser.parse_args()
    with args.annotations.open(newline="", encoding="utf-8-sig") as handle:
        result = evaluate(list(csv.DictReader(handle)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())