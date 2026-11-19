"""Narration-aligned media timeline composition for MuseSceneTimeline."""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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
        asset = source_assets[index % len(source_assets)]
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
