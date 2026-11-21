"""Tests for the bounded autonomous-runtime lifecycle controller."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from muse.daemon_control import DaemonError, MuseDaemonController


class DaemonControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        self.controller = MuseDaemonController(workspace=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_status_defaults_to_stopped(self) -> None:
        status = self.controller.status()
        self.assertEqual(status["state"], "stopped")
        self.assertFalse(status["running"])
        self.assertIsNone(status["managed_pid"])

    def test_pause_resume_stop_are_audited(self) -> None:
        self.controller.pause()
        self.assertEqual(self.controller.status()["state"], "paused")
        self.controller.resume()
        self.assertEqual(self.controller.status()["state"], "running")
        self.controller.stop()
        # No live owner exists, so set_control('stopping') resolves to stopped.
        self.assertEqual(self.controller.status()["state"], "stopped")
        log = self.controller.control_log.read_text(encoding="utf-8").splitlines()
        actions = [json.loads(line)["action"] for line in log]
        self.assertEqual(actions, ["pause", "resume", "stop"])

    def test_stop_waits_for_graceful_owner_exit_without_force_signal(self) -> None:
        self.controller._write_lease({"pid": 1234})
        with (
            mock.patch.object(self.controller, "_runtime_owner_matches", side_effect=[True, False]),
            mock.patch.object(self.controller, "_pid_matches_lease", return_value=True),
            mock.patch.object(self.controller, "_wait_for_owner_exit", return_value=True),
            mock.patch.object(self.controller, "_signal_stop") as force,
        ):
            result = self.controller.stop()
        force.assert_not_called()
        self.assertFalse(result["signal_sent"])
        self.assertEqual(result["stop_mode"], "graceful_exit")

    def test_stop_uses_force_fallback_only_after_timeout_and_owner_recheck(self) -> None:
        self.controller._write_lease({"pid": 4321})
        with (
            mock.patch.object(self.controller, "_runtime_owner_matches", return_value=True),
            mock.patch.object(self.controller, "_pid_matches_lease", return_value=True),
            mock.patch.object(self.controller, "_wait_for_owner_exit", return_value=False),
            mock.patch.object(self.controller, "_signal_stop") as force,
        ):
            result = self.controller.stop()
        force.assert_called_once_with(4321)
        self.assertTrue(result["signal_sent"])
        self.assertEqual(result["stop_mode"], "forced_after_timeout")

    def test_stop_never_signals_lease_pid_without_matching_runtime_owner(self) -> None:
        self.controller._write_lease({"pid": 9999})
        with mock.patch.object(self.controller, "_runtime_owner_matches", return_value=False), mock.patch.object(self.controller, "_signal_stop") as force:
            result = self.controller.stop()
        force.assert_not_called()
        self.assertFalse(result["signal_sent"])
        self.assertEqual(result["stop_mode"], "pid_or_owner_mismatch")

    def test_dead_process_with_stale_owner_row_is_not_reported_as_graceful(self) -> None:
        self.controller._write_lease({"pid": 5678})
        with (
            mock.patch.object(self.controller, "_runtime_owner_matches", side_effect=[True, False]),
            mock.patch.object(self.controller, "_pid_matches_lease", return_value=True),
            mock.patch.object(self.controller, "_wait_for_owner_exit", return_value=False),
            mock.patch.object(self.controller, "_runtime_owner_record_matches", return_value=True),
            mock.patch.object(self.controller, "_signal_stop") as force,
        ):
            result = self.controller.stop()
        force.assert_not_called()
        self.assertEqual(result["stop_mode"], "owner_identity_unverified_or_process_exited")

    @unittest.skipUnless(os.name == "nt", "Windows process identity API")
    def test_pid_matches_lease_checks_real_process_creation_time(self) -> None:
        import ctypes
        from ctypes import wintypes

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
        handle = kernel32.OpenProcess(0x1000, False, os.getpid())
        self.assertTrue(handle)
        try:
            self.assertTrue(kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user),
            ))
        finally:
            kernel32.CloseHandle(handle)
        ticks = (created.high << 32) | created.low
        started = datetime.fromtimestamp(
            ticks / 10_000_000 - 11_644_473_600, tz=timezone.utc
        )
        self.assertTrue(self.controller._pid_matches_lease(
            os.getpid(), {"started_at": started.isoformat()}
        ))
        wrong_start = (started - timedelta(seconds=60)).isoformat()
        self.assertFalse(self.controller._pid_matches_lease(
            os.getpid(), {"started_at": wrong_start}
        ))

if __name__ == "__main__":
    unittest.main()
