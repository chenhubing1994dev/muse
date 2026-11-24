"""Narration-aligned media timeline composition for MuseSceneTimeline."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from muse.smart_crop import to_ken_burns

_STYLE_FILTERS = {
    "hk-retro": {
        "css": "filter: sepia(0.35) saturate(1.25) contrast(1.12); --vhs-grain: 0.12; --warm-tint: #d98a4a",
        "ffmpeg": "eq=saturation=1.25:contrast=1.12,colorbalance=rs=0.12:gs=0.03:bs=-0.10,vhs Grain=0.12",
    },
    "pixel": {
        "css": "image-rendering: pixelated; filter: contrast(1.18) saturate(1.1)",
        "ffmpeg": "scale=480:-2:flags=neighbor,scale=iw*4:ih*4:flags=neighbor,eq=contrast=1.18",
    },
    "cosmic": {
        "css": "filter: saturate(1.4) brightness(1.1); --glow: 0 0 32px #7a8cff",
        "ffmpeg": "eq=saturation=1.4:brightness=0.06,gblur=sigma=8,blend=all_mode=screen:all_opacity=0.35",
    },
}
_CAMERA_CYCLE = ("push_in", "drift_left", "pull_out", "drift_right")


def style_filter(style: str) -> dict[str, str]:
    """Return CSS and FFmpeg descriptions for an approved visual style."""
    if style not in _STYLE_FILTERS:
        raise ValueError("style must be hk-retro, pixel or cosmic")
    preset = _STYLE_FILTERS[style]
    return {"style": style, "css": preset["css"], "ffmpeg": preset["ffmpeg"]}


def _asset_dict(asset: Any) -> dict[str, Any]:
    if is_dataclass(asset) and not isinstance(asset, type):
        return asdict(asset)
    if isinstance(asset, dict):
        return dict(asset)
    raise TypeError("each asset must be a MediaAsset or dict")


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9]{2,}")
_KEYWORD_ALIASES = {
    "space": {"space", "cosmic", "universe", "galaxy", "nebula", "太空", "宇宙", "星空", "星系"},
    "food": {"food", "dish", "meal", "restaurant", "食物", "美食", "餐饮", "菜式"},
    "city": {"city", "street", "urban", "skyline", "城市", "街道", "都市", "天际线"},
    "technology": {"technology", "tech", "computer", "digital", "科技", "技术", "电脑", "数码"},
}


def _keyword_tokens(value: Any) -> set[str]:
    text = str(value or "").casefold()
    tokens = set(_WORD_RE.findall(text))
    for run in _CJK_RE.findall(text):
        if len(run) <= 4:
            tokens.add(run)
        tokens.update(run[index:index + size] for size in (2, 3) for index in range(max(0, len(run) - size + 1)))
    for canonical, aliases in _KEYWORD_ALIASES.items():
        if tokens.intersection(aliases):
            tokens.add(canonical)
    return tokens


def match_assets_to_segments(
    assets: Iterable[Any], segments: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Match scene assets by explicit keyword overlap; report deterministic fallbacks.

    This is a transparent lexical matcher, not an embedding or visual-understanding
    model. It prefers one distinct asset per scene where possible and never upgrades
    unknown/unreviewed rights metadata into commercial permission.
    """
    candidates = [_asset_dict(asset) for asset in assets]
    if not candidates:
        raise ValueError("at least one asset is required")
    if not isinstance(segments, list) or not segments or len(segments) > 100:
        raise ValueError("segments must be a non-empty list of at most 100 items")
    asset_tokens = [
        _keyword_tokens(" ".join(str(asset.get(key) or "") for key in ("title", "snippet", "description", "tags")))
        for asset in candidates
    ]
    segment_tokens = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("each segment must be an object")
        segment_tokens.append(_keyword_tokens(" ".join(str(segment.get(key) or "") for key in ("text", "visual", "keywords", "topic"))))
    edges = []
    for scene_index, tokens in enumerate(segment_tokens):
        for asset_index, tokens_for_asset in enumerate(asset_tokens):
            overlap = tokens.intersection(tokens_for_asset)
            if overlap:
                edges.append((len(overlap), scene_index, asset_index, sorted(overlap)))
    assignments: dict[int, tuple[int, int, list[str]]] = {}
    used_assets: set[int] = set()
    for score, scene_index, asset_index, overlap in sorted(edges, key=lambda item: (-item[0], item[1], item[2])):
        if scene_index not in assignments and asset_index not in used_assets:
            assignments[scene_index] = (asset_index, score, overlap)
            used_assets.add(asset_index)
    remaining = [index for index in range(len(candidates)) if index not in used_assets]
    result = []
    for scene_index in range(len(segments)):
        if scene_index in assignments:
            asset_index, score, overlap = assignments[scene_index]
            match = {"status": "keyword_matched", "score": score, "matched_terms": overlap, "asset_index": asset_index}
        else:
            if remaining:
                asset_index = remaining.pop(0)
                status = "fallback_unmatched"
            else:
                asset_index = scene_index % len(candidates)
                status = "reused_fallback"
            match = {"status": status, "score": 0, "matched_terms": [], "asset_index": asset_index}
        result.append((candidates[asset_index], match))
    return result


