from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import Mock

from muse.obsidian_cli import ObsidianCLI, ObsidianCLIError


class ObsidianCLITests(unittest.TestCase):
    def test_search_targets_named_vault_without_shell_and_parses_json(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout='["02-Projects/muse/memory-000002.md"]', stderr=""))
        client = ObsidianCLI("Muse Vault", executable="Obsidian.com", runner=runner)
        result = client.search("不重复造轮子", limit=7)
        self.assertEqual(result, ["02-Projects/muse/memory-000002.md"])
        args, kwargs = runner.call_args
        self.assertEqual(args[0], ["Obsidian.com", "vault=Muse Vault", "search", "query=不重复造轮子", "limit=7", "format=json"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["timeout"], 20.0)

    def test_read_targets_only_safe_relative_note_path(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="# Memory\n", stderr=""))
        client = ObsidianCLI("vault", executable="Obsidian.com", runner=runner)
        self.assertEqual(client.read("02-Projects/muse/memory-000002.md"), "# Memory\n")
        self.assertEqual(runner.call_args.args[0], ["Obsidian.com", "vault=vault", "read", "path=02-Projects/muse/memory-000002.md"])
        for unsafe in ("../outside.md", "a/../../outside.md", "/absolute.md", "C:\\outside.md"):
            with self.subTest(path=unsafe), self.assertRaises(ValueError):
                client.read(unsafe)

    def test_search_rejects_invalid_json_and_nonzero_cli_exit(self) -> None:
        invalid_json = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="not json", stderr=""))
        with self.assertRaises(ObsidianCLIError):
            ObsidianCLI("vault", executable="Obsidian.com", runner=invalid_json).search("query")
        failed = Mock(return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="vault not found"))
        with self.assertRaisesRegex(ObsidianCLIError, "vault not found"):
            ObsidianCLI("vault", executable="Obsidian.com", runner=failed).search("query")

    def test_rejects_invalid_search_parameters(self) -> None:
        client = ObsidianCLI("vault", executable="Obsidian.com", runner=Mock())
        with self.assertRaises(ValueError):
            client.search(" ")
        with self.assertRaises(ValueError):
            client.search("query", limit=0)


if __name__ == "__main__":
    unittest.main()
