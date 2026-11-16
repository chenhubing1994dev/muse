"""Build the 60-90 second local PROMETHEUS Muse product film.

Reuses four previously verified Wan 2.1 shots, generates the remaining scenes locally,
includes one real Muse Dashboard capture, then creates landscape/portrait products.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
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
    {"id": "ignition", "seed": 20261121,
     "prompt": "A single warm golden ember awakens in an immense deep indigo cosmic void, delicate nebula clouds and distant stars slowly emerge, cinematic macro-to-wide camera movement, awe and mystery, premium science fiction film cinematography, volumetric light, no text, no letters, no logos",
     "caption": "PROJECT PROMETHEUS｜让未知成为探索的起点", "role": "brand_mission"},
    {"id": "knowledge", "seed": 20261122,
     "prompt": "An immense ancient library merging with a futuristic knowledge constellation, thousands of luminous pages and fine golden threads connect into a living map, camera glides through the architecture, cinematic realistic fantasy, deep blue and gold palette, no readable text, no letters, no logos",
     "caption": "Muse：自主研究 · 知识沉淀 · 智能体协作", "role": "current_capabilities_illustrative_visual"},
    {"id": "creation", "seed": 20261123,
     "prompt": "A sophisticated cinematic creative studio in a dark futuristic environment, a sequence of film frames and luminous editing timelines float around a central glowing creative core, multiple screens show abstract moving images, elegant camera orbit, realistic high-end production design, no readable text, no logos",
     "caption": "本地 Wan 2.1 生成 · FFmpeg 合成 · 产品封装", "role": "current_capabilities_illustrative_visual"},
    {"id": "future", "seed": 20261124,
     "prompt": "A breathtaking open creative universe where architecture, music waves, moving images, virtual worlds and constellations of ideas converge into a radiant horizon, expansive cinematic camera pullback, visionary science fiction, elegant blue gold and violet light, no text, no letters, no logos",
     "caption": "未来愿景 / 规划中：跨媒介创造工坊", "role": "future_vision"},
    {"id": "idea", "seed": 20261125,
     "prompt": "A creator in a quiet dark studio writes a single idea in an open notebook, a tiny golden spark rises from the page and unfolds into luminous possibilities, intimate cinematic closeup, beautiful natural hand movement, warm gold against midnight blue, no readable writing, no logos",
     "caption": "灵感捕获：一个念头，也可以成为探索任务", "role": "current_capabilities_illustrative_visual"},
    {"id": "research", "seed": 20261126,
     "prompt": "A futuristic autonomous research atelier, books opening by themselves, searchlight beams sweeping across shelves, papers and source cards connecting into a focused investigation, cinematic tracking shot, realistic detail, no readable text, no logos",
     "caption": "自主研究：发现问题 · 搜索资料 · 交叉阅读", "role": "current_capabilities_illustrative_visual"},
    {"id": "evidence", "seed": 20261127,
     "prompt": "Two streams of evidence approach a luminous balance in a dark research chamber, contrasting documents and data fragments are carefully weighed, a clear beam emerges without destroying either side, cinematic scientific visual metaphor, no readable text, no logos",
     "caption": "深度学习：寻找反证 · 比较来源 · 校准判断", "role": "current_capabilities_illustrative_visual"},
    {"id": "graph", "seed": 20261128,
     "prompt": "A vast three dimensional knowledge graph blooms in a dark observatory, thousands of fine glowing nodes and threads connect concepts, sources, hypotheses and discoveries, camera flies through the network, elegant volumetric blue gold light, no letters, no logos",
     "caption": "知识图谱：概念、来源、假设与关系", "role": "current_capabilities_illustrative_visual"},
    {"id": "agents", "seed": 20261129,
     "prompt": "A grand futuristic command center like an orchestra conductor's hall, one central luminous intelligence coordinates many distinct specialist agent lights moving in harmony, elegant radial camera orbit, premium cinematic realism, no readable text, no logos",
     "caption": "Muse Commander：统一调度 · 专业 Agent 协作", "role": "current_capabilities_illustrative_visual"},
    {"id": "engineering", "seed": 20261130,
     "prompt": "A complex engineering blueprint transforms into a working elegant machine, modular components assemble in midair, architecture diagrams become a real prototype, cinematic macro detail and smooth dolly movement, no readable code, no logos",
     "caption": "从方案、任务图到原型与工具", "role": "current_capabilities_illustrative_visual"},
    {"id": "video", "seed": 20261201,
     "prompt": "A cinematic film production stage with a virtual camera moving through glowing generative film frames, a director's monitor and luminous timeline in the background, rich practical lighting, high-end film production atmosphere, no readable text, no logos",
     "caption": "本地视频工坊：ComfyUI · Wan 2.1 · FFmpeg", "role": "current_capabilities_illustrative_visual"},
    {"id": "audio", "seed": 20261202,
     "prompt": "A beautiful sound design studio where orchestral instruments, synthesizer waves and luminous audio ribbons flow together, cinematic slow camera movement, tactile analog equipment and futuristic acoustic visualization, no readable text, no logos",
     "caption": "跨媒介创造：影像、声音、艺术与设计", "role": "creative_capabilities_illustrative_visual"},
    {"id": "products", "seed": 20261203,
     "prompt": "A refined digital product gallery with finished films, cover art, subtitle pages and packaged creative projects displayed like precious objects in a cinematic dark museum, smooth camera glide, premium design, no readable text, no logos",
     "caption": "产品交付：MP4 · 封面 · 字幕 · 分镜 · ZIP", "role": "current_capabilities_illustrative_visual"},
    {"id": "synthesis", "seed": 20261204,
     "prompt": "Science, ancient philosophy, architecture, painting, music and natural forms converge in a breathtaking creative chamber, their distinct visual languages combine into one new luminous structure, sweeping cinematic camera movement, no readable text, no logos",
     "caption": "超融合：连接科学、文化、艺术与工程", "role": "mission_illustrative_visual"},
    {"id": "transmission", "seed": 20261205,
     "prompt": "A small flame is carefully passed from one pair of hands to another across changing eras and cultures, ancient library to modern laboratory to a distant future city, poetic cinematic time transition, warm human emotion, no text, no logos",
     "caption": "知识之火：经验、方法与作品持续传承", "role": "prometheus_mission"},
]

ORDER = [
    "ignition", "idea", "research", "evidence", "graph", "knowledge",
    "agents", "engineering", "creation", "video", "audio", "products",
    "dashboard", "synthesis", "transmission", "future",
]
SCENE_SECONDS = 4
AUDIO_SECONDS = 66


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown process error")[-4000:]
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result


def write_ambient(path: Path, seconds: float = AUDIO_SECONDS, rate: int = 48000) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        block = bytearray()
        for index in range(frames):
            t = index / rate
            envelope = min(1.0, t / 2.0) * min(1.0, max(0.0, (seconds - t) / 2.5))
            pulse = 0.8 + 0.2 * math.sin(2 * math.pi * 0.18 * t)
            pad = (0.22 * math.sin(2 * math.pi * 55 * t)
                   + 0.15 * math.sin(2 * math.pi * 82.41 * t)
                   + 0.11 * math.sin(2 * math.pi * 110 * t)
                   + 0.06 * math.sin(2 * math.pi * 164.81 * t)
                   + 0.03 * math.sin(2 * math.pi * 329.63 * t)
                   + 0.02 * math.sin(2 * math.pi * 660 * t))
            sample = int(max(-0.95, min(0.95, pad * envelope * pulse * 0.38)) * 32767)
            block.extend(struct.pack("<hh", sample, sample))
            if len(block) >= 65536:
                output.writeframesraw(block)
                block.clear()
        if block:
            output.writeframesraw(block)


def build(
    workspace: Path,
    output_dir: Path,
    *,
    dashboard_capture: Path | None = None,
    dashboard_image_override: Path | None = None,
) -> dict[str, object]:
    workspace, output_dir = workspace.resolve(), output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_dir = workspace / "data" / "video_sources" / "muse-prometheus-film-v1"
    source_dir.mkdir(parents=True, exist_ok=True)

    engines = inspect_video_engines(timeout_seconds=2.0)
    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and FFprobe must both be installed")

    # Reuse the four already verified teaser renders; do not spend GPU time regenerating them.
    teaser_sources = workspace / "data" / "video_sources" / "muse-prometheus-teaser-v1"
    for scene in SCENES[:4]:
        for suffix in (".mp4", ".json"):
            source = teaser_sources / f"{scene['id']}{suffix}"
            target = source_dir / source.name
            if not source.is_file():
                raise RuntimeError(f"verified teaser source is missing: {source}")
            if not target.is_file():
                shutil.copy2(source, target)
            if suffix == ".mp4" and sha256(source) != sha256(target):
                raise RuntimeError(f"reused source hash mismatch: {target}")

    # Rebuilding from integrity-verified cached Wan shots does not require ComfyUI.
    # Only block when at least one missing shot actually needs a new GPU generation.
    needs_generation = any(
        not (source_dir / f"{scene['id']}.mp4").is_file()
        or not (source_dir / f"{scene['id']}.json").is_file()
        for scene in SCENES
    )
    if needs_generation and engines["comfyui"].get("status") != "ready":
        raise RuntimeError(f"ComfyUI is not ready and one or more Wan shots need generation: {engines['comfyui']}")

    generated = []
    for scene in SCENES:
        target = source_dir / f"{scene['id']}.mp4"
        metadata_path = source_dir / f"{scene['id']}.json"
        if target.is_file() and metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("status") != "completed" or metadata.get("sha256") != sha256(target):
                raise RuntimeError(f"existing shot failed integrity check: {target}")
        elif scene["id"] in {item["id"] for item in SCENES[:4]}:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["sha256"] = sha256(target)
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            metadata = generate_wan21_t2v(
                scene["prompt"], target, aspect_ratio="16:9", duration_seconds=3.0,
                steps=8, seed=scene["seed"], timeout_seconds=1800,
            )
            if metadata.get("status") != "completed" or not target.is_file():
                raise RuntimeError(f"Wan generation did not complete for {scene['id']}")
            metadata["sha256"] = sha256(target)
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        generated.append({**scene, **metadata, "source_file": target.name, "sha256": sha256(target)})

    # Prefer a verified real screen recording when supplied; otherwise render the legacy real UI screenshot.
    dashboard_image = (
        dashboard_image_override.resolve() if dashboard_image_override
        else workspace / "data" / "prometheus" / "teaser-v1" / "dashboard-ui.png"
    )
    if not dashboard_image.is_file() or dashboard_image.stat().st_size < 10000:
        raise RuntimeError("real Dashboard image evidence is missing")
    if dashboard_capture is not None:
        capture_path = dashboard_capture.resolve()
        if not capture_path.is_file() or capture_path.stat().st_size < 10000:
            raise RuntimeError(f"real Dashboard capture is missing or too small: {capture_path}")
        dashboard_clip = output_dir / "dashboard.mp4"
        if dashboard_clip.exists():
            if sha256(dashboard_clip) != sha256(capture_path):
                raise FileExistsError(f"refusing to overwrite different Dashboard scene: {dashboard_clip}")
        else:
            shutil.copy2(capture_path, dashboard_clip)
        capture_probe = run([
            ffprobe, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate",
            "-of", "json", str(dashboard_clip),
        ], workspace, timeout=30)
        capture_data = json.loads(capture_probe.stdout)
        capture_video = next(
            (item for item in capture_data.get("streams", []) if item.get("codec_type") == "video"),
            None,
        )
        capture_duration = float(capture_data.get("format", {}).get("duration", 0))
        if (not capture_video or capture_video.get("width") != 1920 or capture_video.get("height") != 1080
                or capture_video.get("avg_frame_rate") != "24/1"
                or abs(capture_duration - 3.0) > 0.25):
            raise RuntimeError(
                f"Dashboard recording must be 3s, 1920x1080, 24fps; got {capture_video}, {capture_duration}s"
            )
        dashboard_prompt_id = "dashboard-ui-product-card-recording-20261112"
        dashboard_prompt = "Real Muse Dashboard product library product-card capture, trimmed to a 3-second film scene"
        dashboard_caption = "Muse 产品库：真实预览 · MP4 下载 · 工程 ZIP"
    else:
        dashboard_clip = source_dir / "dashboard.mp4"
        run([
            ffmpeg, "-hide_banner", "-y", "-loop", "1", "-framerate", "24", "-i", str(dashboard_image),
            "-t", str(SCENE_SECONDS),
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                   "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x08101d,fps=24,format=yuv420p",
            "-an", "-c:v", "h264_amf" if "h264_amf" in inventory.hardware_encoders else "libx264",
            *([] if "h264_amf" not in inventory.hardware_encoders else ["-quality", "quality", "-b:v", "5M"]),
            *([] if "h264_amf" in inventory.hardware_encoders else ["-preset", "medium", "-crf", "20"]),
            "-pix_fmt", "yuv420p", "-r", "24", str(dashboard_clip),
        ], workspace, timeout=180)
        dashboard_prompt_id = "dashboard-ui-capture-20261111"
        dashboard_prompt = "Real screenshot of the running Muse Dashboard showing the actual product interface and product library"
        dashboard_caption = "Muse Dashboard：真实灵感入口、历史与产品预览/下载"
    dashboard_scene = {
        "id": "dashboard", "seed": 0, "prompt_id": dashboard_prompt_id,
        "prompt": dashboard_prompt, "caption": dashboard_caption,
        "role": "current_capabilities_real_ui", "source_file": dashboard_clip.name,
        "sha256": sha256(dashboard_clip),
    }
    by_id = {item["id"]: item for item in generated}
    by_id["dashboard"] = dashboard_scene
    visuals = [by_id[item] for item in ORDER]

    audio_path = output_dir / "ambient-original.wav"
    write_ambient(audio_path)
    srt_path = output_dir / "subtitles.srt"
    cues = []
    for index, scene in enumerate(visuals):
        start, end = index * SCENE_SECONDS, (index + 1) * SCENE_SECONDS
        cues.append(f"{index + 1}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n{scene['caption']}\n")
    srt_path.write_text("\n".join(cues), encoding="utf-8-sig")
    storyboard = {
        "schema_version": 1, "title": "MUSE｜点燃一个宇宙（PROMETHEUS 完整宣传片）",
        "production_mode": "local_wan21_ai_video_plus_real_dashboard_capture",
        "duration_target_seconds": len(visuals) * SCENE_SECONDS,
        "aspect_ratio": "16:9", "delivery_aspect_ratios": ["16:9", "9:16"], "fps": 24,
        "model": "Wan 2.1 T2V 1.3B + real Muse Dashboard capture",
        "voiceover": "not_used; no verified local Chinese TTS model/service",
        "audio": "original_synthetic_ambient_bed",
        "scenes": [
            {"order": i + 1, "id": scene["id"], "prompt": scene["prompt"],
             "seed": scene["seed"], "prompt_id": scene["prompt_id"],
             "source_file": scene["source_file"], "duration_seconds": SCENE_SECONDS,
             "caption": scene["caption"], "role": scene["role"], "sha256": scene["sha256"]}
            for i, scene in enumerate(visuals)
        ],
    }
    storyboard_path = output_dir / "storyboard.json"
    storyboard_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    srt_relative = srt_path.relative_to(workspace).as_posix()

    def make_landscape() -> Path:
        target = output_dir / "muse-prometheus-film-16x9.mp4"
        filters = []
        for index in range(len(visuals)):
            filters.append(
                f"[{index}:v]scale=1920:1080:force_original_aspect_ratio=increase,"
                f"crop=1920:1080,fps=24,setsar=1,format=yuv420p,"
                f"tpad=stop_mode=clone:stop_duration=1,setpts=PTS-STARTPTS[v{index}]"
            )
        labels = "".join(f"[v{index}]" for index in range(len(visuals)))
        filters.append(f"{labels}concat=n={len(visuals)}:v=1:a=0[vcat]")
        filters.append(
            f"[vcat]subtitles=filename='{srt_relative}':"
            "force_style='FontName=Microsoft YaHei,FontSize=28,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&HCC000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=72'[v]"
        )
        command = [ffmpeg, "-hide_banner", "-y"]
        for scene in visuals:
            source = dashboard_clip if scene["id"] == "dashboard" else source_dir / f"{scene['id']}.mp4"
            command.extend(["-i", str(source)])
        command.extend(["-i", str(audio_path), "-filter_complex", ";".join(filters),
                        "-map", "[v]", "-map", f"{len(visuals)}:a:0",
                        "-c:v", "h264_amf" if "h264_amf" in inventory.hardware_encoders else "libx264"])
        command.extend(["-quality", "quality", "-b:v", "8M"] if "h264_amf" in inventory.hardware_encoders
                        else ["-preset", "medium", "-crf", "20"])
        command.extend(["-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-b:a", "192k",
                        "-ar", "48000", "-movflags", "+faststart", str(target)])
        run(command, workspace, timeout=3600)
        return target

    final_path = make_landscape()
    probe = run([ffprobe, "-v", "error", "-show_entries",
                 "format=duration,size:stream=codec_type,width,height,avg_frame_rate,channels,sample_rate",
                 "-of", "json", str(final_path)], workspace, timeout=30)
    data = json.loads(probe.stdout)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration", 0))
    if (not video or not audio or video.get("width") != 1920 or video.get("height") != 1080
            or video.get("avg_frame_rate") != "24/1" or not 60 <= duration <= 70):
        raise RuntimeError(f"landscape master failed acceptance: duration={duration}, video={video}, audio={bool(audio)}")
    run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(final_path), "-f", "null", "-"], workspace, timeout=900)

    vertical_path = output_dir / "muse-prometheus-film-9x16.mp4"
    filters = []
    for index in range(len(visuals)):
        filters.extend([
            f"[{index}:v]split=2[bg{index}][fg{index}]",
            f"[bg{index}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:8[blur{index}]",
            f"[fg{index}]scale=1080:-2[main{index}]",
            f"[blur{index}][main{index}]overlay=(W-w)/2:(H-h)/2,fps=24,setsar=1,"
            f"format=yuv420p,tpad=stop_mode=clone:stop_duration=1,setpts=PTS-STARTPTS[v{index}]",
        ])
    labels = "".join(f"[v{index}]" for index in range(len(visuals)))
    filters.append(f"{labels}concat=n={len(visuals)}:v=1:a=0[vcat]")
    filters.append(
        f"[vcat]subtitles=filename='{srt_relative}':"
        "force_style='FontName=Microsoft YaHei,FontSize=34,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&HCC000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=150'[v]"
    )
    command = [ffmpeg, "-hide_banner", "-y"]
    for scene in visuals:
        source = dashboard_clip if scene["id"] == "dashboard" else source_dir / f"{scene['id']}.mp4"
        command.extend(["-i", str(source)])
    command.extend(["-i", str(audio_path), "-filter_complex", ";".join(filters),
                    "-map", "[v]", "-map", f"{len(visuals)}:a:0",
                    "-c:v", "h264_amf" if "h264_amf" in inventory.hardware_encoders else "libx264"])
    command.extend(["-quality", "quality", "-b:v", "8M"] if "h264_amf" in inventory.hardware_encoders
                    else ["-preset", "medium", "-crf", "20"])
    command.extend(["-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", "-movflags", "+faststart", str(vertical_path)])
    run(command, workspace, timeout=3600)
    vertical_probe = run([ffprobe, "-v", "error", "-show_entries",
                          "format=duration,size:stream=codec_type,width,height,avg_frame_rate",
                          "-of", "json", str(vertical_path)], workspace, timeout=30)
    vdata = json.loads(vertical_probe.stdout)
    vstreams = vdata.get("streams", [])
    vvideo = next((item for item in vstreams if item.get("codec_type") == "video"), None)
    vaudio = next((item for item in vstreams if item.get("codec_type") == "audio"), None)
    vduration = float(vdata.get("format", {}).get("duration", 0))
    if (not vvideo or not vaudio or vvideo.get("width") != 1080 or vvideo.get("height") != 1920
            or vvideo.get("avg_frame_rate") != "24/1" or abs(vduration - duration) > 0.25):
        raise RuntimeError("portrait master failed acceptance")
    run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(vertical_path), "-f", "null", "-"], workspace, timeout=900)

    cover = output_dir / "cover.jpg"
    vertical_cover = output_dir / "cover-9x16.jpg"
    run([ffmpeg, "-hide_banner", "-y", "-ss", "8", "-i", str(final_path), "-frames:v", "1", "-q:v", "2", str(cover)], workspace, timeout=60)
    run([ffmpeg, "-hide_banner", "-y", "-ss", "8", "-i", str(vertical_path), "-frames:v", "1", "-q:v", "2", str(vertical_cover)], workspace, timeout=60)

    scene_records = [
        {"order": i + 1, "prompt": scene["prompt"], "prompt_id": scene["prompt_id"],
         "seed": scene["seed"], "generation_status": "generated", "source_file": scene["source_file"]}
        for i, scene in enumerate(visuals)
    ]
    builder = VideoProductBuilder(workspace)
    landscape_product = builder.package_generated_clip(
        "MUSE｜点燃一个宇宙（PROMETHEUS 完整宣传片）",
        "A 60-90 second Muse product film with 15 local Wan scenes and one real Dashboard capture.",
        final_path,
        generation_metadata={"status": "completed", "model": "Wan 2.1 T2V 1.3B + Dashboard capture",
                             "aspect_ratio": "16:9", "fps": 24, "duration_seconds": round(duration, 3),
                             "frames": None, "width": 1920, "height": 1080, "steps": 8,
                             "bytes": final_path.stat().st_size},
        scene_records=scene_records,
    )
    portrait_product = builder.package_generated_clip(
        "MUSE｜点燃一个宇宙（PROMETHEUS 完整竖屏版）",
        "A 60-90 second 9:16 Muse product film with 15 local Wan scenes and one real Dashboard capture.",
        vertical_path,
        generation_metadata={"status": "completed", "model": "Wan 2.1 T2V 1.3B + Dashboard capture",
                             "aspect_ratio": "9:16", "fps": 24, "duration_seconds": round(vduration, 3),
                             "frames": None, "width": 1080, "height": 1920, "steps": 8,
                             "bytes": vertical_path.stat().st_size},
        scene_records=scene_records,
    )
    landscape_dir = workspace / landscape_product["relative_path"]
    portrait_dir = workspace / portrait_product["relative_path"]
    readme = (
        "# MUSE｜点燃一个宇宙（PROMETHEUS 完整宣传片）\n\n"
        "本地 ComfyUI/Wan 2.1 T2V 1.3B 生成 15 个 AI 镜头，并加入真实 Muse Dashboard 界面素材；"
        "FFmpeg/AMD AMF 合成横屏与竖屏成片。包含原创程序合成氛围音轨、中文字幕、分镜、封面与产品 ZIP。"
        "未调用云端视频 API。\n\n"
        f"- 时长：{duration:.3f} 秒；横屏 1920×1080、竖屏 1080×1920；均为 24 FPS。\n"
        "- 旁白：未使用（本机 ChatTTS 模型/服务未验证）；音轨为原创合成氛围音乐。\n"
        "- Dashboard 场景来自实际运行的 Muse Dashboard，不是重绘 UI。\n"
        "- 当前功能的其他 AI 镜头属于视觉示意；未来跨媒介工坊明确标注为“未来愿景 / 规划中”。\n"
        "- 产品状态：testing；正式发布前须人工审片。\n"
    )
    readme_path = output_dir / "README.md"
    readme_path.write_text(readme, encoding="utf-8")
    bundle = output_dir / "delivery-bundle.zip"
    dashboard_image_name = "dashboard-ui-redacted.jpg" if dashboard_image.suffix.lower() in {".jpg", ".jpeg"} else "dashboard-ui.png"
    files = [
        (final_path, "video-16x9.mp4"), (vertical_path, "video-9x16.mp4"),
        (srt_path, "subtitles.srt"), (cover, "cover-16x9.jpg"),
        (vertical_cover, "cover-9x16.jpg"), (dashboard_image, dashboard_image_name),
        (storyboard_path, "storyboard.json"), (readme_path, "README.md"),
        (landscape_dir / "product.zip", "muse-product-16x9.zip"),
        (landscape_dir / "manifest.json", "manifest-16x9.json"),
        (portrait_dir / "product.zip", "muse-product-9x16.zip"),
        (portrait_dir / "manifest.json", "manifest-9x16.json"),
    ]
    if dashboard_capture is not None:
        files.insert(7, (dashboard_clip, "dashboard-ui-capture.mp4"))
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in files:
            archive.write(source, name)
    with zipfile.ZipFile(bundle) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(files):
            raise RuntimeError("delivery bundle CRC or file-count validation failed")

    result = {
        "status": "completed", "title": storyboard["title"],
        "landscape_product": landscape_product, "portrait_product": portrait_product,
        "landscape": {"path": str(final_path), "bytes": final_path.stat().st_size,
                      "sha256": sha256(final_path), "duration_seconds": round(duration, 3),
                      "width": 1920, "height": 1080, "fps": 24, "audio": True},
        "portrait": {"path": str(vertical_path), "bytes": vertical_path.stat().st_size,
                     "sha256": sha256(vertical_path), "duration_seconds": round(vduration, 3),
                     "width": 1080, "height": 1920, "fps": 24, "audio": True},
        "dashboard_capture_sha256": sha256(dashboard_image),
        "dashboard_video_capture_sha256": sha256(dashboard_clip),
        "bundle": {"path": str(bundle), "bytes": bundle.stat().st_size, "sha256": sha256(bundle),
                   "entries": len(files), "crc_valid": True},
        "scenes": [{"id": scene["id"], "prompt_id": scene["prompt_id"],
                    "seed": scene["seed"], "sha256": scene["sha256"]} for scene in visuals],
        "engine_status": engines, "ffmpeg_version": inventory.ffmpeg_version,
        "hardware_encoders": list(inventory.hardware_encoders),
        "voiceover": "not used; local TTS not verified",
        "status_note": "testing; human review required before publication",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "generation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the 60-90 second local PROMETHEUS Muse film")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output-dir", default="data/prometheus/film-v1")
    parser.add_argument("--dashboard-capture", help="Optional verified 3-second 1920x1080 24fps real Dashboard MP4")
    parser.add_argument("--dashboard-image", help="Optional safe/redacted real Dashboard image for the delivery bundle")
    args = parser.parse_args()
    try:
        result = build(
            Path(args.workspace), Path(args.workspace) / args.output_dir,
            dashboard_capture=Path(args.dashboard_capture) if args.dashboard_capture else None,
            dashboard_image_override=Path(args.dashboard_image) if args.dashboard_image else None,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
