"""Versioned story-bible and continuity contracts for Muse video production.

This module validates creative metadata only. It never loads assets, writes files,
calls a model, submits a ComfyUI workflow, or starts a production task.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

STORY_BIBLE_SCHEMA_VERSION = 1
MAX_STORY_BIBLE_BYTES = 65_536
MAX_CHARACTERS = 24
MAX_LOCATIONS = 24
MAX_PROPS = 48
MAX_OUTFITS_PER_CHARACTER = 8
MAX_CONTINUITY_SCENES = 24
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _object(value: Any, required: set[str], allowed: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    keys = set(value)
    if keys - allowed or required - keys:
        raise ValueError(f"{label} has missing or unsupported fields")
    return value


def _text(value: Any, label: str, maximum: int, *, minimum: int = 0) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    result = value.strip()
    if not minimum <= len(result) <= maximum:
        raise ValueError(f"{label} must contain {minimum}-{maximum} characters")
    return result


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label} must be a safe lowercase identifier")
    return value


def _string_list(value: Any, label: str, *, maximum_items: int, maximum_text: int,
                 minimum_items: int = 0) -> list[str]:
    if not isinstance(value, list) or not minimum_items <= len(value) <= maximum_items:
        raise ValueError(f"{label} must contain {minimum_items}-{maximum_items} entries")
    return [_text(item, f"{label} item", maximum_text, minimum=1) for item in value]


def _optional_asset_id(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, label)


def validate_story_bible(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a bounded Story Bible, Style Guide and continuity ledger."""
    top_fields = {
        "schema_version", "title", "logline", "genre", "target_audience",
        "characters", "locations", "props", "style_guide", "continuity",
    }
    data = _object(value, top_fields, top_fields, "story_bible")
    version = data["schema_version"]
    if isinstance(version, bool) or version != STORY_BIBLE_SCHEMA_VERSION:
        raise ValueError("unsupported story_bible schema_version")

    normalized: dict[str, Any] = {
        "schema_version": STORY_BIBLE_SCHEMA_VERSION,
        "title": _text(data["title"], "title", 160, minimum=1),
        "logline": _text(data["logline"], "logline", 500, minimum=1),
        "genre": _text(data["genre"], "genre", 80, minimum=1),
        "target_audience": _text(data["target_audience"], "target_audience", 160, minimum=1),
    }

    seen_ids: set[str] = set()
    character_outfits: dict[str, set[str]] = {}

    def reserve(identifier: str, label: str) -> str:
        if identifier in seen_ids:
            raise ValueError(f"duplicate asset identifier: {identifier}")
        seen_ids.add(identifier)
        return identifier

    characters = data["characters"]
    if not isinstance(characters, list) or not 1 <= len(characters) <= MAX_CHARACTERS:
        raise ValueError(f"characters must contain 1-{MAX_CHARACTERS} entries")
    normalized_characters = []
    for index, item in enumerate(characters, 1):
        label = f"character {index}"
        fields = {"id", "name", "description", "canonical_asset_id", "voice_asset_id", "outfits"}
        character = _object(item, {"id", "name", "description", "outfits"}, fields, label)
        character_id = reserve(_identifier(character["id"], f"{label}.id"), label)
        outfits = character["outfits"]
        if not isinstance(outfits, list) or len(outfits) > MAX_OUTFITS_PER_CHARACTER:
            raise ValueError(f"{label}.outfits must contain 0-{MAX_OUTFITS_PER_CHARACTER} entries")
        normalized_outfits = []
        outfit_ids: set[str] = set()
        for outfit_index, outfit_value in enumerate(outfits, 1):
            outfit_label = f"{label} outfit {outfit_index}"
            outfit = _object(
                outfit_value, {"id", "description"},
                {"id", "description", "reference_asset_id"}, outfit_label,
            )
            outfit_id = reserve(_identifier(outfit["id"], f"{outfit_label}.id"), outfit_label)
            outfit_ids.add(outfit_id)
            normalized_outfit = {
                "id": outfit_id,
                "description": _text(outfit["description"], f"{outfit_label}.description", 500, minimum=1),
            }
            reference_id = _optional_asset_id(outfit.get("reference_asset_id"), f"{outfit_label}.reference_asset_id")
            if reference_id is not None:
                normalized_outfit["reference_asset_id"] = reference_id
            normalized_outfits.append(normalized_outfit)
        character_outfits[character_id] = outfit_ids
        normalized_character = {
            "id": character_id,
            "name": _text(character["name"], f"{label}.name", 80, minimum=1),
            "description": _text(character["description"], f"{label}.description", 1000, minimum=1),
            "outfits": normalized_outfits,
        }
        for field in ("canonical_asset_id", "voice_asset_id"):
            reference_id = _optional_asset_id(character.get(field), f"{label}.{field}")
            if reference_id is not None:
                normalized_character[field] = reference_id
        normalized_characters.append(normalized_character)
    normalized["characters"] = normalized_characters

    def normalize_entities(field: str, maximum: int) -> list[dict[str, Any]]:
        items = data[field]
        if not isinstance(items, list) or len(items) > maximum:
            raise ValueError(f"{field} must contain 0-{maximum} entries")
        result = []
        for index, item in enumerate(items, 1):
            label = f"{field[:-1]} {index}"
            entity = _object(
                item, {"id", "name", "description"},
                {"id", "name", "description", "reference_asset_id"}, label,
            )
            identifier = reserve(_identifier(entity["id"], f"{label}.id"), label)
            normalized_entity = {
                "id": identifier,
                "name": _text(entity["name"], f"{label}.name", 80, minimum=1),
                "description": _text(entity["description"], f"{label}.description", 1000, minimum=1),
            }
            reference_id = _optional_asset_id(entity.get("reference_asset_id"), f"{label}.reference_asset_id")
            if reference_id is not None:
                normalized_entity["reference_asset_id"] = reference_id
            result.append(normalized_entity)
        return result

    normalized["locations"] = normalize_entities("locations", MAX_LOCATIONS)
    normalized["props"] = normalize_entities("props", MAX_PROPS)

    style_fields = {
        "visual_style", "palette", "lighting", "camera_language",
        "audio_mood", "dialogue_language",
    }
    style = _object(data["style_guide"], style_fields, style_fields, "style_guide")
    normalized["style_guide"] = {
        "visual_style": _text(style["visual_style"], "style_guide.visual_style", 200, minimum=1),
        "palette": _string_list(style["palette"], "style_guide.palette", maximum_items=8, maximum_text=48, minimum_items=1),
        "lighting": _text(style["lighting"], "style_guide.lighting", 160, minimum=1),
        "camera_language": _text(style["camera_language"], "style_guide.camera_language", 200, minimum=1),
        "audio_mood": _text(style["audio_mood"], "style_guide.audio_mood", 160, minimum=1),
        "dialogue_language": _text(style["dialogue_language"], "style_guide.dialogue_language", 32, minimum=1),
    }

    continuity = _object(
        data["continuity"], {"rules", "scene_states"},
        {"rules", "scene_states"}, "continuity",
    )
    rules = _string_list(continuity["rules"], "continuity.rules", maximum_items=24, maximum_text=300)
    scene_states = continuity["scene_states"]
    if not isinstance(scene_states, list) or len(scene_states) > MAX_CONTINUITY_SCENES:
        raise ValueError(f"continuity.scene_states must contain 0-{MAX_CONTINUITY_SCENES} entries")

    location_ids = {item["id"] for item in normalized["locations"]}
    prop_ids = {item["id"] for item in normalized["props"]}
    normalized_states = []
    seen_scene_ids: set[str] = set()
    for index, state_value in enumerate(scene_states, 1):
        label = f"continuity scene {index}"
        state = _object(
            state_value, {"scene_id", "character_states", "prop_states"},
            {"scene_id", "location_id", "character_states", "prop_states"}, label,
        )
        scene_id = _identifier(state["scene_id"], f"{label}.scene_id")
        if scene_id in seen_scene_ids:
            raise ValueError(f"duplicate continuity scene state: {scene_id}")
        seen_scene_ids.add(scene_id)
        location_id = state.get("location_id")
        if location_id is not None:
            location_id = _identifier(location_id, f"{label}.location_id")
            if location_id not in location_ids:
                raise ValueError(f"{label} references unknown location: {location_id}")

        character_states = state["character_states"]
        if not isinstance(character_states, list) or len(character_states) > MAX_CHARACTERS:
            raise ValueError(f"{label}.character_states has an invalid size")
        normalized_character_states = []
        seen_character_refs: set[str] = set()
        for ref_index, ref_value in enumerate(character_states, 1):
            ref_label = f"{label} character state {ref_index}"
            ref = _object(ref_value, {"character_id"}, {"character_id", "outfit_id", "state_note"}, ref_label)
            character_id = _identifier(ref["character_id"], f"{ref_label}.character_id")
            if character_id not in character_outfits:
                raise ValueError(f"{ref_label} references unknown character: {character_id}")
            if character_id in seen_character_refs:
                raise ValueError(f"{label} has duplicate character state: {character_id}")
            seen_character_refs.add(character_id)
            normalized_ref: dict[str, Any] = {"character_id": character_id}
            outfit_id = ref.get("outfit_id")
            if outfit_id is not None:
                outfit_id = _identifier(outfit_id, f"{ref_label}.outfit_id")
                if outfit_id not in character_outfits[character_id]:
                    raise ValueError(f"{ref_label} references unknown outfit: {outfit_id}")
                normalized_ref["outfit_id"] = outfit_id
            if "state_note" in ref:
                normalized_ref["state_note"] = _text(ref["state_note"], f"{ref_label}.state_note", 300)
            normalized_character_states.append(normalized_ref)

        prop_states = state["prop_states"]
        if not isinstance(prop_states, list) or len(prop_states) > MAX_PROPS:
            raise ValueError(f"{label}.prop_states has an invalid size")
        normalized_prop_states = []
        seen_prop_refs: set[str] = set()
        for ref_index, ref_value in enumerate(prop_states, 1):
            ref_label = f"{label} prop state {ref_index}"
            ref = _object(ref_value, {"prop_id"}, {"prop_id", "state_note"}, ref_label)
            prop_id = _identifier(ref["prop_id"], f"{ref_label}.prop_id")
            if prop_id not in prop_ids:
                raise ValueError(f"{ref_label} references unknown prop: {prop_id}")
            if prop_id in seen_prop_refs:
                raise ValueError(f"{label} has duplicate prop state: {prop_id}")
            seen_prop_refs.add(prop_id)
            normalized_ref = {"prop_id": prop_id}
            if "state_note" in ref:
                normalized_ref["state_note"] = _text(ref["state_note"], f"{ref_label}.state_note", 300)
            normalized_prop_states.append(normalized_ref)

        normalized_state: dict[str, Any] = {
            "scene_id": scene_id,
            "character_states": normalized_character_states,
            "prop_states": normalized_prop_states,
        }
        if location_id is not None:
            normalized_state["location_id"] = location_id
        normalized_states.append(normalized_state)

    normalized["continuity"] = {"rules": rules, "scene_states": normalized_states}
    try:
        encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("story_bible must contain finite JSON-compatible values") from None
    if len(encoded) > MAX_STORY_BIBLE_BYTES:
        raise ValueError("story_bible exceeds the 64 KiB size limit")
    return normalized


def validate_story_continuity(
    story_bible: Mapping[str, Any], storyboard: Mapping[str, Any],
) -> dict[str, int]:
    """Cross-check continuity scene IDs against a validated storyboard."""
    from muse.video_workflow import validate_storyboard

    bible = validate_story_bible(story_bible)
    normalized_storyboard = validate_storyboard(storyboard)
    scene_ids = {scene["id"] for scene in normalized_storyboard["scenes"]}
    referenced_ids = {state["scene_id"] for state in bible["continuity"]["scene_states"]}
    missing = referenced_ids - scene_ids
    if missing:
        raise ValueError(f"continuity references unknown storyboard scene: {sorted(missing)}")
    return {
        "scene_count": len(scene_ids),
        "continuity_scene_count": len(referenced_ids),
    }
