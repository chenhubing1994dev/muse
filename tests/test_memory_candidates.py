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


class MemoryCandidateTests(unittest.TestCase):
    def test_proposal_stays_pending_and_is_not_searchable_until_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                candidate = memory.propose(
                    "User prefers concise Cantonese replies",
                    project="voice", source="conversation:thread-1", confidence=0.9,
                )
                self.assertEqual(candidate["status"], "pending")
                self.assertEqual(memory.list_pending(), [candidate])
                self.assertEqual(memory.search("Cantonese"), [])

    def test_approval_promotes_candidate_once_and_records_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                candidate = memory.propose(
                    "User prefers concise Cantonese replies",
                    project="voice", source="conversation:thread-1", confidence=0.9,
                )
                approved = memory.approve_candidate(candidate["id"], approved_by="user")
                repeated = memory.approve_candidate(candidate["id"], approved_by="user")
                self.assertEqual(approved["status"], "approved")
                self.assertEqual(repeated["memory_id"], approved["memory_id"])
                self.assertEqual(len(memory.search("Cantonese")), 1)
                saved = memory.get(approved["memory_id"])
                self.assertEqual(saved["source"], "conversation:thread-1")
                self.assertEqual(saved["project"], "voice")
                self.assertEqual(saved["confidence"], 0.9)
                self.assertEqual(memory.history(saved["id"])[0]["content"], saved["content"])

    def test_rejection_is_idempotent_and_never_promotes_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                candidate = memory.propose("Keep this out", project="general", source="test")
                rejected = memory.reject_candidate(candidate["id"], rejected_by="user", reason="not useful")
                repeated = memory.reject_candidate(candidate["id"], rejected_by="user", reason="not useful")
                self.assertEqual(rejected["status"], "rejected")
                self.assertEqual(repeated["id"], candidate["id"])
                self.assertEqual(memory.search("Keep this out"), [])
                with self.assertRaises(ValueError):
                    memory.approve_candidate(candidate["id"], approved_by="user")

    def test_pending_duplicate_proposals_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                first = memory.propose("A stable preference", project="voice", source="thread:1")
                second = memory.propose("A stable preference", project="voice", source="thread:1")
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(len(memory.list_pending()), 1)

    def test_sensitive_credentials_are_rejected_before_candidate_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                with self.assertRaisesRegex(ValueError, "sensitive"):
                    memory.propose("My password is hunter2", project="general", source="chat")
                with self.assertRaisesRegex(ValueError, "sensitive"):
                    memory.propose("API key: sk-12345678901234567890", project="general", source="chat")
                self.assertEqual(memory.list_pending(), [])


class MemoryCandidateCliTests(unittest.TestCase):
    def run_cli(self, database: Path, *arguments: str) -> str:
        output = io.StringIO()
        with patch("sys.argv", ["muse-memory", "--db", str(database), *arguments]):
            with contextlib.redirect_stdout(output):
                main()
        return output.getvalue()

    def test_cli_propose_list_pending_approve_and_reject(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            proposed = json.loads(self.run_cli(
                database, "propose", "User prefers concise Cantonese", "--project", "voice",
                "--source", "thread:cli", "--confidence", "0.8",
            ))
            self.assertEqual(proposed["status"], "pending")
            listed = json.loads(self.run_cli(database, "list-pending"))
            self.assertEqual([item["id"] for item in listed], [proposed["id"]])
            approved = json.loads(self.run_cli(database, "approve", str(proposed["id"]), "--by", "user"))
            self.assertEqual(approved["status"], "approved")
            with MemoryStore(database) as memory:
                self.assertEqual(len(memory.search("Cantonese")), 1)

            second = json.loads(self.run_cli(
                database, "propose", "A separate candidate", "--source", "thread:cli",
            ))
            rejected = json.loads(self.run_cli(
                database, "reject", str(second["id"]), "--by", "user", "--reason", "not wanted",
            ))
            self.assertEqual(rejected["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
