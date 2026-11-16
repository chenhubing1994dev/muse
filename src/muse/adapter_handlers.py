"""TaskQueue handlers that execute approved, registered tool adapters."""

from __future__ import annotations

from typing import Any, Callable

from pathlib import Path
from muse.adapters import create_adapter
from muse.task_queue import TaskQueue


AdapterFactory = Callable[[Any, str], Any]


def build_adapter_handlers(
    registry: Any, *, adapter_factory: AdapterFactory = create_adapter,
    timeout_seconds: float = 3600,
) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """Build handlers for explicitly supported adapter-backed task kinds."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    def ffmpeg_transcode(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("_approved") is not True:
            raise PermissionError("FFmpeg task requires explicit approval")
        request = payload.get("request")
        if not isinstance(request, dict):
            raise ValueError("FFmpeg task requires a request object")
        capability_id = payload.get("capability_id", "media.ffmpeg")
        adapter = adapter_factory(registry, capability_id)
        started = adapter.execute(request, approved=True)
        if started.get("status") != "running":
            raise RuntimeError(f"FFmpeg adapter did not start: {started.get('status', 'unknown')}")
        result = adapter.verify(started["job_id"], timeout=timeout_seconds)
        if result.get("status") == "running":
            adapter.cancel(started["job_id"])
            raise TimeoutError("FFmpeg task exceeded its execution time budget and was cancelled")
        if result.get("status") != "completed" or not result.get("artifacts"):
            raise RuntimeError("FFmpeg task failed output verification")
        return {
            "adapter": "ffmpeg", "status": "completed",
            "artifacts": result["artifacts"],
        }

    return {"ffmpeg_transcode": ffmpeg_transcode}


def enqueue_ffmpeg_task(
    database: str | Path, registry: Any, request: dict[str, Any], *,
    adapter_factory: AdapterFactory = create_adapter,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a dry-run plan, then enqueue it without approving or executing it."""
    adapter = adapter_factory(registry, "media.ffmpeg")
    preview = adapter.preview(request)
    if preview.get("status") != "planned" or preview.get("execution_started") is not False:
        raise RuntimeError("FFmpeg adapter did not produce a safe preview")
    payload = {
        "kind": "ffmpeg_transcode", "capability_id": "media.ffmpeg",
        "request": dict(request),
    }
    with TaskQueue(database) as queue:
        task = queue.enqueue(
            "FFmpeg transcode", payload, priority=50, estimated_seconds=900,
            recovery_policy="manual",
        )
    return task, preview