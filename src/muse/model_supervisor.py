"""Supervise Muse's explicitly configured local llama.cpp service."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from muse.gpu_resources import GpuResourceCoordinator
from muse.model_service import probe_configured_model_service
from muse.ruflo_adapter import load_muse_model_config
from muse.video_engines import ComfyUIClient


def _load_runtime(workspace: str | Path) -> tuple[dict[str, object], Path]:
    root = Path(workspace).resolve()
    path = root / "config" / "model-runtime.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("model runtime configuration cannot be read") from exc
    if not isinstance(config, dict):
        raise ValueError("model runtime configuration must be an object")
    executable, args, cwd = config.get("executable"), config.get("args"), config.get("cwd")
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError("model runtime executable is missing")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("model runtime args must be a string array")
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError("model runtime cwd is missing")
    exe_path, cwd_path = Path(executable), Path(cwd)
    if not exe_path.is_absolute() or not exe_path.is_file():
        raise ValueError("configured model executable does not exist as an absolute file")
    if not cwd_path.is_absolute() or not cwd_path.is_dir():
        raise ValueError("configured model working directory does not exist")
    for flag in ("-m", "--mmproj"):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args) or not Path(args[index + 1]).is_absolute() or not Path(args[index + 1]).is_file():
                raise ValueError("configured model asset is missing: " + flag)
    endpoint = config.get("health_url")
    if not isinstance(endpoint, str):
        raise ValueError("model runtime health_url is missing")
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.rstrip("/").endswith("/v1")):
        raise ValueError("model runtime health_url must be a credential-free loopback OpenAI endpoint")
    try:
        route = load_muse_model_config(str(root))
    except (OSError, ValueError) as exc:
        raise ValueError("Muse model route cannot be loaded") from exc
    if route.get("provider") != "llama_cpp" or route.get("base_url") != endpoint:
        raise ValueError("runtime health_url does not match Muse's configured llama.cpp route")
    if "--host" not in args or "--port" not in args or "--alias" not in args:
        raise ValueError("model runtime args must explicitly bind host, port and model alias")
    host_index, port_index, alias_index = args.index("--host"), args.index("--port"), args.index("--alias")
    if max(host_index, port_index, alias_index) + 1 >= len(args):
        raise ValueError("model runtime bind or alias argument is incomplete")
    if args[host_index + 1] != parsed.hostname or args[port_index + 1] != str(parsed.port or 80):
        raise ValueError("model runtime bind address does not match health_url")
    if args[alias_index + 1] != route.get("model"):
        raise ValueError("runtime model alias does not match Muse's configured model")
    return config, root


def _probe(workspace: Path) -> dict[str, object]:
    return probe_configured_model_service(str(workspace))


def _loopback_port_status(port: int, *, timeout_seconds: float = 0.5) -> str:
    """Distinguish a closed local service from a timeout/uncertain connection."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout_seconds):
            return "listening"
    except OSError as exc:
        if isinstance(exc, ConnectionRefusedError) or getattr(exc, "winerror", None) == 10061:
            return "closed"
        return "unknown"


def _comfyui_gpu_memory() -> dict[str, object]:
    """Read local VRAM; a reachable external model endpoint blocks Muse model startup."""
    lm_studio_port = _loopback_port_status(1234)
    if lm_studio_port == "listening":
        return {"status": "external_model_active", "service": "lm_studio"}
    if lm_studio_port == "unknown":
        return {"status": "unknown"}

    comfyui_port = _loopback_port_status(8188)
    if comfyui_port == "closed":
        return {"status": "unavailable"}
    if comfyui_port == "unknown":
        return {"status": "unknown"}
    try:
        return {"status": "available", **ComfyUIClient(timeout_seconds=3.0).gpu_memory()}
    except (RuntimeError, ValueError, TypeError, OSError):
        # A live ComfyUI listener with an unreadable VRAM endpoint is not proof of free GPU.
        return {"status": "unknown"}


