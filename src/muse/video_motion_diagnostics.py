"""Advisory optical-flow diagnostics for rendered video files.

This module reports measurable frame-to-frame movement. It deliberately does not
assign a universal pass/fail motion threshold: static shots and subtle movement
can be editorially intentional and thresholds require human-labelled calibration.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def inspect_video_motion(path: str | Path, *, max_frames: int = 180) -> dict[str, Any]:
    source = Path(path)
    if source.suffix.casefold() != ".mp4":
        raise ValueError("video motion diagnostics currently accepts .mp4 files only")
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError("video file is missing or unsafe")
    if isinstance(max_frames, bool) or not isinstance(max_frames, int) or max_frames < 2:
        raise ValueError("max_frames must be an integer >= 2")

    try:
        import cv2
        import numpy as np
    except Exception as exc:
        return {"status": "unavailable", "reason": f"opencv_dependency_unavailable:{type(exc).__name__}"}

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        return {"status": "failed", "reason": "video_open_failed", "sampled_frames": 0}

    frames = []
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        stride = max(1, math.ceil(total / max_frames)) if total else 1
        index = 0
        while len(frames) < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if index % stride == 0:
                frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            index += 1
    finally:
        capture.release()

    if not frames:
        return {"status": "failed", "reason": "no_decodable_frames", "sampled_frames": 0}

    pairs = []
    for pair_index, (first, second) in enumerate(zip(frames, frames[1:])):
        if first.shape != second.shape:
            second = cv2.resize(second, (first.shape[1], first.shape[0]), interpolation=cv2.INTER_AREA)
        flow = cv2.calcOpticalFlowFarneback(first, second, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        magnitude = cv2.magnitude(flow[:, :, 0], flow[:, :, 1])
        pairs.append({
            "pair_index": pair_index,
            "mean_flow_px": round(float(np.mean(magnitude)), 6),
            "p95_flow_px": round(float(np.percentile(magnitude, 95)), 6),
            "pixels_over_0_5px_pct": round(float(np.mean(magnitude > 0.5) * 100), 4),
            "mean_abs_luma_delta": round(float(cv2.absdiff(first, second).mean()), 6),
        })

    means = [item["mean_flow_px"] for item in pairs]
    return {
        "status": "observed" if len(frames) >= 2 else "insufficient_frames",
        "method": "opencv_farneback_dense_optical_flow",
        "path": str(source),
        "sampled_frames": len(frames),
        "sampled_pairs": len(pairs),
        "source_frame_count": total or None,
        "frame_stride": stride,
        "summary": {
            "median_mean_flow_px": round(float(np.median(means)), 6) if means else None,
            "max_mean_flow_px": round(float(max(means)), 6) if means else None,
            "note": "Advisory only; no universal motion pass threshold is applied.",
        },
        "pairs": pairs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report advisory optical-flow metrics for an MP4")
    parser.add_argument("video")
    parser.add_argument("--max-frames", type=int, default=180)
    args = parser.parse_args(argv)
    try:
        result = inspect_video_motion(args.video, max_frames=args.max_frames)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"observed", "unavailable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
