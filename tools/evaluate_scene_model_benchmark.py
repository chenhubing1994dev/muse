"""Compare independent model prediction CSVs against one human-labelled dataset."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from tools.evaluate_scene_annotations import evaluate

PREDICTION_FIELDS = ("sample_id", "pred_presence", "pred_x_min", "pred_y_min", "pred_x_max", "pred_y_max", "pred_confidence", "pred_source")


def _validate_prediction(row: dict[str, str], path: Path, sample_id: str) -> None:
    presence = (row.get("pred_presence") or "").strip().lower()
    if presence not in {"", "present", "absent", "unknown"}:
        raise ValueError(f"{path}: {sample_id}: pred_presence must be present, absent, unknown, or blank")
    box_keys = ("pred_x_min", "pred_y_min", "pred_x_max", "pred_y_max")
    raw_box = [(row.get(key) or "").strip() for key in box_keys]
    if any(raw_box) and not all(raw_box):
        raise ValueError(f"{path}: {sample_id}: incomplete prediction bounding box")
    if all(raw_box):
        box = [float(value) for value in raw_box]
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"{path}: {sample_id}: prediction box coordinates must be finite and within [0, 1]")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError(f"{path}: {sample_id}: invalid prediction bounding box")
        if presence == "absent":
            raise ValueError(f"{path}: {sample_id}: absent prediction cannot include a bounding box")
    confidence = (row.get("pred_confidence") or "").strip()
    if confidence:
        value = float(confidence)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{path}: {sample_id}: pred_confidence must be finite and within [0, 1]")


def load_prediction_file(path: Path) -> dict[str, dict[str, str]]:
    predictions: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"sample_id", "pred_presence"}.issubset(reader.fieldnames):
            raise ValueError(f"{path}: required columns are sample_id and pred_presence")
        for row in reader:
            sample_id = (row.get("sample_id") or "").strip()
            if not sample_id:
                raise ValueError(f"{path}: blank sample_id")
            if sample_id in predictions:
                raise ValueError(f"{path}: duplicate sample_id {sample_id}")
            cleaned = {key: (row.get(key) or "").strip() for key in PREDICTION_FIELDS if key in row}
            _validate_prediction(cleaned, path, sample_id)
            predictions[sample_id] = cleaned
    return predictions


def create_prediction_template(annotations_path: Path, output_path: Path) -> int:
    with annotations_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    ids = [row.get("sample_id", "").strip() for row in rows]
    if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("annotation dataset must contain unique, non-empty sample_id values")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing prediction file: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREDICTION_FIELDS)
        writer.writeheader()
        writer.writerows({"sample_id": sample_id, "pred_presence": "", "pred_source": ""} for sample_id in ids)
    return len(ids)


def compare_models(annotations: list[dict[str, str]], model_files: dict[str, Path]) -> dict[str, Any]:
    if not annotations:
        raise ValueError("human annotation dataset is empty")
    ids = [row.get("sample_id", "").strip() for row in annotations]
    if any(not sample_id for sample_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("human annotations contain blank or duplicate sample_id")
    known = set(ids)
    results: dict[str, Any] = {}
    for model_name, path in model_files.items():
        if not model_name.strip() or model_name in results:
            raise ValueError(f"invalid or duplicate model name: {model_name!r}")
        predictions = load_prediction_file(path)
        unknown = sorted(set(predictions) - known)
        if unknown:
            raise ValueError(f"{model_name}: predictions contain unknown sample IDs: {unknown[:5]}")
        merged: list[dict[str, str]] = []
        for annotation in annotations:
            row = dict(annotation)
            for key in PREDICTION_FIELDS:
                if key != "sample_id":
                    row[key] = ""
            row.update(predictions.get(annotation["sample_id"].strip(), {}))
            merged.append(row)
        metrics = evaluate(merged)
        metrics["model"] = model_name
        metrics["prediction_rows"] = len(predictions)
        metrics["missing_prediction_rows"] = len(known - set(predictions))
        metrics["prediction_source"] = str(path)
        results[model_name] = metrics
    return {
        "status": "scored" if any(item["status"] == "scored" for item in results.values()) else "not_scored",
        "human_samples": len(annotations),
        "models": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=Path("data/annotation/b160/annotations.csv"))
    parser.add_argument("--predictions", action="append", default=[], metavar="NAME=CSV", help="repeat once per model")
    parser.add_argument("--make-template", type=Path, help="create a blank model prediction CSV; refuses overwrite")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.make_template:
        count = create_prediction_template(args.annotations, args.make_template)
        print(json.dumps({"status": "template_created", "samples": count, "path": str(args.make_template)}, ensure_ascii=False))
        return 0
    model_files: dict[str, Path] = {}
    for item in args.predictions:
        if "=" not in item:
            parser.error(f"invalid --predictions value {item!r}; expected NAME=CSV")
        name, raw_path = item.split("=", 1)
        if not name or not raw_path or name in model_files:
            parser.error(f"invalid or duplicate model entry {item!r}")
        model_files[name] = Path(raw_path)
    with args.annotations.open(newline="", encoding="utf-8-sig") as handle:
        annotations = list(csv.DictReader(handle))
    result = compare_models(annotations, model_files)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
