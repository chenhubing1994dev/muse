"""Non-semantic, local visual diagnostics for generated video.

Metrics are review signals, not an aesthetic score or proof of visual quality.
The analyzer deliberately does not identify people, objects, actions or identity.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


MAX_SAMPLES = 48
MAX_EDGE = 640


def _unavailable(reason: str, *, source_name: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "unavailable",
        "method": "opencv_sampled_visual_diagnostics_v1",
        "semantic_recognition": False,
        "source_name": source_name,
        "reason": reason[:180],
        "frames": [],
        "temporal": None,
        "signals": [],
    }


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def analyze_video_visual_quality(
    video_path: str | Path,
    *,
    sample_count: int = 24,
    cv2_module: Any = None,
    numpy_module: Any = None,
) -> dict[str, Any]:
    """Sample a video and report exposure, detail, color and temporal signals."""
    path = Path(video_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("video source must be a non-empty regular file")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or not 6 <= sample_count <= MAX_SAMPLES:
        raise ValueError(f"sample_count must be an integer between 6 and {MAX_SAMPLES}")

    try:
        if cv2_module is None:
            import cv2 as cv2_module
        if numpy_module is None:
            import numpy as numpy_module
    except Exception as exc:
        return _unavailable(f"opencv_or_numpy_unavailable:{type(exc).__name__}", source_name=path.name)

    try:
        before = path.stat()
        signature = (before.st_size, before.st_mtime_ns, getattr(before, "st_ino", 0))
        capture = cv2_module.VideoCapture(str(path))
        if not capture.isOpened():
            capture.release()
            return _unavailable("video_decode_unavailable", source_name=path.name)
        try:
            frame_count = int(capture.get(cv2_module.CAP_PROP_FRAME_COUNT))
            fps = _finite(capture.get(cv2_module.CAP_PROP_FPS))
            if frame_count < 2 or fps is None or fps <= 0:
                return _unavailable("video_frame_count_or_fps_unavailable", source_name=path.name)
            indexes = sorted({
                int(round(value))
                for value in numpy_module.linspace(0, frame_count - 1, min(sample_count, frame_count))
            })
            frames: list[dict[str, Any]] = []
            gray_frames: list[Any] = []
            for frame_index in indexes:
                capture.set(cv2_module.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = capture.read()
                if not ok or frame is None or getattr(frame, "ndim", 0) < 2:
                    continue
                height, width = frame.shape[:2]
                if width < 16 or height < 16:
                    continue
                scale = min(1.0, MAX_EDGE / float(max(width, height)))
                if scale < 1.0:
                    frame = cv2_module.resize(
                        frame,
                        (max(16, int(round(width * scale))), max(16, int(round(height * scale)))),
                        interpolation=cv2_module.INTER_AREA,
                    )
                gray = cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2GRAY)
                hsv = cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2HSV)
                gray_float = gray.astype(numpy_module.float32)
                p05, p50, p95 = numpy_module.percentile(gray_float, [5, 50, 95])
                sharpness = cv2_module.Laplacian(gray, cv2_module.CV_64F).var()
                saturation = hsv[:, :, 1].astype(numpy_module.float32)
                pixel_count = max(1, int(gray.size))
                dark_fraction = float(numpy_module.count_nonzero(gray < 16)) / pixel_count
                highlight_fraction = float(numpy_module.count_nonzero(gray > 245)) / pixel_count
                frames.append({
                    "frame_index": int(frame_index),
                    "time_seconds": round(frame_index / fps, 3),
                    "width": int(width),
                    "height": int(height),
                    "mean_luma": round(float(gray_float.mean()), 3),
                    "p05_luma": round(float(p05), 3),
                    "median_luma": round(float(p50), 3),
                    "p95_luma": round(float(p95), 3),
                    "contrast_stddev": round(float(gray_float.std()), 3),
                    "sharpness_laplacian_variance": round(float(sharpness), 3),
                    "mean_saturation": round(float(saturation.mean()), 3),
                    "shadow_fraction": round(dark_fraction, 5),
                    "highlight_fraction": round(highlight_fraction, 5),
                })
                gray_frames.append(gray)
        finally:
            capture.release()

        after = path.stat()
        if (after.st_size, after.st_mtime_ns, getattr(after, "st_ino", 0)) != signature:
            return _unavailable("source_changed_during_analysis", source_name=path.name)
        if len(frames) < 2:
            return _unavailable("insufficient_decodable_sample_frames", source_name=path.name)

        temporal_pairs: list[dict[str, float]] = []
        for left, right in zip(gray_frames, gray_frames[1:]):
            a = left
            b = right
            if a.shape != b.shape:
                b = cv2_module.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2_module.INTER_AREA)
            mean_delta = abs(float(a.astype(numpy_module.float32).mean()) -
                             float(b.astype(numpy_module.float32).mean()))
            flow = cv2_module.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            magnitude = cv2_module.magnitude(flow[:, :, 0], flow[:, :, 1])
            median_dx = float(numpy_module.median(flow[:, :, 0]))
            median_dy = float(numpy_module.median(flow[:, :, 1]))
            residual_x = flow[:, :, 0] - median_dx
            residual_y = flow[:, :, 1] - median_dy
            residual = cv2_module.magnitude(residual_x, residual_y)
            temporal_pairs.append({
                "mean_luma_delta": round(mean_delta, 3),
                "median_flow_pixels": round(float(numpy_module.median(magnitude)), 3),
                "p90_flow_pixels": round(float(numpy_module.percentile(magnitude, 90)), 3),
                "p90_residual_flow_pixels": round(float(numpy_module.percentile(residual, 90)), 3),
            })

        signals: list[dict[str, Any]] = []
        for item in frames:
            if item["shadow_fraction"] >= 0.65:
                signals.append({"code": "shadow_heavy_frame", "time_seconds": item["time_seconds"],
                                "severity": "review", "metric": item["shadow_fraction"]})
            if item["highlight_fraction"] >= 0.25:
                signals.append({"code": "highlight_clipping_risk", "time_seconds": item["time_seconds"],
                                "severity": "review", "metric": item["highlight_fraction"]})
            if item["contrast_stddev"] < 18.0:
                signals.append({"code": "low_contrast_frame", "time_seconds": item["time_seconds"],
                                "severity": "review", "metric": item["contrast_stddev"]})
            if item["sharpness_laplacian_variance"] < 18.0:
                signals.append({"code": "low_detail_or_soft_focus", "time_seconds": item["time_seconds"],
                                "severity": "review", "metric": item["sharpness_laplacian_variance"]})
        for index, pair in enumerate(temporal_pairs):
            if pair["mean_luma_delta"] >= 32.0:
                signals.append({
                    "code": "large_luminance_change",
                    "time_seconds": frames[index + 1]["time_seconds"],
                    "severity": "review",
                    "metric": pair["mean_luma_delta"],
                    "note": "May be an intentional cut, lighting change or flicker; inspect the actual frames.",
                })

        return {
            "schema_version": 1,
            "status": "observed",
            "method": "opencv_sampled_visual_diagnostics_v1",
            "semantic_recognition": False,
            "source_name": path.name,
            "duration_seconds": round(frame_count / fps, 3),
            "source_fps": round(fps, 4),
            "source_frame_count": frame_count,
            "sampled_frame_count": len(frames),
            "frames": frames,
            "temporal": {
                "pair_count": len(temporal_pairs),
                "max_mean_luma_delta": round(max(item["mean_luma_delta"] for item in temporal_pairs), 3),
                "median_flow_pixels": round(float(numpy_module.median(
                    [item["median_flow_pixels"] for item in temporal_pairs]
                )), 3),
                "p90_residual_flow_pixels": round(float(numpy_module.percentile(
                    [item["p90_residual_flow_pixels"] for item in temporal_pairs], 90
                )), 3),
                "pairs": temporal_pairs,
                "interpretation": "Motion measurements are non-semantic diagnostics, not proof of natural action or stable identity.",
            },
            "signals": signals,
            "review_required": True,
            "note": "Metrics flag frames for inspection; they do not score aesthetics, identify subjects, verify identity consistency or approve publication.",
        }
    except Exception as exc:
        return _unavailable(f"analysis_failed:{type(exc).__name__}", source_name=path.name)
