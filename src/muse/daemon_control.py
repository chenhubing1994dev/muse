"""Bounded local lifecycle control for the Muse autonomous runtime daemon.

The desktop client can start/pause/resume/stop the long-running supervisor, but
this module is a thin, auditable wrapper around the existing, authoritative
``muse.autonomous_runtime`` CLI. It never bypasses the runtime's own SQLite
singleton lease, never executes arbitrary commands, and keeps an append-only
control log so every lifecycle action can be reviewed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from muse.autonomous_runtime import AutonomousSupervisor, RuntimeStateStore, _pid_alive

class DaemonError(RuntimeError):
    """A lifecycle action could not be performed safely."""


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _python_executable() -> str:
    return sys.executable or "python"


class MuseDaemonController:
    """Start/stop the foreground supervisor as one bounded child process."""

    MAX_LOG_BYTES = 64 * 1024

    def __init__(
        self,
        *,
        workspace: str | Path,
        state_db: str | Path = "data/autonomy/runtime.sqlite3",
        task_db: str | Path = "data/tasks/muse.sqlite3",
        routine_db: str | Path = "data/routines/muse.sqlite3",
        improvement_db: str | Path = "data/improvements/muse.sqlite3",
        knowledge_db: str | Path = "data/research/knowledge.sqlite3",
        reminder_db: str | Path = "data/reminders/muse.sqlite3",
        control_log: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.state_db = self._resolve(state_db)
        self.task_db = self._resolve(task_db)
        self.routine_db = self._resolve(routine_db)
        self.improvement_db = self._resolve(improvement_db)
        self.knowledge_db = self._resolve(knowledge_db)
        self.reminder_db = self._resolve(reminder_db)
        self.control_log = self._resolve(control_log or "data/autonomy/control-log.jsonl")

    def _resolve(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.workspace / path

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        with RuntimeStateStore(self.state_db) as state:
            runtime = state.status()
        lease = self._read_lease()
        return {
            "state": runtime.get("state", "stopped"),
            "running": bool(runtime.get("running")),
            "heartbeat_age_seconds": runtime.get("heartbeat_age_seconds"),
            "stale": runtime.get("stale", False),
            "last_error": runtime.get("last_error"),
            "started_at": runtime.get("started_at"),
            "owner_id": runtime.get("owner_id"),
            "managed_pid": lease.get("pid"),
            "control_log": str(self.control_log.relative_to(self.workspace)).replace("\\", "/"),
        }

    # -------------------------------------------------------------- lifecycle
    def _rollback_unowned_start(self) -> None:
        """Undo the pre-spawn running marker without touching a concurrently acquired owner."""
        with RuntimeStateStore(self.state_db) as state:
            state.reset_unowned()

    def start(self, *, poll_seconds: int = 5) -> dict[str, Any]:
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, int) or not 1 <= poll_seconds <= 60:
            raise DaemonError("poll_seconds must be an integer between 1 and 60")
        current = self.status()
        if current["running"]:
            return {"changed": False, "reason": "already_running", **current}

        # Clear a stale 'stopping' lease before spawning a fresh owner. A
        # paused runtime is left paused by acquire(); explicitly resume it so
        # a fresh start always does work.
        with RuntimeStateStore(self.state_db) as state:
            raw = state.status()
            if raw.get("state") in {"stopping", "paused"} and not raw.get("running"):
                state.set_control("running")

        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.workspace / "src")
        env["PYTHONUNBUFFERED"] = "1"
        log_dir = self.state_db.parent
        stdout = stderr = None
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = log_dir / "runtime.stdout.log"
            stderr_path = log_dir / "runtime.stderr.log"
            stdout = open(stdout_path, "ab", buffering=0)
            stderr = open(stderr_path, "ab", buffering=0)
        except OSError as exc:
            for stream in (stdout, stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self._rollback_unowned_start()
            raise DaemonError(f"cannot open runtime log: {exc}") from exc

        args = [
            _python_executable(), "-u", "-m", "muse.autonomous_runtime",
            "--task-db", str(self.task_db),
            "--routine-db", str(self.routine_db),
            "--state-db", str(self.state_db),
            "--improvement-db", str(self.improvement_db),
            "--knowledge-db", str(self.knowledge_db),
            "--reminder-db", str(self.reminder_db),
            "run", "--poll-seconds", str(poll_seconds),
        ]
        try:
            child = subprocess.Popen(
                args, cwd=self.workspace, env=env,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self._rollback_unowned_start()
            raise DaemonError(f"cannot start autonomous runtime: {exc}") from exc
        finally:
            for stream in (stdout, stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

        self._write_lease({"pid": child.pid, "started_at": _stamp(), "poll_seconds": poll_seconds})
        self._audit("start", {"pid": child.pid})
        return {"changed": True, "action": "start", "pid": child.pid, **self.status()}

    def pause(self) -> dict[str, Any]:
        return self._control("pause", "paused")

    def resume(self) -> dict[str, Any]:
        return self._control("resume", "running")

    STOP_GRACE_SECONDS = 5.0
    STOP_POLL_SECONDS = 0.1

    def stop(self) -> dict[str, Any]:
        result = self._control("stop", "stopping")
        lease = self._read_lease() or {}
        pid = lease.get("pid")
        signalled = False
        stop_mode = "already_stopped"
        if isinstance(pid, int) and pid > 0 and self._runtime_owner_matches(pid) and self._pid_matches_lease(pid, lease):
            if self._wait_for_owner_exit(pid, self.STOP_GRACE_SECONDS):
                stop_mode = "graceful_exit"
            elif self._runtime_owner_matches(pid) and self._pid_matches_lease(pid, lease):
                # Re-check both the authoritative SQLite owner and Windows
                # process creation identity immediately before force termination.
                self._signal_stop(pid)
                signalled = True
                stop_mode = "forced_after_timeout"
            elif self._runtime_owner_record_matches(pid):
                stop_mode = "owner_identity_unverified_or_process_exited"
            else:
                stop_mode = "owner_changed_during_timeout"
        elif isinstance(pid, int) and pid > 0:
            stop_mode = "pid_or_owner_mismatch"
        # Never manufacture a stopped DB row after force termination. The
        # Supervisor's own finally/release path remains authoritative.
        return {**result, "signal_sent": signalled, "stop_mode": stop_mode, **self.status()}

    def _runtime_owner_record_matches(self, pid: int) -> bool:
        """True only while SQLite still names this PID as the active owner."""
        with RuntimeStateStore(self.state_db) as state:
            current = state.status()
        return bool(current.get("owner_id")) and current.get("pid") == pid

    def _runtime_owner_matches(self, pid: int) -> bool:
        with RuntimeStateStore(self.state_db) as state:
            current = state.status()
        return bool(current.get("running")) and bool(current.get("owner_id")) and current.get("pid") == pid and self._pid_alive(pid)

    def _pid_matches_lease(self, pid: int, lease: dict[str, Any]) -> bool:
        """Fail closed unless Windows process creation time matches our lease."""
        if os.name != "nt" or not isinstance(lease.get("started_at"), str):
            return False
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            class FILETIME(ctypes.Structure):
                _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                ok = kernel32.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel), ctypes.byref(user),
                )
                if not ok:
                    return False
            finally:
                kernel32.CloseHandle(handle)
            ticks = (created.high << 32) | created.low
            process_started = ticks / 10_000_000 - 11_644_473_600
            lease_started = datetime.fromisoformat(lease["started_at"]).timestamp()
            return abs(process_started - lease_started) <= 5.0
        except (OSError, AttributeError, TypeError, ValueError, OverflowError):
            return False

    def _wait_for_owner_exit(self, pid: int, timeout_seconds: float) -> bool:
        """Wait for the owner row to be released; a dead PID alone is not graceful."""
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while self._runtime_owner_record_matches(pid):
            if not self._pid_alive(pid):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(self.STOP_POLL_SECONDS, remaining))
        return True

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        return _pid_alive(pid)

    @staticmethod
    def _signal_stop(pid: int) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=10, check=False,
                )
            else:
                os.kill(pid, 15)
        except (OSError, subprocess.SubprocessError):
            pass
    def _control(self, action: str, target: str) -> dict[str, Any]:
        current = self.status()
        with RuntimeStateStore(self.state_db) as state:
            state.set_control(target)
        self._audit(action, {"from": current["state"], "to": target})
        return {"changed": current["state"] != target, "action": action, **self.status()}

    def run_once(self) -> dict[str, Any]:
        """Run one bounded deterministic tick in-process (no long-lived daemon)."""
        if self.status()["running"]:
            raise DaemonError("stop the runtime before running a one-shot tick")
        video_admission_runtime = None
        video_admission_setup: dict[str, Any] = {"status": "disabled"}
        if os.environ.get("MUSE_ENABLE_VIDEO_WORKER", "").strip() == "1":
            try:
                from muse.video_admission_runtime import build_video_admission_runtime
                video_admission_runtime = build_video_admission_runtime(
                    self.workspace, task_database=self.task_db,
                )
                video_admission_setup = video_admission_runtime.readiness()
            except (OSError, ValueError, RuntimeError) as exc:
                video_admission_setup = {
                    "status": "blocked",
                    "reason": f"{type(exc).__name__}: {exc}"[:300],
                }
                video_admission_runtime = None
        try:
            supervisor = AutonomousSupervisor(
                task_db=self.task_db, routine_db=self.routine_db, state_db=self.state_db,
                improvement_db=self.improvement_db, knowledge_db=self.knowledge_db,
                reminder_db=self.reminder_db, workspace=self.workspace,
                video_admission_provider=video_admission_runtime,
                video_admission_request_factory=(
                    video_admission_runtime.request_factory if video_admission_runtime is not None else None
                ),
            )
            supervisor.ensure_default_routines()
            result = supervisor.run_tick()
            result["video_admission_setup"] = video_admission_setup
            self._audit("run_once", {
                "task": bool(result.get("task")),
                "video_admission_setup": video_admission_setup,
            })
            return result
        finally:
            if video_admission_runtime is not None:
                video_admission_runtime.close()

    # ------------------------------------------------------------------ audit
    def _lease_path(self) -> Path:
        return self.state_db.parent / "runtime.lease.json"

    def _read_lease(self) -> dict[str, Any]:
        path = self._lease_path()
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_lease(self, value: dict[str, Any]) -> None:
        self._lease_path().parent.mkdir(parents=True, exist_ok=True)
        self._lease_path().write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _audit(self, action: str, detail: dict[str, Any]) -> None:
        self.control_log.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": _stamp(), "action": action, "detail": detail}
        with open(self.control_log, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.control_log.stat().st_size > self.MAX_LOG_BYTES:
            lines = self.control_log.read_text(encoding="utf-8", errors="replace").splitlines()
            self.control_log.write_text("\n".join(lines[-200:]) + "\n", encoding="utf-8")
