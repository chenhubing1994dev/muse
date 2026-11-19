"""Export a verified Muse video product as an editable OpenFilm multitrack project."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
import shutil
from typing import Any

from muse.openfilm_adapter import export_openfilm_project
from muse.subtitle_review import parse_srt_cues
from muse.video_product_builder import VideoProductBuilder, VideoProductError


class VideoEditableExportError(ValueError):
    """A verified video product cannot be exported as an editable project."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VideoEditableExportError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise VideoEditableExportError(f"{field} must be finite and positive")
    return number


def _nonnegative_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VideoEditableExportError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise VideoEditableExportError(f"{field} must be finite and non-negative")
    return number


def _safe_member(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise VideoEditableExportError(f"{field} archive path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise VideoEditableExportError(f"{field} archive path is unsafe")
    return value


def _read_archive_asset(archive: zipfile.ZipFile, member: str, destination: Path) -> Path:
    try:
        info = archive.getinfo(member)
    except KeyError as exc:
        raise VideoEditableExportError("required audio asset is missing from product ZIP") from exc
    if info.is_dir() or info.file_size <= 0 or info.file_size > 8 * 1024 * 1024 * 1024:
        raise VideoEditableExportError("audio asset size is invalid")
    with archive.open(member, "r") as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
    if destination.stat().st_size != info.file_size:
        raise VideoEditableExportError("audio asset size changed while extracting")
    return destination


def export_verified_video_product(
    products_root: str | Path,
    initiative_id: str,
    version: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Export a verified product to editable footage, subtitle/title and separate audio tracks.

    The source MP4's embedded audio is muted in the editable timeline. Narration and
    music are restored as separate tracks from the integrity-checked product ZIP.
    This is an editable handoff, not a new render or publication approval.
    """
    verified = VideoProductBuilder.verify_path(products_root, initiative_id, version)
    if not isinstance(verified, dict) or verified.get("validated") is not True:
        raise VideoEditableExportError("source video product has not passed integrity verification")

    root = Path(products_root).expanduser().absolute()
    product_dir = root / initiative_id / version
    initiative_root = root / initiative_id
    if (root.is_symlink() or initiative_root.is_symlink() or product_dir.is_symlink()
            or not product_dir.is_dir() or product_dir.resolve().parent != initiative_root.resolve()):
        raise VideoEditableExportError("verified product directory is unsafe")
    try:
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoEditableExportError("video product manifest or storyboard is invalid") from exc
    if not isinstance(manifest, dict) or not isinstance(storyboard, dict):
        raise VideoEditableExportError("video product metadata must be JSON objects")
    expected_hashes = {
        "manifest.json": verified.get("manifest_sha256"),
        "storyboard.json": verified.get("storyboard_sha256"),
        "video.mp4": verified.get("mp4_sha256"),
        "product.zip": verified.get("package_sha256"),
    }
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        raise VideoEditableExportError("video product file hash map is invalid")
    if "subtitles.srt" in manifest_files:
        expected_hashes["subtitles.srt"] = manifest_files["subtitles.srt"]
    for name, expected_hash in expected_hashes.items():
        candidate = product_dir / name
        if (candidate.is_symlink() or not isinstance(expected_hash, str) or len(expected_hash) != 64
                or _sha256_file(candidate) != expected_hash):
            raise VideoEditableExportError(f"verified product artifact changed after verification: {name}")

    metadata = manifest.get("video_metadata")
    if not isinstance(metadata, dict):
        raise VideoEditableExportError("verified video metadata is missing")
    duration = _positive_number(metadata.get("duration_seconds"), "duration_seconds")
    width, height = metadata.get("width"), metadata.get("height")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 320 or value > 7680
           for value in (width, height)):
        raise VideoEditableExportError("verified video dimensions are invalid")
    fps_value = _positive_number(metadata.get("fps"), "fps")
    fps = int(round(fps_value))
    if not 1 <= fps <= 120 or abs(fps_value - fps) > 0.01:
        raise VideoEditableExportError("OpenFilm requires an integer frame rate between 1 and 120")

    video_path = product_dir / "video.mp4"
    if video_path.is_symlink() or not video_path.is_file():
        raise VideoEditableExportError("verified MP4 is missing or unsafe")

    title_cues: list[dict[str, Any]] = []
    subtitle_path = product_dir / "subtitles.srt"
    if subtitle_path.exists():
        if subtitle_path.is_symlink() or not subtitle_path.is_file():
            raise VideoEditableExportError("subtitle file is unsafe")
        try:
            parsed = parse_srt_cues(subtitle_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise VideoEditableExportError("subtitle file could not be parsed") from exc
        for cue in parsed:
            if cue["end"] > duration + 0.05:
                raise VideoEditableExportError("subtitle cue extends beyond the final video duration")
            if len(cue["text"]) > 200:
                raise VideoEditableExportError("subtitle cue exceeds OpenFilm's 200-character title limit")
            title_cues.append({"start": cue["start"], "end": cue["end"], "text": cue["text"]})
        subtitle_expected_hash = manifest_files.get("subtitles.srt")
        if not isinstance(subtitle_expected_hash, str) or _sha256_file(subtitle_path) != subtitle_expected_hash:
            raise VideoEditableExportError("subtitle file changed after product verification")

    narration = manifest.get("narration")
    background_music = manifest.get("background_music")
    audio_tracks: list[dict[str, Any]] = []
    output = Path(output_dir).expanduser().absolute()
    zip_output = output.with_name(output.name + ".zip")
    if zip_output.exists() or zip_output.is_symlink():
        raise VideoEditableExportError("editable project ZIP already exists; refusing to overwrite")
    for parent in (output.parent, *output.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise VideoEditableExportError("editable project output path contains a symlink")

    with tempfile.TemporaryDirectory(prefix="muse-openfilm-audio-") as temporary:
        temporary_root = Path(temporary)
        with zipfile.ZipFile(product_dir / "product.zip", "r") as archive:
            if narration is not None:
                if not isinstance(narration, dict):
                    raise VideoEditableExportError("narration manifest is invalid")
                member = _safe_member(narration.get("audio_file"), "narration")
                suffix = Path(member).suffix.casefold()
                if suffix not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
                    raise VideoEditableExportError("narration audio format is unsupported")
                voice = _read_archive_asset(archive, member, temporary_root / ("voiceover" + suffix))
                audio_tracks.append({
                    "source": voice, "kind": "narration", "at": 0.0,
                    "duration": duration, "volume": 1.0,
                })
            if background_music is not None:
                if narration is None:
                    raise VideoEditableExportError("background music cannot be exported without its narration track")
                if not isinstance(background_music, dict):
                    raise VideoEditableExportError("background music manifest is invalid")
                member = _safe_member(background_music.get("archive_path"), "background music")
                suffix = Path(member).suffix.casefold()
                if suffix not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
                    raise VideoEditableExportError("background music format is unsupported")
                music = _read_archive_asset(archive, member, temporary_root / ("background" + suffix))
                mix = background_music.get("mix")
                music_gain = mix.get("music_gain", 0.22) if isinstance(mix, dict) else 0.22
                music_gain = _nonnegative_number(music_gain, "music_gain")
                if music_gain > 4:
                    raise VideoEditableExportError("music_gain exceeds OpenFilm's supported volume")
                audio_tracks.append({
                    "source": music, "kind": "music", "at": 0.0,
                    "duration": duration, "volume": music_gain,
                })

        if _sha256_file(product_dir / "product.zip") != verified.get("package_sha256"):
            raise VideoEditableExportError("product ZIP changed while audio tracks were being extracted")

        exported = export_openfilm_project(
            output,
            title=str(manifest.get("title") or storyboard.get("title") or "Muse Film"),
            width=width, height=height, fps=fps,
            shots=[{
                "id": "final-video", "source": video_path, "at": 0.0,
                "in_point": 0.0, "duration": duration, "volume": 0.0 if audio_tracks else 1.0,
            }],
            audio_tracks=audio_tracks,
            title_cues=title_cues,
        )

    manifest_path = Path(exported["manifest"])
    openfilm_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    footage_assets = [item for item in openfilm_manifest.get("assets", []) if item.get("role") == "footage"]
    if len(footage_assets) != 1 or footage_assets[0].get("sha256") != verified.get("mp4_sha256"):
        raise VideoEditableExportError("source MP4 changed while it was copied into the editable project")
    openfilm_manifest["source_product"] = {
        "initiative_id": initiative_id,
        "version_id": version,
        "manifest_sha256": verified.get("manifest_sha256"),
        "mp4_sha256": verified.get("mp4_sha256"),
        "editable_source_audio": "muted; narration/music are separate tracks" if audio_tracks else "original MP4 audio retained",
    }
    openfilm_manifest["mix_note"] = (
        "The original product mix may use FFmpeg sidechain ducking and loudness normalization. "
        "This editable handoff preserves source tracks and nominal gains; it does not recreate "
        "dynamic sidechain automation. Review the mix in the editor."
        if background_music is not None else
        "Narration is a separate editable track; review sync and loudness in the editor."
        if narration is not None else
        "The original MP4 audio is retained as part of the footage clip."
    )
    manifest_path.write_text(json.dumps(openfilm_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(zip_output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_symlink():
                raise VideoEditableExportError("editable project contains a symlink")
            if path.is_file():
                archive.write(path, path.relative_to(output).as_posix())
    with zipfile.ZipFile(zip_output, "r") as archive:
        if archive.testzip() is not None:
            raise VideoEditableExportError("editable project ZIP CRC verification failed")

    zip_digest = _sha256_file(zip_output)
    return {
        "status": "exported",
        "project_dir": str(output),
        "project_zip": str(zip_output),
        "film_html": str(output / "film.html"),
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "zip_sha256": zip_digest,
        "duration_seconds": duration,
        "resolution": f"{width}x{height}",
        "fps": fps,
        "subtitle_cue_count": len(title_cues),
        "audio_tracks": [item["kind"] for item in audio_tracks],
        "asset_count": exported["asset_count"],
        "source_product": f"{initiative_id}/{version}",
        "publication_approved": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a verified Muse video product to editable OpenFilm")
    parser.add_argument("--products-root", default="data/products")
    parser.add_argument("--initiative-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        result = export_verified_video_product(
            args.products_root, args.initiative_id, args.version, args.output_dir,
        )
    except (OSError, ValueError, VideoProductError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
