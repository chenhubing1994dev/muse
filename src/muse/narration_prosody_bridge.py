"""Bridge structured narration into the fail-closed prosody contract.

This module converts style intent into a backend-neutral plan. It does not
synthesize audio and never claims that a backend applied an unsupported effect.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from muse.narration_generator import NarrationStyle, synth_params
from muse.prosody_contract import build_prosody_plan

# Verified against the current segmented canto-tts-nano implementation:
# it inserts trailing PCM silence, not leading silence or other prosody effects.
CANTO_TTS_NANO_CAPABILITIES = ("pause_after",)


def _resolve_style(value: Any) -> NarrationStyle:
    if isinstance(value, NarrationStyle):
        return value
    if isinstance(value, str):
        try:
            return NarrationStyle(value)
        except ValueError as exc:
            raise ValueError("script.style must be a supported NarrationStyle") from exc
    raise ValueError("script.style must be a supported NarrationStyle")


def build_narration_prosody_plan(
    script: Mapping[str, Any],
    *,
    backend_capabilities: Iterable[str] = (),
) -> dict[str, Any]:
    """Convert a NarrationGenerator result into an explicit prosody plan.

    The generator's legacy speed_scale field is deliberately mapped to the
    contract's rate_scale field. Backend capabilities must be supplied by
    the caller; style metadata alone never means an audio effect was applied.
    """
    if not isinstance(script, Mapping):
        raise ValueError("script must be a mapping")
    raw_segments = script.get("segments")
    if not isinstance(raw_segments, list) or not 1 <= len(raw_segments) <= 16:
        raise ValueError("script.segments must contain 1-16 segment mappings")

    style = _resolve_style(script.get("style", NarrationStyle.YUJIE_CANTO.value))
    profile = synth_params(style)
    planned_segments: list[dict[str, Any]] = []

    for index, segment in enumerate(raw_segments):
        if not isinstance(segment, Mapping):
            raise ValueError(f"script.segments[{index}] must be a mapping")
        text = segment.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"script.segments[{index}].text must be non-empty")

        voice = segment.get("voice", profile)
        if not isinstance(voice, Mapping):
            raise ValueError(f"script.segments[{index}].voice must be a mapping")
        intent: dict[str, Any] = {
            "text": text,
            "emotion": segment.get("emotion", "neutral"),
        }
        if "speed_scale" in voice:
            intent["rate_scale"] = voice["speed_scale"]
        if "pitch_semitones" in voice:
            intent["pitch_semitones"] = voice["pitch_semitones"]
        if "breath_hint" in voice:
            intent["breath_hint"] = voice["breath_hint"]
        for pause_field in ("pause_before_ms", "pause_after_ms"):
            if pause_field in segment:
                intent[pause_field] = segment[pause_field]
        planned_segments.append(intent)

    return build_prosody_plan(
        planned_segments,
        backend_capabilities=backend_capabilities,
    )


def build_cantonese_narration_prosody_plan(script: Mapping[str, Any]) -> dict[str, Any]:
    """Build a plan against only the capabilities verified for canto-tts-nano."""
    return build_narration_prosody_plan(
        script,
        backend_capabilities=CANTO_TTS_NANO_CAPABILITIES,
    )



def build_cantonese_tts_segments(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build a conservative payload for the current Cantonese TTS segment API.

    Only pause_after_ms is understood by the current canto-tts-nano segmented
    engine. This function prepares input data; it does not call the engine and
    does not claim that audio effects were applied or acoustically verified.
    Unsupported prosody intent is intentionally omitted from the payload.
    """
    if not isinstance(plan, Mapping) or plan.get("schema") != "muse.narration.prosody.v1":
        raise ValueError("plan must be a muse.narration.prosody.v1 mapping")
    if plan.get("audio_effects_verified") is not False:
        raise ValueError("Cantonese TTS plan must not claim verified audio effects")
    if plan.get("requires_backend_and_audio_validation") is not True:
        raise ValueError("Cantonese TTS plan must require backend and audio validation")
    raw_segments = plan.get("segments")
    declared = plan.get("backend_capabilities", [])
    if not isinstance(declared, list) or any(not isinstance(name, str) for name in declared):
        raise ValueError("plan.backend_capabilities must be a list of names")
    if not isinstance(raw_segments, list) or not 1 <= len(raw_segments) <= 16:
        raise ValueError("plan.segments must contain 1-16 segment mappings")
    if "segment_pause" in declared and any(
        isinstance(segment, Mapping)
        and isinstance(segment.get("applied_controls", {}), Mapping)
        and "pause_after_ms" in segment.get("applied_controls", {})
        for segment in raw_segments
    ):
        raise ValueError("pause_after_ms requires the explicit pause_after backend capability")
    if set(declared) - set(CANTO_TTS_NANO_CAPABILITIES):
        raise ValueError("plan.backend_capabilities exceed verified canto-tts-nano capabilities")

    if len(declared) != len(set(declared)) or declared != sorted(declared):
        raise ValueError("plan.backend_capabilities must be unique and sorted")
    segment_count = plan.get("segment_count")
    if (isinstance(segment_count, bool) or not isinstance(segment_count, int)
            or segment_count != len(raw_segments)):
        raise ValueError("plan.segment_count must match plan.segments")

    payload: list[dict[str, Any]] = []
    for index, segment in enumerate(raw_segments):
        if not isinstance(segment, Mapping):
            raise ValueError(f"plan.segments[{index}] must be a mapping")
        segment_index = segment.get("index")
        if (isinstance(segment_index, bool) or not isinstance(segment_index, int)
                or segment_index != index):
            raise ValueError(f"plan.segments[{index}].index must preserve contiguous segment order")
        text = segment.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 280:
            raise ValueError(f"plan.segments[{index}].text must contain 1-280 characters")

        requested = segment.get("requested_intent")
        requested_controls = segment.get("requested_controls")
        unsupported = segment.get("unsupported_controls")
        applied = segment.get("applied_controls")
        if not isinstance(requested, Mapping):
            raise ValueError(f"plan.segments[{index}].requested_intent must be a mapping")
        if (not isinstance(requested_controls, list)
                or any(not isinstance(name, str) for name in requested_controls)
                or requested_controls != sorted(set(requested_controls))):
            raise ValueError(f"plan.segments[{index}].requested_controls must be unique sorted names")
        if (not isinstance(unsupported, list)
                or any(not isinstance(name, str) for name in unsupported)
                or unsupported != sorted(set(unsupported))):
            raise ValueError(f"plan.segments[{index}].unsupported_controls must be unique sorted names")
        if not isinstance(applied, Mapping):
            raise ValueError(f"plan.segments[{index}].applied_controls must be a mapping")

        if any(not isinstance(name, str) for name in applied):
            raise ValueError(f"plan.segments[{index}].applied_controls keys must be strings")
        applied_names = set(applied)
        requested_names = set(requested_controls)
        unsupported_names = set(unsupported)
        capability_for = {
            "emotion": "emotion",
            "rate_scale": "rate",
            "pitch_semitones": "pitch",
            "breath_hint": "breath",
            "pause_before_ms": "pause_before",
            "pause_after_ms": "pause_after",
        }
        if requested_names - set(capability_for):
            raise ValueError(f"plan.segments[{index}] contains unknown requested controls")
        expected_applied = {
            name for name in requested_names if capability_for[name] in declared
        }
        expected_unsupported = requested_names - expected_applied
        if (applied_names != expected_applied
                or unsupported_names != expected_unsupported):
            raise ValueError(f"plan.segments[{index}] control classifications are inconsistent")

        item: dict[str, Any] = {"text": text.strip()}
        if "pause_after_ms" in applied:
            if "pause_after" not in declared:
                raise ValueError("pause_after_ms requires the explicit pause_after backend capability")
            pause = applied["pause_after_ms"]
            if (isinstance(pause, bool) or not isinstance(pause, int)
                    or not 0 <= pause <= 3000):
                raise ValueError(f"plan.segments[{index}].applied_controls.pause_after_ms must be 0-3000")
            if requested.get("pause_after_ms") != pause:
                raise ValueError(f"plan.segments[{index}] applied pause must match requested intent")
            item["pause_after_ms"] = pause
        payload.append(item)
    return payload
