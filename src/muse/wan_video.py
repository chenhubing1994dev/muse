"""Local Wan 2.1 text-to-video generation through a fixed ComfyUI workflow.

Only loopback ComfyUI is contacted. The workflow uses built-in, explicitly
allowlisted nodes and never executes user-supplied custom code or node classes.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

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
    torch_total = memory.get("torch_vram_total_bytes")
    torch_free = memory.get("torch_vram_free_bytes")
    if isinstance(torch_total, int) and not isinstance(torch_total, bool) and torch_total > 0:
        if (isinstance(torch_free, bool) or not isinstance(torch_free, int)
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
    duration_seconds: float = 2.0, steps: int = 8, seed: int = 0,
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

    width, height = {"16:9": (512, 288), "9:16": (288, 512), "1:1": (384, 384)}[aspect_ratio]
    # Wan latent video length must be 4n+1. Twelve FPS keeps short previews
    # compact while preserving smooth enough motion for a first local prototype.
    frames = max(13, min(61, int(round(float(duration_seconds) * 12))))
    frames = ((frames - 1 + 3) // 4) * 4 + 1
    frames = min(frames, 61)

    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": WAN21_MODEL_FILES["diffusion_model"], "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "3": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": WAN21_MODEL_FILES["text_encoder"], "type": "wan", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": WAN21_MODEL_FILES["vae"]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt.strip(), "clip": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt.strip(), "clip": ["3", 0]}},
        "7": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["5", 0], "negative": ["6", 0], "vae": ["4", 0],
            "width": width, "height": height, "length": frames, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["2", 0], "seed": seed, "steps": steps, "cfg": 6.0,
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["7", 0], "negative": ["7", 1],
            "latent_image": ["7", 2], "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeTiled", "inputs": {
            "samples": ["8", 0], "vae": ["4", 0],
            "tile_size": 256, "overlap": 64,
            "temporal_size": 8, "temporal_overlap": 4,
        }},
        "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": 12.0}},
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


def _generate_wan21_t2v_unleased(
    prompt: str, output_path: str | Path, *, negative_prompt: str = "",
    aspect_ratio: str = "16:9", duration_seconds: float = 2.0,
    steps: int = 8, seed: int = 0, timeout_seconds: int = 1800,
    endpoint: str = "http://127.0.0.1:8188",
) -> dict[str, Any]:
    """Submit one approved fixed workflow, wait for completion and retrieve MP4."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 30 and 3600")
    job_tag = __import__("secrets").token_hex(8)
    workflow = build_wan21_t2v_workflow(
        prompt, negative_prompt=negative_prompt, aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds, steps=steps, seed=seed,
        filename_prefix="muse_wan21_" + job_tag,
    )
    client = ComfyUIClient(endpoint, timeout_seconds=10.0)
    try:
        gpu_memory = _require_gpu_headroom(client)
        submitted = client.submit_workflow(
            workflow, node_allowlist=WAN21_NODE_ALLOWLIST, approved=True,
        )
        prompt_id = submitted["prompt_id"]
        deadline = time.monotonic() + timeout_seconds
        entry = None
        while time.monotonic() < deadline:
            history = client.history(prompt_id)
            candidate = history.get(prompt_id)
            if isinstance(candidate, dict):
                entry = candidate
                status = candidate.get("status", {})
                if isinstance(status, dict) and status.get("status_str") == "error":
                    messages = status.get("messages", [])
                    detail = str(messages[-1])[:800] if isinstance(messages, list) and messages else "unknown ComfyUI error"
                    raise WanVideoError("Wan generation failed: " + detail)
                metadata = _video_output(candidate)
                if metadata:
                    target = Path(output_path)
                    size = _download_local_output(client, metadata, target)
                    return {
                        "status": "completed", "prompt_id": prompt_id,
                        "model": "Wan 2.1 T2V 1.3B", "seed": seed,
                        "aspect_ratio": aspect_ratio, "fps": 12,
                        "duration_seconds": round((workflow["7"]["inputs"]["length"] - 1) / 12, 3),
                        "frames": workflow["7"]["inputs"]["length"],
                        "width": workflow["7"]["inputs"]["width"],
                        "height": workflow["7"]["inputs"]["height"],
                        "steps": steps, "bytes": size, "output": str(target),
                        "gpu_memory_preflight": {key: gpu_memory[key] for key in ("vram_total_bytes", "vram_free_bytes", "vram_free_ratio") if key in gpu_memory},
                    }
            time.sleep(2)
        if entry is not None:
            status = entry.get("status", {})
            if isinstance(status, dict) and status.get("status_str") == "error":
                raise WanVideoError("Wan generation failed")
        raise TimeoutError("Wan generation exceeded the configured time limit")
    except (RuntimeError, ValueError, OSError) as exc:
        if isinstance(exc, WanVideoError):
            raise
        raise WanVideoError(str(exc)) from exc


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
    aspect_ratio: str = "16:9", duration_seconds: float = 2.0,
    steps: int = 8, seed: int = 0, timeout_seconds: int = 1800,
    endpoint: str = "http://127.0.0.1:8188",
    resource_db: str | Path | None = None, workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Generate Wan media under a cross-process GPU lease when resource_db is supplied."""
    if resource_db is None:
        return _generate_wan21_t2v_unleased(
            prompt, output_path, negative_prompt=negative_prompt, aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds, steps=steps, seed=seed,
            timeout_seconds=timeout_seconds, endpoint=endpoint,
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
            result = _generate_wan21_t2v_unleased(
                prompt, output_path, negative_prompt=negative_prompt, aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds, steps=steps, seed=seed,
                timeout_seconds=timeout_seconds, endpoint=endpoint,
            )
            lease.assert_valid()
            memory_release = _request_comfyui_model_release(endpoint)
            coordinator.record_comfyui_memory_release(memory_release)
            lease.assert_valid()
            result["gpu_resource_coordination"] = lease.coordination
            result["gpu_memory_release"] = memory_release
            return result
    except GpuResourceBusy as exc:
        raise WanVideoError("GPU resource coordination blocked Wan generation: " + str(exc)) from exc
