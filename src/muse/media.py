"""Local media-tool discovery, task routing and safe AMD AMF transcoding.

This module does not launch GUI applications, download models or invoke LLMs.
Media processing is delegated to already-installed tools such as FFmpeg.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class MediaTool:
    tool_id: str
    name: str
    executable: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class MediaInventory:
    tools: tuple[MediaTool, ...]
    ffmpeg_version: str | None
    hardware_encoders: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "tools": [asdict(tool) for tool in self.tools],
            "ffmpeg_version": self.ffmpeg_version,
            "hardware_encoders": list(self.hardware_encoders),
        }


_TOOL_CANDIDATES = (
    ("obs", "OBS Studio", "D:/Program Files/obs-studio/bin/64bit/obs64.exe",
     ("video_capture", "screen_recording", "live_streaming")),
    ("audacity", "Audacity", "C:/Program Files/Audacity/audacity.exe",
     ("audio_edit", "audio_cleanup", "audio_export")),
    ("studio_one", "PreSonus Studio One 6", "C:/Program Files/PreSonus/Studio One 6/Studio One.exe",
     ("audio_mix", "audio_master", "music_production")),
    ("boilsoft", "Boilsoft Video Splitter", "D:/software/BoilsoftVideoSplitter/BoilsoftVideoSplitterPortable.exe",
     ("video_split", "lossless_clip_selection")),
    ("hd_video_converter", "HD Video Converter Factory Pro",
     "D:/software/HDVideoConverterFactoryPro/App/HDVideoConverterFactoryPro/VideoConverterFactoryPro.exe",
     ("video_convert_gui", "video_format_conversion")),
    ("piper_tts", "Piper TTS (local CPU)", "D:/Tools/MuseTTS/Piper/venv-zh/Scripts/piper.exe",
     ("speech_synthesis", "voiceover_generation", "offline_tts")),
    ("chattts_webui", "ChatTTS WebUI", "D:/software/ChatTTS-WebUI/run_webui.bat",
     ("speech_synthesis", "voiceover_generation")),
)

_CODEC_ENCODERS = {
    "h264": "h264_amf",
    "hevc": "hevc_amf",
    "av1": "av1_amf",
}


def discover_media_tools(
    *,
    which: Callable[[str], str | None] | None = None,
    exists: Callable[[str], bool] | None = None,
    runner: Callable = subprocess.run,
) -> MediaInventory:
    """Discover known installed tools and query FFmpeg's advertised AMF encoders."""
    which = which or shutil.which
    exists = exists or (lambda value: Path(value).is_file())
    tools: list[MediaTool] = []

    ffmpeg_path = which("ffmpeg")
    if not ffmpeg_path:
        fallback = "D:/software/ffmpeg-release-full/ffmpeg-8.1.1-full_build/bin/ffmpeg.exe"
        if exists(fallback):
            ffmpeg_path = fallback
    ffprobe_path = which("ffprobe")
    if ffmpeg_path:
        tools.append(MediaTool("ffmpeg", "FFmpeg", ffmpeg_path,
                               ("video_transcode", "video_filter", "audio_convert", "media_probe")))
    if ffprobe_path:
        tools.append(MediaTool("ffprobe", "FFprobe", ffprobe_path,
                               ("media_probe", "metadata_extract")))

    for tool_id, name, path, capabilities in _TOOL_CANDIDATES:
        if exists(path):
            tools.append(MediaTool(tool_id, name, path, capabilities))

    version = None
    encoders: set[str] = set()
    if ffmpeg_path:
        try:
            version_result = runner(
                [ffmpeg_path, "-hide_banner", "-version"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            version_text = (version_result.stdout or "") + "\n" + (version_result.stderr or "")
            match = re.search(r"\bffmpeg version\s+([^\s]+)", version_text, re.IGNORECASE)
            version = match.group(1) if match else None

            encoder_result = runner(
                [ffmpeg_path, "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            encoder_text = (encoder_result.stdout or "") + "\n" + (encoder_result.stderr or "")
            for encoder in _CODEC_ENCODERS.values():
                if re.search(rf"\b{re.escape(encoder)}\b", encoder_text):
                    encoders.add(encoder)
        except (OSError, subprocess.SubprocessError):
            # Discovery remains useful even when FFmpeg cannot be queried.
            pass

    return MediaInventory(
        tools=tuple(tools),
        ffmpeg_version=version,
        hardware_encoders=tuple(sorted(encoders)),
    )


_TASK_ROUTES = {
    "video_transcode": ("ffmpeg", "Use AMD AMF hardware encoding when the requested AMF encoder is available."),
    "video_probe": ("ffprobe", "Read media metadata without re-encoding."),
    "video_capture": ("obs", "Use OBS for screen/camera capture and recording."),
    "video_split": ("boilsoft", "Use the existing splitter for quick clip extraction."),
    "video_convert_gui": ("hd_video_converter", "Use the installed GUI converter for guided format conversion."),
    "audio_edit": ("audacity", "Use Audacity for waveform editing, cleanup and export."),
    "audio_mix": ("studio_one", "Use Studio One and its installed audio plug-ins for mixing/mastering."),
    "speech_synthesis": ("piper_tts", "Use the local Piper CLI on CPU; an installed CC0 Chinese ONNX voice model and JSON config are still required."),
}


def plan_media_task(task: str, inventory: MediaInventory) -> dict[str, object]:
    """Recommend one installed tool; report missing capabilities rather than guessing."""
    if task == "video_timeline_edit":
        return {
            "task": task,
            "status": "missing",
            "primary_tool": None,
            "limitation": "No dedicated non-linear timeline editor (such as Resolve, Premiere or CapCut) was confirmed in the installed-app inventory.",
            "workaround": "Use FFmpeg for deterministic cuts, filters, overlays and batch assembly; this is not a replacement for a full timeline editor.",
        }
    if task not in _TASK_ROUTES:
        raise ValueError(f"unknown media task: {task}")
    tool_id, guidance = _TASK_ROUTES[task]
    available = {tool.tool_id for tool in inventory.tools}
    if task == "speech_synthesis" and tool_id == "piper_tts" and tool_id in available:
        model = Path("D:/Tools/MuseTTS/Piper/models/zh/zh_CN-chaowen-medium.onnx")
        if not model.is_file() or not Path(f"{model}.json").is_file():
            return {
                "task": task, "status": "partial", "primary_tool": tool_id,
                "guidance": guidance,
                "limitation": "Piper runtime is installed, but the configured Chinese voice model/config is missing; no model download is attempted.",
            }
    if tool_id not in available:
        return {
            "task": task, "status": "missing", "primary_tool": None,
            "guidance": guidance, "limitation": f"Required installed tool '{tool_id}' was not detected.",
        }
    result: dict[str, object] = {
        "task": task, "status": "ready", "primary_tool": tool_id, "guidance": guidance,
    }
    if task == "video_transcode":
        result["hardware_encoders"] = list(inventory.hardware_encoders)
        result["hardware_acceleration_ready"] = bool(inventory.hardware_encoders)
    return result


def build_transcode_command(
    input_path: str | Path,
    output_path: str | Path,
    *,
    ffmpeg_path: str,
    codec: str = "h264",
    available_encoders: set[str] | frozenset[str] | tuple[str, ...] = (),
    bitrate: str = "8M",
    scale: str | None = None,
) -> list[str]:
    """Build a no-overwrite FFmpeg command using AMD AMF only (no CPU fallback)."""
    source = Path(input_path)
    target = Path(output_path)
    if not ffmpeg_path.strip():
        raise ValueError("ffmpeg_path must not be empty")
    if not source.is_file():
        raise FileNotFoundError(f"input video does not exist: {source}")
    if target.exists():
        raise FileExistsError(f"output already exists; refusing to overwrite: {target}")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {target.parent}")
    if source.resolve() == target.resolve():
        raise ValueError("input and output paths must be different")
    if target.suffix.lower() != ".mp4":
        raise ValueError("AMF transcode output must use an .mp4 extension")
    if codec not in _CODEC_ENCODERS:
        raise ValueError("codec must be h264, hevc or av1")
    encoder = _CODEC_ENCODERS[codec]
    if encoder not in set(available_encoders):
        raise RuntimeError(f"FFmpeg does not advertise the required AMD AMF encoder: {encoder}")
    if not re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?[KMG]", bitrate, re.IGNORECASE):
        raise ValueError("bitrate must be a positive value such as 8M or 4500K")
    command = [
        ffmpeg_path, "-hide_banner", "-nostdin", "-n",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", encoder, "-b:v", bitrate, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
    ]
    if scale:
        if not re.fullmatch(r"[1-9][0-9]*x(?:[1-9][0-9]*|-2)", scale):
            raise ValueError("scale must look like 1920x1080 or 1280x-2")
        width, height = scale.split("x", 1)
        command.extend(["-vf", f"scale={width}:{height}"])
    command.append(str(target))
    return command


def transcode_video(
    input_path: str | Path,
    output_path: str | Path,
    *,
    ffmpeg_path: str,
    available_encoders: set[str] | frozenset[str] | tuple[str, ...],
    codec: str = "h264",
    bitrate: str = "8M",
    scale: str | None = None,
    execute: bool = False,
    runner: Callable = subprocess.run,
    gpu_resource_db: str | Path | None = None,
) -> dict[str, object]:
    """Plan by default; execute only when explicitly requested."""
    command = build_transcode_command(
        input_path, output_path, ffmpeg_path=ffmpeg_path, codec=codec,
        available_encoders=available_encoders, bitrate=bitrate, scale=scale,
    )
    if not execute:
        return {"status": "planned", "command": command, "hardware_encoder": _CODEC_ENCODERS[codec]}
    try:
        from muse.gpu_resources import GpuLeaseSession, GpuResourceCoordinator, probe_gpu_workloads
        workspace = Path(__file__).resolve().parents[2]
        database = (
            Path(gpu_resource_db).expanduser()
            if gpu_resource_db is not None
            else workspace / "data" / "gpu-resources.sqlite3"
        )
        coordinator = GpuResourceCoordinator(database)
        with GpuLeaseSession(
            coordinator, "media-transcode-" + uuid.uuid4().hex[:16],
            owner_kind="video_encoding", model_probe=lambda: probe_gpu_workloads(workspace),
        ):
            result = runner(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=3600, check=False,
            )
    except subprocess.TimeoutExpired:
        return {"status": "timed_out", "timeout_seconds": 3600, "output": str(output_path)}
    except OSError as exc:
        return {"status": "failed", "error": str(exc), "output": str(output_path)}
    except RuntimeError as exc:
        return {"status": "blocked", "stage": "gpu_resource_lock", "reason": str(exc),
                "output": str(output_path)}
    stderr = (result.stderr or "").splitlines()
    return {
        "status": "completed" if result.returncode == 0 else "failed",
        "exit_code": result.returncode,
        "output": str(output_path),
        "hardware_encoder": _CODEC_ENCODERS[codec],
        "stderr_tail": stderr[-12:],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover and route local media tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="inventory installed media tools and AMD AMF encoders")
    plan_parser = subparsers.add_parser("plan", help="recommend a tool for a media task")
    plan_parser.add_argument("task", choices=sorted((*_TASK_ROUTES.keys(), "video_timeline_edit")))
    transcode_parser = subparsers.add_parser("transcode", help="plan or execute AMD AMF video transcode")
    transcode_parser.add_argument("input")
    transcode_parser.add_argument("output")
    transcode_parser.add_argument("--codec", choices=sorted(_CODEC_ENCODERS), default="h264")
    transcode_parser.add_argument("--bitrate", default="8M")
    transcode_parser.add_argument("--scale")
    transcode_parser.add_argument("--execute", action="store_true", help="actually run FFmpeg; default is dry-run")
    args = parser.parse_args(argv)

    inventory = discover_media_tools()
    if args.command == "doctor":
        print(json.dumps(inventory.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "plan":
        result = plan_media_task(args.task, inventory)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ready" else 2
    ffmpeg = next((tool.executable for tool in inventory.tools if tool.tool_id == "ffmpeg"), None)
    if not ffmpeg:
        print(json.dumps({"status": "missing", "error": "FFmpeg was not detected."}, indent=2))
        return 2
    ffprobe = next((tool.executable for tool in inventory.tools if tool.tool_id == "ffprobe"), None)
    if not ffprobe:
        print(json.dumps({"status": "blocked", "reason": "FFprobe is required for real encoder capability verification"}, indent=2))
        return 2
    from muse.video_capabilities import probe_ffmpeg_encoder
    required_encoder = _CODEC_ENCODERS[args.codec]
    encoder_capability = probe_ffmpeg_encoder(ffmpeg, ffprobe, required_encoder)
    if encoder_capability.get("capability", {}).get("status") != "available":
        print(json.dumps({"status": "blocked", "reason": "requested AMD AMF encoder failed real MP4/FFprobe/full-decode validation",
                          "encoder_capability": encoder_capability}, ensure_ascii=False, indent=2))
        return 2
    result = transcode_video(
        args.input, args.output, ffmpeg_path=ffmpeg,
        available_encoders={required_encoder},
        codec=args.codec, bitrate=args.bitrate, scale=args.scale, execute=args.execute,
    )
    result["encoder_capability"] = encoder_capability
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"planned", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