def build_scene_asset_bundle(
    search_engine: Any,
    query: str,
    style: str,
    segments: list[dict[str, Any]],
    *,
    limit: int = 20,
    min_width: int = 1280,
    min_height: int = 720,
    target_ratio: float | None = 16 / 9,
) -> dict[str, Any]:
    """Search, filter, de-duplicate and match real media candidates to scenes.

    The search engine is injected so this boundary can be tested offline. A
    provider's license label is preserved as provenance, never treated as
    commercial-use approval. Missing candidates and unmatched scenes remain
    explicit fallbacks instead of being presented as successful matches.
    """
    if not callable(getattr(search_engine, "search_images", None)):
        raise TypeError("search_engine must provide search_images")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("query must contain 1-500 characters")
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
    if not isinstance(segments, list) or not segments or len(segments) > 100:
        raise ValueError("segments must be a non-empty list of at most 100 items")

    candidates = search_engine.search_images(
        query.strip(), style, limit=limit, min_width=min_width,
        target_ratio=target_ratio,
    )
    if not isinstance(candidates, (list, tuple)):
        raise TypeError("search_images must return a list or tuple")
    accepted: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for candidate in candidates:
        asset = _asset_dict(candidate)
        url = str(asset.get("url") or "").strip()
        parts = urlsplit(url)
        if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
            continue
        if parts.username or parts.password:
            continue
        canonical_url = urlunsplit((
            parts.scheme.casefold(), parts.netloc.casefold(), parts.path,
            parts.query, "",
        ))
        if canonical_url in seen_urls:
            continue
        width, height = asset.get("width"), asset.get("height")
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0 or width < min_width:
            continue
        if isinstance(height, bool) or not isinstance(height, int) or height <= 0 or height < min_height:
            continue
        if target_ratio is not None and abs(width / height - target_ratio) > 0.02:
            continue
        seen_urls.add(canonical_url)
        asset["url"] = url
        asset["license"] = str(asset.get("license") or "unknown")
        asset["license_status"] = "unknown" if asset["license"].casefold() == "unknown" else "unreviewed"
        asset["commercial_use_allowed"] = False
        asset["rights_review_required"] = True
        accepted.append(asset)
        if len(accepted) >= limit:
            break

    if accepted:
        matches = match_assets_to_segments(accepted, segments)
        scene_records = [
            {"segment_index": index, "asset": asset, "asset_match": match}
            for index, (asset, match) in enumerate(matches)
        ]
    else:
        scene_records = [
            {
                "segment_index": index,
                "asset": None,
                "asset_match": {
                    "status": "fallback_no_assets", "score": 0,
                    "matched_terms": [], "asset_index": None,
                },
            }
            for index, _segment in enumerate(segments)
        ]
    return {
        "query": query.strip(),
        "style": style,
        "assets": accepted,
        "scenes": scene_records,
        "provenance": {
            "provider": type(search_engine).__name__,
            "candidate_count": len(candidates),
            "accepted_count": len(accepted),
            "rights_review_required": True,
            "commercial_use_allowed": False,
        },
    }


