"""Local video capability probes with health and capability reported independently.

Encoder availability is established by a real MP4 encode, FFprobe inspection and a
full FFmpeg decode. Service reachability never implies model/workflow capability.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


_ENCODER_FORMATS = {
    "h264_amf": "nv12",
    "hevc_amf": "nv12",
    "av1_amf": "nv12",
    "libx264": "yuv420p",
}
_ENCODER_CODECS = {
    "h264_amf": "h264",
    "hevc_amf": "hevc",
    "av1_amf": "av1",
    "libx264": "h264",
}


def _observation(
    health_status: str, health_detail: dict[str, Any],
    capability_status: str, capability_detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "health": {"status": health_status, **health_detail},
        "capability": {"status": capability_status, **capability_detail},
        "ready": health_status == "healthy" and capability_status == "available",
    }


def probe_ffmpeg_encoder(
    ffmpeg_path: str, ffprobe_path: str, encoder: str, *,
    runner: Callable = subprocess.run, timeout_seconds: int = 45,
) -> dict[str, Any]:
    """Prove one encoder by writing a tiny MP4, probing metadata and decoding all frames."""
    if encoder not in _ENCODER_FORMATS:
        raise ValueError("encoder must be h264_amf, hevc_amf, av1_amf or libx264")
    if not ffmpeg_path or not ffprobe_path:
        return _observation("unavailable", {"reason": "FFmpeg or FFprobe executable is missing"},
                            "unavailable", {"encoder": encoder})
    try:
        with tempfile.TemporaryDirectory(prefix="muse-encoder-probe-") as temporary:
            output = Path(temporary) / (encoder + ".mp4")
            encode_command = [
                ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x320:r=2:d=1",
                "-frames:v", "2", "-an", "-c:v", encoder,
                "-pix_fmt", _ENCODER_FORMATS[encoder], "-movflags", "+faststart", str(output),
            ]
            encoded = runner(
                encode_command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout_seconds, check=False,
            )
            if encoded.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
                reason = (getattr(encoded, "stderr", "") or "encoder produced no MP4")[-1200:]
                return _observation("healthy", {"ffmpeg_invoked": True}, "unavailable",
                                    {"encoder": encoder, "stage": "encode", "exit_code": encoded.returncode,
                                     "reason": reason})
            probe_command = [
                ffprobe_path, "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(output),
            ]
            probed = runner(
                probe_command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=min(timeout_seconds, 20), check=False,
            )
            if probed.returncode != 0:
                return _observation("healthy", {"ffmpeg_invoked": True}, "unavailable",
                                    {"encoder": encoder, "stage": "ffprobe", "reason": (probed.stderr or "")[-1200:]})
            metadata = json.loads(probed.stdout or "{}")
            streams = metadata.get("streams", [])
            video = next((item for item in streams if item.get("codec_type") == "video"), None)
            duration = float(metadata.get("format", {}).get("duration", 0))
            if (not isinstance(video, dict) or video.get("width") != 320 or video.get("height") != 320
                    or video.get("codec_name") != _ENCODER_CODECS[encoder] or duration <= 0):
                return _observation("healthy", {"ffmpeg_invoked": True}, "unavailable",
                                    {"encoder": encoder, "stage": "metadata", "reason": "MP4 metadata failed validation"})
            decode_command = [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-i", str(output), "-f", "null", "-"]
            decoded = runner(
                decode_command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout_seconds, check=False,
            )
            if decoded.returncode != 0 or (decoded.stderr or "").strip():
                return _observation("healthy", {"ffmpeg_invoked": True}, "unavailable",
                                    {"encoder": encoder, "stage": "full_decode", "exit_code": decoded.returncode,
                                     "reason": (decoded.stderr or "")[-1200:]})
            return _observation("healthy", {"ffmpeg_invoked": True}, "available", {
                "encoder": encoder, "codec": video["codec_name"], "width": video["width"],
                "height": video["height"], "duration_seconds": duration,
                "encode_exit_code": encoded.returncode, "ffprobe_exit_code": probed.returncode,
                "full_decode_exit_code": decoded.returncode, "output_bytes": output.stat().st_size,
            })
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        return _observation("unavailable", {"reason": type(exc).__name__}, "unavailable",
                            {"encoder": encoder, "reason": str(exc)[:500]})


def probe_ffmpeg_encoders(
    ffmpeg_path: str, ffprobe_path: str, *, runner: Callable = subprocess.run,
    timeout_seconds: int = 45,
) -> dict[str, dict[str, Any]]:
    """Probe AMD AMF and CPU H.264 independently; listing an encoder is not proof."""
    return {
        encoder: probe_ffmpeg_encoder(
            ffmpeg_path, ffprobe_path, encoder, runner=runner, timeout_seconds=timeout_seconds,
        )
        for encoder in ("h264_amf", "hevc_amf", "av1_amf", "libx264")
    }


def _service(health: dict[str, Any], capability: dict[str, Any]) -> dict[str, Any]:
    return _observation(
        str(health.get("status", "unknown")), {key: value for key, value in health.items() if key != "status"},
        str(capability.get("status", "unknown")), {key: value for key, value in capability.items() if key != "status"},
    )


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _find_model_file(models_root: Path, filename: str) -> Path | None:
    direct = models_root / filename
    if direct.is_file():
        return direct
    try:
        return next((path for path in models_root.rglob(filename) if path.is_file()), None)
    except OSError:
        return None


def probe_video_services(
    workspace: str | Path, *, comfyui_endpoint: str | None = None,
    runner: Callable = subprocess.run,
) -> dict[str, dict[str, Any]]:
    """Inspect local service health and installed capability without downloading models."""
    root = Path(workspace).resolve()
    endpoint = comfyui_endpoint or os.environ.get("MUSE_COMFYUI_ENDPOINT", "http://127.0.0.1:8188")
    from muse.video_engines import ComfyUIClient

    comfy = ComfyUIClient(endpoint, timeout_seconds=2.0)
    comfy_health_raw = comfy.health()
    comfy_healthy = comfy_health_raw.get("status") == "ready"
    comfy_health = {"status": "healthy" if comfy_healthy else "unavailable",
                    "queue_running": comfy_health_raw.get("running_jobs"),
                    "queue_pending": comfy_health_raw.get("pending_jobs"),
                    "reason": comfy_health_raw.get("reason")}
    core_nodes = ["KSampler", "VAEDecode", "SaveImage"]
    if comfy_healthy:
        try:
            core = comfy.object_info_summary(core_nodes)
            missing = core.get("missing_required_nodes", [])
            comfy_capability = {"status": "available" if not missing else "unavailable",
                                "required_nodes": core_nodes, "missing_nodes": missing}
        except (RuntimeError, ValueError, TypeError) as exc:
            comfy_capability = {"status": "unknown", "reason": type(exc).__name__}
    else:
        comfy_capability = {"status": "unknown", "reason": "ComfyUI is offline; node capability was not queried"}

    workflow_path = root / "workflows" / "wan21_t2v_smoke.json"
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        if not isinstance(workflow, dict) or not workflow:
            raise ValueError("Wan workflow is empty or invalid")
        required_nodes = sorted({
            str(node.get("class_type")) for node in workflow.values()
            if isinstance(node, dict) and isinstance(node.get("class_type"), str)
        })
        model_names = sorted({
            value for node in workflow.values() if isinstance(node, dict)
            for value in _walk_strings(node.get("inputs", {}))
            if value.casefold().endswith((".safetensors", ".ckpt", ".gguf"))
        })
        models_root_value = os.environ.get("MUSE_COMFYUI_MODELS_DIR", "").strip()
        if not models_root_value:
            candidates = [
                Path("D:/AI/ComfyUI-Rinsei/portable/ComfyUI_windows_portable/ComfyUI/models"),
                root / "ComfyUI" / "models",
            ]
            detected = next((candidate for candidate in candidates if candidate.is_dir()), None)
            models_root_value = str(detected) if detected else ""
        models_root = Path(models_root_value) if models_root_value else None
        missing_models = (
            [name for name in model_names if _find_model_file(models_root, name) is None]
            if models_root is not None else model_names
        )
        if comfy_healthy:
            node_report = comfy.object_info_summary(required_nodes)
            missing_nodes = node_report.get("missing_required_nodes", [])
        else:
            missing_nodes = None
        if not comfy_healthy:
            wan_capability = {"status": "unknown", "workflow": str(workflow_path),
                              "reason": "ComfyUI is offline; custom-node capability cannot be verified",
                              "model_files_present": [name for name in model_names if name not in missing_models],
                              "missing_model_files": missing_models, "required_nodes": required_nodes}
        elif missing_nodes:
            wan_capability = {"status": "unavailable", "workflow": str(workflow_path),
                              "missing_nodes": missing_nodes, "model_files": model_names,
                              "missing_model_files": missing_models}
        elif models_root is None:
            wan_capability = {"status": "unknown", "workflow": str(workflow_path),
                              "reason": "Wan model directory could not be detected or configured",
                              "required_nodes": required_nodes, "model_files": model_names}
        else:
            wan_capability = {"status": "unavailable" if missing_models else "available",
                              "workflow": str(workflow_path), "required_nodes": required_nodes,
                              "models_root": str(models_root), "model_files": model_names,
                              "missing_model_files": missing_models}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        wan_capability = {"status": "unavailable", "reason": str(exc)[:500]}
    wan_health = {"status": "healthy" if comfy_healthy else "unavailable",
                  "dependency": "ComfyUI", "reason": comfy_health_raw.get("reason")}

    tts_packages = root / ".muse-canto-tts-py314"
    tts_model = root / "data" / "models" / "canto-tts-nano"
    launcher = shutil.which("py")
    if launcher and (tts_packages / "canto_tts").is_dir():
        env = os.environ.copy()
        env["PYTHONPATH"] = str(tts_packages) + os.pathsep + env.get("PYTHONPATH", "")
        try:
            imported = runner([launcher, "-3.14", "-c", "import canto_tts"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30, check=False, env=env)
            tts_health = {"status": "healthy" if imported.returncode == 0 else "unavailable",
                          "runtime": "Python 3.14", "exit_code": imported.returncode,
                          "reason": (imported.stderr or "")[-500:] if imported.returncode else None}
        except (OSError, subprocess.SubprocessError) as exc:
            tts_health = {"status": "unavailable", "reason": type(exc).__name__}
    else:
        tts_health = {"status": "unavailable", "reason": "Python launcher or project-local canto_tts package is missing"}
    tts_manifest = tts_model / "browser_poc_manifest.json"
    tts_capability = {"status": "available" if tts_manifest.is_file() else "unavailable",
                      "model_manifest_present": tts_manifest.is_file(), "model_dir": str(tts_model)}

    asr_model = root / "models" / "whisper-small-cantonese" / "cts" / "model.bin"
    try:
        asr_runtime = importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        asr_runtime = False
    asr_health = {"status": "healthy" if asr_runtime else "unavailable",
                  "runtime": "current Python", "faster_whisper_importable": asr_runtime}
    asr_capability = {"status": "available" if asr_model.is_file() and asr_model.stat().st_size > 0 else "unavailable",
                      "model_file_present": asr_model.is_file(),
                      "model_bytes": asr_model.stat().st_size if asr_model.is_file() else 0,
                      "model_dir": str(asr_model.parent)}

    return {
        "comfyui": _service(comfy_health, comfy_capability),
        "wan": _service(wan_health, wan_capability),
        "tts": _service(tts_health, tts_capability),
        "asr": _service(asr_health, asr_capability),
    }
