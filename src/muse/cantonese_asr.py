"""Pinned, local-only Cantonese ASR and audio-timed subtitle generation.

This module never downloads model weights. It verifies the pinned CTranslate2
artifact before loading faster-whisper, and derives subtitle cues from decoded
audio timestamps rather than estimating timing from script length.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable

MODEL_REPO = "alvanlii/whisper-small-cantonese"
MODEL_REVISION = "b03a4e5d482f06858ce10ebbabebabec9f447a0d"
_MODEL_BYTES = 967_072_097
_MODEL_SHA256 = "d343caa7ef827ddb60bc2c1c8c741b29d2bd99d5387aa5c6f96cbd26f6804b34"


class CantoneseASRError(RuntimeError):
    """Pinned Cantonese ASR could not run safely or produce valid timestamps."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.is_file():
        raise CantoneseASRError(f"{label} must be an existing regular file without symlink parents")
    return path.resolve(strict=True)


def _normalized_characters(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("transcript text must be a string")
    return "".join(
        character.casefold()
        for character in text
        if unicodedata.category(character)[0] in {"L", "N"}
    )


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def compare_cantonese_transcript(
    reference_text: str, recognized_text: str, *, max_cer: float = 0.18,
) -> dict[str, Any]:
    """Compare Cantonese text with punctuation-insensitive character error rate."""
    if not isinstance(max_cer, (int, float)) or isinstance(max_cer, bool) or not math.isfinite(max_cer) or not 0 <= max_cer <= 1:
        raise ValueError("max_cer must be a finite number between 0 and 1")
    reference = _normalized_characters(reference_text)
    recognized = _normalized_characters(recognized_text)
    if not reference:
        raise ValueError("reference_text must contain at least one letter or number")
    distance = _edit_distance(reference, recognized)
    cer = distance / len(reference)
    return {
        "status": "passed" if cer <= max_cer else "needs_review",
        "metric": "character_error_rate",
        "cer": round(cer, 6),
        "max_cer": float(max_cer),
        "edit_distance": distance,
        "reference_characters": len(reference),
        "recognized_characters": len(recognized),
        "reference_normalized": reference,
        "recognized_normalized": recognized,
    }


def _srt_timestamp(seconds: float) -> str:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
        raise CantoneseASRError("ASR segment timestamps must be finite non-negative numbers")
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def _write_srt(path: Path, segments: Iterable[dict[str, Any]]) -> dict[str, Any]:
    if path.suffix.casefold() != ".srt":
        raise ValueError("subtitle output must use the .srt extension")
    if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.parent.is_dir():
        raise CantoneseASRError("subtitle output parent must be an existing regular directory")
    if path.is_symlink():
        raise CantoneseASRError("refusing to overwrite a symlink subtitle output")
    cues: list[str] = []
    word_timed_segments = 0
    segment_fallback_count = 0

    def append_cue(start: float, end: float, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start):
            raise CantoneseASRError("ASR word/segment has invalid start/end timestamps")
        cues.append(f"{len(cues) + 1}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}")

    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        segment_start, segment_end = segment.get("start"), segment.get("end")
        if (not isinstance(segment_start, (int, float)) or isinstance(segment_start, bool)
                or not isinstance(segment_end, (int, float)) or isinstance(segment_end, bool)
                or not math.isfinite(segment_start) or not math.isfinite(segment_end)
                or segment_start < 0 or segment_end <= segment_start):
            raise CantoneseASRError("ASR segment has invalid start/end timestamps")

        words = segment.get("words")
        normalized_words: list[tuple[float, float, str]] = []
        word_timing_valid = isinstance(words, list) and bool(words)
        previous_start = -1.0
        if word_timing_valid:
            pending_prefix = ""
            for index, word in enumerate(words):
                if not isinstance(word, dict):
                    word_timing_valid = False
                    break
                raw_text, start, end = word.get("text"), word.get("start"), word.get("end")
                if not isinstance(raw_text, str) or not raw_text.strip():
                    # Whisper may emit blank alignment tokens; they carry no caption text.
                    continue
                if (not isinstance(start, (int, float)) or isinstance(start, bool)
                        or not isinstance(end, (int, float)) or isinstance(end, bool)
                        or not math.isfinite(start) or not math.isfinite(end)
                        or start < 0 or end < start or start < previous_start):
                    word_timing_valid = False
                    break
                if end == start:
                    next_word = words[index + 1] if index + 1 < len(words) else None
                    next_start = next_word.get("start") if isinstance(next_word, dict) else None
                    next_end = next_word.get("end") if isinstance(next_word, dict) else None
                    if (isinstance(next_start, (int, float)) and not isinstance(next_start, bool)
                            and isinstance(next_end, (int, float)) and not isinstance(next_end, bool)
                            and math.isfinite(next_start) and math.isfinite(next_end)
                            and math.isclose(float(next_start), float(start), abs_tol=0.02)
                            and next_end > next_start):
                        # A zero-duration token at the next token's exact onset can be
                        # joined to that token without inventing a new time interval.
                        pending_prefix += raw_text
                        continue
                    word_timing_valid = False
                    break
                if pending_prefix:
                    raw_text = pending_prefix + raw_text
                    pending_prefix = ""
                normalized_words.append((float(start), float(end), raw_text))
                previous_start = float(start)
            if pending_prefix:
                word_timing_valid = False
        if word_timing_valid and normalized_words:
            word_timed_segments += 1
            chunk_text = ""
            chunk_start = chunk_end = None
            for start, end, word_text in normalized_words:
                if chunk_text and (
                    len(chunk_text) + len(word_text) > 16
                    or end - float(chunk_start) > 4.5
                    or chunk_text.rstrip().endswith(("。", "！", "？", "；", ".", "!", "?"))
                ):
                    append_cue(float(chunk_start), float(chunk_end), chunk_text)
                    chunk_text, chunk_start, chunk_end = "", None, None
                if chunk_start is None:
                    chunk_start = start
                chunk_text += word_text
                chunk_end = end
            if chunk_text and chunk_start is not None and chunk_end is not None:
                append_cue(chunk_start, chunk_end, chunk_text)
        else:
            if isinstance(words, list) and words:
                segment_fallback_count += 1
            # Use the model's real segment bounds; never invent missing word timings.
            append_cue(float(segment_start), float(segment_end), text)
    if not cues:
        raise CantoneseASRError("ASR returned no non-empty subtitle segments")
    payload = "\n\n".join(cues) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=".muse-subtitles-", suffix=".srt", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(payload, encoding="utf-8-sig", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    timing_mode = "mixed" if word_timed_segments and segment_fallback_count else (
        "word" if word_timed_segments else "segment"
    )
    return {
        "path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path),
        "cue_count": len(cues), "timing_mode": timing_mode,
        "word_timed_segment_count": word_timed_segments,
        "segment_fallback_count": segment_fallback_count,
    }


def _verified_model(model_dir: str | Path) -> tuple[Path, str]:
    root = Path(model_dir).expanduser()
    cts = root / "cts"
    if (any(parent.is_symlink() for parent in (root, *root.parents))
            or root.is_symlink() or not cts.is_dir() or cts.is_symlink()):
        raise CantoneseASRError("pinned Whisper model directory is missing or unsafe")
    for name in ("config.json", "vocabulary.json", "preprocessor_config.json"):
        _regular_file(cts / name, f"Whisper {name}")
    model_file = _regular_file(cts / "model.bin", "pinned Whisper model")
    if model_file.stat().st_size != _MODEL_BYTES:
        raise CantoneseASRError("pinned Whisper model is incomplete or has an unexpected size")
    digest = _sha256(model_file)
    if digest != _MODEL_SHA256:
        raise CantoneseASRError("pinned Whisper model SHA-256 does not match the reviewed revision")
    return cts, digest


def transcribe_cantonese(
    audio_path: str | Path, *, model_dir: str | Path,
    output_srt: str | Path | None = None, reference_text: str | None = None,
    max_cer: float = 0.10, device: str = "cpu", compute_type: str = "int8",
    beam_size: int = 5, vad_filter: bool = False,
    condition_on_previous_text: bool = True,
) -> dict[str, Any]:
    """Transcribe one local audio file with the pinned Cantonese model.

    The model is loaded from the local CTS directory only. If reference_text
    is supplied, a CER gate is reported; a low CER is a machine check, not a
    substitute for human listening or editorial approval.
    """
    audio = _regular_file(Path(audio_path).expanduser(), "audio input")
    model_path, model_digest = _verified_model(model_dir)
    if device not in {"cpu", "cuda", "auto"}:
        raise ValueError("device must be cpu, cuda or auto")
    if compute_type not in {"int8", "int8_float16", "float16", "float32"}:
        raise ValueError("unsupported faster-whisper compute_type")
    if isinstance(beam_size, bool) or not isinstance(beam_size, int) or not 1 <= beam_size <= 10:
        raise ValueError("beam_size must be an integer between 1 and 10")
    if not isinstance(vad_filter, bool) or not isinstance(condition_on_previous_text, bool):
        raise ValueError("vad_filter and condition_on_previous_text must be booleans")
    if reference_text is not None and (not isinstance(reference_text, str) or not reference_text.strip()):
        raise ValueError("reference_text must be non-empty text when provided")

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise CantoneseASRError("faster-whisper is not installed in this Python environment") from exc
    try:
        model = WhisperModel(str(model_path), device=device, compute_type=compute_type)
        raw_segments, info = model.transcribe(
            str(audio), language="zh", beam_size=beam_size, vad_filter=vad_filter,
            word_timestamps=True, condition_on_previous_text=condition_on_previous_text,
            temperature=0.0, compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0, no_speech_threshold=0.6,
        )
        segments: list[dict[str, Any]] = []
        for item in raw_segments:
            word_items = getattr(item, "words", None) or []
            words = [{
                "start": float(word.start), "end": float(word.end), "text": str(word.word),
            } for word in word_items]
            segments.append({
                "start": float(item.start), "end": float(item.end), "text": str(item.text).strip(),
                "words": words,
                "avg_logprob": getattr(item, "avg_logprob", None),
                "no_speech_prob": getattr(item, "no_speech_prob", None),
            })
    except CantoneseASRError:
        raise
    except Exception as exc:
        raise CantoneseASRError(f"local Cantonese ASR failed: {type(exc).__name__}: {exc}") from exc

    transcript = "".join(item["text"] for item in segments).strip()
    if not transcript:
        raise CantoneseASRError("ASR returned an empty transcript")
    subtitle = _write_srt(Path(output_srt).expanduser(), segments) if output_srt is not None else None
    comparison = compare_cantonese_transcript(reference_text, transcript, max_cer=max_cer) if reference_text is not None else None
    return {
        "status": comparison["status"] if comparison else "transcribed",
        "engine": "faster-whisper-local",
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "model_sha256": model_digest,
        "language": getattr(info, "language", "zh"),
        "language_probability": getattr(info, "language_probability", None),
        "audio_sha256": _sha256(audio),
        "transcript": transcript,
        "segment_count": len(segments),
        "segments": segments,
        "comparison": comparison,
        "subtitles": subtitle,
        "subtitle_timing": ({key: subtitle[key] for key in ("timing_mode", "word_timed_segment_count", "segment_fallback_count")} if subtitle else None),
        "human_listening_review_required": True,
    }