def compose_timeline(
    assets: Iterable[Any],
    narration_segments: list[dict[str, Any]],
    style: str,
    *,
    fps: int = 30,
) -> dict[str, Any]:
    """Map narration intervals onto bounded media scenes with camera/VFX presets."""
    preset = style_filter(style)
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise ValueError("fps must be between 1 and 60")
    source_assets = [_asset_dict(asset) for asset in assets]
    if not source_assets:
        raise ValueError("at least one asset is required")
    if not isinstance(narration_segments, list) or not narration_segments:
        raise ValueError("narration_segments must be a non-empty list")
    if len(narration_segments) > 100:
        raise ValueError("at most 100 narration segments are accepted")

    matched_assets = match_assets_to_segments(source_assets, narration_segments)
    scenes: list[dict[str, Any]] = []
    cursor = 0
    for index, segment in enumerate(narration_segments):
        if not isinstance(segment, dict):
            raise ValueError("each narration segment must be a dict")
        start = float(segment.get("start", cursor))
        end = float(segment.get("end", start + 2.0))
        if not 0 <= start < end <= 3600:
            raise ValueError("narration segment time range is invalid")
        duration = end - start
        frames = max(1, round(duration * fps))
        asset, asset_match = matched_assets[index]
        effect = _CAMERA_CYCLE[index % len(_CAMERA_CYCLE)]
        url = str(asset.get("url") or "")
        source_ref = str(asset.get("source_ref") or url)
        plan = to_ken_burns(url, max(1.0, duration), effect, fps=fps)
        vfx = f"{preset['ffmpeg']};{plan['filter']}"
        scenes.append({
            "src": source_ref,
            "sourceUrl": url,
            "startFrame": cursor,
            "durationInFrames": frames,
            "cameraMove": effect,
            "vfx": vfx,
            "styleFilter": preset["css"],
            "narration": str(segment.get("text") or "")[:2000],
            "license": str(asset.get("license") or "unknown"),
            "asset_match": asset_match,
            "rights_review_required": True,
        })
        cursor += frames
    return {
        "compositionId": "MuseSceneTimeline",
        "props": {
            "scenes": scenes,
            "width": 1920, "height": 1080, "fps": fps,
            "style": style,
            "styleFilter": preset,
            "transitionDurationInFrames": min(12, max(1, min(scene["durationInFrames"] for scene in scenes) // 4)),
            "totalDurationInFrames": cursor,
        },
        "style_filter": preset,
    }


class MediaAssetStore:
    """SQLite-backed, bounded search manifest persistence."""

    def __init__(self, database: str | Path) -> None:
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.executescript(
            """CREATE TABLE IF NOT EXISTS media_manifests (
                   manifest_id TEXT PRIMARY KEY, query TEXT NOT NULL, style TEXT NOT NULL,
                   created_at TEXT NOT NULL, asset_count INTEGER NOT NULL, payload TEXT NOT NULL
               );"""
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "MediaAssetStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save_manifest(self, query: str, assets: Iterable[Any], *, style: str = "hk-retro") -> dict[str, Any]:
        records = [_asset_dict(asset) for asset in assets][:20]
        manifest_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps({
            "schema_version": 1, "manifest_id": manifest_id, "query": query[:500],
            "style": style, "created_at": created_at,
            "asset_count": len(records), "assets": records,
            "bounded": {"max_assets": 20, "rights_review_required": True},
        }, ensure_ascii=False)
        self.connection.execute(
            "INSERT INTO media_manifests VALUES (?, ?, ?, ?, ?, ?)",
            (manifest_id, query[:500], style, created_at, len(records), payload),
        )
        self.connection.commit()
        return json.loads(payload)

    def load_manifest(self, manifest_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload FROM media_manifests WHERE manifest_id=?", (manifest_id,)
        ).fetchone()
        if row is None:
            raise KeyError(manifest_id)
        return json.loads(row["payload"])
