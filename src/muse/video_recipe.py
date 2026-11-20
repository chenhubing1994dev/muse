"""Safe, dependency-free adapters for external short-video parameter recipes.

This module normalizes only fields with an unambiguous Muse equivalent. It never
executes providers, schedules work, reads credentials, or silently discards options.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from muse.video_workflow import validate_storyboard

RECIPE_SCHEMA_VERSION = 1
_MAX_FIELDS = 128
_MAX_FIELD_NAME = 80

# These aliases are deliberately narrow. Search hints, generated scripts, provider
# selection, voice/audio, subtitles, and batch settings require an explicit adapter.
_SAFE_ALIASES = {
    "title": "title",
    "video_subject": "title",
    "aspect_ratio": "aspect_ratio",
    "video_aspect": "aspect_ratio",
    "fps": "fps",
    "video_fps": "fps",
    "style": "style",
    "video_style": "style",
    "negative_prompt": "negative_prompt",
    "video_negative_prompt": "negative_prompt",
    "scenes": "scenes",
}
_ASPECT_ALIASES = {
    "landscape": "16:9",
    "horizontal": "16:9",
    "portrait": "9:16",
    "vertical": "9:16",
    "square": "1:1",
    "16:9": "16:9",
    "9:16": "9:16",
    "1:1": "1:1",
}
_CONFIRMATION_PREFIXES = (
    "video_", "voice_", "bgm_", "subtitle_", "audio_", "music_", "font_",
)
_CONFIRMATION_FIELDS = frozenset({
    "video_script", "video_terms", "video_count", "video_clip_duration",
    "video_source", "video_materials", "video_language", "paragraph_number",
    "n_threads", "video_concat_mode", "video_transition_mode", "video_model",
    "video_quality", "video_width", "video_height", "voice_name", "voice_volume",
    "bgm_type", "bgm_file", "bgm_volume", "subtitle_enabled", "subtitle_position",
    "font_name", "text_color", "stroke_color", "stroke_width",
})


def _is_secret_field(name: str) -> bool:
    lowered = name.casefold()
    return any(token in lowered for token in ("api_key", "apikey", "secret", "password", "token", "credential"))


def _normalize_aspect(value: Any) -> str:
    if not isinstance(value, str) or value.strip().casefold() not in _ASPECT_ALIASES:
        raise ValueError("aspect ratio must be landscape/portrait/square or 16:9/9:16/1:1")
    return _ASPECT_ALIASES[value.strip().casefold()]


def adapt_moneyprinterturbo_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Return a partial Muse recipe plus an explicit field-mapping report.

    The result is not a Commander plan. A validated ``scenes`` array is required
    before ``ready_for_commander_plan`` becomes true. Unmapped values are never
    copied into the report, which also prevents accidental credential disclosure.
    """
    if not isinstance(params, Mapping):
        raise ValueError("MoneyPrinterTurbo params must be a mapping")
    if len(params) > _MAX_FIELDS:
        raise ValueError(f"at most {_MAX_FIELDS} parameter fields are accepted")

    recipe: dict[str, Any] = {"schema_version": RECIPE_SCHEMA_VERSION}
    mapped: list[dict[str, str]] = []
    unsupported: list[str] = []
    requires_confirmation: list[dict[str, str]] = []
    seen_targets: set[str] = set()

    for raw_name, value in params.items():
        if not isinstance(raw_name, str) or not raw_name or len(raw_name) > _MAX_FIELD_NAME:
            raise ValueError("parameter names must be non-empty strings of at most 80 characters")
        name = raw_name.strip()
        if not name or name != raw_name:
            raise ValueError("parameter names must not contain surrounding whitespace")
        lowered = name.casefold()
        if _is_secret_field(lowered):
            requires_confirmation.append({"field": name, "reason": "credential_field_rejected_value_not_retained"})
            continue

        target = _SAFE_ALIASES.get(lowered)
        if target is not None:
            if target in seen_targets:
                raise ValueError(f"multiple source fields map to the same Muse field: {target}")
            if target == "aspect_ratio":
                value = _normalize_aspect(value)
            elif target in {"title", "style", "negative_prompt"}:
                maximum = 160 if target == "title" else (500 if target == "style" else 1000)
                if not isinstance(value, str) or len(value.strip()) > maximum or (target == "title" and not value.strip()):
                    raise ValueError(f"{name} must be text within the Muse {target} limit")
                value = value.strip()
            elif target == "fps":
                if isinstance(value, bool) or not isinstance(value, int) or value not in {24, 25, 30, 50, 60}:
                    raise ValueError("fps must be one of 24, 25, 30, 50 or 60")
            elif target == "scenes":
                if not isinstance(value, list):
                    raise ValueError("scenes must be a list of Muse-compatible storyboard scenes")
            recipe[target] = value
            seen_targets.add(target)
            mapped.append({"source": name, "target": target})
        elif lowered in _CONFIRMATION_FIELDS or lowered.startswith(_CONFIRMATION_PREFIXES):
            requires_confirmation.append({"field": name, "reason": "requires_explicit_muse_capability_and_cost_review"})
        else:
            unsupported.append(name)

    storyboard = None
    if "scenes" in recipe:
        if "title" not in recipe:
            raise ValueError("a title/video_subject is required when scenes are supplied")
        storyboard_input = {
            "schema_version": 1,
            "title": recipe["title"],
            "aspect_ratio": recipe.get("aspect_ratio", "16:9"),
            "fps": recipe.get("fps", 30),
            "style": recipe.get("style", ""),
            "negative_prompt": recipe.get("negative_prompt", ""),
            "scenes": recipe["scenes"],
        }
        storyboard = validate_storyboard(storyboard_input)
        recipe = storyboard

    ready = storyboard is not None and not requires_confirmation and not unsupported
    return {
        "adapter": "moneyprinterturbo_to_muse",
        "adapter_version": 1,
        "recipe": recipe,
        "mapping_report": {
            "status": "ready" if ready else ("needs_review" if requires_confirmation else "incomplete"),
            "mapped": mapped,
            "unsupported": sorted(unsupported),
            "requires_confirmation": requires_confirmation,
            "ready_for_commander_plan": ready,
        },
    }


