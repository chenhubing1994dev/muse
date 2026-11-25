"""Compile researched inspiration blueprints into broadcast-ready 1080p 9:16 & 16:9 video deliverables.

Reuses Muse Commander / PipelineTaskHandler contracts, local Remotion 1080p motion graphics,
local Mandarin/Cantonese speech synthesis, broadcast -16 LUFS mastering, VideoProductBuilder
integrity packaging, video_qa inspection, and multi-platform manual publish packages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import zipfile

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from muse.inspiration_evolution import evolve_inspiration_closed_loop
from muse.media import discover_media_tools
from muse.progress import new_evolution_cycle_id, record_evolution_cycle_event
from muse.video_product_builder import VideoProductBuilder
from muse.video_publish_package import (
    VIRAL_MONETIZATION_PLATFORMS_16X9,
    VIRAL_MONETIZATION_PLATFORMS_9X16,
    build_publish_package_with_optional_generated_contact_sheet,
)

LEMO_PYTHON = Path(os.environ.get("MUSE_LEMO_PYTHON") or (Path.home() / "lemo-opuscar" / ".venv" / "Scripts" / "python.exe"))
PY314_EXEC = Path(os.environ.get("MUSE_PY314_EXEC") or (Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Programs" / "Python" / "Python314" / "python.exe"))
SAMPLE_RATE = 48_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], cwd: Path, *, env: dict[str, str] | None = None, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown command error")[-2400:]
        raise RuntimeError(f"Command failed ({proc.returncode}): {command[0]} -> {detail}")
    return proc


def _srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _read_pcm_mono_48k(wav_path: Path, ffmpeg: str, cwd: Path) -> tuple[list[float], float]:
    """Decode any WAV/audio file into normalized [-1, 1] mono 48kHz samples without external deps."""
    tmp_wav = wav_path.with_suffix(".48k.wav")
    _run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(wav_path),
            "-af", "silenceremove=start_periods=1:start_threshold=-46dB:start_duration=0.02,areverse,silenceremove=start_periods=1:start_threshold=-46dB:start_duration=0.02,areverse",
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(tmp_wav),
        ],
        cwd=cwd,
        timeout=60,
    )
    with wave.open(str(tmp_wav), "rb") as w:
        frames = w.readframes(w.getnframes())
    tmp_wav.unlink(missing_ok=True)
    count = len(frames) // 2
    if count == 0:
        raise RuntimeError(f"Decoded silent audio from {wav_path.name}")
    ints = struct.unpack(f"<{count}h", frames)
    samples = [v / 32768.0 for v in ints]
    return samples, count / SAMPLE_RATE


def _synthesize_act_voices_mandarin(
    acts: list[dict[str, Any]],
    voice_dir: Path,
    ffmpeg: str,
    cwd: Path,
) -> dict[str, Any]:
    """Synthesize broadcast Mandarin neural narration per act using installed lemo edge-tts."""
    if not LEMO_PYTHON.is_file():
        raise RuntimeError("lemo edge-tts environment is not present")
    voice_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        {"id": act["id"], "text": act["narration_zh"], "rate": "+6%", "pitch": "-2Hz"}
        for act in acts
    ]
    lines_file = voice_dir / "lines.json"
    lines_file.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
    script_code = (
        "import asyncio, json, pathlib, sys, edge_tts\n"
        "lines = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "out_dir = pathlib.Path(sys.argv[2])\n"
        "async def run():\n"
        "    for item in lines:\n"
        "        mp3 = out_dir / (item['id'] + '.mp3')\n"
        "        comm = edge_tts.Communicate(item['text'], 'zh-CN-YunxiNeural', rate=item['rate'], pitch=item['pitch'])\n"
        "        await comm.save(str(mp3))\n"
        "asyncio.run(run())\n"
    )
    _run([str(LEMO_PYTHON), "-c", script_code, str(lines_file), str(voice_dir)], cwd=cwd, timeout=120)
    per_act: list[dict[str, Any]] = []
    for act in acts:
        mp3_path = voice_dir / f"{act['id']}.mp3"
        if not mp3_path.is_file() or mp3_path.stat().st_size < 512:
            raise RuntimeError(f"edge-tts produced invalid audio for {act['id']}")
        samples, duration = _read_pcm_mono_48k(mp3_path, ffmpeg, cwd)
        per_act.append({"id": act["id"], "samples": samples, "duration": duration})
    return {
        "engine": "Microsoft Edge Neural TTS (zh-CN-YunxiNeural)",
        "language": "zh-CN",
        "voice_profile": "zh-CN-YunxiNeural (+6%, -2Hz documentary male voice)",
        "offline": False,
        "acts": per_act,
    }


def _synthesize_act_voices_cantonese(
    acts: list[dict[str, Any]],
    voice_dir: Path,
    ffmpeg: str,
    cwd: Path,
) -> dict[str, Any]:
    """Synthesize 100% offline Cantonese narration per act using Muse canto-tts-nano ONNX."""
    if not PY314_EXEC.is_file():
        raise RuntimeError("Python 3.14 runtime for canto-tts-nano is missing")
    model_dir = cwd / "data" / "models" / "canto-tts-nano"
    tts_pkg_dir = cwd / ".muse-canto-tts-py314"
    if not (model_dir / "browser_poc_manifest.json").is_file() or not (tts_pkg_dir / "canto_tts").is_dir():
        raise RuntimeError("canto-tts-nano ONNX model or packages are missing")
    voice_dir.mkdir(parents=True, exist_ok=True)
    lines = [{"id": act["id"], "text": act.get("narration_yue") or act["narration_zh"]} for act in acts]
    lines_file = voice_dir / "lines_yue.json"
    lines_file.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
    code = (
        "import json, pathlib, sys\n"
        "from muse.cantonese_tts import synthesize_cantonese\n"
        "lines = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "out_dir = pathlib.Path(sys.argv[2])\n"
        "model_dir = pathlib.Path(sys.argv[3])\n"
        "for item in lines:\n"
        "    wav = out_dir / (item['id'] + '.wav')\n"
        "    synthesize_cantonese(item['text'], wav, model_dir=model_dir, quality='duration_filter')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{cwd / 'src'}{os.pathsep}{tts_pkg_dir}"
    _run([str(PY314_EXEC), "-c", code, str(lines_file), str(voice_dir), str(model_dir)], cwd=cwd, env=env, timeout=240)
    per_act: list[dict[str, Any]] = []
    for act in acts:
        wav_path = voice_dir / f"{act['id']}.wav"
        samples, duration = _read_pcm_mono_48k(wav_path, ffmpeg, cwd)
        per_act.append({"id": act["id"], "samples": samples, "duration": duration})
    return {
        "engine": "canto-tts-onnx (offline canto-tts-nano)",
        "language": "zh-HK",
        "voice_profile": "canto-tts-nano neutral Hong Kong Cantonese ONNX",
        "offline": True,
        "acts": per_act,
    }


def _synthesize_act_voices_cantonese_royal(
    acts: list[dict[str, Any]],
    voice_dir: Path,
    ffmpeg: str,
    cwd: Path,
) -> dict[str, Any]:
    """Mature, low, alluring Cantonese 'royal sister' narration via edge-tts.

    zh-HK-HiuGaaiNeural tuned down (-18Hz pitch, -12% rate) yields a deep,
    languid 176Hz delivery measured against the neutral HK voice.
    Uses narration_yue from each act, falling back to narration_zh.
    """
    if not LEMO_PYTHON.is_file():
        raise RuntimeError("edge-tts environment is not present")
    voice_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "id": act["id"],
            "text": act.get("narration_yue") or act["narration_zh"],
            "rate": "-12%",
            "pitch": "-18Hz",
            "voice": "zh-HK-HiuGaaiNeural",
        }
        for act in acts
    ]
    lines_file = voice_dir / "lines_royal.json"
    lines_file.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
    script_code = (
        "import asyncio, json, pathlib, sys, edge_tts\n"
        "lines = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "out_dir = pathlib.Path(sys.argv[2])\n"
        "async def run():\n"
        "    for item in lines:\n"
        "        mp3 = out_dir / (item['id'] + '.mp3')\n"
        "        comm = edge_tts.Communicate(item['text'], item['voice'], rate=item['rate'], pitch=item['pitch'])\n"
        "        await comm.save(str(mp3))\n"
        "asyncio.run(run())\n"
    )
    _run([str(LEMO_PYTHON), "-c", script_code, str(lines_file), str(voice_dir)], cwd=cwd, timeout=180)
    per_act: list[dict[str, Any]] = []
    for act in acts:
        mp3_path = voice_dir / f"{act['id']}.mp3"
        if not mp3_path.is_file() or mp3_path.stat().st_size < 512:
            raise RuntimeError(f"edge-tts royal voice produced invalid audio for {act['id']}")
        samples, duration = _read_pcm_mono_48k(mp3_path, ffmpeg, cwd)
        per_act.append({"id": act["id"], "samples": samples, "duration": duration})
    return {
        "engine": "Microsoft Edge Neural TTS (zh-HK-HiuGaaiNeural)",
        "language": "zh-HK",
        "voice_profile": "zh-HK-HiuGaaiNeural (-18Hz, -12% mature alluring royal-sister Cantonese)",
        "offline": False,
        "acts": per_act,
    }


def _load_external_voice_wavs(
    acts: list[dict[str, Any]],
    source_dir: Path,
    voice_dir: Path,
    ffmpeg: str,
    cwd: Path,
) -> dict[str, Any]:
    """Load pre-generated neural voice WAVs (e.g. CosyVoice3 emotional clones).

    Expects one file per act named '<act_id>.wav' inside source_dir. Files are
    copied into the deliverable voice_dir so packages stay self-contained.
    """
    if not source_dir.is_dir():
        raise RuntimeError(f"external voice directory is missing: {source_dir}")
    voice_dir.mkdir(parents=True, exist_ok=True)
    per_act: list[dict[str, Any]] = []
    for act in acts:
        src = source_dir / f"{act['id']}.wav"
        if not src.is_file():
            raise RuntimeError(f"external voice WAV missing for {act['id']}: {src}")
        dst = voice_dir / f"{act['id']}.wav"
        shutil.copyfile(src, dst)
        samples, duration = _read_pcm_mono_48k(dst, ffmpeg, cwd)
        per_act.append({"id": act["id"], "samples": samples, "duration": duration})
    return {
        "engine": "external neural voice WAVs (CosyVoice3 emotional Cantonese)",
        "language": "zh-HK",
        "voice_profile": "CosyVoice3-0.5B instruct-driven mature alluring royal-sister Cantonese",
        "offline": True,
        "acts": per_act,
    }


def _synthesize_voices(
    acts: list[dict[str, Any]],
    voice_dir: Path,
    ffmpeg: str,
    cwd: Path,
    voice_mode: str,
    external_dir: Path | None = None,
) -> dict[str, Any]:
    if voice_mode == "external":
        if external_dir is None:
            raise RuntimeError("--voice-dir is required when --voice-mode external")
        return _load_external_voice_wavs(acts, external_dir, voice_dir, ffmpeg, cwd)
    if voice_mode == "mandarin_neural":
        return _synthesize_act_voices_mandarin(acts, voice_dir, ffmpeg, cwd)
    if voice_mode == "cantonese_local":
        return _synthesize_act_voices_cantonese(acts, voice_dir, ffmpeg, cwd)
    if voice_mode == "cantonese_royal":
        return _synthesize_act_voices_cantonese_royal(acts, voice_dir, ffmpeg, cwd)
    try:
        return _synthesize_act_voices_mandarin(acts, voice_dir, ffmpeg, cwd)
    except Exception:
        return _synthesize_act_voices_cantonese(acts, voice_dir, ffmpeg, cwd)


def _master_soundtrack(
    *,
    voice_plan: list[dict[str, Any]],
    total_duration: float,
    output_wav: Path,
) -> None:
    """Render an original cosmic ambient + pulse bed with smooth voice-driven side-chain ducking."""
    n_samples = max(SAMPLE_RATE, int(round(total_duration * SAMPLE_RATE)))
    bgm_l = [0.0] * n_samples
    bgm_r = [0.0] * n_samples
    voice_mix = [0.0] * n_samples
    duck = [1.0] * n_samples

    # 1. Cosmic pulse + warm drone + cyber-pentatonic arpeggio notes
    note_freqs = (220.0, 261.63, 293.66, 329.63, 392.0, 440.0, 523.25)
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        fade_in = min(1.0, t / 0.35)
        fade_out = min(1.0, max(0.0, (total_duration - t) / 0.65))
        envelope = fade_in * fade_out
        pulse = 0.78 + 0.22 * math.sin(2.0 * math.pi * 1.15 * t)
        drone = (
            0.095 * math.sin(2.0 * math.pi * 55.0 * t)
            + 0.060 * math.sin(2.0 * math.pi * 82.41 * t + 0.4)
            + 0.042 * math.sin(2.0 * math.pi * 110.0 * t + 0.9)
            + 0.024 * math.sin(2.0 * math.pi * 164.81 * t + 1.3)
        )
        # Subtle melodic pluck every 1.25 seconds
        beat_pos = t % 1.25
        beat_idx = int(t // 1.25)
        freq = note_freqs[beat_idx % len(note_freqs)]
        pluck_env = math.exp(-beat_pos * 3.8) * min(1.0, beat_pos * 70.0)
        pluck = 0.045 * pluck_env * (math.sin(2.0 * math.pi * freq * t) + 0.25 * math.sin(4.0 * math.pi * freq * t))
        pan = 0.28 * math.sin(beat_idx * 1.3)
        val_l = (drone + pluck * (1.0 - pan * 0.4)) * envelope * pulse
        val_r = (drone * 0.96 + pluck * (1.0 + pan * 0.4)) * envelope * pulse
        bgm_l[i] = val_l
        bgm_r[i] = val_r

    # 2. Chapter strike accents + place voice samples + compute ducking windows
    ramp_samples = int(0.18 * SAMPLE_RATE)
    for idx, item in enumerate(voice_plan):
        act_start_i = max(0, min(n_samples - 1, int(item["act_start_seconds"] * SAMPLE_RATE)))
        strike_len = min(int(0.55 * SAMPLE_RATE), n_samples - act_start_i)
        strike_freq = 440.0 if idx == 0 else (587.33 if idx == len(voice_plan) - 1 else 329.63)
        for k in range(strike_len):
            kt = k / SAMPLE_RATE
            env = math.exp(-kt * 6.5) * min(1.0, kt * 120.0) * 0.08
            tone = env * (math.sin(2.0 * math.pi * strike_freq * kt) + 0.35 * math.sin(2.0 * math.pi * (strike_freq * 1.5) * kt))
            bgm_l[act_start_i + k] += tone
            bgm_r[act_start_i + k] += tone * 0.92

        samples = item["samples"]
        rms = math.sqrt(sum(s * s for s in samples) / len(samples)) or 1e-6
        gain = min(1.4, 0.26 / rms)
        v_start_i = max(0, min(n_samples - 1, int(item["voice_start_seconds"] * SAMPLE_RATE)))
        v_end_i = min(n_samples, v_start_i + len(samples))
        fade_len = min(int(0.02 * SAMPLE_RATE), (v_end_i - v_start_i) // 4)
        for k in range(v_end_i - v_start_i):
            w = 1.0
            if fade_len > 0:
                if k < fade_len:
                    w = k / fade_len
                elif k >= (v_end_i - v_start_i - fade_len):
                    w = max(0.0, (v_end_i - v_start_i - k) / fade_len)
            voice_mix[v_start_i + k] += samples[k] * gain * w

        d_lo = max(0, v_start_i - ramp_samples)
        d_hi = min(n_samples, v_end_i + ramp_samples)
        for k in range(d_lo, d_hi):
            if k < v_start_i:
                frac = (v_start_i - k) / max(1, ramp_samples)
                target = 0.24 + 0.76 * frac
            elif k > v_end_i:
                frac = (k - v_end_i) / max(1, ramp_samples)
                target = 0.24 + 0.76 * frac
            else:
                target = 0.24
            if target < duck[k]:
                duck[k] = target

    # 3. Mix stereo bus with soft saturation & write 48kHz stereo WAV
    with wave.open(str(output_wav), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        buf = bytearray()
        for i in range(n_samples):
            v = voice_mix[i]
            d = duck[i]
            l = math.tanh((bgm_l[i] * d + v) * 1.1) * 0.85
            r = math.tanh((bgm_r[i] * d + v) * 1.1) * 0.85
            il = max(-32767, min(32767, int(l * 32767)))
            ir = max(-32767, min(32767, int(r * 32767)))
            buf.extend(struct.pack("<hh", il, ir))
            if len(buf) >= 65536:
                wav.writeframesraw(buf)
                buf.clear()
        if buf:
            wav.writeframesraw(buf)


def _find_chrome_executable() -> Path | None:
    for candidate in (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "C:/Users/Default/AppData/Local")) / "Google/Chrome/Application/chrome.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def _write_publish_guide_cn(
    *,
    output_dir: Path,
    title: str,
    blueprint: dict[str, Any],
    aspect_ratio: str,
    master_mp4: Path,
    cover_jpg: Path,
    srt_path: Path,
    publish_res: dict[str, Any],
) -> Path:
    """Write a creator-friendly Chinese manual upload guide for Douyin, WeChat Channels, and YouTube."""
    copy = blueprint.get("platform_copy") or {}
    lines = [
        f"# {title} — 多平台成片交付与变现上架指南 ({aspect_ratio})",
        "",
        "## 一、交付核心文件（经技术 QA 全解码通过）",
        f"- **成片 MP4**: `{master_mp4.name}`（1080P · 30 FPS · H.264 + AAC 48kHz · -16 LUFS 响度达标）",
        f"- **高点击率封面图**: `{cover_jpg.name}`",
        f"- **精准时间轴字幕**: `{srt_path.name}`",
        f"- **多平台完整草稿压缩包**: `{publish_res['package_path']}`",
        "",
        "## 二、可直接复制的平台发布文案",
        "",
    ]
    platforms = (
        ("douyin", "抖音 (Douyin · 9:16 竖屏推荐)"),
        ("wechat_channels", "微信视频号 (9:16 竖屏 / 16:9 横屏均适用)"),
        ("youtube_shorts" if aspect_ratio == "9:16" else "youtube", f"YouTube ({aspect_ratio})"),
        ("bilibili", f"哔哩哔哩 B站 ({aspect_ratio})"),
    )
    for key, label in platforms:
        if key not in copy:
            continue
        p = copy[key]
        lines.extend([
            f"### {label}",
            "- **爆款备选标题**：",
            *[f"  {idx}. {t}" for idx, t in enumerate(p.get("title_variants", [title]), 1)],
            f"- **推荐标签/话题**: `{' '.join(p.get('hashtags') or p.get('tags') or [])}`",
            f"- **建议置顶评论（拉高互动完播）**: {p.get('pinned_comment', '')}",
            "- **完整简介/描述**：",
            "```text",
            p.get("description", ""),
            "```",
            "",
        ])
    lines.extend([
        "## 三、上架前人工核对清单（宪章红线）",
        "- [ ] 观看并试听全片，确认字幕与配音同步、研究引用与边界声明准确。",
        "- [ ] 依据各平台当期规则勾选「AI 合成内容 / 辅助创作」标识（如适用）。",
        "- [ ] 本文案包由本机流水线生成，**未自动登录账号、未调用发布 API、不承诺播放量或收益预测**。",
    ])
    guide = output_dir / "PUBLISH_GUIDE_CN.md"
    guide.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return guide


def render_aspect_deliverable(
    *,
    workspace: Path,
    blueprint: dict[str, Any],
    aspect_ratio: str,
    output_dir: Path,
    voice_mode: str = "auto",
    voice_dir_source: Path | None = None,
) -> dict[str, Any]:
    """Render one complete 1080p deliverable video, QA check it, package product & publish pack."""
    if aspect_ratio not in {"9:16", "16:9"}:
        raise ValueError("aspect_ratio must be '9:16' or '16:9'")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing deliverable directory: {output_dir}")

    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and FFprobe must both be installed")

    motion_root = workspace / "video-motion"
    node = shutil.which("node")
    remotion_cli = motion_root / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    if not node or not remotion_cli.is_file():
        raise RuntimeError("Node.js and local video-motion @remotion/cli are required")
    browser = _find_chrome_executable()

    width, height = (1920, 1080) if aspect_ratio == "16:9" else (1080, 1920)
    fps = 30
    transition_frames = 18
    acts = blueprint["acts"]

    cycle_id = blueprint.get("evolution_cycle_id") or new_evolution_cycle_id()
    progress_db = workspace / "data" / "tasks" / "muse.sqlite3"
    progress_db.parent.mkdir(parents=True, exist_ok=True)
    record_evolution_cycle_event(
        progress_db, cycle_id, "started",
        summary=f"Flagship research video synthesis started ({aspect_ratio}).",
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    try:
        # 1. Synthesize real narration per act
        voice_dir = output_dir / "voice"
        voice_bundle = _synthesize_voices(acts, voice_dir, ffmpeg, workspace, voice_mode, voice_dir_source)

        # 2. Compute deterministic frame-exact TransitionSeries schedule
        frame_cursor = 0
        remotion_acts: list[dict[str, Any]] = []
        voice_plan: list[dict[str, Any]] = []
        srt_cues: list[tuple[float, float, str]] = []

        for idx, (act, v_info) in enumerate(zip(acts, voice_bundle["acts"])):
            is_first = idx == 0
            is_last = idx == len(acts) - 1
            lead_frames = 10 if is_first else 16
            tail_frames = 14 + (0 if is_last else transition_frames)
            voice_frames = math.ceil(v_info["duration"] * fps)
            act_frames = max(90, lead_frames + voice_frames + tail_frames)

            act_start_sec = frame_cursor / fps
            voice_start_sec = (frame_cursor + lead_frames) / fps
            voice_end_sec = voice_start_sec + v_info["duration"]
            cue_end_sec = voice_start_sec + max(v_info["duration"] + 0.35, 2.2)

            voice_plan.append({
                "id": act["id"],
                "act_start_seconds": act_start_sec,
                "voice_start_seconds": voice_start_sec,
                "voice_end_seconds": voice_end_sec,
                "samples": v_info["samples"],
                "duration": v_info["duration"],
            })
            srt_cues.append((voice_start_sec, cue_end_sec, act["caption"]))
            remotion_acts.append({
                "id": act["id"],
                "chapter": act["chapter"],
                "headline": act["headline"],
                "subheadline": act["subheadline"],
                "statValue": act["statValue"] if "statValue" in act else act["stat_value"],
                "statLabel": act["statLabel"] if "statLabel" in act else act["stat_label"],
                "citationBadge": act["citationBadge"] if "citationBadge" in act else act["citation_badge"],
                "visualMode": act["visualMode"] if "visualMode" in act else act["visual_mode"],
                "accent": act["accent"],
                "caption": act["caption"],
                "durationInFrames": act_frames,
            })
            frame_cursor += act_frames - (transition_frames if not is_last else 0)

        total_frames = max(60, frame_cursor)
        total_duration = total_frames / fps

        # 3. Render mastered original soundtrack + voice mix + write SRT
        raw_mix_wav = output_dir / "master-mix-raw.wav"
        _master_soundtrack(voice_plan=voice_plan, total_duration=total_duration, output_wav=raw_mix_wav)

        srt_path = output_dir / "subtitles.srt"
        srt_lines = [
            f"{idx}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}"
            for idx, (start, end, text) in enumerate(srt_cues, 1)
        ]
        srt_path.write_text("\n\n".join(srt_lines) + "\n", encoding="utf-8-sig")

        # 4. Render native 1080p video + viral cover still with local Remotion
        props = {
            "title": blueprint["title"],
            "subtitle": blueprint["subtitle"],
            "hookBadge": "MUSE · PROMETHEUS STUDIO",
            "aspectRatio": aspect_ratio,
            "fps": fps,
            "transitionFrames": transition_frames,
            "coverMode": False,
            "acts": remotion_acts,
        }
        props_path = output_dir / "remotion-props.json"
        props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")

        silent_mp4 = output_dir / "remotion-silent.mp4"
        remotion_cmd = [
            node, str(remotion_cli), "render", "src/index.tsx", "MuseViralResearchFilm",
            str(silent_mp4), f"--props={props_path}", "--codec=h264", "--pixel-format=yuv420p", "--crf=17",
        ]
        if browser is not None:
            remotion_cmd.extend(["--browser-executable", str(browser)])
        _run(remotion_cmd, cwd=motion_root, timeout=1200)

        cover_props = {**props, "coverMode": True}
        cover_props_path = output_dir / "remotion-cover-props.json"
        cover_props_path.write_text(json.dumps(cover_props, ensure_ascii=False, indent=2), encoding="utf-8")
        cover_png = output_dir / "cover-raw.png"
        cover_cmd = [
            node, str(remotion_cli), "still", "src/index.tsx", "MuseViralResearchFilm",
            str(cover_png), f"--props={cover_props_path}", "--frame=18", "--image-format=png",
        ]
        if browser is not None:
            cover_cmd.extend(["--browser-executable", str(browser)])
        _run(cover_cmd, cwd=motion_root, timeout=180)
        cover_jpg = output_dir / "cover.jpg"
        _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(cover_png), "-q:v", "2", str(cover_jpg)], cwd=workspace, timeout=60)
        cover_png.unlink(missing_ok=True)

        # 5. Mux silent 1080p video + -16 LUFS loudnorm broadcast audio into faststart MP4
        slug = "9x16" if aspect_ratio == "9:16" else "16x9"
        final_mp4 = output_dir / f"muse-flagship-{slug}.mp4"
        _run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(silent_mp4),
                "-i", str(raw_mix_wav),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=9,apad",
                "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-ac", "2",
                "-t", f"{total_duration:.6f}",
                "-movflags", "+faststart",
                str(final_mp4),
            ],
            cwd=workspace,
            timeout=600,
        )
        silent_mp4.unlink(missing_ok=True)

        # 6. Probe & full decode check before registering product
        probe = json.loads(
            _run(
                [
                    ffprobe, "-v", "error",
                    "-show_entries", "format=duration,size:stream=codec_name,codec_type,width,height,avg_frame_rate,channels,sample_rate",
                    "-of", "json", str(final_mp4),
                ],
                cwd=workspace, timeout=30,
            ).stdout
        )
        streams = probe.get("streams", [])
        v_stream = next((x for x in streams if x.get("codec_type") == "video"), None)
        a_stream = next((x for x in streams if x.get("codec_type") == "audio"), None)
        actual_duration = float(probe.get("format", {}).get("duration", 0))
        if (
            not v_stream or not a_stream
            or int(v_stream.get("width", 0)) != width or int(v_stream.get("height", 0)) != height
            or a_stream.get("sample_rate") != str(SAMPLE_RATE)
        ):
            raise RuntimeError(f"Rendered master MP4 failed stream verification: {probe}")
        _run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(final_mp4), "-f", "null", "-"], cwd=workspace, timeout=300)

        # 7. Package into VideoProductBuilder (runs full video_qa gate!)
        builder = VideoProductBuilder(workspace)
        generation_meta = {
            "status": "completed",
            "model": "Muse Remotion 4.0.543 native React 1080p + FFmpeg -16 LUFS mastering",
            "aspect_ratio": aspect_ratio,
            "fps": fps,
            "duration_seconds": round(actual_duration, 3),
            "frames": round(actual_duration * fps),
            "width": width,
            "height": height,
            "bytes": final_mp4.stat().st_size,
            "has_audio": True,
        }
        scene_records = [
            {
                "order": idx,
                "prompt": act["headline"],
                "generation_status": "generated",
            }
            for idx, act in enumerate(remotion_acts, 1)
        ]
        storyboard_scenes = [
            {
                "order": idx,
                "id": act["id"],
                "prompt": act["headline"],
                "generation_status": "remotion_native_motion_graphics",
                "duration_seconds": round(act["durationInFrames"] / fps, 3),
            }
            for idx, act in enumerate(remotion_acts, 1)
        ]
        product = builder.package_generated_clip(
            blueprint["title"],
            f"Muse 灵感深研→自我升级→1080P ({aspect_ratio}) 视听成片。",
            final_mp4,
            generation_metadata=generation_meta,
            scene_records=scene_records,
            evolution_cycle_id=cycle_id,
        )
        verified = builder.verify_path(workspace / "data" / "products", product["initiative_id"], product["version_id"])
        if not verified.get("validated") or verified.get("mp4_sha256") != _sha256(final_mp4):
            raise RuntimeError("VideoProductBuilder integrity verification failed")

        product_dir = workspace / product["relative_path"]
        qa_report = json.loads((product_dir / "qa-report.json").read_text(encoding="utf-8"))

        storyboard = {
            "schema_version": 1,
            "title": blueprint["title"],
            "production_mode": "remotion_1080p_native_motion_plus_neural_voiceover",
            "evolution_cycle_id": cycle_id,
            "inspiration_id": blueprint.get("inspiration_id"),
            "duration_seconds": round(actual_duration, 3),
            "aspect_ratio": aspect_ratio,
            "fps": fps,
            "width": width,
            "height": height,
            "voiceover": f"{voice_bundle['engine']} ({voice_bundle['language']})",
            "audio": "Original synthesized cosmic ambient & pentatonic arpeggio bed with voice-driven sidechain ducking and -16 LUFS mastering",
            "platform_copy": blueprint.get("platform_copy", {}),
            "scenes": storyboard_scenes,
        }
        storyboard_path = output_dir / "storyboard.json"
        storyboard_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        notes_path = output_dir / "source-notes.json"
        notes_path.write_text(json.dumps(blueprint["source_notes"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        readme_text = (
            f"# {blueprint['title']} ({aspect_ratio})\n\n"
            f"- 画面：{width}×{height} · {fps} FPS · 本地 Remotion 4.0.543 原生 React 动态数据视觉渲染（无低清放大、无静止卡顿帧）。\n"
            f"- 配音：{voice_bundle['engine']}（{voice_bundle['voice_profile']}） + 原创侧链避让配乐（-16 LUFS 响度）。\n"
            f"- 研发闭环：源自 Muse 灵感池 -> 自主正反双路深研 -> 自我升级提案（人工审批门禁）-> 自动编译成片。\n"
            f"- 技术 QA 状态：{qa_report.get('status')}；警示数：{len(qa_report.get('warnings') or [])}；产品状态：testing。\n"
        )
        readme_path = output_dir / "README.md"
        readme_path.write_text(readme_text, encoding="utf-8")

        bundle_path = output_dir / "delivery-bundle.zip"
        bundle_files = [
            (final_mp4, final_mp4.name),
            (cover_jpg, "cover.jpg"),
            (srt_path, "subtitles.srt"),
            (storyboard_path, "storyboard.json"),
            (notes_path, "source-notes.json"),
            (readme_path, "README.md"),
            (product_dir / "product.zip", "muse-product.zip"),
            (product_dir / "manifest.json", "muse-product-manifest.json"),
        ]
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as arc:
            for src, name in bundle_files:
                arc.write(src, name)

        result = {
            "status": "completed",
            "title": blueprint["title"],
            "aspect_ratio": aspect_ratio,
            "evolution_cycle_id": cycle_id,
            "inspiration_id": blueprint.get("inspiration_id"),
            "visual_provenance": "Native 1080p Remotion 4.0.543 dynamic motion graphics + FFmpeg mastering",
            "mp4": {
                "path": final_mp4.relative_to(workspace).as_posix(),
                "bytes": final_mp4.stat().st_size,
                "sha256": _sha256(final_mp4),
                "duration_seconds": round(actual_duration, 3),
                "width": width,
                "height": height,
                "fps": fps,
                "codec": "h264",
                "audio_codec": "aac",
                "audio_sample_rate": SAMPLE_RATE,
                "full_decode": "passed",
            },
            "cover": {
                "path": cover_jpg.relative_to(workspace).as_posix(),
                "sha256": _sha256(cover_jpg),
            },
            "product": product,
            "product_validation": verified,
            "qa": qa_report,
            "bundle": {
                "path": bundle_path.relative_to(workspace).as_posix(),
                "bytes": bundle_path.stat().st_size,
                "sha256": _sha256(bundle_path),
            },
            "voiceover": {
                "engine": voice_bundle["engine"],
                "language": voice_bundle["language"],
                "offline": voice_bundle["offline"],
                "acts": [
                    {"id": a["id"], "start": round(a["voice_start_seconds"], 3), "end": round(a["voice_end_seconds"], 3)}
                    for a in voice_plan
                ],
            },
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        (output_dir / "generation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # 8. Build the product-scoped handoff package and optional visual-review evidence.
        # Contact-sheet failure is recorded explicitly; it never blocks technical QA or
        # gets represented as completed visual review. Package integrity errors still fail.
        platforms = VIRAL_MONETIZATION_PLATFORMS_9X16 if aspect_ratio == "9:16" else VIRAL_MONETIZATION_PLATFORMS_16X9
        publish_pack = build_publish_package_with_optional_generated_contact_sheet(
            workspace,
            output_dir,
            platforms=platforms,
        )
        guide_path = _write_publish_guide_cn(
            output_dir=output_dir,
            title=blueprint["title"],            blueprint=blueprint,
            aspect_ratio=aspect_ratio,
            master_mp4=final_mp4,
            cover_jpg=cover_jpg,
            srt_path=srt_path,
            publish_res=publish_pack,
        )
        result["publish_package"] = publish_pack
        result["publish_guide_path"] = guide_path.relative_to(workspace).as_posix()
        (output_dir / "generation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        record_evolution_cycle_event(
            progress_db, cycle_id, "completed",
            summary=f"Flagship {aspect_ratio} research video & multi-platform publish pack generated.",
        )
        return result
    except Exception:
        record_evolution_cycle_event(
            progress_db, cycle_id, "failed",
            summary=f"Flagship {aspect_ratio} research video failed.",
            error_code="video.flagship_render_failed",
        )
        raise


def _load_or_compile_blueprint(
    workspace: Path,
    *,
    blueprint_path: str | None,
    inspiration_id: str | None,
) -> dict[str, Any]:
    if blueprint_path:
        raw = Path(blueprint_path)
        candidate = raw if raw.is_absolute() else (workspace / raw)
        candidate = candidate.resolve(strict=True)
        if workspace not in candidate.parents:
            raise ValueError("blueprint path escapes workspace")
        return json.loads(candidate.read_text(encoding="utf-8"))

    latest = workspace / "data" / "prometheus" / "blueprints" / "latest-blueprint.json"
    if latest.is_file() and not inspiration_id:
        return json.loads(latest.read_text(encoding="utf-8"))

    evolved = evolve_inspiration_closed_loop(workspace, inspiration_id=inspiration_id)
    bp = workspace / evolved["blueprint_path"]
    return json.loads(bp.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render Muse flagship 9:16 & 16:9 research-to-video deliverables")
    parser.add_argument("--workspace", default=str(ROOT_DIR))
    parser.add_argument("--output-dir", default=os.environ.get("MUSE_PIPELINE_OUTPUT_DIR", ""))
    parser.add_argument("--aspect", choices=("9:16", "16:9", "both"), default=os.environ.get("MUSE_PIPELINE_ASPECT_RATIO", ""))
    parser.add_argument("--recipe", default=os.environ.get("MUSE_PIPELINE_RECIPE_ID", "inspiration_viral_9x16_v1"))
    parser.add_argument("--blueprint", default=os.environ.get("MUSE_PIPELINE_BLUEPRINT", ""))
    parser.add_argument("--inspiration-id", default=os.environ.get("MUSE_PIPELINE_INSPIRATION_ID", ""))
    parser.add_argument("--title", default=os.environ.get("MUSE_PIPELINE_TITLE", ""))
    parser.add_argument("--voice-mode", choices=("auto", "mandarin_neural", "cantonese_local", "cantonese_royal", "external"), default="auto")
    parser.add_argument("--voice-dir", default="", help="Directory of per-act WAVs for --voice-mode external")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    voice_dir_source = Path(args.voice_dir).resolve() if args.voice_dir.strip() else None
    aspect = args.aspect
    if not aspect:
        aspect = "16:9" if args.recipe == "inspiration_viral_16x9_v1" else "9:16"

    try:
        blueprint = _load_or_compile_blueprint(
            workspace,
            blueprint_path=args.blueprint or None,
            inspiration_id=args.inspiration_id or None,
        )
        if args.title.strip():
            blueprint["title"] = args.title.strip()[:160]

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base_out = (
            Path(args.output_dir).resolve()
            if args.output_dir
            else (workspace / "data" / "prometheus" / f"flagship-{stamp}").resolve()
        )
        if aspect == "both":
            res_9 = render_aspect_deliverable(
                workspace=workspace, blueprint=blueprint, aspect_ratio="9:16",
                output_dir=base_out / "vertical-9x16", voice_mode=args.voice_mode,
                voice_dir_source=voice_dir_source,
            )
            res_16 = render_aspect_deliverable(
                workspace=workspace, blueprint=blueprint, aspect_ratio="16:9",
                output_dir=base_out / "landscape-16x9", voice_mode=args.voice_mode,
                voice_dir_source=voice_dir_source,
            )
            print(json.dumps({"status": "completed", "vertical_9x16": res_9, "landscape_16x9": res_16}, ensure_ascii=False, indent=2))
        else:
            res = render_aspect_deliverable(
                workspace=workspace, blueprint=blueprint, aspect_ratio=aspect,
                output_dir=base_out, voice_mode=args.voice_mode,
                voice_dir_source=voice_dir_source,
            )
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "message": str(exc)[:1200]}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
