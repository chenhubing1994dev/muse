"""Local audio-first highlight selection and vertical clip rendering.

Inspired by NeonClipping's published workflow, independently implemented for Muse.
No network access, model download, or LLM is required by this module.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Sequence
from muse.ffmpeg_skill import probe_media



def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def score_audio_windows(
    rms_db: Sequence[float],
    *,
    shout_scores: Sequence[float] | None = None,
    band_energy_db: Sequence[float] | None = None,
    baseline_seconds: int = 120,
) -> list[dict[str, float]]:
    """Score RMS windows; optional shout and 2–4 kHz prominence are auxiliary signals."""
    if not isinstance(baseline_seconds, int) or isinstance(baseline_seconds, bool) or not 10 <= baseline_seconds <= 600:
        raise ValueError("baseline_seconds must be between 10 and 600")
    levels = [_finite_number(value, "RMS level") for value in rms_db]
    if not levels:
        raise ValueError("at least one RMS window is required")
    if shout_scores is not None:
        if len(shout_scores) != len(levels):
            raise ValueError("shout_scores must match the RMS window count")
        shouts = [_finite_number(value, "shout score") for value in shout_scores]
        if any(value < 0 or value > 1 for value in shouts):
            raise ValueError("shout_scores must be between 0 and 1")
    else:
        shouts = [0.0] * len(levels)
    if band_energy_db is not None:
        if len(band_energy_db) != len(levels):
            raise ValueError("band_energy_db must match the RMS window count")
        band_levels = [_finite_number(value, "2-4 kHz band energy") for value in band_energy_db]
    else:
        band_levels = [0.0] * len(levels)

    results: list[dict[str, float]] = []
    for index, level in enumerate(levels):
        baseline = levels[max(0, index - baseline_seconds):index]
        if len(baseline) < 3:
            loudness, transition = 0.0, 0.0
        else:
            median = statistics.median(baseline)
            deviations = [abs(item - median) for item in baseline]
            mad = statistics.median(deviations)
            scale = max(1.4826 * mad, 1.0)
            loudness = max(0.0, min(1.0, (level - median) / (4.0 * scale)))
            previous = levels[index - 1]
            transition = max(0.0, min(1.0, (level - previous - 2.0) / 12.0))
        if band_energy_db is None:
            band_prominence = 0.0
        else:
            band_baseline = band_levels[max(0, index - baseline_seconds):index]
            if len(band_baseline) < 3:
                band_prominence = 0.0
            else:
                band_median = statistics.median(band_baseline)
                band_mad = statistics.median(abs(item - band_median) for item in band_baseline)
                band_scale = max(1.4826 * band_mad, 1.0)
                band_prominence = max(0.0, min(1.0, (band_levels[index] - band_median) / (4.0 * band_scale)))
        if shout_scores is None:
            base_score = 0.75 * loudness + 0.25 * transition
        else:
            base_score = 0.60 * loudness + 0.20 * transition + 0.20 * shouts[index]
        score = base_score if band_energy_db is None else 0.85 * base_score + 0.15 * band_prominence
        item = {
            "second": float(index), "rms_db": level,
            "loudness_jump": loudness, "quiet_to_loud": transition,
            "shout": shouts[index], "score": score,
        }
        if band_energy_db is not None:
            item["band_energy_2_4khz_db"] = band_levels[index]
            item["band_prominence_2_4khz"] = band_prominence
        results.append(item)
    return results

def _word_boundaries(words: Sequence[dict[str, object]]) -> tuple[list[float], list[float]]:
    starts: list[float] = []
    sentence_ends: list[float] = []
    for item in words:
        if not isinstance(item, dict):
            raise ValueError("transcript words must be objects")
        start = _finite_number(item.get("start"), "word start")
        end = _finite_number(item.get("end"), "word end")
        text = item.get("text")
        if start < 0 or end <= start or not isinstance(text, str) or not text.strip():
            raise ValueError("transcript words need valid timestamps and text")
        starts.append(start)
        if re.search(r"[.!?。！？；;]$", text.strip()):
            sentence_ends.append(end)
    return starts, sentence_ends


def select_highlight_candidates(
    windows: Sequence[dict[str, float]],
    duration_seconds: float,
    *,
    count: int = 3,
    min_length_seconds: float = 20.0,
    max_length_seconds: float = 60.0,
    words: Sequence[dict[str, object]] = (),
    snap_tolerance_seconds: float = 6.0,
) -> list[dict[str, object]]:
    """Select high-scoring, non-overlapping ranges and optionally snap to words."""
    duration = _finite_number(duration_seconds, "duration_seconds")
    minimum = _finite_number(min_length_seconds, "min_length_seconds")
    maximum = _finite_number(max_length_seconds, "max_length_seconds")
    tolerance = _finite_number(snap_tolerance_seconds, "snap_tolerance_seconds")
    if duration <= 0 or not 1 <= count <= 20 or minimum < 3 or maximum < minimum or maximum > 600:
        raise ValueError("invalid duration, count or clip length bounds")
    if tolerance < 0 or tolerance > 30:
        raise ValueError("snap_tolerance_seconds must be between 0 and 30")
    if not windows:
        raise ValueError("audio windows are required")
    starts, sentence_ends = _word_boundaries(words) if words else ([], [])
    length = min(maximum, max(minimum, min(30.0, duration)))
    length = min(length, duration)
    ordered = sorted(windows, key=lambda item: _finite_number(item.get("score"), "window score"), reverse=True)
    selected: list[dict[str, object]] = []
    for window in ordered:
        center = _finite_number(window.get("second"), "window second")
        if center < 0 or center > duration:
            continue
        start = max(0.0, min(center - length * 0.4, duration - length))
        end = min(duration, start + length)
        original = (start, end)
        if starts:
            nearest_start = min(starts, key=lambda point: abs(point - start))
            if abs(nearest_start - start) <= tolerance:
                start = nearest_start
        if sentence_ends:
            eligible = [point for point in sentence_ends if abs(point - end) <= tolerance]
            if eligible:
                end = min(eligible, key=lambda point: abs(point - end))
        if end - start < minimum or end - start > maximum or end <= start:
            start, end = original
        if any(start < float(item["end"]) and end > float(item["start"]) for item in selected):
            continue
        selected.append({
            "start": round(start, 3), "end": round(end, 3),
            "score": round(_finite_number(window.get("score"), "window score"), 6),
            "reason": "audio activity peak; heuristic candidate, editorial review required",
            "signals": {
                key: round(float(window[key]), 6)
                for key in ("loudness_jump", "quiet_to_loud", "shout", "band_energy_2_4khz_db", "band_prominence_2_4khz")
                if key in window
            },
        })
        if len(selected) >= count:
            break
    return sorted(selected, key=lambda item: float(item["start"]))


def analyze_video_audio(
    source: str | Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    baseline_seconds: int = 120,
    spectral_2_4khz: bool = False,
    runner: Callable = subprocess.run,
) -> dict[str, object]:
    """Decode audio-only RMS windows and optionally a 2–4 kHz band-energy pass."""
    path = Path(source).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError("source must be an existing regular local file")
    probe = runner([
        ffprobe_path, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(path),
    ], capture_output=True, text=True, timeout=30, check=False)
    if probe.returncode != 0:
        raise RuntimeError("ffprobe could not inspect the source")
    metadata = json.loads(probe.stdout or "{}")
    duration = _finite_number(float(metadata.get("format", {}).get("duration", 0)), "source duration")
    if duration <= 0 or not any(item.get("codec_type") == "audio" for item in metadata.get("streams", [])):
        raise ValueError("source must contain a valid audio stream and duration")

    def read_rms(filter_chain: str, feature_name: str) -> list[float]:
        result = runner([
            ffmpeg_path, "-hide_banner", "-nostdin", "-i", str(path), "-vn",
            "-af", filter_chain, "-f", "null", "-",
        ], capture_output=True, text=True, timeout=3600, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg {feature_name} analysis failed: " + (result.stderr or "")[-1200:])
        metadata_text = (result.stdout or "") + "\n" + (result.stderr or "")
        raw = re.findall(r"lavfi\.astats\.Overall\.RMS_level=(-?(?:\d+(?:\.\d+)?|inf))", metadata_text)
        values = [-120.0 if value.casefold() in {"inf", "-inf"} else float(value) for value in raw]
        if not values:
            raise RuntimeError(f"FFmpeg returned no per-window {feature_name} measurements")
        return values
    window_filter = "aresample=8000,asetnsamples=n=8000:p=1,astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    levels = read_rms(window_filter, "audio RMS")
    band_levels = None
    if spectral_2_4khz:
        band_filter = "highpass=f=2000,lowpass=f=4000,aresample=8000,asetnsamples=n=8000:p=1,astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
        band_levels = read_rms(band_filter, "2-4 kHz band")
        if len(band_levels) != len(levels):
            raise RuntimeError("2-4 kHz band analysis window count does not match RMS analysis")
    windows = score_audio_windows(levels, baseline_seconds=baseline_seconds, band_energy_db=band_levels)
    candidates = select_highlight_candidates(windows, duration)
    return {
        "status": "candidates_ready", "source_name": path.name,
        "duration_seconds": round(duration, 3), "analysis_mode": "audio_only",
        "spectral_feature_2_4khz": bool(spectral_2_4khz),
        "window_count": len(windows), "candidate_count": len(candidates),
        "windows": windows, "candidates": candidates,
        "limitations": [
            "Audio heuristics identify review candidates, not guaranteed viral moments.",
            "2-4 kHz energy is a weak speech-presence cue, not emotion, speaker identity, or semantic evidence.",
            "Transcript semantics, visual motion, and face tracking are not included in this audio-first analysis.",
        ],
    }

def _srt_timestamp(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def write_candidate_srt(
    words: Sequence[dict[str, object]], start_seconds: float, end_seconds: float,
    output: str | Path,
) -> dict[str, object]:
    """Write clip-relative SRT cues from source word timestamps without inventing timing."""
    start = _finite_number(start_seconds, "start_seconds")
    end = _finite_number(end_seconds, "end_seconds")
    target = Path(output).expanduser()
    if start < 0 or end <= start or target.suffix.casefold() != ".srt":
        raise ValueError("invalid clip interval or SRT output path")
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise FileExistsError(target)
    selected: list[tuple[float, float, str]] = []
    for item in words:
        if not isinstance(item, dict):
            raise ValueError("transcript words must be objects")
        word_start = _finite_number(item.get("start"), "word start")
        word_end = _finite_number(item.get("end"), "word end")
        value = item.get("text")
        if word_end <= word_start or not isinstance(value, str) or not value.strip():
            raise ValueError("transcript words need valid timestamps and text")
        clipped_start, clipped_end = max(start, word_start), min(end, word_end)
        if clipped_end > clipped_start:
            selected.append((clipped_start - start, clipped_end - start, value.strip()))
    if not selected:
        raise ValueError("no timed transcript words overlap this candidate")
    cues: list[str] = []
    chunk: list[tuple[float, float, str]] = []
    for word in selected:
        prospective = "".join(part[2] for part in chunk) + word[2]
        if chunk and (
            len(prospective) > 16 or word[1] - chunk[0][0] > 4.5
            or chunk[-1][2].rstrip().endswith(("。", "！", "？", "；", ".", "!", "?"))
        ):
            cues.append((chunk[0][0], chunk[-1][1], "".join(part[2] for part in chunk)))
            chunk = []
        chunk.append(word)
    if chunk:
        cues.append((chunk[0][0], chunk[-1][1], "".join(part[2] for part in chunk)))
    payload = "\ufeff" + "\n\n".join(
        f"{index}\n{_srt_timestamp(cue_start)} --> {_srt_timestamp(cue_end)}\n{text}"
        for index, (cue_start, cue_end, text) in enumerate(cues, start=1)
    ) + "\n"
    target.write_text(payload, encoding="utf-8", newline="\n")
    return {"path": str(target.resolve()), "cue_count": len(cues), "word_count": len(selected)}


def analyze_video_with_cantonese_asr(
    source: str | Path, *, model_dir: str | Path,
    ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe",
    device: str = "cpu", compute_type: str = "int8",
    spectral_2_4khz: bool = False,
    transcriber: Callable | None = None, runner: Callable = subprocess.run,
) -> dict[str, object]:
    """Run audio-first ranking, then reuse Muse's pinned local Cantonese ASR word timings."""
    source_path = Path(source).expanduser()
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError("source must be an existing regular local file")
    analysis = analyze_video_audio(
        source_path, ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path,
        spectral_2_4khz=spectral_2_4khz, runner=runner,
    )
    with tempfile.TemporaryDirectory(prefix="muse-highlight-asr-") as temporary:
        audio_path = Path(temporary) / "source-audio.wav"
        extracted = runner([
            ffmpeg_path, "-hide_banner", "-nostdin", "-n", "-i", str(source_path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio_path),
        ], capture_output=True, text=True, timeout=1800, check=False)
        if extracted.returncode != 0 or not audio_path.is_file():
            raise RuntimeError("FFmpeg could not extract source audio for local Cantonese ASR")
        if transcriber is None:
            from muse.cantonese_asr import transcribe_cantonese
            transcriber = transcribe_cantonese
        transcript = transcriber(
            audio_path, model_dir=model_dir, device=device, compute_type=compute_type,
        )
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        raise ValueError("Cantonese ASR returned an invalid transcript document")
    words: list[dict[str, object]] = []
    for segment in transcript["segments"]:
        if not isinstance(segment, dict):
            raise ValueError("Cantonese ASR segment must be an object")
        segment_words = segment.get("words")
        if isinstance(segment_words, list):
            words.extend(segment_words)
    if not words:
        raise ValueError("Cantonese ASR returned no word-level timestamps")
    candidates = select_highlight_candidates(
        analysis["windows"], float(analysis["duration_seconds"]), words=words,
    )
    for candidate in candidates:
        candidate_words = [
            word for word in words
            if isinstance(word, dict)
            and float(word.get("end", 0)) > float(candidate["start"])
            and float(word.get("start", 0)) < float(candidate["end"])
        ]
        candidate["transcript"] = "".join(str(word.get("text", "")) for word in candidate_words).strip()
        candidate["subtitle_timing"] = "word"
    return {
        **{key: value for key, value in analysis.items() if key not in {"windows", "candidates"}},
        "analysis_mode": "audio_plus_local_cantonese_asr",
        "transcript_engine": transcript.get("engine", "local_cantonese_asr"),
        "transcript": transcript.get("transcript", ""),
        "words": words, "candidates": candidates, "candidate_count": len(candidates),
        "human_listening_review_required": True,
    }