def adapt_moneyprinterturbo_batch(candidates: Any, *, max_candidates: int = 8) -> dict[str, Any]:
    """Normalize a bounded list of independent MPT parameter candidates.

    This is a planning/reporting helper only: candidate IDs are deterministic for
    a given order and recipe, and no candidate is submitted to Commander.
    """
    from collections.abc import Sequence
    import hashlib
    import json

    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= 8:
        raise ValueError("max_candidates must be between 1 and 8")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise ValueError("batch candidates must be a list or tuple of parameter objects")
    if not 1 <= len(candidates) <= max_candidates:
        raise ValueError(f"batch must contain between 1 and {max_candidates} candidates")

    results: list[dict[str, Any]] = []
    ready_count = 0
    review_count = 0
    incomplete_count = 0
    for index, params in enumerate(candidates, start=1):
        try:
            adapted = adapt_moneyprinterturbo_params(params)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"candidate {index}: {exc}") from None
        report = adapted["mapping_report"]
        ready = report["ready_for_commander_plan"]
        ready_count += int(ready)
        review_count += int(report["status"] == "needs_review")
        incomplete_count += int(report["status"] == "incomplete")
        recipe_bytes = json.dumps(
            adapted["recipe"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        recipe_sha256 = hashlib.sha256(recipe_bytes).hexdigest()
        candidate_fingerprint = hashlib.sha256(json.dumps(
            {"recipe_sha256": recipe_sha256, "mapping_report": report},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        results.append({
            "candidate_id": f"candidate-{index:02d}-{candidate_fingerprint[:12]}",
            "ordinal": index,
            "status": report["status"],
            "recipe_sha256": recipe_sha256,
            "recipe": adapted["recipe"],
            "mapping_report": report,
        })
    total = len(results)
    return {
        "batch_schema_version": 1,
        "status": "ready" if ready_count == total else ("needs_review" if review_count else "incomplete"),
        "summary": {
            "candidate_count": total,
            "ready_count": ready_count,
            "needs_review_count": review_count,
            "incomplete_count": incomplete_count,
            "blocked_count": total - ready_count,
        },
        "candidates": results,
        "execution": "not_started",
        "commander_submission": "not_submitted",
    }
