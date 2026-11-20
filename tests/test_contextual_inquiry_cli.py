from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from muse.contextual_inquiry_cli import command_submit, inquiry_from_dict
from muse.contextual_inquiry_security import unprotect_case_bytes
from muse.task_queue import TaskQueue


class ContextualInquiryConsentCliTests(unittest.TestCase):
    def inquiry_data(self):
        return {
            "inquiry_id": "cli-case-1",
            "user_goal": "Understand a sensitive situation without assuming motives.",
            "observations": [{
                "observation_id": "obs-1", "source_kind": "user_quote",
                "content": "PRIVATE OBSERVATION MUST NOT ENTER THE TASK QUEUE",
                "source_ref": "private-conversation:turn-9",
            }],
            "hypotheses": [{
                "hypothesis_id": "hyp-1", "statement": "A tentative explanation.",
                "source_refs": ["obs-1"], "counter_refs": [], "confidence": 0.4,
                "unsourced": False, "falsification_condition": "An independent record contradicts it.",
            }],
            "unknowns": ["The other person's perspective is not known."],
            "safety_route": "normal",
        }

    def write_input(self, root):
        source = root / "inquiry.json"
        source.write_text(json.dumps(self.inquiry_data()), encoding="utf-8")
        return source

    def make_args(self, source, database):
        return type("Args", (), {
            "input": str(source), "database": str(database), "priority": 50,
        })()

    def test_input_file_cannot_self_grant_storage_consent(self):
        data = self.inquiry_data()
        data["consent_to_store"] = True
        with self.assertRaisesRegex(ValueError, "unsupported inquiry fields"):
            inquiry_from_dict(data)
        inquiry = inquiry_from_dict(self.inquiry_data())
        self.assertFalse(inquiry.consent_to_store)

    def test_declining_task_creation_performs_no_database_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_input(root)
            database = root / "tasks.sqlite3"
            answers = iter(["no"])
            self.assertEqual(command_submit(
                self.make_args(source, database),
                input_fn=lambda _prompt: next(answers),
            ), 2)
            self.assertFalse(database.exists())

    def test_default_consent_creates_generic_approval_gated_task_without_case_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_input(root)
            database = root / "tasks.sqlite3"
            answers = iter(["yes", "no", "no"])
            self.assertEqual(command_submit(
                self.make_args(source, database),
                input_fn=lambda _prompt: next(answers),
            ), 0)
            with TaskQueue(database) as queue:
                task = queue.list_tasks()[0]
                self.assertEqual(task["status"], "waiting_approval")
                self.assertEqual(
                    task["payload"]["workflow_contract"]["goal"],
                    "Review a contextual inquiry using evidence provenance and safety boundaries.",
                )
                serialized = json.dumps(task, ensure_ascii=False)
                self.assertNotIn("PRIVATE OBSERVATION", serialized)
                self.assertNotIn("private-conversation:turn-9", serialized)
                self.assertNotIn("Understand a sensitive situation", serialized)
                self.assertFalse(task["payload"]["case_context_envelope_stored"])
                self.assertNotIn("case_context_envelope", task["payload"])
                self.assertFalse(task["payload"]["case_context_attached"])
                self.assertTrue(task["payload"]["requires_context_reentry"])

    def test_goal_is_stored_only_after_its_separate_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_input(root)
            database = root / "tasks.sqlite3"
            answers = iter(["yes", "yes", "no"])
            self.assertEqual(command_submit(
                self.make_args(source, database),
                input_fn=lambda _prompt: next(answers),
            ), 0)
            with TaskQueue(database) as queue:
                task = queue.list_tasks()[0]
                self.assertEqual(task["status"], "waiting_approval")
                self.assertEqual(
                    task["payload"]["workflow_contract"]["goal"],
                    "Understand a sensitive situation without assuming motives.",
                )
                serialized = json.dumps(task, ensure_ascii=False)
                self.assertNotIn("PRIVATE OBSERVATION", serialized)
                self.assertNotIn("private-conversation:turn-9", serialized)
                self.assertNotIn("A tentative explanation", serialized)
                self.assertFalse(task["payload"]["case_context_envelope_stored"])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration test")
    def test_complete_case_is_ciphertext_only_after_third_consent_and_remains_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_input(root)
            database = root / "tasks.sqlite3"
            answers = iter(["yes", "no", "yes"])
            self.assertEqual(command_submit(
                self.make_args(source, database),
                input_fn=lambda _prompt: next(answers),
            ), 0)
            with TaskQueue(database) as queue:
                task = queue.list_tasks()[0]
                payload = task["payload"]
                self.assertTrue(payload["case_context_envelope_stored"])
                self.assertEqual(payload["context_status"], "encrypted_pending_handler")
                self.assertFalse(payload["case_context_attached"])
                self.assertTrue(payload["requires_context_reentry"])
                self.assertFalse(payload["long_term_storage_performed"])

                persisted = " ".join(
                    str(value)
                    for table in ("tasks", "task_events")
                    for row in queue._connection.execute(f"SELECT * FROM {table}").fetchall()
                    for value in tuple(row)
                )
                for private_value in (
                    "PRIVATE OBSERVATION MUST NOT ENTER THE TASK QUEUE",
                    "private-conversation:turn-9",
                    "A tentative explanation.",
                    "The other person's perspective is not known.",
                    "Understand a sensitive situation without assuming motives.",
                ):
                    self.assertNotIn(private_value, persisted)

                decrypted = unprotect_case_bytes(payload["case_context_envelope"]).decode("utf-8")
                self.assertIn("PRIVATE OBSERVATION MUST NOT ENTER THE TASK QUEUE", decrypted)
                self.assertIn("private-conversation:turn-9", decrypted)
                with self.assertRaises(PermissionError):
                    queue.approve(task["id"], approved_by="security-test")


if __name__ == "__main__":
    unittest.main()