def _gpu_memory_ready(snapshot: dict[str, object], *, minimum_free_ratio: float = 0.70) -> bool | None:
    status = snapshot.get("status")
    if status == "unavailable":
        return True  # ComfyUI is not running; this probe has no local workload to wait for.
    if status != "available":
        return None
    total = snapshot.get("vram_total_bytes")
    free = snapshot.get("vram_free_bytes")
    ratio = snapshot.get("vram_free_ratio")
    if (isinstance(total, bool) or not isinstance(total, int) or total <= 0
            or isinstance(free, bool) or not isinstance(free, int) or free < 0
            or not isinstance(ratio, (int, float)) or isinstance(ratio, bool)):
        return None
    if ratio < minimum_free_ratio or free < int(total * minimum_free_ratio):
        return False
    torch_free = snapshot.get("torch_vram_free_bytes")
    torch_total = snapshot.get("torch_vram_total_bytes")
    if isinstance(torch_total, int) and not isinstance(torch_total, bool) and torch_total > 0:
        if (not isinstance(torch_free, int) or isinstance(torch_free, bool)
                or torch_free < int(torch_total * minimum_free_ratio)):
            return False
    return True


def start_if_needed(
    workspace: str | Path, *, startup_timeout: float | None = None, retain_process: bool = False
) -> dict[str, object]:
    root = Path(workspace).resolve()
    resources = GpuResourceCoordinator(root / "data" / "gpu-resources.sqlite3")
    lease = resources.active_lease()
    if lease:
        return {"action": "gpu_reserved_for_video", "lease_owner": str(lease.get("owner_kind", "unknown"))}
    health = _probe(root)
    if health.get("status") == "healthy":
        return {"action": "already_healthy", "health": health}
    # Do not launch a second server when the endpoint exists but exposes the wrong model,
    # has a rejected URL, or has a broken Muse route. Only a genuinely offline endpoint
    # is eligible for automatic process creation.
    if health.get("status") != "unavailable":
        return {"action": "not_started", "health": health}
    gpu_memory = _comfyui_gpu_memory()
    gpu_ready = _gpu_memory_ready(gpu_memory)
    if gpu_ready is False:
        return {"action": "gpu_memory_insufficient", "health": health, "gpu_memory": gpu_memory}
    if gpu_ready is None:
        action = "external_model_active" if gpu_memory.get("status") == "external_model_active" else "gpu_memory_unknown"
        return {"action": action, "health": health, "gpu_memory": gpu_memory}
    config, _ = _load_runtime(root)
    executable, args, cwd = str(config["executable"]), list(config["args"]), str(config["cwd"])
    log_dir = Path(str(config.get("log_dir", Path(cwd) / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = log_dir / "muse-model-supervisor.stdout.log", log_dir / "muse-model-supervisor.stderr.log"
    timeout = float(startup_timeout if startup_timeout is not None else config.get("startup_timeout_seconds", 180))
    timeout = max(5.0, min(timeout, 600.0))
    with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open("a", encoding="utf-8") as stderr:
        kwargs: dict[str, object] = {
            "cwd": cwd, "stdout": stdout, "stderr": stderr,
            "stdin": subprocess.DEVNULL, "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen([executable, *args], **kwargs)
        except OSError as exc:
            return {"action": "start_failed", "reason": type(exc).__name__, "health": health}
    deadline = time.monotonic() + timeout
    interval = max(0.5, min(float(config.get("health_interval_seconds", 5)), 15.0))
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            result: dict[str, object] = {
                "action": "start_failed", "exit_code": code, "health": _probe(root),
                "process_started": True,
            }
            if retain_process:
                result["_process"] = process
            return result
        health = _probe(root)
        if health.get("status") == "healthy":
            result = {"action": "started", "pid": process.pid, "health": health, "process_started": True}
            if retain_process:
                result["_process"] = process
            return result
        time.sleep(interval)
    result = {
        "action": "startup_timeout", "pid": process.pid, "health": _probe(root),
        "process_started": True,
    }
    if retain_process:
        result["_process"] = process
    return result


def run_supervisor(workspace: str | Path, *, once: bool = False) -> int:
    root = Path(workspace).resolve()
    try:
        config, _ = _load_runtime(root)
    except ValueError as exc:
        print(json.dumps({"status": "configuration_error", "reason": str(exc)}, ensure_ascii=False))
        return 2
    interval = max(5, min(int(config.get("health_interval_seconds", 30)), 300))
    max_restarts = max(0, min(int(config.get("max_restarts_per_hour", 3)), 12))
    resources = GpuResourceCoordinator(root / "data" / "gpu-resources.sqlite3")
    supervisor_id = __import__("uuid").uuid4().hex
    previous = resources.supervisor_state()
    restore_pending = bool(previous and previous.get("restore_pending"))
    restart_window: deque[float] = deque()
    child: subprocess.Popen[bytes] | None = None
    while True:
        lease = resources.active_lease()
        health = _probe(root)
        acknowledged_lease_id: str | None = None
        paused_owned_model = False
        action: str
        if lease and lease.get("owner_kind") in {"video_generation", "video_encoding", "comfyui_generation"}:
            if child is not None and child.poll() is None:
                try:
                    child.terminate()
                    child.wait(timeout=10)
                    child = None
                    restore_pending = True
                    paused_owned_model = True
                    health = _probe(root)
                except (OSError, subprocess.TimeoutExpired):
                    action = "pause_failed"
                else:
                    action = "paused_for_video" if health.get("status") == "unavailable" else "external_model_active"
            elif health.get("status") == "unavailable":
                action = "paused_for_video"
            else:
                action = "external_model_active"
            if action == "paused_for_video":
                acknowledged_lease_id = str(lease["lease_id"])
        elif health.get("status") == "healthy":
            action = "healthy"
            restore_pending = False
        elif health.get("status") != "unavailable":
            action = "model_route_attention_required"
        else:
            now = time.monotonic()
            while restart_window and now - restart_window[0] > 3600:
                restart_window.popleft()
            if child is not None and child.poll() is None:
                action = "starting"
            elif len(restart_window) >= max_restarts:
                action = "restart_limit_reached"
            else:
                restart_window.append(now)
                result = start_if_needed(root, retain_process=True)
                child = result.get("_process") if hasattr(result.get("_process"), "poll") else None
                action = str(result.get("action"))
                if action in {"gpu_memory_insufficient", "gpu_memory_unknown", "gpu_reserved_for_video", "external_model_active"}:
                    restart_window.pop()
                if restore_pending and action in {"started", "already_healthy"}:
                    restore_pending = False
        resources.record_supervisor(
            supervisor_id=supervisor_id, pid=os.getpid(), action=action,
            acknowledged_lease_id=acknowledged_lease_id,
            child_pid=child.pid if child is not None and child.poll() is None else None,
            restore_pending=restore_pending,
            detail={"model_status": health.get("status"), "paused_owned_model": paused_owned_model},
        )
        print(json.dumps({"status": action, "model_health": health.get("status")}, ensure_ascii=False), flush=True)
        if once:
            return 0 if action in {"healthy", "already_healthy", "started", "paused_for_video"} else 1
        time.sleep(interval)


def install_startup_task(workspace: str | Path) -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("Windows Task Scheduler is only available on Windows")
    root = Path(workspace).resolve()
    _load_runtime(root)
    task_name = "MuseLocalModelSupervisor"
    command = f'"{sys.executable}" -m muse.model_supervisor --workspace "{root}"'
    result = subprocess.run(
        ["schtasks.exe", "/Create", "/SC", "ONLOGON", "/TN", task_name, "/TR", command, "/F"],
        capture_output=True, text=True, errors="replace", timeout=30, check=False,
    )
    return {
        "success": result.returncode == 0, "task_name": task_name,
        "returncode": result.returncode, "output": (result.stdout + result.stderr)[-2000:],
    }


def remove_startup_task() -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("Windows Task Scheduler is only available on Windows")
    result = subprocess.run(
        ["schtasks.exe", "/Delete", "/TN", "MuseLocalModelSupervisor", "/F"],
        capture_output=True, text=True, errors="replace", timeout=30, check=False,
    )
    return {
        "success": result.returncode == 0, "returncode": result.returncode,
        "output": (result.stdout + result.stderr)[-2000:],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervise Muse's configured local model service")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--once", action="store_true", help="check and start once, then exit")
    parser.add_argument("--install-startup", action="store_true", help="register a per-user Windows logon task")
    parser.add_argument("--remove-startup", action="store_true", help="remove the per-user Windows logon task")
    args = parser.parse_args(argv)
    if args.install_startup:
        result = install_startup_task(args.workspace)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["success"] else 1
    if args.remove_startup:
        result = remove_startup_task()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["success"] else 1
    try:
        return run_supervisor(args.workspace, once=args.once)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
