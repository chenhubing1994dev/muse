"""Isolated real-process acceptance for the daemon stop handshake.

The child owns only temporary SQLite files; this test never touches a user daemon.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import types

# Isolate lifecycle imports from the unrelated watchdog/agent-discovery branch.
_watchdog_stub = types.ModuleType("muse.watchdog")
_watchdog_stub.run_check = lambda *args, **kwargs: {"status": "stubbed-in-lifecycle-test"}
_previous_watchdog = sys.modules.get("muse.watchdog")
sys.modules["muse.watchdog"] = _watchdog_stub
try:
    from muse.daemon_control import MuseDaemonController
    from muse.autonomous_runtime import RuntimeStateStore
finally:
    if _previous_watchdog is None:
        sys.modules.pop("muse.watchdog", None)
    else:
        sys.modules["muse.watchdog"] = _previous_watchdog


@unittest.skipUnless(os.name == "nt", "requires Windows process creation-time API")
class DaemonLifecycleIntegrationTests(unittest.TestCase):
    @staticmethod
    def _process_started_at(pid: int) -> str:
        class FILETIME(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user),
            ):
                raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
        finally:
            kernel32.CloseHandle(handle)
        ticks = (created.high << 32) | created.low
        unix_seconds = ticks / 10_000_000 - 11_644_473_600
        return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()

    def test_real_isolated_supervisor_releases_owner_without_force_kill(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="muse-daemon-it-") as directory:
            root = Path(directory)
            paths = {name: str(root / filename) for name, filename in {
                "task_db": "tasks.sqlite3", "routine_db": "routines.sqlite3",
                "state_db": "runtime.sqlite3", "improvement_db": "improvements.sqlite3",
                "knowledge_db": "knowledge.sqlite3", "reminder_db": "reminders.sqlite3",
                "memory_db": "memory.sqlite3", "conversation_db": "conversations.sqlite3",
            }.items()}
            child_code = r'''import json, sys, types
from pathlib import Path
watchdog_stub = types.ModuleType("muse.watchdog")
watchdog_stub.run_check = lambda *args, **kwargs: {"status": "stubbed-in-lifecycle-test"}
sys.modules["muse.watchdog"] = watchdog_stub
from muse.autonomous_runtime import AutonomousSupervisor
p = json.loads(sys.argv[1])
s = AutonomousSupervisor(**p, workspace=Path.cwd())
s.ensure_default_routines = lambda: []
def idle_tick(*, stop_event=None):
    if stop_event is not None:
        stop_event.wait(0.05)
    return {"routines": {"processed": 0, "outcomes": []}, "task": None}
s.run_tick = idle_tick
print("READY", flush=True)
print(json.dumps(s.run_forever(poll_seconds=1)), flush=True)
'''
            env = dict(os.environ)
            env["PYTHONPATH"] = str(project_root / "src")
            env["PYTHONUNBUFFERED"] = "1"
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", child_code, json.dumps(paths)],
                cwd=project_root, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            controller = MuseDaemonController(
                workspace=root,
                state_db=paths["state_db"], task_db=paths["task_db"],
                routine_db=paths["routine_db"], improvement_db=paths["improvement_db"],
                knowledge_db=paths["knowledge_db"], reminder_db=paths["reminder_db"],
                control_log=(root / "control-log.jsonl").resolve(),
            )
            try:
                deadline = time.monotonic() + 10
                owned = False
                while time.monotonic() < deadline and child.poll() is None:
                    with RuntimeStateStore(paths["state_db"]) as state:
                        status = state.status()
                    if status.get("owner_id") and status.get("pid") == child.pid and status.get("running"):
                        owned = True
                        break
                    time.sleep(0.05)
                self.assertTrue(owned, "isolated child never acquired the runtime owner")

                started_at = self._process_started_at(child.pid)
                lease = {"pid": child.pid, "started_at": started_at, "poll_seconds": 1}
                controller._write_lease(lease)
                self.assertTrue(controller._pid_matches_lease(child.pid, lease))
                wrong_lease = {**lease, "started_at": "2000-01-01T00:00:00+00:00"}
                self.assertFalse(controller._pid_matches_lease(child.pid, wrong_lease))

                result = controller.stop()
                stdout, stderr = child.communicate(timeout=8)
                self.assertEqual(child.returncode, 0, msg=f"stdout={stdout!r} stderr={stderr!r}")
                self.assertEqual(result["stop_mode"], "graceful_exit")
                self.assertFalse(result["signal_sent"])
                with RuntimeStateStore(paths["state_db"]) as state:
                    final = state.status()
                self.assertEqual(final.get("state"), "stopped")
                self.assertIsNone(final.get("owner_id"))
                self.assertIsNone(final.get("pid"))
            finally:
                if child.poll() is None:
                    # Cleanup is scoped to this exact Popen child in this temporary test.
                    child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=3)


    def test_force_fallback_kills_only_identity_matched_isolated_child(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="muse-daemon-force-it-") as directory:
            root = Path(directory)
            state_db = root / "runtime.sqlite3"
            child_code = r'''import json, sys, time, types, uuid
watchdog_stub = types.ModuleType("muse.watchdog")
watchdog_stub.run_check = lambda *args, **kwargs: {}
sys.modules["muse.watchdog"] = watchdog_stub
from muse.autonomous_runtime import RuntimeStateStore
state = RuntimeStateStore(sys.argv[1])
owner = uuid.uuid4().hex
state.acquire(owner)
print("OWNER_READY", flush=True)
while True:
    state.heartbeat(owner)
    time.sleep(0.05)
'''
            env = dict(os.environ)
            env["PYTHONPATH"] = str(project_root / "src")
            env["PYTHONUNBUFFERED"] = "1"
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", child_code, str(state_db)],
                cwd=project_root, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            controller = MuseDaemonController(
                workspace=root, state_db=state_db, task_db=root / "tasks.sqlite3",
                routine_db=root / "routines.sqlite3", improvement_db=root / "improvements.sqlite3",
                knowledge_db=root / "knowledge.sqlite3", reminder_db=root / "reminders.sqlite3",
                control_log=(root / "control-log.jsonl").resolve(),
            )
            try:
                deadline = time.monotonic() + 10
                owned = False
                while time.monotonic() < deadline and child.poll() is None:
                    with RuntimeStateStore(state_db) as state:
                        status = state.status()
                    if status.get("owner_id") and status.get("pid") == child.pid and status.get("running"):
                        owned = True
                        break
                    time.sleep(0.05)
                self.assertTrue(owned, "isolated heartbeat child never acquired Runtime owner")
                lease = {"pid": child.pid, "started_at": self._process_started_at(child.pid)}
                controller._write_lease(lease)
                self.assertTrue(controller._pid_matches_lease(child.pid, lease))

                result = controller.stop()
                stdout, stderr = child.communicate(timeout=8)
                self.assertIsNotNone(child.returncode, msg=f"child remained alive; stdout={stdout!r} stderr={stderr!r}")
                self.assertEqual(result["stop_mode"], "forced_after_timeout")
                self.assertTrue(result["signal_sent"])
                with RuntimeStateStore(state_db) as state:
                    final = state.status()
                # Force-kill must not forge Supervisor.release() or a stopped owner row.
                self.assertEqual(final.get("state"), "stopping")
                self.assertEqual(final.get("pid"), child.pid)
                self.assertTrue(final.get("owner_id"))
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=3)

if __name__ == "__main__":
    unittest.main()
