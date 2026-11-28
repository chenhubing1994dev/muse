"""Optional local Haar face framing for vertical highlight clips.

This is a conservative clip-level crop hint, not identity recognition or
continuous multi-person tracking. Missing dependencies or detections preserve
Muse's deterministic center crop.
"""
from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any
from muse.error_journal import record_suppressed_error


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def build_dynamic_crop_expression(
    keyframes: list[dict[str, object]], *, duration_seconds: float,
) -> str:
    """Build a bounded FFmpeg crop-x expression from clip-relative face keyframes."""
    duration = _finite(duration_seconds, "duration_seconds")
    if duration <= 0:
        raise ValueError("duration_seconds must be positive")
    if not isinstance(keyframes, list) or len(keyframes) < 2:
        raise ValueError("dynamic crop requires at least two keyframes")
    normalized: list[tuple[float, float]] = []
    previous_time = -1.0
    for item in keyframes:
        if not isinstance(item, dict):
            raise ValueError("keyframes must be objects")
        timestamp = _finite(item.get("time_seconds"), "keyframe time_seconds")
        center = _finite(item.get("center_x"), "keyframe center_x")
        if timestamp < 0 or timestamp > duration + 1e-6:
            raise ValueError("keyframe time must be within clip duration")
        if timestamp <= previous_time:
            raise ValueError("keyframe times must be strictly increasing")
        if not 0.0 <= center <= 1.0:
            raise ValueError("keyframe center_x must be between 0 and 1")
        normalized.append((min(duration, timestamp), center))
        previous_time = timestamp
    if normalized[-1][0] <= normalized[0][0]:
        raise ValueError("keyframe times must be increasing")

    expression = f"{normalized[-1][1]:.6f}"
    for index in range(len(normalized) - 2, -1, -1):
        t0, x0 = normalized[index]
        t1, x1 = normalized[index + 1]
        interpolation = f"{x0:.6f}+({x1 - x0:.6f})*(t-{t0:.6f})/{t1 - t0:.6f}"
        expression = f"if(lt(t,{t1:.6f}),{interpolation},{expression})"
        if index == 0 and t0 > 0:
            expression = f"if(lt(t,{t0:.6f}),{x0:.6f},{expression})"
    x_expression = f"iw*({expression})-540"
    return f"max(0,min(iw-1080,{x_expression}))"


