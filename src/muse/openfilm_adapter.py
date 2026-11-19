"""Export Muse shot/audio manifests into an editable OpenFilm project."""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any


class OpenFilmExportError(ValueError):
    """An OpenFilm project could not be safely exported."""


def _number(value: Any, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpenFilmExportError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise OpenFilmExportError(f"{name} must be finite and >= {minimum}")
    return result


def _safe_source(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise OpenFilmExportError("media source must be a non-empty path")
    source = Path(value).expanduser()
    if source.is_symlink() or not source.is_file():
        raise OpenFilmExportError(f"media source must be a regular non-symlink file: {source}")
    return source.resolve(strict=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_asset(source: Path, assets_dir: Path, index: int) -> tuple[str, str]:
    suffix = source.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        raise OpenFilmExportError("media source must have a simple file extension")
    digest = _file_sha256(source)
    name = f"asset-{index:04d}-{digest[:12]}{suffix}"
    target = assets_dir / name
    if not target.exists():
        shutil.copy2(source, target)
    return f"assets/{name}", digest


def _clip(src: str, at: float, start: float, duration: float, *, tag: str,
          volume: float | None = None, style: str | None = None) -> str:
    attrs = [
        f'src="{html.escape(src, quote=True)}#t={start:.3f},{start + duration:.3f}"',
        f'at="{at:.3f}"',
    ]
    if volume is not None:
        attrs.append(f'volume="{volume:.3f}"')
    if style:
        attrs.append(f'style="{html.escape(style, quote=True)}"')
    return f"<{tag} {' '.join(attrs)}></{tag}>"


def export_openfilm_project(
    output_dir: str | Path,
    *,
    shots: list[dict[str, Any]],
    audio_tracks: list[dict[str, Any]] | None = None,
    title_cues: list[dict[str, Any]] | None = None,
    title: str = "Muse Film",
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
) -> dict[str, Any]:
    """Create an editable OpenFilm folder with footage, timed title overlays and audio tracks.

    Each shot requires source, at, duration; optional in_point, id, title, volume.
    Audio tracks require source, kind, at, duration; optional in_point, volume.
    title_cues require start, end and text and become independently timed title overlays.
    Existing projects are never overwritten.
    """
    if not isinstance(shots, list) or not shots or len(shots) > 100:
        raise OpenFilmExportError("shots must contain between 1 and 100 entries")
    if not isinstance(audio_tracks if audio_tracks is not None else [], list):
        raise OpenFilmExportError("audio_tracks must be a list")
    if not isinstance(title_cues if title_cues is not None else [], list) or len(title_cues or []) > 500:
        raise OpenFilmExportError("title_cues must be a list containing at most 500 cues")

    for name, value, low, high in (("width", width, 320, 7680), ("height", height, 320, 7680), ("fps", fps, 1, 120)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise OpenFilmExportError(f"{name} must be an integer in [{low}, {high}]")
    if not isinstance(title, str) or not title.strip() or len(title) > 160:
        raise OpenFilmExportError("title must be a non-empty string of at most 160 characters")

    output = Path(output_dir).expanduser().absolute()
    if output.is_symlink():
        raise OpenFilmExportError("output directory must not be a symlink")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise OpenFilmExportError("output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    assets_dir = output / "assets"
    pages_dir = output / "pages"
    assets_dir.mkdir(exist_ok=True)
    pages_dir.mkdir(exist_ok=True)

    asset_index = 0
    manifest_assets: list[dict[str, str]] = []
    video_clips: list[str] = []
    title_tracks: list[str] = []
    duration_end = 0.0
    shot_ranges: list[tuple[float, float]] = []

    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise OpenFilmExportError("each shot must be an object")
        source = _safe_source(shot.get("source"))
        at = _number(shot.get("at"), "shot.at")
        duration = _number(shot.get("duration"), "shot.duration", minimum=0.001)
        in_point = _number(shot.get("in_point", 0), "shot.in_point")
        shot_id = shot.get("id", f"shot-{index + 1:03d}")
        if not isinstance(shot_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", shot_id):
            raise OpenFilmExportError("shot id contains unsupported characters")
        shot_ranges.append((at, at + duration))
        duration_end = max(duration_end, at + duration)

        asset_index += 1
        asset_ref, digest = _copy_asset(source, assets_dir, asset_index)
        manifest_assets.append({"role": "footage", "shot_id": shot_id, "source": str(source),
                                "asset": asset_ref, "sha256": digest})
        shot_volume = _number(shot.get("volume", 1.0), "shot.volume")
        if shot_volume > 4:
            raise OpenFilmExportError("shot.volume must be <= 4")
        video_clips.append(_clip(asset_ref, at, in_point, duration, tag="video", volume=shot_volume,
                                 style=f"left:0px;top:0px;width:{width}px;height:{height}px;object-fit:cover"))
        shot_title = shot.get("title")
        if shot_title is not None:
            if not isinstance(shot_title, str) or len(shot_title) > 200:
                raise OpenFilmExportError("shot title must be a string of at most 200 characters")
            page_name = f"{shot_id}.html"
            page_path = pages_dir / page_name
            if page_path.exists():
                raise OpenFilmExportError("shot ids must be unique")
            safe_title = json.dumps(shot_title, ensure_ascii=False).replace("</", "<\\/")
            page_path.write_text(
                "<!doctype html><html><head><meta charset=\"utf-8\">"
                f"<meta name=\"viewport\" content=\"width={width},height={height}\">"
                "<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}"
                "#title{position:absolute;left:8%;right:8%;bottom:14%;color:#fff;"
                "font:700 4.2vw/1.25 system-ui,sans-serif;text-shadow:0 3px 18px #000;"
                "opacity:0;transform:translateY(24px);will-change:opacity,transform}</style></head>"
                "<body><div id=\"title\"></div><script>const label="
                + safe_title + ";const node=document.getElementById('title');node.textContent=label;"
                "window.film={duration:" + f"{duration:.3f}" + ",frame(t){"
                "const p=Math.max(0,Math.min(1,t/Math.max(0.001," + f"{duration:.3f}" + ")));"
                "const a=Math.min(1,p*5);const b=Math.min(1,(" + f"{duration:.3f}" + "-t)*5);"
                "node.style.opacity=String(Math.min(a,b));"
                "node.style.transform='translateY('+((1-a)*24)+'px)';}};"
                "</script></body></html>",
                encoding="utf-8",
            )
            title_tracks.append(
                f'<section><iframe src="pages/{page_name}#t=0,{duration:.3f}" at="{at:.3f}" '
                f'style="left:0px;top:0px;width:{width}px;height:{height}px"></iframe></section>'
            )

    previous_title_end = 0.0
    for index, cue in enumerate(title_cues or [], start=1):
        if not isinstance(cue, dict) or set(cue) != {"start", "end", "text"}:
            raise OpenFilmExportError("title cue must contain only start, end and text")
        cue_start = _number(cue.get("start"), "title_cue.start")
        cue_end = _number(cue.get("end"), "title_cue.end")
        cue_text = cue.get("text")
        if (cue_start < previous_title_end - 0.001 or cue_end <= cue_start
                or not isinstance(cue_text, str) or not cue_text.strip() or len(cue_text) > 200):
            raise OpenFilmExportError("title cue timing or text is invalid")
        previous_title_end = cue_end
        duration_end = max(duration_end, cue_end)
        cue_duration = cue_end - cue_start
        page_name = f"subtitle-{index:03d}.html"
        page_path = pages_dir / page_name
        safe_title = json.dumps(cue_text.strip(), ensure_ascii=False).replace("</", "<\\/")
        page_path.write_text(
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width={width},height={height}\">"
            "<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}"
            "#title{position:absolute;left:8%;right:8%;bottom:12%;color:#fff;"
            "font:700 3.4vw/1.3 system-ui,sans-serif;text-align:center;"
            "text-shadow:0 3px 18px #000,0 1px 4px #000;opacity:0;}</style></head>"
            "<body><div id=\"title\"></div><script>const label=" + safe_title + ";"
            "const node=document.getElementById('title');node.textContent=label;"
            "window.film={duration:" + f"{cue_duration:.3f}" + ",frame(t){"
            "const p=Math.max(0,Math.min(1,t/Math.max(0.001," + f"{cue_duration:.3f}" + ")));"
            "const a=Math.min(1,p*8);const b=Math.min(1,(" + f"{cue_duration:.3f}" + "-t)*8);"
            "node.style.opacity=String(Math.min(a,b));}};</script></body></html>",
            encoding="utf-8",
        )
        title_tracks.append(
            f'<section><iframe src="pages/{page_name}#t=0,{cue_duration:.3f}" at="{cue_start:.3f}" '
            f'style="left:0px;top:0px;width:{width}px;height:{height}px"></iframe></section>'
        )

    ordered_ranges = sorted(shot_ranges)
    for previous, current in zip(ordered_ranges, ordered_ranges[1:]):
        if current[0] < previous[1] - 0.001:
            raise OpenFilmExportError("footage clips overlap on the same OpenFilm track")

    track_sections = title_tracks + ["<section>" + "".join(video_clips) + "</section>"]

    audio_sections: list[str] = []
    audio_kinds: list[str] = []
    for item in audio_tracks or []:
        if not isinstance(item, dict):
            raise OpenFilmExportError("each audio track must be an object")
        source = _safe_source(item.get("source"))
        at = _number(item.get("at"), "audio.at")
        duration = _number(item.get("duration"), "audio.duration", minimum=0.001)
        in_point = _number(item.get("in_point", 0), "audio.in_point")
        volume = _number(item.get("volume", 1), "audio.volume")
        if volume > 4:
            raise OpenFilmExportError("audio.volume must be <= 4")
        kind = item.get("kind", "sfx")
        if kind not in {"narration", "dialogue", "music", "ambience", "sfx"}:
            raise OpenFilmExportError("audio kind must be narration, dialogue, music, ambience or sfx")
        asset_index += 1
        asset_ref, digest = _copy_asset(source, assets_dir, asset_index)
        manifest_assets.append({"role": kind, "source": str(source), "asset": asset_ref, "sha256": digest})
        audio_sections.append("<section>" + _clip(asset_ref, at, in_point, duration, tag="audio", volume=volume) + "</section>")
        if kind not in audio_kinds:
            audio_kinds.append(kind)
        duration_end = max(duration_end, at + duration)
    track_sections.extend(audio_sections)
    film_html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width={width},height={height}\">"
        f"<title>{html.escape(title)}</title>"
        "<style>html,body{margin:0;width:100%;height:100%;background:#000}</style>"
        "</head><body>" + "".join(track_sections) + "</body></html>"
    )
    (output / "film.html").write_text(film_html, encoding="utf-8")
    manifest = {
        "schema_version": 1, "engine": "openfilm", "engine_version": "0.3.2",
        "title": title, "width": width, "height": height, "fps": fps,
        "duration_seconds": round(duration_end, 3), "assets": manifest_assets,
        "tracks": ["titles", "footage"] + audio_kinds,
        "subtitle_cue_count": len(title_cues or []),
        "creator_review_state": "content_review_pending",
    }
    manifest_path = output / "muse-openfilm-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"project_dir": str(output), "film_html": str(output / "film.html"), "manifest": str(manifest_path),
            "duration_seconds": manifest["duration_seconds"], "asset_count": len(manifest_assets),
            "track_count": len(manifest["tracks"]), "subtitle_cue_count": len(title_cues or []),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}