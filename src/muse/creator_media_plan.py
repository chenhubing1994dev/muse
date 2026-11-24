"""Build rights-aware, narration-aligned media plans for Creator workflows."""
from __future__ import annotations

import math
from typing import Any

from muse.media_compositor import build_scene_asset_bundle, compose_timeline, style_filter
from muse.media_search import MediaSearchBlocked


def _normalise_narration_segments(segments: Any) -> list[dict[str, Any]]:
    if not isinstance(segments, list) or not segments or len(segments) > 100:
        raise ValueError("narration_segments must be a non-empty list of at most 100 items")
    normalized: list[dict[str, Any]] = []
    cursor = 0.0
    for index, raw in enumerate(segments):
        if not isinstance(raw, dict):
            raise ValueError(f"narration segment {index} must be an object")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError(f"narration segment {index} requires text")
        if "start" in raw or "end" in raw:
            start, end = raw.get("start"), raw.get("end")
            if (
                isinstance(start, bool) or not isinstance(start, (int, float))
                or isinstance(end, bool) or not isinstance(end, (int, float))
                or not math.isfinite(float(start)) or not math.isfinite(float(end))
            ):
                raise ValueError(f"narration segment {index} requires finite start and end")
            start, end = float(start), float(end)
        else:
            duration = raw.get("estimated_duration_seconds")
            if (
                isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration)) or not 0 < float(duration) <= 3600
            ):
                raise ValueError(f"narration segment {index} requires a positive duration")
            start, end = cursor, cursor + float(duration)
        if not 0 <= start < end <= 3600:
            raise ValueError(f"narration segment {index} has an invalid time range")
        normalized.append({
            "text": text.strip(),
            "visual": str(raw.get("visual") or "")[:500],
            "keywords": [str(item)[:100] for item in raw.get("keywords", [])[:20]]
            if isinstance(raw.get("keywords", []), list) else [],
            "topic": str(raw.get("topic") or "")[:300],
            "start": start,
            "end": end,
        })
        cursor = end
    return normalized


def build_creator_media_plan(
    search_engine: Any,
    *,
    query: str,
    style: str,
    narration_segments: list[dict[str, Any]],
    fps: int = 30,
    limit: int = 20,
    min_width: int = 1280,
    min_height: int = 720,
    target_ratio: float | None = 16 / 9,
) -> dict[str, Any]:
    """Search and match scene visuals without granting rights or dispatching work."""
    if not callable(getattr(search_engine, "search_images", None)):
        raise TypeError("search_engine must provide search_images")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("query must contain 1-500 characters")
    style_filter(style)
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise ValueError("fps must be between 1 and 60")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if isinstance(min_width, bool) or not isinstance(min_width, int) or min_width < 0:
        raise ValueError("min_width must be a non-negative integer")
    if isinstance(min_height, bool) or not isinstance(min_height, int) or min_height < 0:
        raise ValueError("min_height must be a non-negative integer")
    if target_ratio is not None and (
        isinstance(target_ratio, bool) or not isinstance(target_ratio, (int, float))
        or not math.isfinite(float(target_ratio)) or target_ratio <= 0
    ):
        raise ValueError("target_ratio must be positive or None")

    normalized = _normalise_narration_segments(narration_segments)
    try:
        bundle = build_scene_asset_bundle(
            search_engine,
            query.strip(),
            style,
            normalized,
            limit=limit,
            min_width=min_width,
            min_height=min_height,
            target_ratio=target_ratio,
        )
    except MediaSearchBlocked:
        return {
            "status": "blocked_search_provider",
            "error_code": "media_search_unavailable",
            "assets": [],
            "scene_matches": [],
            "timeline": None,
            "fallback_required": True,
            "commercial_use_allowed": False,
            "publishable": False,
            "auto_publish": False,
            "render_dispatched": False,
            "rights": {"rights_review_required": True, "unreviewed_asset_count": 0},
            "provenance": {"accepted_count": 0, "candidate_count": 0},
        }

    assets = bundle["assets"]
    bundle_scenes = bundle.get("scenes")
    if not isinstance(assets, list) or not isinstance(bundle_scenes, list):
        raise ValueError("media search returned an invalid scene bundle")
    if len(bundle_scenes) != len(normalized):
        raise ValueError("media search scene mapping does not match narration segments")
    available_urls = {
        item.get("url") for item in assets
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    }
    scene_matches = []
    missing_scene_count = 0
    for index, item in enumerate(bundle_scenes):
        if not isinstance(item, dict) or item.get("segment_index") != index:
            raise ValueError("media search scene mapping is not in narration order")
        asset = item.get("asset")
        if asset is not None and (
            not isinstance(asset, dict) or asset.get("url") not in available_urls
        ):
            raise ValueError("media search scene references an unknown asset")
        if asset is None:
            missing_scene_count += 1
        scene_matches.append({
            "segment_index": index,
            "asset_match": dict(item.get("asset_match") or {}),
            "asset_url": asset.get("url") if asset else None,
            "source_ref": asset.get("source_ref") if asset else None,
            "status": "rights_review_required" if asset else "fallback_no_assets",
        })

    if not assets or missing_scene_count:
        return {
            "status": "needs_original_visuals",
            "error_code": None,
            "assets": assets,
            "scene_matches": scene_matches,
            "timeline": None,
            "fallback_required": True,
            "commercial_use_allowed": False,
            "publishable": False,
            "auto_publish": False,
            "render_dispatched": False,
            "rights": {
                "rights_review_required": True,
                "unreviewed_asset_count": len(assets),
                "missing_scene_count": missing_scene_count,
            },
            "provenance": bundle["provenance"],
        }

    timeline = compose_timeline(assets, normalized, style, fps=fps)
    for index, scene in enumerate(timeline["props"]["scenes"]):
        matched = scene_matches[index]
        scene["source_ref"] = matched["source_ref"] or scene.get("src")
        scene["rights_review_required"] = True
        scene["commercial_use_allowed"] = False
        scene["license_status"] = "unreviewed"
        scene["asset_match"] = matched["asset_match"]

    return {
        "status": "needs_rights_review",
        "error_code": None,
        "assets": assets,
        "scene_matches": scene_matches,
        "timeline": timeline,
        "fallback_required": False,
        "commercial_use_allowed": False,
        "publishable": False,
        "auto_publish": False,
        "render_dispatched": False,
        "rights": {"rights_review_required": True, "unreviewed_asset_count": len(assets)},
        "provenance": bundle["provenance"],
    }
