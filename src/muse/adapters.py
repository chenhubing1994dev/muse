"""Permission-aware adapters that turn registered capabilities into real tools."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from muse.media import build_transcode_command
from muse.process_utils import no_console_window_kwargs



class ToolAdapter(Protocol):
    """Common lifecycle expected from executable tool adapters."""

    def validate(self, request: dict[str, Any]) -> Any: ...
    def preview(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def execute(self, request: dict[str, Any], *, approved: bool = False) -> dict[str, Any]: ...
    def cancel(self, job_id: str) -> dict[str, Any]: ...
    def verify(self, job_id: str, *, timeout: float | None = None) -> dict[str, Any]: ...
    def collect_artifacts(self, job_id: str) -> list[str]: ...

class FFmpegTranscodeAdapter:
    """Start, cancel and verify a local AMD AMF transcode using literal argv."""

    def __init__(
        self, *, ffmpeg_path: str, available_encoders: set[str] | frozenset[str] | tuple[str, ...],
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        if not isinstance(ffmpeg_path, str) or not ffmpeg_path.strip():
            raise ValueError("ffmpeg_path must be non-empty text")
        self.ffmpeg_path = ffmpeg_path
        self.available_encoders = frozenset(available_encoders)
        self._popen = popen
        self._jobs: dict[str, dict[str, Any]] = {}

    def validate(self, request: dict[str, Any]) -> list[str]:
        if not isinstance(request, dict):
            raise ValueError("request must be a dictionary")
        if not {"input", "output"}.issubset(request):
            raise ValueError("request requires input and output paths")
        return build_transcode_command(
            request["input"], request["output"], ffmpeg_path=self.ffmpeg_path,
            codec=request.get("codec", "h264"), available_encoders=self.available_encoders,
            bitrate=request.get("bitrate", "8M"), scale=request.get("scale"),
        )

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        command = self.validate(request)
        return {"status": "planned", "command": command, "execution_started": False}

    def start(self, request: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
        if approved is not True:
            return {"status": "waiting_approval", "execution_started": False}
        command = self.validate(request)
        process = self._popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True, shell=False,
            **no_console_window_kwargs(),
        )
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"process": process, "output": str(request["output"]), "command": command}
        return {"status": "running", "job_id": job_id, "execution_started": True}

    def execute(self, request: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
        return self.start(request, approved=approved)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        process = job["process"]
        if process.poll() is not None:
            return {"status": "already_finished", "job_id": job_id}
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return {"status": "cancelled", "job_id": job_id}

    def collect_artifacts(self, job_id: str) -> list[str]:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        output = Path(job["output"])
        if output.is_file() and output.stat().st_size > 0:
            return [str(output)]
        return []

    def wait(self, job_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        process = job["process"]
        try:
            process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "running", "job_id": job_id}
        if process.returncode != 0:
            return {
                "status": "failed", "job_id": job_id, "exit_code": process.returncode,
                "artifacts": self.collect_artifacts(job_id),
            }
        artifacts = self.collect_artifacts(job_id)
        if not artifacts:
            return {"status": "failed", "job_id": job_id, "error": "FFmpeg exited successfully but no non-empty output artifact was found.", "artifacts": []}
        return {"status": "completed", "job_id": job_id, "artifacts": artifacts}

    def verify(self, job_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        return self.wait(job_id, timeout=timeout)


def create_adapter(registry: Any, capability_id: str) -> ToolAdapter:
    """Create a supported adapter only from a ready, explicitly registered capability."""
    item = registry.get(capability_id)
    if item is None:
        raise ValueError(f"unknown capability: {capability_id}")
    if item["status"] != "ready":
        raise PermissionError(f"capability is not ready: {capability_id}")
    if capability_id == "media.ffmpeg":
        if not item.get("executable") or "video_transcode" not in item.get("capabilities", []):
            raise PermissionError("registered FFmpeg entry lacks a verified video_transcode capability")
        encoders = item.get("metadata", {}).get("hardware_encoders", [])
        return FFmpegTranscodeAdapter(ffmpeg_path=item["executable"], available_encoders=encoders)
    raise ValueError(f"no adapter implementation is registered for: {capability_id}")
