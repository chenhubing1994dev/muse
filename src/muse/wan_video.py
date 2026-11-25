"""Local Wan 2.1 text-to-video generation through a fixed ComfyUI workflow.

Only loopback ComfyUI is contacted. The workflow uses built-in, explicitly
allowlisted nodes and never executes user-supplied custom code or node classes.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from muse.video_engines import ComfyUIClient

WAN21_NODE_ALLOWLIST = frozenset({
    "UNETLoader", "ModelSamplingSD3", "CLIPLoader", "VAELoader",
    "CLIPTextEncode", "WanImageToVideo", "KSampler", "VAEDecodeTiled",
    "CreateVideo", "SaveVideo",
})
WAN21_MODEL_FILES = {
    "diffusion_model": "wan2.1_t2v_1.3B_fp16.safetensors",
    "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
}
WAN21_T2V_480P_PROFILE = {
    "name": "wan21_t2v_1.3b_480p_quality_first",
    "fps": 16,
    "default_steps": 20,
    "default_duration_seconds": 5.0,
    "min_frames": 17,
    "max_frames": 81,
    "resolution_by_aspect": {"16:9": (832, 480), "9:16": (480, 832), "1:1": (480, 480)},
    "cfg": 6.0,
    "shift": 8.0,
    "minimum_free_vram_gib": 7.7,
}
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
MINIMUM_FREE_VRAM_RATIO = 0.70


def _require_gpu_headroom(client: ComfyUIClient) -> dict[str, int | float]:
    """Require measured free VRAM before queueing a Wan job; never infer capacity."""
    try:
        memory = client.gpu_memory()
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        raise WanVideoError("Wan generation blocked: reliable GPU memory telemetry is unavailable") from exc
    total = memory.get("vram_total_bytes")
    free = memory.get("vram_free_bytes")
    ratio = memory.get("vram_free_ratio")
    if (isinstance(total, bool) or not isinstance(total, int) or total <= 0
            or isinstance(free, bool) or not isinstance(free, int) or not 0 <= free <= total
            or isinstance(ratio, bool) or not isinstance(ratio, (int, float))):
        raise WanVideoError("Wan generation blocked: GPU memory telemetry is incomplete")
    if ratio < MINIMUM_FREE_VRAM_RATIO or free < int(total * MINIMUM_FREE_VRAM_RATIO):
        raise WanVideoError("Wan generation blocked: measured free GPU memory is below the 70% safety threshold")
    minimum_free_bytes = int(WAN21_T2V_480P_PROFILE["minimum_free_vram_gib"] * 1024**3)
    if free < minimum_free_bytes:
        raise WanVideoError(
            "Wan 2.1 T2V 1.3B 480P requires at least "
            f"{WAN21_T2V_480P_PROFILE['minimum_free_vram_gib']:.1f} GiB free VRAM"
        )
    # ROCm may expose a tiny PyTorch allocator pool after ComfyUI unloads
    # models (for example, 64 MiB on a 12 GiB device). Such a pool is not a
    # measure of device headroom; the device-level counters above remain the
    # authoritative hard gate. Apply the allocator check only when its pool
    # represents at least half of the physical device.
    torch_total = memory.get("torch_vram_total_bytes")
    torch_free = memory.get("torch_vram_free_bytes")
    if (isinstance(torch_total, int) and not isinstance(torch_total, bool)
            and torch_total >= int(total * 0.5)):
        if (isinstance(torch_free, bool) or not isinstance(torch_free, int)
                or not 0 <= torch_free <= torch_total
                or torch_free < int(torch_total * MINIMUM_FREE_VRAM_RATIO)):
            raise WanVideoError("Wan generation blocked: measured PyTorch GPU memory is below the 70% safety threshold")
    return memory



class WanVideoError(RuntimeError):
    """A local Wan video generation job failed or returned an unsafe artifact."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_wan21_t2v_workflow(
    prompt: str, *, negative_prompt: str = "", aspect_ratio: str = "16:9",
    duration_seconds: float = WAN21_T2V_480P_PROFILE["default_duration_seconds"],
    steps: int = WAN21_T2V_480P_PROFILE["default_steps"], seed: int = 0,
    filename_prefix: str = "muse_wan21",
) -> dict[str, dict[str, Any]]:
    """Build a bounded, fixed Wan 2.1 1.3B T2V graph; no custom nodes."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 2000:
        raise ValueError("prompt must contain 1-2000 characters")
    if not isinstance(negative_prompt, str) or len(negative_prompt) > 1000:
        raise ValueError("negative_prompt must be at most 1000 characters")
    if aspect_ratio not in {"16:9", "9:16", "1:1"}:
        raise ValueError("aspect_ratio must be 16:9, 9:16 or 1:1")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or not 1 <= duration_seconds <= 5:
        raise ValueError("duration_seconds must be between 1 and 5")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 8 <= steps <= 30:
        raise ValueError("steps must be an integer between 8 and 30")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if not isinstance(filename_prefix, str) or not re.fullmatch(r"muse_wan21_[a-f0-9]{8,32}", filename_prefix):
        raise ValueError("filename_prefix must be a generated Muse job identifier")

    width, height = WAN21_T2V_480P_PROFILE["resolution_by_aspect"][aspect_ratio]
    # Wan latent video length must be 4n+1. The 1.3B checkpoint is trained
    # primarily for 480P; use the model's 832x480 landscape-equivalent profile.
    fps = WAN21_T2V_480P_PROFILE["fps"]
    frames = max(
        WAN21_T2V_480P_PROFILE["min_frames"],
        min(WAN21_T2V_480P_PROFILE["max_frames"], int(round(float(duration_seconds) * fps))),
    )
    frames = ((frames - 1 + 3) // 4) * 4 + 1
    frames = min(frames, WAN21_T2V_480P_PROFILE["max_frames"])

    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": WAN21_MODEL_FILES["diffusion_model"], "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": WAN21_T2V_480P_PROFILE["shift"]}},
        "3": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": WAN21_MODEL_FILES["text_encoder"], "type": "wan", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": WAN21_MODEL_FILES["vae"]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt.strip(), "clip": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt.strip(), "clip": ["3", 0]}},
        "7": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["5", 0], "negative": ["6", 0], "vae": ["4", 0],
            "width": width, "height": height, "length": frames, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["2", 0], "seed": seed, "steps": steps, "cfg": WAN21_T2V_480P_PROFILE["cfg"],
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["7", 0], "negative": ["7", 1],
            "latent_image": ["7", 2], "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeTiled", "inputs": {
            "samples": ["8", 0], "vae": ["4", 0],
            "tile_size": 256, "overlap": 64,
            "temporal_size": 8, "temporal_overlap": 4,
        }},
        "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": float(fps)}},
        "11": {"class_type": "SaveVideo", "inputs": {
            "video": ["10", 0], "filename_prefix": filename_prefix,
            "format": "mp4", "codec": "h264"}},
    }


def _video_output(history_entry: dict[str, Any]) -> dict[str, str] | None:
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        return None
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for key in ("videos", "gifs", "images"):
            candidates = node_output.get(key)
            if not isinstance(candidates, list):
                continue
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                subfolder = item.get("subfolder", "")
                kind = item.get("type", "output")
                if (isinstance(filename, str) and filename.endswith(".mp4")
                        and Path(filename).name == filename and len(filename) <= 180
                        and subfolder == "" and kind == "output"):
                    return {"filename": filename, "subfolder": "", "type": "output"}
    return None


def _download_local_output(client: ComfyUIClient, metadata: dict[str, str], target: Path) -> int:
    if target.exists() or target.is_symlink() or target.parent.is_symlink():
        raise WanVideoError("video output target already exists or is unsafe")
    target.parent.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode(metadata)
    url = client.client.origin + "/view?" + query
    request = urllib.request.Request(url, headers={"Accept": "video/mp4, application/octet-stream"})
    opener = urllib.request.build_opener(_NoRedirect())
    partial = target.with_name(target.name + ".download")
    try:
        with opener.open(request, timeout=60) as response:
            if response.status != 200:
                raise WanVideoError("ComfyUI output download returned an unexpected status")
            content_length = response.headers.get("Content-Length")
            if content_length and (not content_length.isdigit() or int(content_length) > MAX_VIDEO_BYTES):
                raise WanVideoError("generated video exceeds the 2 GiB limit")
            total = 0
            with partial.open("xb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise WanVideoError("generated video exceeds the 2 GiB limit")
                    stream.write(chunk)
        with partial.open("rb") as stream:
            header = stream.read(12)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise WanVideoError("ComfyUI returned a file that is not an MP4")
        partial.replace(target)
        return total
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise WanVideoError("could not retrieve the generated MP4 from local ComfyUI") from exc
    finally:
        if partial.exists():
            partial.unlink(missing_ok=True)


def _require_comfyui_idle_queue(client: ComfyUIClient) -> dict[str, int]:
    """Refuse to overlap a Muse generation with any already queued ComfyUI work."""
    try:
        queue = client.queue_status()
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        raise WanVideoError("Wan generation blocked: ComfyUI queue state is unavailable") from exc
    running, pending = queue.get("running_jobs"), queue.get("pending_jobs")
    if (
        isinstance(running, bool) or not isinstance(running, int) or running < 0
        or isinstance(pending, bool) or not isinstance(pending, int) or pending < 0
    ):
        raise WanVideoError("Wan generation blocked: ComfyUI queue state is unknown")
    if running or pending:
        raise WanVideoError(
            f"Wan generation blocked: ComfyUI queue is not idle "
            f"(running={running}, pending={pending})"
        )
    return {"running_jobs": running, "pending_jobs": pending}


def _validate_generated_mp4(
    path: str | Path, *, expected_width: int, expected_height: int,
    expected_frames: int, expected_fps: int, timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Require FFprobe metadata and the exact FFmpeg full-decode gate for every shot."""
    from muse.media import discover_media_tools

    target = Path(path)
    if not target.is_file() or target.is_symlink() or target.stat().st_size < 12:
        raise WanVideoError("generated Wan MP4 is missing, unsafe or empty")
    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    ffprobe, ffmpeg = tools.get("ffprobe"), tools.get("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise WanVideoError("FFprobe and FFmpeg executables are required for strict Wan clip validation")
    probe_command = [
        ffprobe, "-v", "error", "-count_frames",
        "-show_entries",
        "format=format_name,duration,size:stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,nb_frames",
        "-of", "json", str(target),
    ]
    try:
        probe = subprocess.run(
            probe_command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=max(30, min(timeout_seconds, 300)), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WanVideoError(f"FFprobe could not inspect generated Wan MP4: {exc}") from exc
    if probe.returncode != 0 or probe.stderr.strip():
        detail = probe.stderr.strip() or f"exit={probe.returncode}"
        raise WanVideoError("FFprobe metadata validation failed: " + detail[:700])
    try:
        metadata = json.loads(probe.stdout)
        container = metadata["format"]["format_name"]
        duration = float(metadata["format"]["duration"])
        streams = [item for item in metadata["streams"] if item.get("codec_type") == "video"]
        if len(streams) != 1:
            raise ValueError("expected exactly one video stream")
        stream = streams[0]
        width, height = int(stream["width"]), int(stream["height"])
        codec = str(stream["codec_name"])
        frame_rate = float(Fraction(str(stream["avg_frame_rate"])))
        frame_value = stream.get("nb_read_frames") or stream.get("nb_frames")
        frame_count = int(frame_value)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise WanVideoError(f"FFprobe returned incomplete or invalid MP4 metadata: {exc}") from exc
    if "mp4" not in str(container).split(","):
        raise WanVideoError(f"generated clip container is not MP4: {container}")
    if codec != "h264":
        raise WanVideoError(f"generated clip codec must be H.264, got {codec}")
    if (width, height) != (expected_width, expected_height):
        raise WanVideoError(
            f"generated clip dimensions differ from the approved workflow: {width}x{height}"
        )
    if abs(frame_rate - expected_fps) > 0.01:
        raise WanVideoError(f"generated clip frame rate differs from the approved workflow: {frame_rate}")
    if frame_count != expected_frames:
        raise WanVideoError(
            f"generated clip frame count differs from the approved workflow: {frame_count}/{expected_frames}"
        )
    expected_duration = max(0.0, (expected_frames - 1) / expected_fps)
    if not math.isfinite(duration) or duration <= 0 or abs(duration - expected_duration) > max(0.25, 1.5 / expected_fps):
        raise WanVideoError(
            f"generated clip duration differs from the approved workflow: {duration:.3f}s"
        )
    decode_command = [ffmpeg, "-v", "error", "-i", str(target), "-f", "null", "-"]
    try:
        decode = subprocess.run(
            decode_command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=max(60, min(timeout_seconds, 600)), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WanVideoError(f"FFmpeg full decode could not complete: {exc}") from exc
    if decode.returncode != 0 or decode.stderr.strip():
        detail = decode.stderr.strip() or f"exit={decode.returncode}"
        raise WanVideoError("FFmpeg full decode failed: " + detail[:700])
    return {
        "status": "passed",
        "container": container,
        "codec": codec,
        "width": width,
        "height": height,
        "fps": frame_rate,
        "frames": frame_count,
        "duration_seconds": duration,
        "ffprobe_status": "passed",
        "full_decode_status": "passed",
        "full_decode_command": decode_command,
    }


def _retryable_wan_failure(error: BaseException) -> bool:
    message = str(error).casefold()
    permanent_markers = (
        "out of memory", "not enough memory", "missing required node",
        "missing model", "model file not found", "unknown node",
        "http 400", "http 401", "http 403", "http 404", "http 422",
        "queue is not idle", "queue state is unknown", "queue state is unavailable",
        "already exists or is unsafe", "ffprobe and ffmpeg executables are required",
    )
    return not any(marker in message for marker in permanent_markers)



def _generate_wan21_t2v_unleased(
    prompt: str, output_path: str | Path, *, negative_prompt: str = "",
    aspect_ratio: str = "16:9",
    duration_seconds: float = WAN21_T2V_480P_PROFILE["default_duration_seconds"],
    steps: int = WAN21_T2V_480P_PROFILE["default_steps"], seed: int = 0,
    timeout_seconds: int = 1800, max_attempts: int = 3,
    endpoint: str = "http://127.0.0.1:8188",
    _before_submit: Callable[[], None] | None = None,
    _on_submitted: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Generate one shot with one Commander grant and at most two internal retries."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 30 and 3600")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 3:
        raise ValueError("max_attempts must be between 1 and 3 (initial attempt plus at most two retries)")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("seed must be an unsigned 32-bit integer")
    target = Path(output_path)
    if target.exists() or target.is_symlink():
        raise WanVideoError("video output target already exists or is unsafe")
    client = ComfyUIClient(endpoint, timeout_seconds=10.0)
    gpu_memory = _require_gpu_headroom(client)
    _require_comfyui_idle_queue(client)
    if _before_submit is not None:
        _before_submit()

    deadline = time.monotonic() + timeout_seconds
    attempt_results: list[dict[str, Any]] = []
    for attempt_index in range(1, max_attempts + 1):
        if attempt_index > 1:
            _require_comfyui_idle_queue(client)
        actual_seed = (seed + attempt_index - 1) & 0xFFFFFFFF
        workflow = build_wan21_t2v_workflow(
            prompt, negative_prompt=negative_prompt, aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds, steps=steps, seed=actual_seed,
            filename_prefix="muse_wan21_" + __import__("secrets").token_hex(8),
        )
        try:
            submitted = client.submit_workflow(
                workflow, node_allowlist=WAN21_NODE_ALLOWLIST, approved=True,
            )
        except (ValueError, PermissionError) as exc:
            raise WanVideoError("ComfyUI workflow validation blocked Wan generation: " + str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            failure = WanVideoError(str(exc))
            attempt_results.append({
                "attempt": attempt_index, "seed": actual_seed,
                "status": "failed", "reason": str(failure)[:800],
            })
            if not _retryable_wan_failure(failure) or attempt_index >= max_attempts or time.monotonic() >= deadline:
                break
            time.sleep(1)
            continue
        if submitted.get("node_errors"):
            raise WanVideoError(
                "ComfyUI workflow validation failed: " + str(submitted["node_errors"])[:800]
            )
        prompt_id = submitted.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise WanVideoError("ComfyUI did not return a valid prompt ID")
        if _on_submitted is not None:
            _on_submitted()

        failure: BaseException | None = None
        last_poll_error: str | None = None
        while time.monotonic() < deadline:
            try:
                history = client.history(prompt_id)
                last_poll_error = None
            except RuntimeError as exc:
                if str(exc) == "local video engine is unavailable":
                    last_poll_error = str(exc)
                    time.sleep(2)
                    continue
                failure = WanVideoError(str(exc))
                break
            candidate = history.get(prompt_id)
            if isinstance(candidate, dict):
                status = candidate.get("status", {})
                if isinstance(status, dict) and status.get("status_str") == "error":
                    messages = status.get("messages", [])
                    detail = (
                        str(messages[-1])[:800]
                        if isinstance(messages, list) and messages
                        else "unknown ComfyUI execution error"
                    )
                    failure = WanVideoError("Wan generation failed: " + detail)
                    break
                metadata = _video_output(candidate)
                if metadata:
                    attempt_target = target.with_name(
                        f"{target.stem}.attempt{attempt_index}{target.suffix}"
                    )
                    try:
                        size = _download_local_output(client, metadata, attempt_target)
                        validation = _validate_generated_mp4(
                            attempt_target,
                            expected_width=workflow["7"]["inputs"]["width"],
                            expected_height=workflow["7"]["inputs"]["height"],
                            expected_frames=workflow["7"]["inputs"]["length"],
                            expected_fps=WAN21_T2V_480P_PROFILE["fps"],
                            timeout_seconds=min(timeout_seconds, 600),
                        )
                        if target.exists() or target.is_symlink():
                            raise WanVideoError("video output target already exists or is unsafe")
                        attempt_target.replace(target)
                        attempt_results.append({
                            "attempt": attempt_index, "seed": actual_seed,
                            "prompt_id": prompt_id, "status": "passed",
                            "validation": validation,
                        })
                        return {
                            "status": "completed", "prompt_id": prompt_id,
                            "model": "Wan 2.1 T2V 1.3B", "seed": actual_seed,
                            "requested_seed": seed, "retry_count": attempt_index - 1,
                            "generation_attempts": attempt_results,
                            "aspect_ratio": aspect_ratio,
                            "fps": WAN21_T2V_480P_PROFILE["fps"],
                            "duration_seconds": validation["duration_seconds"],
                            "resolution_tier": "wan21_480p_master",
                            "quality_profile": WAN21_T2V_480P_PROFILE["name"],
                            "frames": validation["frames"],
                            "width": validation["width"], "height": validation["height"],
                            "steps": steps, "bytes": size, "output": str(target),
                            "ffprobe_validation": validation,
                            "full_decode_status": validation["full_decode_status"],
                            "gpu_memory_preflight": {
                                key: gpu_memory[key]
                                for key in ("vram_total_bytes", "vram_free_bytes", "vram_free_ratio")
                                if key in gpu_memory
                            },
                        }
                    except (WanVideoError, OSError, subprocess.TimeoutExpired) as exc:
                        attempt_target.unlink(missing_ok=True)
                        failure = exc if isinstance(exc, WanVideoError) else WanVideoError(str(exc))
                        break
                if isinstance(status, dict) and (
                    status.get("completed") is True
                    or status.get("status_str") in {"success", "completed"}
                ):
                    failure = WanVideoError("ComfyUI completed the prompt but returned no MP4 output")
                    break
            time.sleep(2)

        if failure is None:
            try:
                queue = _require_comfyui_idle_queue(client)
            except WanVideoError as exc:
                raise WanVideoError(
                    "Wan generation timed out and ComfyUI is not safely idle; refusing to overlap another attempt: "
                    + str(exc)
                ) from exc
            failure = TimeoutError(
                "ComfyUI remained temporarily unavailable while the submitted Wan job was running"
                if last_poll_error else "Wan generation exceeded the configured time limit"
            )
        attempt_results.append({
            "attempt": attempt_index, "seed": actual_seed,
            "prompt_id": prompt_id, "status": "failed",
            "reason": str(failure)[:800],
        })
        if (
            not _retryable_wan_failure(failure)
            or attempt_index >= max_attempts
            or time.monotonic() >= deadline
        ):
            break
        time.sleep(1)

    details = " | ".join(
        f"attempt {item['attempt']} seed={item['seed']}: {item.get('reason', 'unknown failure')}"
        for item in attempt_results
    )
    raise WanVideoError(
        f"Wan shot rejected after {len(attempt_results)} attempt(s); "
        f"no quality downgrade or fallback was applied. {details}"
    )



def _request_comfyui_model_release(
    endpoint: str, *, timeout_seconds: float = 30.0, minimum_free_ratio: float = 0.70,
) -> dict[str, Any]:
    """Unload idle ComfyUI model weights and verify real device free memory."""
    timeout = max(0.0, min(float(timeout_seconds), 30.0))
    if not 0.0 < minimum_free_ratio <= 1.0:
        raise ValueError("minimum_free_ratio must be between 0 and 1")
    client = ComfyUIClient(endpoint, timeout_seconds=3.0)
    try:
        queue = client.queue_status()
        running, pending = queue.get("running_jobs"), queue.get("pending_jobs")
        if (isinstance(running, bool) or not isinstance(running, int)
                or isinstance(pending, bool) or not isinstance(pending, int)):
            return {"status": "unconfirmed", "reason": "queue_state_unknown"}
        if running or pending:
            return {
                "status": "deferred_queue_busy",
                "queue_running": running, "queue_pending": pending,
            }
        client.free_models()
        deadline = time.monotonic() + timeout
        last_memory: dict[str, Any] = {}
        while True:
            last_memory = client.gpu_memory()
            total = last_memory.get("vram_total_bytes")
            free = last_memory.get("vram_free_bytes")
            ratio = last_memory.get("vram_free_ratio")
            if (isinstance(total, int) and total > 0 and isinstance(free, int)
                    and isinstance(ratio, (int, float)) and ratio >= minimum_free_ratio
                    and free >= int(total * minimum_free_ratio)):
                return {
                    "status": "released", **last_memory,
                    "queue_running": running, "queue_pending": pending,
                }
            if time.monotonic() >= deadline:
                return {
                    "status": "unconfirmed", **last_memory,
                    "queue_running": running, "queue_pending": pending,
                }
            time.sleep(1)
    except (RuntimeError, ValueError, TypeError, OSError):
        return {"status": "unavailable", "reason": "comfyui_memory_release_unavailable"}


def generate_wan21_t2v(
    prompt: str, output_path: str | Path, *, negative_prompt: str = "",
    aspect_ratio: str = "16:9",
    duration_seconds: float = WAN21_T2V_480P_PROFILE["default_duration_seconds"],
    steps: int = WAN21_T2V_480P_PROFILE["default_steps"], seed: int = 0,
    timeout_seconds: int = 1800, max_attempts: int = 3,
    endpoint: str = "http://127.0.0.1:8188",
    resource_db: str | Path | None = None, workspace: str | Path | None = None,
    admission_guard: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """Generate Wan media; an admission guard runs inside a live GPU lease before submission."""
    if admission_guard is not None and not callable(admission_guard):
        raise ValueError("admission_guard must be callable")
    if resource_db is None:
        if admission_guard is not None:
            raise WanVideoError("Commander admission_guard requires resource_db and a live GPU lease")
        return _generate_wan21_t2v_unleased(
            prompt, output_path, negative_prompt=negative_prompt, aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds, steps=steps, seed=seed,
            timeout_seconds=timeout_seconds, max_attempts=max_attempts, endpoint=endpoint,
        )
    from muse.gpu_resources import GpuLeaseSession, GpuResourceBusy, GpuResourceCoordinator
    from muse.model_service import probe_configured_model_service

    workspace_root = Path(workspace).resolve() if workspace is not None else Path(resource_db).resolve().parent.parent
    coordinator = GpuResourceCoordinator(resource_db)
    owner_id = "wan21-" + __import__("secrets").token_hex(8)
    try:
        with GpuLeaseSession(
            coordinator, owner_id,
            model_probe=lambda: probe_configured_model_service(str(workspace_root)),
            wait_timeout_seconds=min(90, max(0, timeout_seconds)),
        ) as lease:
            before_submit = None
            if admission_guard is not None:
                def before_submit() -> None:
                    lease.assert_valid()
                    admission_guard(lease)
                    lease.assert_valid()
            submission_started = False

            def on_submitted() -> None:
                nonlocal submission_started
                submission_started = True

            try:
                result = _generate_wan21_t2v_unleased(
                    prompt, output_path, negative_prompt=negative_prompt, aspect_ratio=aspect_ratio,
                    duration_seconds=duration_seconds, steps=steps, seed=seed,
                    timeout_seconds=timeout_seconds, max_attempts=max_attempts,
                    endpoint=endpoint, _before_submit=before_submit,
                    _on_submitted=on_submitted,
                )
            except Exception as generation_error:
                if submission_started:
                    memory_release = _request_comfyui_model_release(endpoint)
                    coordinator.record_comfyui_memory_release(memory_release)
                    if memory_release.get("status") != "released":
                        raise WanVideoError(
                            f"{generation_error}; ComfyUI model memory release was not confirmed: "
                            f"{memory_release.get('status', 'unknown')}"
                        ) from generation_error
                raise
            if not submission_started:
                raise WanVideoError("Wan generation returned without submitting a ComfyUI prompt")
            lease.assert_valid()
            memory_release = _request_comfyui_model_release(endpoint)
            coordinator.record_comfyui_memory_release(memory_release)
            if memory_release.get("status") != "released":
                raise WanVideoError(
                    "Wan generation completed, but ComfyUI GPU memory release was not confirmed; "
                    "refusing to hand the GPU to AMF encoding"
                )
            lease.assert_valid()
            result["gpu_resource_coordination"] = lease.coordination
            result["gpu_memory_release"] = memory_release
            return result
    except GpuResourceBusy as exc:
        raise WanVideoError("GPU resource coordination blocked Wan generation: " + str(exc)) from exc
