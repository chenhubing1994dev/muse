from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from muse.improvement_cli import main
from muse.improvement import ImprovementStore


class ImprovementCliTests(unittest.TestCase):
    def test_cli_creates_lists_and_shows_durable_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--db", str(database), "add", "--title", "Add reviewer", "--problem", "No reviewer", "--hypothesis", "A separate reviewer catches regressions"]), 0)
            self.assertIn('"status": "proposed"', output.getvalue())
            with ImprovementStore(database) as store:
                proposals = store.list_proposals()
                proposal_id = proposals[0]["id"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--db", str(database), "list"]), 0)
            self.assertIn(proposal_id, output.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--db", str(database), "show", proposal_id]), 0)
            self.assertIn("Add reviewer", output.getvalue())

    def test_cli_records_research_source_and_approval_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            with ImprovementStore(database) as store:
                proposal = store.create_proposal("Adapter", "No adapter", "Use provider protocol")
            self.assertEqual(main(["--db", str(database), "move", proposal["id"], "researching", "--by", "researcher"]), 0)
            self.assertEqual(main(["--db", str(database), "source", proposal["id"], "--title", "Official docs", "--url", "https://example.org/docs", "--license-status", "not_applicable", "--security-status", "reviewed"]), 0)
            self.assertEqual(main(["--db", str(database), "move", proposal["id"], "ready_for_review", "--by", "reviewer"]), 0)
            self.assertEqual(main(["--db", str(database), "approve", proposal["id"], "--by", "user"]), 0)
            with ImprovementStore(database) as store:
                self.assertEqual(store.get(proposal["id"])["status"], "approved")


    def test_prepare_ticket_records_project_usage_without_task_or_agent(self) -> None:
        from unittest.mock import patch
        from muse.model_call_context import record_current_model_usage
        from muse.usage_ledger import UsageLedger

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def list_models(self):
                return [{"id": "fake-builder"}]

            def chat(self, *args, **kwargs):
                record_current_model_usage(
                    "lm-studio-local", "improvement-builder-model",
                    {"prompt_tokens": 21, "completion_tokens": 8, "total_tokens": 29},
                )
                return "{}"

        class FakePlanner:
            def __init__(self, _workspace, **kwargs):
                self.builder = kwargs["builder"]

            def prepare_ticket(self, proposal_id):
                self.builder("prepare a bounded patch preview")
                return {"status": "ticket_prepared", "proposal_id": proposal_id}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "improvements.sqlite3"
            task_db = root / "tasks.sqlite3"
            workspace = root / "workspace"
            workspace.mkdir()
            with ImprovementStore(database) as store:
                proposal = store.create_proposal("Adapter", "No adapter", "Use local provider protocol")
            output = io.StringIO()
            with (
                patch("muse.ruflo_adapter.load_muse_model_config", return_value={
                    "provider": "lmstudio", "model": "fake-builder", "base_url": "http://127.0.0.1:1234/v1",
                }),
                patch("muse.model_client.LMStudioClient", FakeClient),
                patch("muse.improvement_patch_planner.ImprovementPatchPlanner", FakePlanner),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(main([
                    "--db", str(database), "prepare-ticket", proposal["id"],
                    "--task-db", str(task_db), "--workspace", str(workspace),
                ]), 0)
            self.assertEqual(__import__("json").loads(output.getvalue())["status"], "ticket_prepared")
            with UsageLedger(task_db.with_name("model_usage.sqlite3")) as ledger:
                events = ledger.list_events(limit=10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["project_id"], "muse")
            self.assertIsNone(events[0]["task_id"])
            self.assertIsNone(events[0]["agent_id"])
            self.assertEqual(events[0]["input_tokens"], 21)
            self.assertEqual(events[0]["output_tokens"], 8)


if __name__ == "__main__":
    unittest.main()
