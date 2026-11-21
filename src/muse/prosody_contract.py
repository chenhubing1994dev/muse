"""Explicit, fail-closed prosody intent contract for segmented narration.

This module describes requested delivery; it never claims a TTS backend applied
controls unless that backend advertises the corresponding capability.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_MAX_SEGMENTS = 16
_MAX_TEXT_CHARS = 280
_ALLOWED_EMOTIONS = {
    "neutral", "calm", "warm", "thoughtful", "confident", "bright",
    "playful", "excited", "curious", "formal", "measured", "sad",
    "angry", "fearful", "soothing", "serious",
}
_ALLOWED_CAPABILITIES = {
    "emotion", "rate", "pitch", "breath", "segment_pause",
    "pause_before", "pause_after",
}


class ProsodyContractError(ValueError):
    """A narration prosody request violates the bounded contract."""


def _bounded_number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProsodyContractError(f"{label} must be numeric")
    number = float(value)
    if not low <= number <= high:
        raise ProsodyContractError(f"{label} must be between {low} and {high}")
    return number


def build_prosody_plan(
    segments: Iterable[Mapping[str, Any]],
    *,
    backend_capabilities: Iterable[str] = (),
) -> dict[str, Any]:
    """Normalize segmented narration and explicitly separate intent from effects.

    Supported backend capabilities are emotion, rate, pitch, breath,
    pause_before, and pause_after. The legacy segment_pause capability remains
    an alias for both pause directions. With no capabilities, every requested
    control is reported as unsupported; metadata is never represented as applied audio processing.
    """
    if isinstance(segments, (str, bytes, Mapping)):
        raise ProsodyContractError("segments must be a sequence of segment mappings")
    try:
        raw_segments = list(segments)
    except TypeError as exc:
        raise ProsodyContractError("segments must be iterable") from exc
    if not 1 <= len(raw_segments) <= _MAX_SEGMENTS:
        raise ProsodyContractError(f"segments must contain 1-{_MAX_SEGMENTS} items")

    try:
        raw_capabilities = list(backend_capabilities)
    except TypeError as exc:
        raise ProsodyContractError("backend_capabilities must be an iterable of names") from exc
    if any(not isinstance(item, str) for item in raw_capabilities):
        raise ProsodyContractError("backend_capabilities must contain only strings")
    capabilities = set(raw_capabilities)
    unknown_capabilities = capabilities - _ALLOWED_CAPABILITIES
    if unknown_capabilities:
        raise ProsodyContractError(
            "unknown backend capabilities: " + ", ".join(sorted(unknown_capabilities))
        )
    planned: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, Mapping):
            raise ProsodyContractError(f"segments[{index}] must be a mapping")
        allowed_fields = {
            "text", "emotion", "rate_scale", "pitch_semitones", "breath_hint",
            "pause_before_ms", "pause_after_ms",
        }
        extra = set(raw) - allowed_fields
        if extra:
            raise ProsodyContractError(
                f"segments[{index}] contains unsupported fields: " + ", ".join(sorted(extra))
            )

        text = raw.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > _MAX_TEXT_CHARS:
            raise ProsodyContractError(
                f"segments[{index}].text must contain 1-{_MAX_TEXT_CHARS} characters"
            )
        emotion = raw.get("emotion", "neutral")
        if not isinstance(emotion, str) or emotion not in _ALLOWED_EMOTIONS:
            raise ProsodyContractError(f"segments[{index}].emotion is not a supported intent label")

        rate = _bounded_number(raw.get("rate_scale", 1.0), f"segments[{index}].rate_scale", 0.7, 1.3)
        pitch = _bounded_number(
            raw.get("pitch_semitones", 0.0), f"segments[{index}].pitch_semitones", -6.0, 6.0
        )
        breath = raw.get("breath_hint", False)
        if not isinstance(breath, bool):
            raise ProsodyContractError(f"segments[{index}].breath_hint must be boolean")

        pauses: dict[str, int] = {}
        for field in ("pause_before_ms", "pause_after_ms"):
            value = raw.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3000:
                raise ProsodyContractError(f"segments[{index}].{field} must be an integer from 0 to 3000")
            pauses[field] = value

        requested: dict[str, Any] = {
            "emotion": emotion,
            "rate_scale": rate,
            "pitch_semitones": pitch,
            "breath_hint": breath,
            **pauses,
        }
        requested_fields = set(raw) - {"text"}
        capability_for = {
            "emotion": "emotion",
            "rate_scale": "rate",
            "pitch_semitones": "pitch",
            "breath_hint": "breath",
            "pause_before_ms": "pause_before",
            "pause_after_ms": "pause_after",
        }
        applied = {
            key: requested[key] for key in requested_fields
            if ((key == "emotion" and "emotion" in capabilities)
                or (key == "rate_scale" and "rate" in capabilities)
                or (key == "pitch_semitones" and "pitch" in capabilities)
                or (key == "breath_hint" and "breath" in capabilities)
                or (key in pauses and (
                    capability_for[key] in capabilities or "segment_pause" in capabilities
                )))
        }
        unsupported = sorted(
            key for key in requested_fields
            if capability_for[key] not in capabilities
            and not (key in pauses and "segment_pause" in capabilities)
        )
        status = (
            "no_controls_requested" if not requested_fields else
            "fully_supported" if not unsupported else
            "partially_supported" if applied else "metadata_only"
        )
        planned.append({
            "index": index,
            "text": text.strip(),
            "requested_intent": requested,
            "requested_controls": sorted(requested_fields),
            "applied_controls": applied,
            "unsupported_controls": unsupported,
            "application_status": status,
        })
    return {
        "schema": "muse.narration.prosody.v1",
        "backend_capabilities": sorted(capabilities),
        "segment_count": len(planned),
        "segments": planned,
        "audio_effects_verified": False,
        "requires_backend_and_audio_validation": True,
    }
