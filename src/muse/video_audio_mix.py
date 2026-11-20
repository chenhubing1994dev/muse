"""Safe, subtitle-timed background-music ducking helpers.

Subtitle windows provide a coarse speech-presence envelope; the existing
FFmpeg sidechain compressor remains responsible for fast, signal-driven dips.
This module deliberately emits numeric FFmpeg expressions only.
"""
from __future__ import annotations

import math
import re
from typing import Iterable

_MAX_SRT_BYTES = 4 * 1024 * 1024
_MAX_CUES = 2000
_TIMECODE = re.compile(
    r"^\s*(\d+):([0-5]\d):([0-5]\d)[,.](\d{3})\s*-->\s*"
    r"(\d+):([0-5]\d):([0-5]\d)[,.](\d{3})(?:\s+.*)?$"
)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _duration(value: object) -> float:
    result = _finite_number(value, "duration_seconds")
    if not 0 < result <= 86400:
        raise ValueError("duration_seconds must be between 0 and 86400 seconds")
    return result


def _timecode_seconds(parts: tuple[str, ...]) -> tuple[float, float]:
    values = [int(item) for item in parts]
    start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
    end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
    return start, end


def normalize_speech_windows(
    windows: Iterable[tuple[float, float] | list[float]],
    duration_seconds: float,
    *,
    max_cues: int = _MAX_CUES,
    merge_gap_seconds: float = 0.06,
) -> list[tuple[float, float]]:
    """Validate, sort and merge speech windows without inventing timestamps."""
    duration = _duration(duration_seconds)
    if isinstance(max_cues, bool) or not isinstance(max_cues, int) or not 1 <= max_cues <= _MAX_CUES:
        raise ValueError(f"max_cues must be between 1 and {_MAX_CUES}")
    merge_gap = _finite_number(merge_gap_seconds, "merge_gap_seconds")
    if not 0 <= merge_gap <= 5:
        raise ValueError("merge_gap_seconds must be between 0 and 5 seconds")

    normalized: list[tuple[float, float]] = []
    for index, window in enumerate(windows):
        if index >= max_cues:
            raise ValueError(f"speech windows exceed the {max_cues}-cue limit")
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            raise ValueError(f"speech window {index} must be [start, end]")
        start = _finite_number(window[0], f"speech window {index} start")
        end = _finite_number(window[1], f"speech window {index} end")
        if start < 0 or end <= start or end > duration:
            raise ValueError(f"speech window {index} is outside the media duration")
        normalized.append((start, end))

    normalized.sort()
    merged: list[tuple[float, float]] = []
    for start, end in normalized:
        if merged and start <= merged[-1][1] + merge_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def parse_srt_speech_windows(
    srt_text: str,
    duration_seconds: float,
    *,
    max_cues: int = _MAX_CUES,
) -> list[tuple[float, float]]:
    """Extract only SRT timing lines; cue text is never interpreted as a filter."""
    _duration(duration_seconds)
    if isinstance(max_cues, bool) or not isinstance(max_cues, int) or not 1 <= max_cues <= _MAX_CUES:
        raise ValueError(f"max_cues must be between 1 and {_MAX_CUES}")
    if not isinstance(srt_text, str) or not srt_text.strip():
        raise ValueError("SRT text must be non-empty")
    try:
        srt_size = len(srt_text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("SRT text must be valid UTF-8 text") from exc
    if srt_size > _MAX_SRT_BYTES:
        raise ValueError("SRT text exceeds 4 MiB")

    windows: list[tuple[float, float]] = []
    for line in srt_text.splitlines():
        if "-->" not in line:
            continue
        match = _TIMECODE.fullmatch(line)
        if match is None:
            raise ValueError("SRT contains a malformed timing line")
        windows.append(_timecode_seconds(match.groups()))
        if len(windows) > max_cues:
            raise ValueError(f"SRT exceeds the {max_cues}-cue limit")
    if not windows:
        return []
    return normalize_speech_windows(windows, duration_seconds, max_cues=max_cues)


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def build_bgm_envelope_expression(
    speech_windows: Iterable[tuple[float, float] | list[float]],
    duration_seconds: float,
    *,
    base_gain: float = 0.22,
    duck_gain: float = 0.10,
    attack_ms: float = 80,
    release_ms: float = 450,
    fade_out_seconds: float = 1.5,
    max_cues: int = _MAX_CUES,
) -> str:
    """Build a smooth FFmpeg volume expression for a low BGM bed.

    The speech windows should come from reviewed/ASR-aligned subtitles. They
    are a coarse baseline only; retain sidechaincompress for actual voice peaks.
    """
    duration = _duration(duration_seconds)
    base = _finite_number(base_gain, "base_gain")
    duck = _finite_number(duck_gain, "duck_gain")
    attack = _finite_number(attack_ms, "attack_ms") / 1000
    release = _finite_number(release_ms, "release_ms") / 1000
    if not 0 <= duck <= base <= 1:
        raise ValueError("gains must satisfy 0 <= duck_gain <= base_gain <= 1")
    fade_out = _finite_number(fade_out_seconds, "fade_out_seconds")
    if not 0 <= attack <= 2 or not 0 <= release <= 3:
        raise ValueError("attack_ms must be 0-2000 and release_ms 0-3000")
    if not 0 <= fade_out <= min(10.0, duration):
        raise ValueError("fade_out_seconds must be between 0 and min(10, duration)")

    merged = normalize_speech_windows(
        speech_windows,
        duration,
        max_cues=max_cues,
        merge_gap_seconds=min(5.0, attack + release),
    )
    base_text, duck_text = _fmt(base), _fmt(duck)
    expression = base_text

    for start, end in reversed(merged):
        ramp_start = max(0.0, start - attack)
        ramp_duration = start - ramp_start
        if ramp_duration > 0:
            ramp_down = f"{base_text}+({duck_text}-{base_text})*(t-{_fmt(ramp_start)})/{_fmt(ramp_duration)}"
        else:
            ramp_down = duck_text

        if release > 0:
            ramp_up = f"{duck_text}+({base_text}-{duck_text})*(t-{_fmt(end)})/{_fmt(release)}"
            tail = f"if(lt(t,{_fmt(end + release)}),{ramp_up},{base_text})"
        else:
            tail = base_text

        local = f"if(lt(t,{_fmt(start)}),{ramp_down},if(lt(t,{_fmt(end)}),{duck_text},{tail}))"
        expression = (
            f"if(between(t,{_fmt(ramp_start)},{_fmt(end + release)}),"
            f"{local},{expression})"
        )
    if fade_out > 0:
        fade_start = max(0.0, duration - fade_out)
        fade = f"if(lt(t,{_fmt(fade_start)}),1,max(0,({_fmt(duration)}-t)/{_fmt(fade_out)}))"
        expression = f"({expression})*{fade}"
    return expression
