"""Fail-closed technical quality gate for locally rendered MP4 files.

The gate verifies container metadata and performs a full FFmpeg decode. It does
not claim editorial, factual, rights, or platform-policy approval.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Callable
import argparse

from muse.media import discover_media_tools


def _rate(value: object) -> float | None:
    try:
        result = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _check(passed: bool, detail: str) -> dict[str, object]:
    return {"passed": bool(passed), "detail": detail}


def _detected_intervals(stderr: str, detector: str) -> list[dict[str, float]]:
    """Parse bounded FFmpeg detector summaries without treating them as verdicts."""
    pattern = re.compile(
        rf"{re.escape(detector)}_start:\s*(-?\d+(?:\.\d+)?).*?"
        rf"{re.escape(detector)}_duration:\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE | re.DOTALL,
    )
    intervals: list[dict[str, float]] = []
    for match in pattern.finditer(stderr):
        try:
            start, duration = float(match.group(1)), float(match.group(2))
        except ValueError:
            continue
        if math.isfinite(start) and math.isfinite(duration) and duration >= 0:
            intervals.append({"start_seconds": start, "duration_seconds": duration})
    return intervals


def _quality_signal_report(stderr: str, *, has_audio: bool,
                           black_warning_seconds: float,
                           freeze_warning_seconds: float,
                           silence_warning_seconds: float) -> tuple[dict[str, object], list[dict[str, object]]]:
    black = _detected_intervals(stderr, "black")
    frozen = _detected_intervals(stderr, "freeze")
    silent = _detected_intervals(stderr, "silence") if has_audio else []
    warnings: list[dict[str, object]] = []
    for code, label, intervals, threshold in (
        ("sustained_black", "Sustained black frames detected", black, black_warning_seconds),
        ("frozen_frames", "Potential frozen frame interval detected", frozen, freeze_warning_seconds),
        ("sustained_silence", "Sustained audio silence detected", silent, silence_warning_seconds),
    ):
        for interval in intervals:
            if interval["duration_seconds"] >= threshold:
                warnings.append({"code": code, "message": label, **interval,
                                 "review_threshold_seconds": threshold})
    return ({"status": "observed", "method": "ffmpeg_blackdetect_freezedetect_silencedetect",
             "black_intervals": black, "frozen_intervals": frozen,
             "silent_intervals": silent,
             "note": "Heuristic signals only; intentional fades, pauses or static shots require editorial review."},
            warnings)


def _audio_quality_report(stderr: str, *, has_audio: bool) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Parse FFmpeg EBU R128/astats measurements; absent metrics never count as a pass."""
    if not has_audio:
        return ({"status": "not_applicable", "method": "ffmpeg_ebur128_astats",
                 "missing_metrics": []}, [])

    def number(pattern: str) -> float | None:
        match = re.search(pattern, stderr, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    # Anchor to the final EBU R128 summary, not the early-window rolling I: values.
    integrated = number(r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
    true_peak = number(r"True peak:\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")
    peak_level = number(r"Overall\s+.*?Peak level dB:\s*(-?\d+(?:\.\d+)?)")
    rms = number(r"Overall\s+.*?RMS level dB:\s*(-?\d+(?:\.\d+)?)")
    missing = [name for name, value in (
        ("integrated_lufs", integrated), ("true_peak_dbfs", true_peak),
        ("peak_level_dbfs", peak_level),
    ) if value is None]
    clipping_suspected = peak_level is not None and peak_level >= -0.1
    report: dict[str, object] = {
        "status": "observed" if not missing else "unavailable",
        "method": "ffmpeg_ebur128_astats",
        "integrated_lufs": integrated, "true_peak_dbfs": true_peak,
        "peak_level_dbfs": peak_level, "rms_dbfs": rms,
        "clipping_suspected": clipping_suspected,
        "clipping_detection": "peak_level_heuristic_not_exact_sample_count",
        "missing_metrics": missing,
        "thresholds": {"integrated_lufs_min": -24.0, "integrated_lufs_max": -14.0,
                       "true_peak_max_dbfs": -1.0, "peak_level_clipping_suspect_dbfs": -0.1},
    }
    warnings: list[dict[str, object]] = []
    if integrated is not None and integrated > -14.0:
        warnings.append({"code": "audio_loudness_high", "message": "Integrated loudness is above the review target", "value": integrated, "unit": "LUFS"})
    elif integrated is not None and integrated < -24.0:
        warnings.append({"code": "audio_loudness_low", "message": "Integrated loudness is below the review target", "value": integrated, "unit": "LUFS"})
    if true_peak is not None and true_peak > -1.0:
        warnings.append({"code": "audio_true_peak_high", "message": "True peak exceeds the -1 dBFS review ceiling", "value": true_peak, "unit": "dBFS"})
    if clipping_suspected:
        warnings.append({"code": "audio_clipping_suspected", "message": "Sample peak is at the digital ceiling; inspect for clipping (heuristic, not a clipped-sample count)", "value": peak_level, "unit": "dBFS"})
    if missing:
        warnings.append({"code": "audio_metrics_incomplete", "message": "Audio quality measurements are incomplete", "missing_metrics": missing})
    return report, warnings




def inspect_video_file(
    path: str | Path,
    *,
    ffprobe_path: str | None,
    ffmpeg_path: str | None,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    min_duration_seconds: float = 0.1,
    max_duration_seconds: float | None = None,
    require_audio: bool = False,
    black_warning_seconds: float = 0.5,
    freeze_warning_seconds: float = 1.0,
    silence_warning_seconds: float = 1.0,
    runner: Callable = subprocess.run,
    timeout_seconds: int = 900,
) -> dict[str, object]:
    """Probe and fully decode an MP4; missing tools are blocked, never passed."""
    source = Path(path)
    if source.suffix.casefold() != ".mp4":
        raise ValueError("video QA currently accepts .mp4 files only")
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError("video file is missing or unsafe")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    for name, value in (("expected_width", expected_width), ("expected_height", expected_height)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise ValueError(f"{name} must be a positive integer")
    if expected_fps is not None and (isinstance(expected_fps, bool) or not isinstance(expected_fps, (int, float)) or not math.isfinite(expected_fps) or expected_fps <= 0):
        raise ValueError("expected_fps must be positive and finite")
    if not math.isfinite(min_duration_seconds) or min_duration_seconds <= 0:
        raise ValueError("min_duration_seconds must be positive and finite")
    if max_duration_seconds is not None and (not math.isfinite(max_duration_seconds) or max_duration_seconds < min_duration_seconds):
        raise ValueError("max_duration_seconds must be finite and >= min_duration_seconds")
    for name, value in (("black_warning_seconds", black_warning_seconds),
                        ("freeze_warning_seconds", freeze_warning_seconds),
                        ("silence_warning_seconds", silence_warning_seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite")

    missing = [name for name, executable in (("ffprobe", ffprobe_path), ("ffmpeg", ffmpeg_path)) if not executable]
    if missing:
        return {"status": "blocked", "path": str(source), "missing_tools": missing,
                "checks": {}, "metadata": {},
                "quality_signals": {"status": "unavailable", "method": "ffmpeg_blackdetect_freezedetect_silencedetect"},
                "audio_quality": {"status": "unavailable", "method": "ffmpeg_ebur128_astats", "missing_metrics": []},
                "warnings": []}

    checks: dict[str, dict[str, object]] = {}
    metadata: dict[str, object] = {}
    try:
        probe = runner(
            [str(ffprobe_path), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        if probe.returncode != 0:
            detail = (probe.stderr or "ffprobe returned a non-zero exit code")[-1200:]
            return {"status": "failed", "path": str(source), "missing_tools": [],
                    "checks": {"probe": _check(False, detail)}, "metadata": {},
                    "quality_signals": {"status": "unavailable", "method": "ffmpeg_blackdetect_freezedetect_silencedetect"},
                    "audio_quality": {"status": "unavailable", "method": "ffmpeg_ebur128_astats", "missing_metrics": []},
                    "warnings": []}
        document = json.loads(probe.stdout)
        if not isinstance(document, dict) or not isinstance(document.get("streams"), list):
            raise ValueError("ffprobe returned an invalid document")
        format_info = document.get("format") if isinstance(document.get("format"), dict) else {}
        streams = document["streams"]
        video_streams = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"]
        audio_streams = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"]
        video = video_streams[0] if video_streams else {}
        duration_raw = format_info.get("duration") or video.get("duration")
        try:
            duration = float(duration_raw)
        except (TypeError, ValueError):
            duration = None
        fps = _rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        width, height = video.get("width"), video.get("height")
        metadata = {
            "format_name": format_info.get("format_name"), "duration_seconds": duration,
            "width": width, "height": height, "fps": fps,
            "video_codec": video.get("codec_name"),
            "audio_codecs": [item.get("codec_name") for item in audio_streams],
            "file_bytes": source.stat().st_size,
        }
        format_name = str(format_info.get("format_name") or "").lower().split(",")
        checks["container"] = _check(bool(format_name and any(name in {"mp4", "mov", "m4a", "3gp", "3g2", "mj2"} for name in format_name)), f"format_name={format_info.get('format_name')}")
        checks["video_stream"] = _check(bool(video_streams), f"video_stream_count={len(video_streams)}")
        checks["duration"] = _check(duration is not None and math.isfinite(duration) and min_duration_seconds <= duration and (max_duration_seconds is None or duration <= max_duration_seconds), f"duration_seconds={duration}")
        checks["dimensions"] = _check(bool(video_streams) and isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0 and (expected_width is None or width == expected_width) and (expected_height is None or height == expected_height), f"actual={width}x{height}; expected={expected_width}x{expected_height}")
        checks["frame_rate"] = _check(fps is not None and (expected_fps is None or math.isclose(fps, float(expected_fps), rel_tol=0.01, abs_tol=0.05)), f"actual_fps={fps}; expected_fps={expected_fps}")
        checks["audio_stream"] = _check(bool(audio_streams) or not require_audio, f"audio_stream_count={len(audio_streams)}; required={require_audio}")
        if source.stat().st_size <= 0:
            checks["file_size"] = _check(False, "file is empty")
        else:
            checks["file_size"] = _check(True, f"bytes={source.stat().st_size}")
        black_detect_seconds = min(0.5, float(black_warning_seconds))
        freeze_detect_seconds = min(1.0, float(freeze_warning_seconds))
        silence_detect_seconds = min(1.0, float(silence_warning_seconds))
        filters = (f"blackdetect=d={black_detect_seconds:g}:pix_th=0.10,"
                   f"freezedetect=n=-50dB:d={freeze_detect_seconds:g}")
        decode_command = [str(ffmpeg_path), "-hide_banner", "-v", "info", "-nostdin",
                          "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
                          "-vf", filters]
        if audio_streams:
            decode_command.extend(["-af", f"silencedetect=n=-50dB:d={silence_detect_seconds:g},ebur128=peak=true,astats=metadata=0:reset=0"])
        decode_command.extend(["-f", "null", "-"])
        decoded = runner(
            decode_command, capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        checks["full_decode"] = _check(decoded.returncode == 0, "full decode completed" if decoded.returncode == 0 else (decoded.stderr or "ffmpeg decode failed")[-1200:])
        quality_signals, warnings = _quality_signal_report(
            decoded.stderr or "", has_audio=bool(audio_streams),
            black_warning_seconds=float(black_warning_seconds),
            freeze_warning_seconds=float(freeze_warning_seconds),
            silence_warning_seconds=float(silence_warning_seconds),
        )
        quality_signals["status"] = "observed" if decoded.returncode == 0 else "partial"
        audio_quality, audio_warnings = _audio_quality_report(decoded.stderr or "", has_audio=bool(audio_streams))
        warnings.extend(audio_warnings)
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        passed = all(item["passed"] for item in checks.values())
        return {"status": "passed" if passed else "failed", "path": str(source), "missing_tools": [],
                "checks": checks, "metadata": metadata, "quality_signals": quality_signals,
                "audio_quality": audio_quality, "warnings": warnings, "sha256": digest.hexdigest()}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"status": "failed", "path": str(source), "missing_tools": [],
                "checks": {**checks, "inspection": _check(False, f"{type(exc).__name__}: {exc}")},
                "metadata": metadata,
                "quality_signals": {"status": "unavailable", "method": "ffmpeg_blackdetect_freezedetect_silencedetect"},
                "audio_quality": {"status": "unavailable", "method": "ffmpeg_ebur128_astats", "missing_metrics": []},
                "warnings": []}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Muse's fail-closed MP4 technical QA gate")
    parser.add_argument("video", help="MP4 file to inspect")
    parser.add_argument("--expect-width", type=int)
    parser.add_argument("--expect-height", type=int)
    parser.add_argument("--expect-fps", type=float)
    parser.add_argument("--min-duration", type=float, default=0.1)
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--require-audio", action="store_true")
    parser.add_argument("--black-warning-seconds", type=float, default=0.5)
    parser.add_argument("--freeze-warning-seconds", type=float, default=1.0)
    parser.add_argument("--silence-warning-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    result = inspect_video_file(
        args.video, ffprobe_path=tools.get("ffprobe"), ffmpeg_path=tools.get("ffmpeg"),
        expected_width=args.expect_width, expected_height=args.expect_height,
        expected_fps=args.expect_fps, min_duration_seconds=args.min_duration,
        max_duration_seconds=args.max_duration, require_audio=args.require_audio,
        black_warning_seconds=args.black_warning_seconds,
        freeze_warning_seconds=args.freeze_warning_seconds,
        silence_warning_seconds=args.silence_warning_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else (2 if result["status"] == "blocked" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
