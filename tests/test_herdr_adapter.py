from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from muse.agent_registry import AgentRegistry
from muse.herdr_adapter import format_osc7501_status, muse_state_to_herdr, write_osc7501_status
from muse.herdr_adapter import main as herdr_status_main

class HerdrAdapterTests(unittest.TestCase):
    def test_muse_state_mapping_is_conservative(self) -> None:
        self.assertEqual(muse_state_to_herdr("busy"), "working")
        self.assertEqual(muse_state_to_herdr("blocked"), "blocked")
        self.assertEqual(muse_state_to_herdr("online"), "idle")
        self.assertIsNone(muse_state_to_herdr("offline"))
        self.assertIsNone(muse_state_to_herdr("disabled"))
        with self.assertRaises(ValueError):
            muse_state_to_herdr("approved")

    def test_osc_sequence_matches_protocol_shape(self) -> None:
        self.assertEqual(format_osc7501_status("blocked", app="muse"), "\x1b]7501;state=blocked:app=muse\x1b\\")
        stream = io.StringIO()
        write_osc7501_status(stream, "working")
        self.assertEqual(stream.getvalue(), "\x1b]7501;state=working:app=muse\x1b\\")

    def test_osc_rejects_control_and_untrusted_fields(self) -> None:
        for state in ("unknown", "approved", "blocked\x1b[31m"):
            with self.subTest(state=state), self.assertRaises(ValueError):
                format_osc7501_status(state)
        for app in ("", "muse:evil", "muse\x1b\\", "x" * 33):
            with self.subTest(app=app), self.assertRaises(ValueError):
                format_osc7501_status("idle", app=app)

    def test_cli_validates_osc_before_persisting_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "agents.sqlite3"
            with self.assertRaises(ValueError):
                herdr_status_main([
                    "--db", str(database), "--agent-id", "worker",
                    "--state", "blocked", "--osc", "--app", "bad:slug",
                ])
            self.assertFalse(database.exists())

    def test_registry_report_is_durable_and_does_not_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "agents.sqlite3"
            with AgentRegistry(database) as registry:
                registry.register("worker", role="developer")
                self.assertTrue(registry.report_state("worker", "blocked", message="needs review"))
            with AgentRegistry(database) as registry:
                agent = registry.get("worker")
                self.assertEqual(agent["reported_state"], "blocked")
                self.assertEqual(agent["status"], "blocked")
                self.assertEqual(agent["reported_message"], "needs review")
                self.assertIsNone(agent["current_task_id"])


if __name__ == "__main__":
    unittest.main()
