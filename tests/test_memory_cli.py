from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from muse.memory import MemoryStore
from muse.memory_cli import main


class MemoryCliTests(unittest.TestCase):
    def run_cli(self, database: Path, *arguments: str) -> str:
        output = io.StringIO()
        with patch("sys.argv", ["muse-memory", "--db", str(database), *arguments]):
            with contextlib.redirect_stdout(output):
                main()
        return output.getvalue()

    def test_list_shows_saved_memories_and_filters_by_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with MemoryStore(database) as memory:
                memory.add("Cantonese voice preference", project="voice", source="user")
                memory.add("Muse uses SQLite", project="Muse", source="project")

            result = json.loads(self.run_cli(database, "list", "--project", "voice"))

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["project"], "voice")

    def test_revise_command_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with MemoryStore(database) as memory:
                saved = memory.add("Old preference", project="voice", source="user")

            result = json.loads(self.run_cli(database, "revise", str(saved["id"]), "New preference", "--confidence", "0.8"))

            self.assertEqual(result["content"], "New preference")
            self.assertEqual(result["revision"], 2)
            with MemoryStore(database) as memory:
                self.assertEqual([item["content"] for item in memory.history(saved["id"])], ["Old preference", "New preference"])

    def test_delete_command_soft_deletes_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with MemoryStore(database) as memory:
                saved = memory.add("Temporary note", project="general", source="user")

            result = json.loads(self.run_cli(database, "delete", str(saved["id"])))

            self.assertTrue(result["deleted"])
            with MemoryStore(database) as memory:
                self.assertIsNone(memory.get(saved["id"]))
                self.assertEqual([item["content"] for item in memory.history(saved["id"])], ["Temporary note"])

    def run_cli_status(self, database: Path, *arguments: str) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["muse-memory", "--db", str(database), *arguments]):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main()
        return code, output.getvalue(), errors.getvalue()

    def test_approve_by_agent_is_refused_with_exit_code_3_and_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with MemoryStore(database) as memory:
                candidate = memory.propose("Prefers concise replies", project="general", source="user", track="user")
                before = len(memory.list_memories())

            for approver in ("agent:planner", "owner"):
                code, out, err = self.run_cli_status(database, "approve", str(candidate["id"]), "--by", approver)
                self.assertEqual(code, 3)
                self.assertEqual(out, "")
                self.assertNotIn("Traceback", err)
                report = json.loads(err)
                self.assertFalse(report["approved"])
                self.assertEqual(report["error_type"], "PermissionError")
            with MemoryStore(database) as memory:
                self.assertEqual(memory.get_candidate(candidate["id"])["status"], "pending")
                self.assertEqual(len(memory.list_memories()), before)

    def test_approve_unknown_candidate_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            code, out, err = self.run_cli_status(database, "approve", "999", "--by", "user:alice")
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertEqual(json.loads(err)["error_type"], "ValueError")

    def test_approve_by_user_still_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with MemoryStore(database) as memory:
                candidate = memory.propose("Prefers concise replies", project="general", source="user", track="user")
            code, out, err = self.run_cli_status(database, "approve", str(candidate["id"]), "--by", "user:alice")
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            self.assertEqual(json.loads(out)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
