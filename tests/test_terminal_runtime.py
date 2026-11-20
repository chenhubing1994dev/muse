from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from muse.terminal_runtime import HerdrCoreProvider, PsmuxProvider


class TerminalRuntimeProviderTests(unittest.TestCase):
    def _fake_hc(self, root: Path) -> tuple[list[str], Path]:
        script = root / "fake_hc.py"
        log = root / "hc_calls.jsonl"
        script.write_text(
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['HC_TEST_LOG'], 'a', encoding='utf-8') as f: f.write(json.dumps(args) + '\\n')\n"
            "if args == ['--version']:\n"
            " print('hc 0.1.19')\n"
            "elif args == ['schema', '--json']:\n"
            " print(json.dumps({'openrpc':'1.2.6','info':{'title':'hc'},'methods':[{'name':'terminal.list'}]}))\n"
            "elif args == ['ls', '--json']:\n"
            " print(json.dumps({'terminals':[{'id':'t1','status':'working'}]}))\n"
            "else:\n"
            " print('unsupported arguments', file=sys.stderr); sys.exit(2)\n",
            encoding="utf-8",
        )
        return [sys.executable, str(script)], log

    def test_herdr_probe_uses_documented_version_and_openrpc_schema_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command, log = self._fake_hc(root)
            old = os.environ.get("HC_TEST_LOG")
            os.environ["HC_TEST_LOG"] = str(log)
            try:
                provider = HerdrCoreProvider(command=command)
                result = provider.probe()
            finally:
                if old is None:
                    os.environ.pop("HC_TEST_LOG", None)
                else:
                    os.environ["HC_TEST_LOG"] = old
            self.assertTrue(result.available)
            self.assertEqual(result.version, "hc 0.1.19")
            self.assertTrue(result.schema_valid)
            self.assertEqual(result.schema_methods, ("terminal.list",))
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls, [["--version"], ["schema", "--json"]])

    def test_herdr_list_sessions_uses_only_read_only_ls_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command, log = self._fake_hc(root)
            old = os.environ.get("HC_TEST_LOG")
            os.environ["HC_TEST_LOG"] = str(log)
            try:
                result = HerdrCoreProvider(command=command).list_sessions()
            finally:
                if old is None:
                    os.environ.pop("HC_TEST_LOG", None)
                else:
                    os.environ["HC_TEST_LOG"] = old
            self.assertTrue(result.available)
            self.assertEqual(result.payload["terminals"][0]["id"], "t1")
            self.assertEqual([json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()], [["ls", "--json"]])

    def test_herdr_provider_fails_closed_when_binary_is_missing(self):
        result = HerdrCoreProvider(executable_lookup=lambda _name: None).probe()
        self.assertFalse(result.available)
        self.assertEqual(result.error_code, "hc_not_found")

    def test_herdr_provider_rejects_invalid_schema_without_claiming_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fake_hc.py"
            script.write_text(
                "import sys\n"
                "print('hc 0.1.19') if sys.argv[1:] == ['--version'] else print('{bad json')\n",
                encoding="utf-8",
            )
            result = HerdrCoreProvider(command=[sys.executable, str(script)]).probe()
        self.assertFalse(result.available)
        self.assertFalse(result.schema_valid)
        self.assertEqual(result.error_code, "hc_schema_invalid")
        self.assertEqual(result.schema_methods, ())

    def test_psmux_provider_delegates_to_existing_read_only_observers(self):
        calls: list[tuple[str, dict[str, object]]] = []

        def inspect(request):
            calls.append(("inspect", request))
            return {"status": "completed", "session": "muse-dev", "active_job_id": None, "windows": ["0|main"]}

        def jobs(request):
            calls.append(("jobs", request))
            return {"status": "completed", "jobs": [], "count": 0, "read_only": True}

        provider = PsmuxProvider(inspect_session=inspect, list_jobs=jobs, session="muse-dev")
        probe = provider.probe()
        listing = provider.list_sessions()
        self.assertTrue(probe.available)
        self.assertEqual(probe.provider_id, "psmux")
        self.assertTrue(listing.available)
        self.assertEqual(listing.payload["psmux"]["active_job_id"], None)
        self.assertEqual(calls, [("inspect", {"session": "muse-dev"}), ("inspect", {"session": "muse-dev"}), ("jobs", {"session": "muse-dev", "limit": 20})])

    def test_herdr_command_rejects_empty_or_shell_string_commands(self):
        with self.assertRaises(ValueError):
            HerdrCoreProvider(command=[])
        with self.assertRaises(ValueError):
            HerdrCoreProvider(command="hc")

    def test_psmux_listing_redacts_job_output_and_filesystem_paths(self):
        def inspect(_request):
            return {"status": "completed", "session": "muse-dev", "active_job_id": None, "windows": ["0|main"]}

        def jobs(_request):
            return {"status": "completed", "count": 1, "jobs": [{
                "job_id": "a" * 32, "status": "completed", "exit_code": 0,
                "output_tail": "private terminal output", "output_file": "C:/private/output.log",
                "working_directory": "D:/private/project", "language": "python",
            }]}

        result = PsmuxProvider(inspect_session=inspect, list_jobs=jobs).list_sessions()
        serialized = json.dumps(result.payload)
        self.assertNotIn("private terminal output", serialized)
        self.assertNotIn("C:/private/output.log", serialized)
        self.assertNotIn("D:/private/project", serialized)
        self.assertEqual(result.payload["jobs"]["jobs"][0]["job_id"], "a" * 32)


if __name__ == "__main__":
    unittest.main()
