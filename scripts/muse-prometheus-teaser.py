"""Build a local Wan 2.1 PROMETHEUS teaser and a verifiable Muse delivery bundle.

This is a deliberately bounded first teaser, not the planned 60-90 second master film.
It uses only the already-installed local ComfyUI/Wan 2.1 and FFmpeg toolchain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import subprocess
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from muse.media import discover_media_tools
from muse.video_engines import inspect_video_engines
from muse.video_product_builder import VideoProductBuilder
from muse.wan_video import generate_wan21_t2v


SCENES = [
    {
        "id": "ignition",
        "seed": 20261121,
        "prompt": "A single warm golden ember awakens in an immense deep indigo cosmic void, delicate nebula clouds and distant stars slowly emerge, cinematic macro-to-wide camera movement, awe and mystery, premium science fiction film cinematography, volumetric light, no text, no letters, no logos",
        "caption": "PROJECT PROMETHEUS｜让未知成为探索的起点",
        "role": "brand_mission",
    },
    {
        "id": "knowledge",
        "seed": 20261122,
        "prompt": "An immense ancient library merging with a futuristic knowledge constellation, thousands of luminous pages and fine golden threads connect into a living map, camera glides through the architecture, cinematic realistic fantasy, deep blue and gold palette, no readable text, no letters, no logos",
        "caption": "Muse：自主研究 · 知识沉淀 · 智能体协作",
        "role": "current_capabilities_illustrative_visual",
    },
    {
        "id": "creation",
        "seed": 20261123,
        "prompt": "A sophisticated cinematic creative studio in a dark futuristic environment, a sequence of film frames and luminous editing timelines float around a central glowing creative core, multiple screens show abstract moving images, elegant camera orbit, realistic high-end production design, no readable text, no logos",
        "caption": "本地 Wan 2.1 生成 · FFmpeg 合成 · 产品封装",
        "role": "current_capabilities_illustrative_visual",
    },
    {
        "id": "future",
        "seed": 20261124,
        "prompt": "A breathtaking open creative universe where architecture, music waves, moving images, virtual worlds and constellations of ideas converge into a radiant horizon, expansive cinematic camera pullback, visionary science fiction, elegant blue gold and violet light, no text, no letters, no logos",
        "caption": "未来愿景 / 规划中：跨媒介创造工坊",
        "role": "future_vision",
    },
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_ambient(path: Path, seconds: float = 16.0, sample_rate: int = 48000) -> None:
    """Create an original, quiet synthetic ambient bed; no external music asset."""
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        batch = bytearray()
        for index in range(frames):
            t = index / sample_rate
            fade_in = min(1.0, t / 1.6)
            fade_out = min(1.0, max(0.0, (seconds - t) / 1.8))
            pulse = 0.78 + 0.22 * math.sin(2 * math.pi * 0.22 * t)
            envelope = fade_in * fade_out * pulse
            pad = (
                0.22 * math.sin(2 * math.pi * 55.0 * t)
                + 0.15 * math.sin(2 * math.pi * 82.41 * t)
                + 0.10 * math.sin(2 * math.pi * 110.0 * t)
                + 0.055 * math.sin(2 * math.pi * 164.81 * t)
                + 0.025 * math.sin(2 * math.pi * 329.63 * t)
            )
            shimmer = 0.025 * math.sin(2 * math.pi * (660.0 + 8.0 * math.sin(t)) * t)
            value = max(-0.95, min(0.95, (pad + shimmer) * envelope * 0.42))
            sample = int(value * 32767)
            batch.extend(struct.pack("<hh", sample, sample))
            if len(batch) >= 65536:
                output.writeframesraw(batch)
                batch.clear()
        if batch:
            output.writeframesraw(batch)


def _run(command: list[str], *, cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown process error")[-4000:]
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result


def build(workspace: Path, output_dir: Path) -> dict[str, object]:
    workspace = workspace.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    shot_dir = workspace / "data" / "video_sources" / "muse-prometheus-teaser-v1"
    shot_dir.mkdir(parents=True, exist_ok=True)

    engines = inspect_video_engines(timeout_seconds=2.0)
    if engines["comfyui"].get("status") != "ready":
        raise RuntimeError(f"ComfyUI is not ready: {engines['comfyui']}")
    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and FFprobe must both be installed")

    shot_metadata = []
    for scene in SCENES:
        target = shot_dir / f"{scene['id']}.mp4"
        metadata_path = shot_dir / f"{scene['id']}.json"
        if target.is_file() and metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("status") != "completed" or metadata.get("sha256") != _sha256(target):
                raise RuntimeError(f"existing shot failed integrity check: {target}")
        else:
            metadata = generate_wan21_t2v(
                scene["prompt"], target, aspect_ratio="16:9", duration_seconds=3.0,
                steps=8, seed=scene["seed"], timeout_seconds=1800,
            )
            if metadata.get("status") != "completed" or not target.is_file():
                raise RuntimeError(f"Wan generation did not complete for {scene['id']}")
            metadata["sha256"] = _sha256(target)
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shot_metadata.append({**scene, **metadata, "source_file": target.name, "sha256": _sha256(target)})

    audio_path = output_dir / "ambient-original.wav"
    _write_ambient(audio_path)
    srt_path = output_dir / "subtitles.srt"
    subtitles = []
    for index, scene in enumerate(SCENES):
        start = index * 4
        end = start + 4
        subtitles.append(
            f"{index + 1}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n{scene['caption']}\n"
        )
    srt_path.write_text("\n".join(subtitles), encoding="utf-8-sig")

    storyboard = {
        "schema_version": 1,
        "title": "MUSE｜点燃一个宇宙（PROMETHEUS Teaser）",
        "production_mode": "local_wan21_ai_video",
        "duration_target_seconds": 16,
        "aspect_ratio": "16:9",        "delivery_aspect_ratios": ["16:9", "9:16"],
        "fps": 24,
        "model": "Wan 2.1 T2V 1.3B",
        "voiceover": "not_used; no verified local Chinese TTS model/service",
        "audio": "original_synthetic_ambient_bed",
        "scenes": [
            {
                "order": index + 1, "id": scene["id"], "prompt": scene["prompt"],
                "seed": scene["seed"], "prompt_id": scene["prompt_id"],
                "source_file": scene["source_file"], "duration_seconds": 4,
                "caption": scene["caption"], "role": scene["role"],
            }
            for index, scene in enumerate(shot_metadata)
        ],
    }
    storyboard_path = output_dir / "storyboard.json"
    storyboard_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    final_path = output_dir / "muse-prometheus-teaser-16x9.mp4"
    srt_relative = srt_path.relative_to(workspace).as_posix()
    filter_parts = []
    for index in range(len(SCENES)):
        filter_parts.append(
            f"[{index}:v]scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,fps=24,setsar=1,format=yuv420p,"
            f"tpad=stop_mode=clone:stop_duration=1,setpts=PTS-STARTPTS[v{index}]"
        )
    labels = "".join(f"[v{index}]" for index in range(len(SCENES)))
    filter_parts.append(f"{labels}concat=n={len(SCENES)}:v=1:a=0[vcat]")
    filter_parts.append(
        f"[vcat]subtitles=filename='{srt_relative}':"
        "force_style='FontName=Microsoft YaHei,FontSize=28,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&HCC000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=72'[v]"
    )
    command = [ffmpeg, "-hide_banner", "-y"]
    for scene in SCENES:
        command.extend(["-i", str(shot_dir / f"{scene['id']}.mp4")])
    command.extend(["-i", str(audio_path), "-filter_complex", ";".join(filter_parts),
                    "-map", "[v]", "-map", f"{len(SCENES)}:a:0",
                    "-c:v", "h264_amf" if "h264_amf" in inventory.hardware_encoders else "libx264"])
    if "h264_amf" in inventory.hardware_encoders:
        command.extend(["-quality", "quality", "-b:v", "8M"])
    else:
        command.extend(["-preset", "medium", "-crf", "20"])
    command.extend(["-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", "-movflags", "+faststart", str(final_path)])
    _run(command, cwd=workspace, timeout=3600)

    probe = _run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,codec_type,width,height,avg_frame_rate,channels,sample_rate",
        "-of", "json", str(final_path),
    ], cwd=workspace, timeout=30)
    probe_data = json.loads(probe.stdout)
    streams = probe_data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float(probe_data.get("format", {}).get("duration", 0))
    if not video or not audio or video.get("width") != 1920 or video.get("height") != 1080:
        raise RuntimeError("final MP4 is missing the expected video/audio streams or 1920x1080 canvas")
    if video.get("avg_frame_rate") != "24/1" or not 14.0 <= duration <= 18.0:
        raise RuntimeError(f"final MP4 duration/FPS is outside acceptance: {duration}, {video.get('avg_frame_rate')}")
    _run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(final_path), "-f", "null", "-"],
         cwd=workspace, timeout=600)
    vertical_path = output_dir / "muse-prometheus-teaser-9x16.mp4"
    vertical_filters = []
    for index in range(len(SCENES)):
        vertical_filters.extend([
            f"[{index}:v]split=2[bg{index}][fg{index}]",
            f"[bg{index}]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,boxblur=24:8[blur{index}]",
            f"[fg{index}]scale=1080:-2[main{index}]",
            f"[blur{index}][main{index}]overlay=(W-w)/2:(H-h)/2,"
            f"fps=24,setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=1,"
            f"setpts=PTS-STARTPTS[v{index}]",
        ])
    vertical_labels = "".join(f"[v{index}]" for index in range(len(SCENES)))
    vertical_filters.append(f"{vertical_labels}concat=n={len(SCENES)}:v=1:a=0[vcat]")
    vertical_filters.append(
        f"[vcat]subtitles=filename='{srt_relative}':"
        "force_style='FontName=Microsoft YaHei,FontSize=34,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&HCC000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=150'[v]"
    )
    vertical_command = [ffmpeg, "-hide_banner", "-y"]
    for scene in SCENES:
        vertical_command.extend(["-i", str(shot_dir / f"{scene['id']}.mp4")])
    vertical_command.extend(["-i", str(audio_path), "-filter_complex", ";".join(vertical_filters),
                             "-map", "[v]", "-map", f"{len(SCENES)}:a:0",
                             "-c:v", "h264_amf" if "h264_amf" in inventory.hardware_encoders else "libx264"])
    if "h264_amf" in inventory.hardware_encoders:
        vertical_command.extend(["-quality", "quality", "-b:v", "8M"])
    else:
        vertical_command.extend(["-preset", "medium", "-crf", "20"])
    vertical_command.extend(["-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-b:a", "192k",
                             "-ar", "48000", "-movflags", "+faststart", str(vertical_path)])
    _run(vertical_command, cwd=workspace, timeout=3600)
    vertical_probe = _run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,width,height,avg_frame_rate,channels,sample_rate",
        "-of", "json", str(vertical_path),
    ], cwd=workspace, timeout=30)
    vertical_data = json.loads(vertical_probe.stdout)
    vertical_streams = vertical_data.get("streams", [])
    vertical_video = next((item for item in vertical_streams if item.get("codec_type") == "video"), None)
    vertical_audio = next((item for item in vertical_streams if item.get("codec_type") == "audio"), None)
    vertical_duration = float(vertical_data.get("format", {}).get("duration", 0))
    if (not vertical_video or not vertical_audio or vertical_video.get("width") != 1080
            or vertical_video.get("height") != 1920 or vertical_video.get("avg_frame_rate") != "24/1"
            or abs(vertical_duration - duration) > 0.25):
        raise RuntimeError("vertical MP4 failed dimensions/FPS/audio/duration verification")
    _run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(vertical_path), "-f", "null", "-"],
         cwd=workspace, timeout=600)
    vertical_cover = output_dir / "cover-9x16.jpg"
    _run([ffmpeg, "-hide_banner", "-y", "-ss", "5", "-i", str(vertical_path),
          "-frames:v", "1", "-q:v", "2", str(vertical_cover)], cwd=workspace, timeout=60)


    cover_path = output_dir / "cover.jpg"
    _run([ffmpeg, "-hide_banner", "-y", "-ss", "5", "-i", str(final_path),
          "-frames:v", "1", "-q:v", "2", str(cover_path)], cwd=workspace, timeout=60)

    scene_records = [
        {"order": index + 1, "prompt": scene["prompt"], "prompt_id": scene["prompt_id"],
         "seed": scene["seed"], "generation_status": "generated", "source_file": scene["source_file"]}
        for index, scene in enumerate(shot_metadata)
    ]
    product = VideoProductBuilder(workspace).package_generated_clip(
        "MUSE｜点燃一个宇宙（PROMETHEUS Teaser）",
        "Four-scene PROMETHEUS product and capability introduction teaser; see storyboard for per-shot prompts.",
        final_path,
        generation_metadata={
            "status": "completed", "model": "Wan 2.1 T2V 1.3B", "aspect_ratio": "16:9",
            "fps": 24, "duration_seconds": round(duration, 3), "frames": None,
            "width": 1920, "height": 1080, "steps": 8, "bytes": final_path.stat().st_size,
        },
        scene_records=scene_records,
    )
    product_dir = workspace / product["relative_path"]
    vertical_product = VideoProductBuilder(workspace).package_generated_clip(
        "MUSE｜点燃一个宇宙（PROMETHEUS 竖屏版）",
        "Four-scene PROMETHEUS teaser adapted to a 9:16 vertical canvas; see storyboard for per-shot prompts.",
        vertical_path,
        generation_metadata={
            "status": "completed", "model": "Wan 2.1 T2V 1.3B", "aspect_ratio": "9:16",
            "fps": 24, "duration_seconds": round(vertical_duration, 3), "frames": None,
            "width": 1080, "height": 1920, "steps": 8, "bytes": vertical_path.stat().st_size,
        },
        scene_records=scene_records,
    )
    vertical_product_dir = workspace / vertical_product["relative_path"]

    readme = (
        "# MUSE｜点燃一个宇宙（PROMETHEUS Teaser）\n\n"
        "本地 Wan 2.1 T2V 1.3B + ComfyUI 生成四个镜头，FFmpeg/AMD AMF 合成。"
        "包含原创合成氛围音轨与中文字幕；未使用云端视频 API。\n\n"
        "- 画幅：横屏 16:9（1920×1080）与竖屏 9:16（1080×1920），均为 24 FPS\n"
        f"- 时长：{duration:.3f} 秒\n- 旁白：未使用（本机 ChatTTS 模型/服务尚未验证）\n"        "- 当前功能镜头为能力示意视觉，不是 Dashboard 实际界面录屏。\n"
        "- 末段“跨媒介创造工坊”明确标注为未来愿景/规划中。\n"
        "- 产品状态：testing；正式发布前须人工审片。\n"
    )
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    bundle_path = output_dir / "delivery-bundle.zip"
    bundle_files = [
        (final_path, "video.mp4"), (srt_path, "subtitles.srt"), (cover_path, "cover.jpg"),
        (vertical_path, "video-9x16.mp4"), (vertical_cover, "cover-9x16.jpg"),        (storyboard_path, "storyboard.json"), (output_dir / "README.md", "README.md"),
        (product_dir / "product.zip", "muse-product.zip"),
        (product_dir / "manifest.json", "product-manifest.json"),
        (vertical_product_dir / "product.zip", "muse-product-9x16.zip"),        (vertical_product_dir / "manifest.json", "product-manifest-9x16.json"),
    ]
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in bundle_files:
            archive.write(source, name)
    with zipfile.ZipFile(bundle_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("delivery bundle ZIP CRC validation failed")
    summary = {
        "status": "completed", "cycle_id": "muse-prometheus-charter-video-audit-20261111",
        "title": storyboard["title"], "product": product,
        "vertical_product": vertical_product,        "video": {"path": str(final_path), "bytes": final_path.stat().st_size,
                  "sha256": _sha256(final_path), "duration_seconds": round(duration, 3),
                  "width": 1920, "height": 1080, "fps": 24, "audio": True},
        "vertical_video": {"path": str(vertical_path), "bytes": vertical_path.stat().st_size,                           "sha256": _sha256(vertical_path), "duration_seconds": round(vertical_duration, 3),
                           "width": 1080, "height": 1920, "fps": 24, "audio": True},
        "subtitle_path": str(srt_path), "cover_path": str(cover_path),
        "bundle_path": str(bundle_path), "bundle_sha256": _sha256(bundle_path),
        "shots": [{"id": item["id"], "prompt_id": item["prompt_id"], "seed": item["seed"],
                   "sha256": item["sha256"]} for item in shot_metadata],
        "engine_status": engines,
        "ffmpeg_version": inventory.ffmpeg_version,
        "hardware_encoders": list(inventory.hardware_encoders),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "status_note": "testing; requires human review before publication",
    }
    (output_dir / "generation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first local PROMETHEUS Muse teaser")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output-dir", default="data/prometheus/teaser-v1")
    args = parser.parse_args()
    try:
        result = build(Path(args.workspace), Path(args.workspace) / args.output_dir)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