def _escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def build_vertical_clip_command(
    source: str | Path,
    output: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    ffmpeg_path: str = "ffmpeg",
    subtitles: str | Path | None = None,
    available_encoders: Sequence[str] = (),
    prefer_hardware: bool = True,
    crop_center_x: float | None = None,
    crop_center_keyframes: list[dict[str, object]] | None = None,
) -> list[str]:
    """Build a no-overwrite 1080x1920 clip command; source files stay untouched."""
    source_path, output_path = Path(source).expanduser(), Path(output).expanduser()
    start = _finite_number(start_seconds, "start_seconds")
    end = _finite_number(end_seconds, "end_seconds")
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError("source must be an existing regular file")
    if output_path.suffix.casefold() != ".mp4" or not output_path.parent.is_dir():
        raise ValueError("output must be an MP4 inside an existing directory")
    if output_path.exists():
        raise FileExistsError(output_path)
    if start < 0 or end <= start or end - start < 3 or end - start > 600:
        raise ValueError("clip range must be 3..600 seconds and increasing")
    if crop_center_x is not None and crop_center_keyframes is not None:
        raise ValueError("crop_center_x and crop_center_keyframes are mutually exclusive")
    if crop_center_keyframes is not None:
        from muse.video_face_framing import build_dynamic_crop_expression
        crop_expression = build_dynamic_crop_expression(
            crop_center_keyframes, duration_seconds=end - start,
        )
        crop_filter = f"crop=1080:1920:'{crop_expression}':0"
    elif crop_center_x is not None:
        crop_center = _finite_number(crop_center_x, "crop_center_x")
        if not 0.0 <= crop_center <= 1.0:
            raise ValueError("crop_center_x must be between 0 and 1")
        crop_filter = f"crop=1080:1920:max(0\\,min(iw-1080\\,iw*{crop_center:.6f}-540)):0"
    else:
        crop_filter = "crop=1080:1920"
    filters = [
        "scale=1080:1920:force_original_aspect_ratio=increase",
        crop_filter, "setsar=1", "format=yuv420p",
    ]
    if subtitles is not None:
        subtitle_path = Path(subtitles).expanduser()
        if subtitle_path.is_symlink() or not subtitle_path.is_file() or subtitle_path.suffix.casefold() != ".srt":
            raise ValueError("subtitles must be an existing regular SRT file")
        filters.append("subtitles=filename='" + _escape_filter_path(subtitle_path) + "'")
    encoder = "h264_amf" if prefer_hardware and "h264_amf" in set(available_encoders) else "libx264"
    command = [
        ffmpeg_path, "-hide_banner", "-nostdin", "-n",
        "-ss", f"{start:.3f}", "-i", str(source_path), "-t", f"{end - start:.3f}",
        "-map", "0:v:0", "-map", "0:a?", "-vf", ",".join(filters),
        "-c:v", encoder,
    ]
    if encoder == "libx264":
        command.extend(["-preset", "veryfast", "-crf", "20"])
    command.extend([
        "-c:a", "aac", "-b:a", "160k", "-r", "30", "-movflags", "+faststart",
        "-pix_fmt", "yuv420p", str(output_path),
    ])
    return command