def stabilize_face_keyframes(
    keyframes: list[dict[str, object]],
    *,
    max_speed_per_second: float = 0.9,
    smoothing_alpha: float = 0.75,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Smooth crop motion and cap horizontal center speed without changing timestamps."""
    max_speed = _finite(max_speed_per_second, "max_speed_per_second")
    alpha = _finite(smoothing_alpha, "smoothing_alpha")
    if max_speed <= 0:
        raise ValueError("max_speed_per_second must be positive")
    if not 0.0 < alpha <= 1.0:
        raise ValueError("smoothing_alpha must be greater than 0 and at most 1")
    if not isinstance(keyframes, list) or len(keyframes) < 2:
        raise ValueError("stabilization requires at least two keyframes")
    if any(not isinstance(item, dict) for item in keyframes):
        raise ValueError("keyframes must be objects")
    duration = _finite(keyframes[-1].get("time_seconds"), "last keyframe time_seconds")
    build_dynamic_crop_expression(keyframes, duration_seconds=duration)

    stabilized = [dict(keyframes[0])]
    previous_time = _finite(keyframes[0].get("time_seconds"), "keyframe time_seconds")
    previous_raw_center = _finite(keyframes[0].get("center_x"), "keyframe center_x")
    previous_center = previous_raw_center
    clamped_count = 0
    max_raw_speed = 0.0
    max_output_speed = 0.0
    for item in keyframes[1:]:
        timestamp = _finite(item.get("time_seconds"), "keyframe time_seconds")
        raw_center = _finite(item.get("center_x"), "keyframe center_x")
        elapsed = timestamp - previous_time
        max_raw_speed = max(max_raw_speed, abs(raw_center - previous_raw_center) / elapsed)
        delta = raw_center - previous_center
        max_step = max_speed * elapsed
        if abs(delta) > max_step:
            limited_target = previous_center + math.copysign(max_step, delta)
            clamped_count += 1
        else:
            limited_target = raw_center
        center = previous_center + alpha * (limited_target - previous_center)
        center = min(1.0, max(0.0, center))
        max_output_speed = max(max_output_speed, abs(center - previous_center) / elapsed)
        stabilized_item = dict(item)
        stabilized_item["center_x"] = round(center, 6)
        stabilized.append(stabilized_item)
        previous_time = timestamp
        previous_raw_center = raw_center
        previous_center = center

    return stabilized, {
        "applied": True,
        "input_keyframes": len(keyframes),
        "output_keyframes": len(stabilized),
        "clamped_keyframes": clamped_count,
        "max_speed_per_second": round(max_speed, 6),
        "smoothing_alpha": round(alpha, 6),
        "max_raw_speed_per_second": round(max_raw_speed, 6),
        "max_output_speed_per_second": round(max_output_speed, 6),
    }


def _track_face_center_with_lk(
    cv2: Any,
    previous_gray: Any,
    current_gray: Any,
    face_box: tuple[int, int, int, int],
) -> dict[str, float | int] | None:
    """Bridge one short Haar miss with bounded forward/backward LK tracking."""
    try:
        import numpy as np

        frame_height, frame_width = previous_gray.shape[:2]
        x, y, face_width, face_height = face_box
        if frame_width < 16 or frame_height < 16 or face_width < 8 or face_height < 8:
            return None
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(frame_width, x + face_width), min(frame_height, y + face_height)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        mask = np.zeros((frame_height, frame_width), dtype=np.uint8)
        mask[y0:y1, x0:x1] = 255
        points = cv2.goodFeaturesToTrack(
            previous_gray, maxCorners=80, qualityLevel=0.01, minDistance=4,
            mask=mask, blockSize=7,
        )
        if points is None or len(points) < 4:
            return None
        forward, forward_status, _ = cv2.calcOpticalFlowPyrLK(
            previous_gray, current_gray, points, None, winSize=(21, 21), maxLevel=3,
        )
        if forward is None or forward_status is None:
            return None
        backward, backward_status, _ = cv2.calcOpticalFlowPyrLK(
            current_gray, previous_gray, forward, None, winSize=(21, 21), maxLevel=3,
        )
        if backward is None or backward_status is None:
            return None
        original = np.asarray(points, dtype=np.float32).reshape(-1, 2)
        moved = np.asarray(forward, dtype=np.float32).reshape(-1, 2)
        returned = np.asarray(backward, dtype=np.float32).reshape(-1, 2)
        forward_ok = np.asarray(forward_status).reshape(-1) > 0
        backward_ok = np.asarray(backward_status).reshape(-1) > 0
        fb_error = np.linalg.norm(returned - original, axis=1)
        valid = forward_ok & backward_ok & (fb_error <= 1.5)
        valid &= np.isfinite(original).all(axis=1) & np.isfinite(moved).all(axis=1)
        required = max(4, int(math.ceil(len(original) * 0.35)))
        if int(valid.sum()) < required:
            return None
        displacement = np.median(moved[valid] - original[valid], axis=0)
        dx, dy = float(displacement[0]), float(displacement[1])
        # Reject implausible jumps rather than allowing a tracker to drag the crop
        # across the frame after a cut, occlusion, or feature mismatch.
        max_displacement = max(frame_width * 0.12, face_width * 2.0)
        if not math.isfinite(dx) or not math.isfinite(dy) or math.hypot(dx, dy) > max_displacement:
            return None
        center_x = (x + face_width / 2.0 + dx) / frame_width
        if not math.isfinite(center_x) or not 0.0 <= center_x <= 1.0:
            return None
        return {
            "center_x": round(center_x, 6),
            "tracked_points": int(valid.sum()),
            "inlier_ratio": round(float(valid.sum()) / len(original), 6),
            "forward_backward_error_px": round(float(np.median(fb_error[valid])), 6),
        }
    except Exception as _muse_suppressed:
        record_suppressed_error("video_face_framing.py:184", _muse_suppressed)
        # Optical flow is an optional bridge, never a reason to fail rendering.
        return None


def detect_clip_face_center(
    source: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    max_samples: int = 12,
) -> dict[str, Any]:
    """Estimate a stable horizontal face center from bounded samples in one clip."""
    path = Path(source).expanduser()
    start = _finite(start_seconds, "start_seconds")
    end = _finite(end_seconds, "end_seconds")
    if path.is_symlink() or not path.is_file():
        raise ValueError("source must be an existing regular local video file")
    if start < 0 or end <= start:
        raise ValueError("clip interval must be non-negative and increasing")
    if isinstance(max_samples, bool) or not isinstance(max_samples, int) or not 3 <= max_samples <= 30:
        raise ValueError("max_samples must be between 3 and 30")

    try:
        import cv2
    except Exception as exc:
        return {
            "status": "unavailable", "reason": f"opencv_dependency_unavailable:{type(exc).__name__}",
            "face_center_x": None, "detected_samples": 0, "sampled_frames": 0,
            "identity_recognition": False, "continuous_tracking": False,
        }

    try:
        cascade_root = Path(cv2.data.haarcascades)
        cascade_path = cascade_root / "haarcascade_frontalface_default.xml"
        if not cascade_path.is_file():
            return {
                "status": "unavailable", "reason": "opencv_frontal_face_cascade_missing",
                "face_center_x": None, "detected_samples": 0, "sampled_frames": 0,
                "identity_recognition": False, "continuous_tracking": False,
            }
        detector = cv2.CascadeClassifier(str(cascade_path))
        if detector.empty():
            return {
                "status": "unavailable", "reason": "opencv_frontal_face_cascade_invalid",
                "face_center_x": None, "detected_samples": 0, "sampled_frames": 0,
                "identity_recognition": False, "continuous_tracking": False,
            }
    except Exception as exc:
        return {
            "status": "unavailable", "reason": f"opencv_face_detector_unavailable:{type(exc).__name__}",
            "face_center_x": None, "detected_samples": 0, "sampled_frames": 0,
            "identity_recognition": False, "continuous_tracking": False,
        }

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        return {
            "status": "failed", "reason": "video_open_failed", "face_center_x": None,
            "detected_samples": 0, "sampled_frames": 0,
            "identity_recognition": False, "continuous_tracking": False,
        }

    centers: list[float] = []
    widths: list[float] = []
    keyframes: list[dict[str, object]] = []
    sampled = 0
    flow_tracked = 0
    previous_anchor: tuple[Any, tuple[int, int, int, int], float] | None = None
    try:
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if frame_count < 2 or not math.isfinite(fps) or fps <= 0:
            return {
                "status": "unavailable", "reason": "video_timing_unavailable", "face_center_x": None,
                "detected_samples": 0, "sampled_frames": 0,
                "identity_recognition": False, "continuous_tracking": False,
            }
        duration = frame_count / fps
        if end > duration + 0.05:
            return {
                "status": "failed", "reason": "clip_interval_exceeds_video_duration", "face_center_x": None,
                "detected_samples": 0, "sampled_frames": 0,
                "identity_recognition": False, "continuous_tracking": False,
            }
        sample_count = min(max_samples, max(3, int(math.ceil((end - start) * 2.0)) + 1))
        frame_indexes = sorted({
            min(frame_count - 1, max(0, int(round((start + (end - start) * index / (sample_count - 1)) * fps))))
            for index in range(sample_count)
        })
        for frame_index in frame_indexes:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            if width < 16 or height < 16:
                continue
            scale = min(1.0, 640.0 / max(width, height))
            if scale < 1.0:
                frame = cv2.resize(
                    frame, (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            _frame_height, frame_width = gray.shape[:2]
            faces = detector.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24),
            )
            sampled += 1
            frame_time = max(0.0, frame_index / fps - start)
            if len(faces):
                # The largest face is a weak primary-subject hint; it is not identity tracking.
                x, y, face_width, face_height = max(faces, key=lambda item: int(item[2]) * int(item[3]))
                center_x = min(1.0, max(0.0, (float(x) + float(face_width) / 2.0) / frame_width))
                centers.append(center_x)
                widths.append(min(1.0, max(0.0, float(face_width) / frame_width)))
                keyframes.append({
                    "time_seconds": round(frame_time, 6),
                    "center_x": round(center_x, 6),
                    "source": "haar",
                })
                previous_anchor = (
                    gray, (int(x), int(y), int(face_width), int(face_height)), frame_time,
                )
            elif previous_anchor is not None:
                previous_gray, previous_box, previous_time = previous_anchor
                gap = frame_time - previous_time
                if 0.0 < gap <= 1.5:
                    tracked = _track_face_center_with_lk(cv2, previous_gray, gray, previous_box)
                    if tracked is not None:
                        keyframes.append({
                            "time_seconds": round(frame_time, 6),
                            "center_x": tracked["center_x"],
                            "source": "lucas_kanade",
                            "tracked_points": tracked["tracked_points"],
                            "inlier_ratio": tracked["inlier_ratio"],
                        })
                        flow_tracked += 1
    except Exception as exc:
        return {
            "status": "failed", "reason": f"face_sampling_failed:{type(exc).__name__}",
            "face_center_x": None, "detected_samples": len(centers), "sampled_frames": sampled,
            "identity_recognition": False, "continuous_tracking": False,
        }
    finally:
        capture.release()

    base = {
        "detector": "opencv_haar_frontal_face", "sampled_frames": sampled,
        "detected_samples": len(centers), "flow_tracked_samples": flow_tracked,
        "identity_recognition": False, "continuous_tracking": False,
        "framing_mode": (
            "sampled_face_keyframes_with_sparse_lk_bridge" if flow_tracked
            else "sampled_face_keyframes"
        ),
    }
    if not centers:
        return {**base, "status": "no_face", "reason": "no_frontal_face_detected", "face_center_x": None}
    coverage = len(centers) / max(1, sampled)
    required_detections = max(2, int(math.ceil(sampled * 0.4)))
    if len(centers) < required_detections:
        return {
            **base, "status": "low_confidence", "reason": "insufficient_detection_coverage",
            "face_center_x": None, "detection_coverage": round(coverage, 6),
            "required_detection_samples": required_detections,
        }
    stabilized_keyframes, crop_stabilization = stabilize_face_keyframes(keyframes)
    return {
        **base, "status": "detected", "reason": None,
        "face_center_x": round(statistics.median(centers), 6),
        "face_keyframes": keyframes,
        "stabilized_face_keyframes": stabilized_keyframes,
        "crop_stabilization": crop_stabilization,
        "median_face_width_ratio": round(statistics.median(widths), 6),
        "center_spread": round(max(centers) - min(centers), 6),
        "detection_coverage": round(coverage, 6),
        "tracking_mode": "haar_plus_bounded_sparse_lucas_kanade" if flow_tracked else "haar_keyframes_only",
        "note": "Haar frontal-face heuristic with optional forward/backward Lucas-Kanade bridge and bounded crop smoothing; not identity or continuous tracking. Review manually, especially with multiple people, cuts, occlusion, or profile views.",
    }
