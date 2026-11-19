"""Inspect an MP4's decode integrity, freeze events, and frame-to-frame motion signal.

This is a technical preflight, not a perceptual video-quality score.
Requires ffprobe and ffmpeg on PATH.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from statistics import mean
from typing import Any

YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([-+0-9.eE]+)")
FREEZE_RE = re.compile(r"lavfi\.freezedetect\.(freeze_start|freeze_duration|freeze_end)=([-+0-9.eE]+)")


def _run(command: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)


def _require_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe must both be available on PATH")
    return ffmpeg, ffprobe


def inspect_video(path: Path, *, motion_floor: float = 1.0, freeze_seconds: float = 0.25) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if not math.isfinite(motion_floor) or motion_floor < 0:
        raise ValueError("motion_floor must be finite and >= 0")
    if not math.isfinite(freeze_seconds) or freeze_seconds <= 0:
        raise ValueError("freeze_seconds must be finite and > 0")

    ffmpeg, ffprobe = _require_tools()
    probe = _run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,codec_type,width,height,r_frame_rate,nb_frames",
        "-of", "json", str(path),
    ])
    if probe.returncode:
        raise RuntimeError(f"ffprobe failed: {probe.stderr.strip()}")
    metadata = json.loads(probe.stdout)
    streams = [item for item in metadata.get("streams", []) if item.get("codec_type") == "video"]
    if not streams:
        raise ValueError("input contains no video stream")
    video = streams[0]
    duration = float(metadata.get("format", {}).get("duration", 0) or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("video duration is missing or invalid")

    decode = _run([ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"])
    decode_ok = decode.returncode == 0

    motion = _run([
        ffmpeg, "-hide_banner", "-i", str(path),
        "-vf", "tblend=all_mode=difference,signalstats,metadata=print",
        "-an", "-f", "null", "-",
    ])
    if motion.returncode:
        raise RuntimeError(f"motion analysis failed: {motion.stderr.strip()}")
    diffs = [float(value) for value in YAVG_RE.findall(motion.stderr)]
    if any(not math.isfinite(value) or value < 0 for value in diffs):
        raise ValueError("motion analysis returned invalid frame-difference values")

    freeze = _run([
        ffmpeg, "-hide_banner", "-i", str(path),
        "-vf", f"freezedetect=n=-60dB:d={freeze_seconds:g}",
        "-an", "-f", "null", "-",
    ])
    if freeze.returncode:
        raise RuntimeError(f"freeze analysis failed: {freeze.stderr.strip()}")
    freeze_events = [
        {"event": event, "seconds": float(value)}
        for event, value in FREEZE_RE.findall(freeze.stderr)
    ]

    frame_count_raw = video.get("nb_frames")
    frame_count = int(frame_count_raw) if str(frame_count_raw or "").isdigit() else None
    mean_diff = mean(diffs) if diffs else 0.0
    warnings: list[str] = []
    if not decode_ok:
        warnings.append("full_decode_failed")
    if not diffs:
        warnings.append("motion_signal_unavailable")
    elif mean_diff < motion_floor:
        warnings.append("low_frame_difference_signal")
    if freeze_events:
        warnings.append("freeze_events_detected")
    if duration < 2:
        warnings.append("very_short_clip_not_suitable_for_motion_quality_judgement")

    return {
        "status": "technical_pass" if decode_ok and not freeze_events and diffs else "technical_review",
        "file": str(path),
        "bytes": int(metadata.get("format", {}).get("size", path.stat().st_size)),
        "duration_seconds": round(duration, 6),
        "video": {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "frame_rate": video.get("r_frame_rate"),
            "frame_count": frame_count,
        },
        "decode": {"ok": decode_ok, "return_code": decode.returncode, "stderr": decode.stderr.strip()},
        "motion_signal": {
            "method": "FFmpeg tblend difference + signalstats YAVG",
            "difference_frame_count": len(diffs),
            "mean_yavg": round(mean_diff, 6),
            "min_yavg": round(min(diffs), 6) if diffs else None,
            "max_yavg": round(max(diffs), 6) if diffs else None,
            "motion_floor": motion_floor,
            "floor_met": bool(diffs) and mean_diff >= motion_floor,
            "note": "Frame difference is not a perceptual motion-quality or coherence score.",
        },
        "freeze": {"threshold_seconds": freeze_seconds, "events": freeze_events},
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--motion-floor", type=float, default=1.0)
    parser.add_argument("--freeze-seconds", type=float, default=0.25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect_video(args.video, motion_floor=args.motion_floor, freeze_seconds=args.freeze_seconds)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        if args.output.exists():
            raise FileExistsError(f"refusing to overwrite existing report: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "technical_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
