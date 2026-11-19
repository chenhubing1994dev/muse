"""Conservative gate for snapping narration cuts to reviewed phrase boundaries.

This module does not detect semantic or acoustic boundaries. It only selects
among externally supplied candidates after a human has confirmed the semantic
boundary and a non-pitch acoustic cue is present. With no eligible candidate,
the original cut is preserved.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


_SUPPORTED_CUES = frozenset({
    "pause",
    "preboundary_lengthening",
    "phrase_initial_acceleration",
    "pitch_reset",
})
_ACCEPTANCE_CUES = frozenset({
    "pause",
    "preboundary_lengthening",
    "phrase_initial_acceleration",
})
_CONTINUITY_RISKS = frozenset({"low", "medium", "high"})


def _finite_non_negative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-negative number")
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite non-negative number") from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return numeric


def snap_cut_to_verified_boundary(
    planned_cut_seconds: float,
    candidates: Iterable[Mapping[str, Any]],
    *,
    window_seconds: float = 0.6,
) -> dict[str, Any]:
    """Select the nearest human-confirmed boundary inside a bounded snap window.

    Candidate schema:
      - time_seconds: finite, non-negative timestamp
      - semantic_boundary_confirmed: bool supplied by human review
      - acoustic_cues: list of supported cue names
      - continuity_risk: low, medium, or high

    A pause or speech-rate cue can support a human-confirmed boundary. Pitch
    reset alone is deliberately insufficient because Cantonese lexical tone,
    focus, and tonal context can confound pitch measurements. High visual
    continuity risk always blocks snapping. This is a selection gate, not an
    automatic prosodic-boundary detector or evidence-quality score.
    """
    planned = _finite_non_negative(planned_cut_seconds, "planned_cut_seconds")
    window = _finite_non_negative(window_seconds, "window_seconds")
    if window > 10:
        raise ValueError("window_seconds must not exceed 10 seconds")
    if isinstance(candidates, (str, bytes, Mapping)):
        raise ValueError("candidates must be an iterable of candidate mappings")

    try:
        iterator = iter(candidates)
    except TypeError as exc:
        raise ValueError("candidates must be iterable") from exc

    eligible: list[dict[str, Any]] = []
    seen = 0
    for candidate in iterator:
        seen += 1
        if seen > 1000:
            raise ValueError("at most 1000 boundary candidates are accepted")
        if not isinstance(candidate, Mapping):
            raise ValueError("each boundary candidate must be a mapping")

        timestamp = _finite_non_negative(
            candidate.get("time_seconds"), "candidate time_seconds"
        )
        semantic_confirmed = candidate.get("semantic_boundary_confirmed")
        if not isinstance(semantic_confirmed, bool):
            raise ValueError("semantic_boundary_confirmed must be a boolean")

        cues = candidate.get("acoustic_cues")
        if not isinstance(cues, (list, tuple)) or any(
            not isinstance(cue, str) for cue in cues
        ):
            raise ValueError("acoustic_cues must be a list or tuple of strings")
        cue_set = set(cues)
        unknown = cue_set - _SUPPORTED_CUES
        if unknown:
            raise ValueError(f"unsupported acoustic cue: {sorted(unknown)[0]}")

        continuity_risk = candidate.get("continuity_risk")
        if (
            not isinstance(continuity_risk, str)
            or continuity_risk not in _CONTINUITY_RISKS
        ):
            raise ValueError("continuity_risk must be low, medium, or high")

        if (
            semantic_confirmed
            and continuity_risk != "high"
            and cue_set.intersection(_ACCEPTANCE_CUES)
            and abs(timestamp - planned) <= window
        ):
            eligible.append({
                "time_seconds": timestamp,
                "semantic_boundary_confirmed": True,
                "acoustic_cues": sorted(cue_set),
                "continuity_risk": continuity_risk,
            })

    if not eligible:
        return {
            "snapped": False,
            "original_cut_seconds": planned,
            "cut_seconds": planned,
            "window_seconds": window,
            "reason": "no_verified_candidate_within_window",
        }

    selected = min(
        eligible,
        key=lambda item: (abs(item["time_seconds"] - planned), item["time_seconds"]),
    )
    return {
        "snapped": True,
        "original_cut_seconds": planned,
        "cut_seconds": selected["time_seconds"],
        "window_seconds": window,
        "reason": "verified_semantic_boundary",
        "candidate": selected,
    }
