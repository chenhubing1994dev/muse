import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.desktop_adapter import AgentDesktopAdapter, DesktopError


class DesktopAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exe = Path(self.tmp.name) / "agent-desktop.exe"
        self.exe.write_bytes(b"test")
        self.adapter = AgentDesktopAdapter(self.exe)

    def tearDown(self):
        self.tmp.cleanup()

    @patch("muse.desktop_adapter.subprocess.run")
    def test_status_parses_json_and_uses_native_argv_without_shell(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "data": {"platform": "windows"}}), "")
        self.assertEqual(self.adapter.status().data["platform"], "windows")
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(self.exe.resolve()), "status"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_actions_require_approval_and_blocked_commands_never_run(self):
        with self.assertRaises(PermissionError):
            self.adapter.call("click", ["@s:e1"])
        with self.assertRaises(PermissionError):
            self.adapter.call("close-app", ["notepad"], approved=True)
        with self.assertRaises(PermissionError):
            self.adapter.call("click", ["@s:e1", "--headed"], approved=True)

    @patch("muse.desktop_adapter.subprocess.run")
    def test_approved_semantic_action_is_invoked_once(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '{"ok":true,"data":{"delivered":"verified"}}', "")
        result = self.adapter.call("click", ["@s:e1"], approved=True)
        self.assertEqual(result.data["delivered"], "verified")
        self.assertEqual(run.call_count, 1)

    @patch("muse.desktop_adapter.subprocess.run")
    def test_invalid_envelope_and_process_errors_fail_closed(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "not-json", "")
        with self.assertRaises(DesktopError):
            self.adapter.status()
        run.return_value = subprocess.CompletedProcess([], 1, '{"ok":false,"error":{"code":"POLICY_DENIED"}}', "")
        with self.assertRaises(DesktopError):
            self.adapter.status()

    def test_argument_bounds_and_unknown_command(self):
        with self.assertRaises(ValueError):
            self.adapter.call("snapshot", ["x" * 2049])
        with self.assertRaises(PermissionError):
            self.adapter.call("run", ["calc.exe"], approved=True)


if __name__ == "__main__":
    unittest.main()
