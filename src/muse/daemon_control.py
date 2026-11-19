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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from muse.autonomous_runtime import AutonomousSupervisor, RuntimeStateStore


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
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "runtime.stdout.log"
        stderr_path = log_dir / "runtime.stderr.log"
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
            stdout = open(stdout_path, "ab", buffering=0)
            stderr = open(stderr_path, "ab", buffering=0)
        except OSError as exc:
            raise DaemonError(f"cannot open runtime log: {exc}") from exc
        try:
            child = subprocess.Popen(
                args, cwd=self.workspace, env=env,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            stdout.close(); stderr.close()
            raise DaemonError(f"cannot start autonomous runtime: {exc}") from exc
        finally:
            # Parent-side handles are no longer needed after spawning.
            try:
                stdout.close(); stderr.close()
            except Exception:
                pass

        self._write_lease({"pid": child.pid, "started_at": _stamp(), "poll_seconds": poll_seconds})
        self._audit("start", {"pid": child.pid, "poll_seconds": poll_seconds})
        return {"changed": True, "action": "start", "pid": child.pid, **self.status()}

    def pause(self) -> dict[str, Any]:
        return self._control("pause", "paused")

    def resume(self) -> dict[str, Any]:
        return self._control("resume", "running")

    def stop(self) -> dict[str, Any]:
        result = self._control("stop", "stopping")
        # The supervisor observes 'stopping' via its control watcher, but a
        # long routine/tick can delay that by minutes. As an operator-facing
        # control, also signal the managed process we spawned so "stop" means
        # the machine actually stops doing work. Failures are non-fatal: the
        # SQLite control state remains authoritative and the runtime watchdog
        # will still refuse to claim new work.
        pid = (self._read_lease() or {}).get("pid")
        signalled = False
        if isinstance(pid, int) and pid > 0 and self._pid_alive(pid):
            self._signal_stop(pid)
            signalled = True
        # Once no live owner remains, close the lease cleanly so the next
        # start does not inherit a stale 'stopping' row.
        if signalled and not self.status()["running"]:
            import sqlite3
            conn = sqlite3.connect(self.state_db, timeout=10)
            try:
                conn.execute(
                    "UPDATE autonomous_runtime SET state='stopped', owner_id=NULL, pid=NULL, updated_at=? "
                    "WHERE singleton=1", (_stamp(),),
                )
                conn.commit()
            finally:
                conn.close()
            lease = self._read_lease()
            if lease:
                lease["stopped_at"] = _stamp()
                self._write_lease(lease)
        return {**result, "signal_sent": signalled, **self.status()}

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                return code.value == 259
            finally:
                kernel.CloseHandle(handle)
        except (OSError, AttributeError):
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

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
        supervisor = AutonomousSupervisor(
            task_db=self.task_db, routine_db=self.routine_db, state_db=self.state_db,
            improvement_db=self.improvement_db, knowledge_db=self.knowledge_db,
            reminder_db=self.reminder_db, workspace=self.workspace,
        )
        supervisor.ensure_default_routines()
        result = supervisor.run_tick()
        self._audit("run_once", {"task": bool(result.get("task"))})
        return result

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
