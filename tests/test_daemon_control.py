"""Tests for the bounded autonomous-runtime lifecycle controller."""
from __future__ import annotations

import json
import tempfile
import unittest
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

    def test_invalid_poll_seconds_rejected(self) -> None:
        with self.assertRaises(DaemonError):
            self.controller.start(poll_seconds=0)
        with self.assertRaises(DaemonError):
            self.controller.start(poll_seconds=61)


if __name__ == "__main__":
    unittest.main()