def render_vertical_clip(
    source: str | Path,
    output: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    subtitles: str | Path | None = None,
    available_encoders: Sequence[str] = (),
    crop_center_x: float | None = None,
    crop_center_keyframes: list[dict[str, object]] | None = None,
    runner: Callable = subprocess.run,
    gpu_resource_db: str | Path | None = None,
) -> dict[str, object]:
    target = Path(output).expanduser()
    created = False
    output_preexisting = target.exists()
    try:
        source_probe = probe_media(source, ffprobe_path=ffprobe_path, runner=runner)

        if source_probe.get("status") != "passed":
            raise RuntimeError("source probe failed: " + str(source_probe.get("reason", source_probe.get("status"))))
        source_duration = source_probe.get("duration_seconds")
        if (not source_probe.get("video") or not isinstance(source_duration, (int, float)) or _finite_number(start_seconds, "start_seconds") < 0 or _finite_number(end_seconds, "end_seconds") <= _finite_number(start_seconds, "start_seconds") or _finite_number(end_seconds, "end_seconds") > source_duration + 0.05): raise ValueError("clip range exceeds probed source duration or source has no video stream")
        command = build_vertical_clip_command(
            source, output, start_seconds, end_seconds, ffmpeg_path=ffmpeg_path,
            subtitles=subtitles, available_encoders=available_encoders,
            crop_center_x=crop_center_x,
            crop_center_keyframes=crop_center_keyframes,
        )
        encoder_capability = None
        if "h264_amf" in command:
            from muse.video_capabilities import probe_ffmpeg_encoder
            encoder_capability = probe_ffmpeg_encoder(ffmpeg_path, ffprobe_path, "h264_amf")
            if encoder_capability.get("capability", {}).get("status") != "available":
                raise RuntimeError("AMF was selected but failed real MP4/FFprobe/full-decode validation")
            from muse.gpu_resources import gpu_video_encoding_session
            workspace = Path(__file__).resolve().parents[2]
            with gpu_video_encoding_session(
                workspace, "video-highlight-" + uuid.uuid4().hex[:16], database=gpu_resource_db,
            ):
                result = runner(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=900, check=False)
        else:
            result = runner(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=900, check=False)
        created = target.is_file()
        if result.returncode != 0 or not created or target.stat().st_size == 0:
            raise RuntimeError("FFmpeg clip render failed: " + (result.stderr or "")[-1200:])

        output_probe = probe_media(target, ffprobe_path=ffprobe_path, runner=runner)
        if output_probe.get("status") != "passed":
            raise RuntimeError("ffprobe could not verify the rendered clip")
        video = output_probe.get("video")
        duration = output_probe.get("duration_seconds")
        expected_duration = _finite_number(end_seconds, "end_seconds") - _finite_number(start_seconds, "start_seconds")
        tolerance = max(0.5, expected_duration * 0.03)
        if (not isinstance(video, dict) or video.get("width") != 1080 or video.get("height") != 1920
                or not isinstance(duration, (int, float)) or not math.isfinite(duration)
                or duration <= 0 or abs(duration - expected_duration) > tolerance):
            raise RuntimeError("rendered clip failed duration or 1080x1920 MP4 verification")
        return {
            "status": "completed", "output": str(target.resolve()),
            "duration_seconds": round(duration, 3), "width": 1080, "height": 1920,
            "encoder_capability": encoder_capability,
            "encoder": "h264_amf" if "h264_amf" in command else "libx264",
            "bytes": target.stat().st_size,
            "framing": {
                "mode": (
                    "sampled_face_keyframes" if crop_center_keyframes is not None
                    else "face_aware_clip_center" if crop_center_x is not None else "center_crop"
                ),
                "face_center_x": crop_center_x,
                "crop_keyframes": crop_center_keyframes,
                "continuous_tracking": False,
            },
            "source_probe": {
                "duration_seconds": source_duration,
                "width": source_probe["video"].get("width"),
                "height": source_probe["video"].get("height"),
                "variable_frame_rate_suspected": source_probe["video"].get("variable_frame_rate_suspected"),
            },
            "output_probe": {
                "duration_seconds": duration,
                "video_codec": video.get("codec"),
                "audio_stream_count": output_probe.get("audio_stream_count"),
            },
        }
    except Exception as exc:
        if not output_preexisting and target.exists():
            target.unlink()
        from muse.gpu_resources import GpuResourceBusy
        if isinstance(exc, GpuResourceBusy):
            return {"status": "blocked", "stage": "gpu_resource_lock", "error": str(exc), "output": str(target)}
        if isinstance(exc, (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError, json.JSONDecodeError)):
            return {"status": "failed", "error": str(exc), "output": str(target)}
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local audio-first video highlight cutter")
    sub = parser.add_subparsers(dest="command", required=True)
    rank = sub.add_parser("rank", help="analyze only the audio stream and save highlight candidates")
    rank.add_argument("source")
    rank.add_argument("output_json")
    rank.add_argument("--ffmpeg", default="ffmpeg")
    rank.add_argument("--ffprobe", default="ffprobe")
    rank.add_argument("--spectral-2-4khz", action="store_true", help="add optional 2-4 kHz speech-band prominence (extra audio pass)")
    rank_cantonese = sub.add_parser("rank-cantonese", help="rank candidates using Muse local Cantonese word timings")
    rank_cantonese.add_argument("source")
    rank_cantonese.add_argument("output_json")
    rank_cantonese.add_argument("--model-dir", required=True)
    rank_cantonese.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    rank_cantonese.add_argument("--compute-type", choices=("int8", "int8_float16", "float16", "float32"), default="int8")
    rank_cantonese.add_argument("--ffmpeg", default="ffmpeg")
    rank_cantonese.add_argument("--ffprobe", default="ffprobe")
    rank_cantonese.add_argument("--spectral-2-4khz", action="store_true", help="add optional 2-4 kHz speech-band prominence (extra audio pass)")
    render = sub.add_parser("render", help="render one candidate from a saved analysis JSON")
    render.add_argument("source")
    render.add_argument("analysis_json")
    render.add_argument("output_mp4")
    render.add_argument("--index", type=int, default=0)
    render.add_argument("--subtitles")
    render.add_argument("--captions-from-analysis", action="store_true", help="write clip-relative SRT from saved Cantonese word timestamps")
    framing_group = render.add_mutually_exclusive_group()
    framing_group.add_argument("--face-aware-crop", action="store_true", help="use one stable local OpenCV frontal-face crop center")
    framing_group.add_argument("--dynamic-face-crop", action="store_true", help="interpolate sampled local face positions into a time-varying crop")
    render.add_argument("--ffmpeg", default="ffmpeg")
    render.add_argument("--ffprobe", default="ffprobe")
    register = sub.add_parser("register", help="package a rendered clip in Muse's canonical video product and attempt ledgers")
    register.add_argument("source")
    register.add_argument("clip_mp4")
    register.add_argument("analysis_json")
    register.add_argument("title")
    register.add_argument("--workspace", default=".")
    register.add_argument("--index", type=int, default=0)
    register.add_argument("--captions")
    register.add_argument("--initiative-id")
    register.add_argument("--evolution-cycle-id")
    register.add_argument("--ffmpeg")
    register.add_argument("--ffprobe")
    args = parser.parse_args(argv)
    try:
        if args.command in {"rank", "rank-cantonese"}:
            if args.command == "rank":
                result = analyze_video_audio(
                    args.source, ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
                    spectral_2_4khz=args.spectral_2_4khz,
                )
            else:
                result = analyze_video_with_cantonese_asr(
                    args.source, model_dir=args.model_dir, ffmpeg_path=args.ffmpeg,
                    ffprobe_path=args.ffprobe, device=args.device, compute_type=args.compute_type,
                    spectral_2_4khz=args.spectral_2_4khz,
                )
            output = Path(args.output_json)
            if output.exists():
                raise FileExistsError(output)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        elif args.command == "render":
            payload = json.loads(Path(args.analysis_json).read_text(encoding="utf-8"))
            candidates = payload.get("candidates", [])
            if not isinstance(args.index, int) or not 0 <= args.index < len(candidates):
                raise ValueError("candidate index is out of range")
            candidate = candidates[args.index]
            subtitle_path = args.subtitles
            generated_srt = None
            if args.captions_from_analysis:
                if not isinstance(payload.get("words"), list):
                    raise ValueError("analysis JSON has no Cantonese word timestamps")
                generated_srt = Path(args.output_mp4).with_suffix(".srt")
                write_candidate_srt(payload["words"], candidate["start"], candidate["end"], generated_srt)
                subtitle_path = str(generated_srt)
            face_framing = None
            crop_center_x = None
            crop_center_keyframes = None
            if args.face_aware_crop or args.dynamic_face_crop:
                from muse.video_face_framing import detect_clip_face_center
                face_framing = detect_clip_face_center(
                    args.source, candidate["start"], candidate["end"],
                )
                if face_framing.get("status") == "detected":
                    if args.dynamic_face_crop:
                        crop_center_keyframes = (
                            face_framing.get("stabilized_face_keyframes")
                            or face_framing.get("face_keyframes")
                        )
                    else:
                        crop_center_x = face_framing.get("face_center_x")
            from muse.media import discover_media_tools
            available_encoders = discover_media_tools().hardware_encoders
            result = render_vertical_clip(
                args.source, args.output_mp4, candidate["start"], candidate["end"],
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe, subtitles=subtitle_path,
                available_encoders=available_encoders, crop_center_x=crop_center_x,
                crop_center_keyframes=crop_center_keyframes,
            )
            if face_framing is not None:
                result["face_framing"] = face_framing
            if result["status"] == "completed":
                from muse.video_qa import inspect_video_file
                qa = inspect_video_file(
                    args.output_mp4, ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
                    expected_width=1080, expected_height=1920, require_audio=True,
                )
                result["technical_qa"] = qa
                if qa.get("status") != "passed":
                    Path(args.output_mp4).unlink(missing_ok=True)
                    raise RuntimeError("Muse technical video QA failed; rendered clip was not retained")
                if generated_srt is not None:
                    result["subtitles"] = str(generated_srt.resolve())
            if result["status"] != "completed":
                raise RuntimeError(str(result.get("error", "render failed")))
        else:
            payload = json.loads(Path(args.analysis_json).read_text(encoding="utf-8"))
            candidates = payload.get("candidates", [])
            if not isinstance(args.index, int) or not 0 <= args.index < len(candidates):
                raise ValueError("candidate index is out of range")
            from muse.clip_product_adapter import register_highlight_clip
            caption_path = args.captions
            if caption_path is None:
                adjacent = Path(args.clip_mp4).with_suffix(".srt")
                caption_path = str(adjacent) if adjacent.is_file() and not adjacent.is_symlink() else None
            result = register_highlight_clip(
                args.workspace, args.source, args.clip_mp4, candidates[args.index],
                title=args.title, captions_srt=caption_path,
                creator_initiative_id=args.initiative_id,
                evolution_cycle_id=args.evolution_cycle_id,
                analysis_mode=str(payload.get("analysis_mode", "audio_only")),
                transcript_engine=payload.get("transcript_engine"),
                transcript=str(candidates[args.index].get("transcript", "")),
                ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())