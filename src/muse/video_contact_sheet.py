"""Generate bounded local video contact sheets for human visual review."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable


def _signature(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def _probe(path: Path, ffprobe_path: str, runner: Callable) -> dict:
    result = runner([ffprobe_path, "-v", "error", "-show_streams", "-show_format",
                     "-of", "json", str(path)], capture_output=True, text=True,
                    timeout=30, check=False)
    if result.returncode != 0:
        raise RuntimeError("FFprobe failed: " + (result.stderr or "")[-800:])
    try:
        payload = json.loads(result.stdout or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("FFprobe returned invalid JSON") from exc
    streams = [item for item in payload.get("streams", []) if item.get("codec_type") == "video"]
    if len(streams) != 1:
        raise RuntimeError("contact sheet must contain exactly one video stream")
    return streams[0]


def build_contact_sheet_command(source: str | Path, output: str | Path, *,
                                duration_seconds: float, samples: int = 12,
                                columns: int = 4, thumbnail_width: int = 320,
                                ffmpeg_path: str = "ffmpeg") -> list[str]:
    if isinstance(samples, bool) or not isinstance(samples, int) or not 3 <= samples <= 30:
        raise ValueError("samples must be an integer between 3 and 30")
    if isinstance(columns, bool) or not isinstance(columns, int) or not 1 <= columns <= 6:
        raise ValueError("columns must be an integer between 1 and 6")
    if isinstance(thumbnail_width, bool) or not isinstance(thumbnail_width, int) or not 96 <= thumbnail_width <= 640:
        raise ValueError("thumbnail_width must be between 96 and 640")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be finite and positive")
    rows = math.ceil(samples / columns)
    rate = samples / float(duration_seconds)
    graph = (f"fps={rate:.9f},scale={thumbnail_width}:-2:force_original_aspect_ratio=decrease,"
             f"setsar=1,tile={columns}x{rows}:padding=8:margin=8:color=black")
    return [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i",
            str(Path(source).expanduser()), "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", graph, "-frames:v", "1", "-f", "image2", "-vcodec", "png",
            "-y", str(Path(output).expanduser())]


def create_contact_sheet(source: str | Path, output: str | Path, *,
                         samples: int = 12, columns: int = 4,
                         thumbnail_width: int = 320, dry_run: bool = False,
                         ffmpeg_path: str = "ffmpeg", ffprobe_path: str | None = None,
                         runner: Callable = subprocess.run,
                         timeout_seconds: int = 300) -> dict[str, object]:
    source_path, target = Path(source).expanduser(), Path(output).expanduser()
    if source_path.is_symlink() or not source_path.is_file() or source_path.stat().st_size <= 0:
        raise ValueError("source must be a non-empty regular local file")
    if target.suffix.casefold() != ".png" or target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("output must be a new PNG in an existing directory")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    ffprobe = ffprobe_path or shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe executable is unavailable")
    signature = _signature(source_path)
    stream = _probe(source_path, ffprobe, runner)
    if _signature(source_path) != signature:
        raise RuntimeError("source changed during contact sheet probe")
    try:
        duration = float(stream.get("duration") or 0)
        if duration <= 0:
            format_duration = None
            # Some containers expose duration only at format level.
            probed = runner([ffprobe, "-v", "error", "-show_format", "-of", "json",
                             str(source_path)], capture_output=True, text=True, timeout=30, check=False)
            if probed.returncode == 0:
                format_duration = float((json.loads(probed.stdout or "{}").get("format") or {}).get("duration") or 0)
            duration = float(format_duration or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        duration = 0
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("source duration is unavailable")
    command = build_contact_sheet_command(source_path, target, duration_seconds=duration,
                                          samples=samples, columns=columns,
                                          thumbnail_width=thumbnail_width, ffmpeg_path=ffmpeg_path)
    if dry_run:
        return {"status": "planned", "command_preview": command, "duration_seconds": duration,
                "samples": samples, "columns": columns, "writes_files": False,
                "visual_review_required": True}
    with tempfile.TemporaryDirectory(prefix=".muse-contact-", dir=str(target.parent)) as temporary:
        temporary_png = Path(temporary) / "contact-sheet.png"
        command[-1] = str(temporary_png)
        try:
            rendered = runner(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("contact sheet generation timed out") from exc
        if rendered.returncode != 0 or not temporary_png.is_file() or temporary_png.stat().st_size < 32:
            raise RuntimeError("FFmpeg contact sheet failed: " + (rendered.stderr or "")[-1000:])
        if temporary_png.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("contact sheet output is not a valid PNG signature")
        image_stream = _probe(temporary_png, ffprobe, runner)
        width, height = image_stream.get("width"), image_stream.get("height")
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise RuntimeError("contact sheet dimensions could not be verified")
        decoded = runner(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i",
             str(temporary_png), "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        if decoded.returncode != 0:
            raise RuntimeError("contact sheet failed full image decode verification")
        if _signature(source_path) != signature:
            raise RuntimeError("source changed during contact sheet generation")
        os.link(temporary_png, target)
    return {"status": "completed", "output": str(target.resolve()), "bytes": target.stat().st_size,
            "width": width, "height": height, "samples_requested": samples,
            "columns": columns, "duration_seconds": duration,
            "visual_review_required": True, "source_unchanged": True, "image_decode": "passed",
            "note": "Contact sheet is a review aid; it does not approve crop, continuity, rights, or release."}
