from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import muse.autonomous_runtime as runtime


class AutonomousRuntimeCliPathTests(unittest.TestCase):
    def test_cli_defaults_are_anchored_to_project_root_outside_project_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory)
            with patch("muse.autonomous_runtime.Path.cwd", return_value=outside):
                with patch("muse.autonomous_runtime.AutonomousSupervisor") as supervisor_class:
                    supervisor_class.return_value.status.return_value = {"runtime": {"running": False}}
                    self.assertEqual(runtime.main(["status"]), 0)

            kwargs = supervisor_class.call_args.kwargs
            project_root = Path(runtime.__file__).resolve().parents[2]
            expected = {
                "task_db": project_root / "data/tasks/muse.sqlite3",
                "routine_db": project_root / "data/routines/muse.sqlite3",
                "state_db": project_root / "data/autonomy/runtime.sqlite3",
                "improvement_db": project_root / "data/improvements/muse.sqlite3",
                "knowledge_db": project_root / "data/research/knowledge.sqlite3",
                "reminder_db": project_root / "data/reminders/muse.sqlite3",
            }
            for key, path in expected.items():
                self.assertEqual(Path(kwargs[key]), path)
                self.assertTrue(Path(kwargs[key]).is_absolute())
            self.assertEqual(kwargs["workspace"], project_root)


    def test_research_once_anchors_evidence_memory_and_conversation_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory)
            cli_args = [
                "--task-db", str(outside / "tasks.sqlite3"),
                "--routine-db", str(outside / "routines.sqlite3"),
                "--state-db", str(outside / "runtime.sqlite3"),
                "--improvement-db", str(outside / "improvements.sqlite3"),
                "--knowledge-db", str(outside / "knowledge.sqlite3"),
                "--reminder-db", str(outside / "reminders.sqlite3"),
                "research-once",
            ]
            with patch("muse.autonomous_runtime.Path.cwd", return_value=outside):
                with patch.object(
                    runtime.AutonomousSupervisor,
                    "_build_feedback_reviewer",
                    return_value=(None, None, {"status": "pending"}, None),
                ):
                    with patch("muse.autonomous_runtime.AutonomousResearchEngine") as engine_class:
                        engine_class.return_value.run_once.return_value = {
                            "status": "completed", "report": {"topic": "test"},
                        }
                        self.assertEqual(runtime.main(cli_args), 0)

            kwargs = engine_class.call_args.kwargs
            project_root = Path(runtime.__file__).resolve().parents[2]
            self.assertEqual(kwargs["evidence_db"], project_root / "data/research/evidence.sqlite3")
            self.assertEqual(kwargs["memory_db"], project_root / "data/memory/muse.sqlite3")
            self.assertEqual(kwargs["conversation_db"], project_root / "data/conversations/muse.sqlite3")
            self.assertEqual(kwargs["workspace"], project_root)


if __name__ == "__main__":
    unittest.main()
