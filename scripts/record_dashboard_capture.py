"""Capture a clean fullscreen Muse Dashboard recording on Windows.

The browser must already be fullscreen on a non-sensitive Dashboard page.
This utility records the visible desktop only; it does not navigate or click.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def capture(ffmpeg: Path, ffprobe: Path, output: Path, duration: int = 12, fps: int = 15) -> dict:
    ffmpeg, ffprobe, output = ffmpeg.resolve(), ffprobe.resolve(), output.resolve()
    if not ffmpeg.is_file() or not ffprobe.is_file():
        raise FileNotFoundError("FFmpeg and FFprobe must both exist")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite recording: {output}")
    if not 3 <= duration <= 60 or not 10 <= fps <= 30:
        raise ValueError("duration must be 3..60 seconds and fps 10..30")
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = output.with_suffix(output.suffix + ".log")
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-f", "gdigrab",
        "-framerate", str(fps), "-draw_mouse", "0", "-i", "desktop",
        "-t", str(duration), "-vf",
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x08101d,format=yuv420p",
        "-an", "-c:v", "h264_amf", "-quality", "quality", "-b:v", "4M",
        "-pix_fmt", "yuv420p", "-r", str(fps), "-movflags", "+faststart", str(output),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(command, capture_output=True, text=True, timeout=duration + 90,
                                    creationflags=flags, check=False)
            log.write(result.stdout or "")
            log.write(result.stderr or "")
        if result.returncode:
            raise RuntimeError(f"FFmpeg exited {result.returncode}; see {log_path}")
        probe = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_entries",
             "format=duration,size:stream=codec_type,width,height,avg_frame_rate,codec_name",
             "-of", "json", str(output)], capture_output=True, text=True, timeout=30, check=False,
            creationflags=flags,
        )
        if probe.returncode:
            raise RuntimeError(f"FFprobe failed: {probe.stderr[-1000:]}")
        data = json.loads(probe.stdout)
        video = next((item for item in data.get("streams", []) if item.get("codec_type") == "video"), None)
        actual_duration = float(data.get("format", {}).get("duration", 0))
        if (not video or video.get("codec_name") != "h264" or video.get("width") != 1920
                or video.get("height") != 1080 or abs(actual_duration - duration) > 0.5):
            raise RuntimeError("recording failed H.264/1920x1080/duration acceptance")
        return {"status": "completed", "output": str(output), "bytes": output.stat().st_size,
                "duration_seconds": round(actual_duration, 3), "width": 1920, "height": 1080,
                "fps": video.get("avg_frame_rate"), "sha256": _sha256(output), "log": str(log_path)}
    except Exception:
        output.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a fullscreen Muse Dashboard window via desktop composition")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=int, default=12)
    parser.add_argument("--fps", type=int, default=15)
    args = parser.parse_args()
    try:
        result = capture(Path(args.ffmpeg), Path(args.ffprobe), Path(args.output), args.duration, args.fps)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
