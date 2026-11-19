"""Optional local OpenCV scene composition heuristics.

This module estimates visual prominence and frame-to-frame motion. It does not
recognize semantic objects, people, faces, or scene meaning.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


_FIELDS = (
    "subject_center_x", "subject_center_y", "subject_scale",
    "negative_space_left", "negative_space_right",
    "negative_space_top", "negative_space_bottom", "motion_level",
)


def analyze_scene_assets(paths: list[Path]) -> dict[str, Any]:
    """Estimate composition where reliable and retain independent motion diagnostics."""
    try:
        import cv2
        import numpy as np
    except Exception as exc:  # optional dependency; never break video creation
        return {"status": "unavailable", "reason": f"opencv_dependency_unavailable:{type(exc).__name__}"}

    if not isinstance(paths, list) or not paths:
        return {"status": "unavailable", "reason": "no_scene_assets"}

    scenes: list[dict[str, float]] = []
    motion_diagnostics: list[dict[str, Any]] = []
    composition_complete = True
    first_reason: str | None = None
    for index, path in enumerate(paths, start=1):
        asset = Path(path)
        try:
            frames = _sample_frames(asset, cv2)
            if not frames:
                composition_complete = False
                first_reason = first_reason or "media_decode_failed"
                motion_diagnostics.append({
                    "scene_index": index, "asset_name": asset.name,
                    "sampled_frame_count": 0, "motion_sample_pairs": 0,
                    "motion_level": None, "motion_status": "unavailable",
                    "composition_status": "unavailable", "reason": "media_decode_failed",
                })
                continue

            motion_level = (
                float(max(_motion_level(a, b, cv2, np) for a, b in zip(frames, frames[1:])))
                if len(frames) > 1 else 0.0
            )
            diagnostic = {
                "scene_index": index, "asset_name": asset.name,
                "sampled_frame_count": len(frames),
                "motion_sample_pairs": max(0, len(frames) - 1),
                "motion_level": motion_level, "motion_status": "completed",
            }
            features = [_composition_features(frame, cv2, np) for frame in frames]
            if any(item is None for item in features):
                composition_complete = False
                first_reason = first_reason or "composition_estimation_failed"
                diagnostic.update({
                    "composition_status": "unavailable",
                    "reason": "composition_estimation_failed",
                })
                motion_diagnostics.append(diagnostic)
                continue

            # Aggregate spatial estimates across samples; retain the peak observed motion.
            keys = _FIELDS[:-1]
            scene = {key: float(np.median([item[key] for item in features])) for key in keys}
            scene["motion_level"] = motion_level
            scenes.append(scene)
            diagnostic["composition_status"] = "completed"
            motion_diagnostics.append(diagnostic)
        except Exception:
            composition_complete = False
            first_reason = first_reason or "media_analysis_failed"
            motion_diagnostics.append({
                "scene_index": index, "asset_name": asset.name,
                "sampled_frame_count": 0, "motion_sample_pairs": 0,
                "motion_level": None, "motion_status": "unavailable",
                "composition_status": "unavailable", "reason": "media_analysis_failed",
            })

    if not composition_complete or len(scenes) != len(paths):
        # Keep motion-only diagnostics, but never expose a partial composition list
        # to the all-scenes content-aware camera-move validator.
        measured = any(item.get("motion_status") == "completed" for item in motion_diagnostics)
        return {
            "status": "partial" if measured else "unavailable",
            "reason": first_reason or "composition_estimation_failed",
            "source": "opencv_visual_heuristic_v1",
            "semantic_recognition": False,
            "motion_diagnostics": motion_diagnostics,
        }

    return {
        "status": "completed",
        "source": "opencv_visual_heuristic_v1",
        "semantic_recognition": False,
        "scenes": scenes,
        "motion_diagnostics": motion_diagnostics,
    }


def _sample_frames(path: Path, cv2: Any) -> list[Any]:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        return [frame] if frame is not None else []
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        return []
    try:
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if count < 1:
            return []
        indexes = sorted({0, count // 2, max(0, count - 1)})
        frames = []
        for index in indexes:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame)
        return frames
    finally:
        capture.release()


def _composition_features(frame: Any, cv2: Any, np: Any) -> dict[str, float] | None:
    height, width = frame.shape[:2]
    if width < 16 or height < 16:
        return None
    small = cv2.resize(frame, (min(320, width), min(320, height)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    blurred = cv2.GaussianBlur(gray, (0, 0), 7.0)
    local_contrast = np.abs(gray - blurred)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32) / 255.0
    edges = cv2.Canny(small, 48, 120).astype(np.float32) / 255.0
    saliency = 0.55 * local_contrast + 0.30 * saturation + 0.15 * edges
    cutoff = float(np.quantile(saliency, 0.78))
    mask = saliency >= max(cutoff, float(saliency.mean() + saliency.std() * 0.25))
    ys, xs = np.nonzero(mask)
    if len(xs) < max(8, int(mask.size * 0.005)):
        ys, xs = np.indices(mask.shape)
        weights = saliency + 1e-6
        center_x = float((xs * weights).sum() / weights.sum()) / max(1, mask.shape[1] - 1)
        center_y = float((ys * weights).sum() / weights.sum()) / max(1, mask.shape[0] - 1)
        return {
            "subject_center_x": center_x, "subject_center_y": center_y,
            "subject_scale": 0.0, "negative_space_left": center_x,
            "negative_space_right": 1.0 - center_x,
            "negative_space_top": center_y, "negative_space_bottom": 1.0 - center_y,
            "motion_level": 0.0,
        }
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    weights = saliency[ys, xs] + 1e-6
    center_x = float((xs * weights).sum() / weights.sum()) / max(1, mask.shape[1] - 1)
    center_y = float((ys * weights).sum() / weights.sum()) / max(1, mask.shape[0] - 1)
    scale = float(np.sqrt(((x1 - x0) * (y1 - y0)) / mask.size))
    # Broad, frame-filling saliency is not a reliable subject box; fail closed.
    if scale >= 0.75:
        return None
    return {
        "subject_center_x": min(1.0, max(0.0, center_x)),
        "subject_center_y": min(1.0, max(0.0, center_y)),
        "subject_scale": min(1.0, max(0.0, scale)),
        "negative_space_left": x0 / mask.shape[1],
        "negative_space_right": (mask.shape[1] - x1) / mask.shape[1],
        "negative_space_top": y0 / mask.shape[0],
        "negative_space_bottom": (mask.shape[0] - y1) / mask.shape[0],
        "motion_level": 0.0,
    }


def _motion_level(first: Any, second: Any, cv2: Any, np: Any) -> float:
    a = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(second, cv2.COLOR_BGR2GRAY)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    magnitude = cv2.magnitude(flow[:, :, 0], flow[:, :, 1])
    diagonal = float((a.shape[0] ** 2 + a.shape[1] ** 2) ** 0.5)
    return min(1.0, max(0.0, float(np.percentile(magnitude, 75)) / max(1.0, diagonal * 0.025)))
