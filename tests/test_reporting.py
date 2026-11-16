from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue
from muse.reporting import create_half_hour_report


class HalfHourReportTests(unittest.TestCase):
    def test_report_summarizes_recent_activity_and_30_minute_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "data" / "tasks" / "tasks.sqlite3"
            database.parent.mkdir(parents=True)
            now = datetime.now(timezone.utc) + timedelta(seconds=1)
            with TaskQueue(database) as queue:
                queue.enqueue(
                    "private task title", {"kind": "echo", "secret": "payload-secret"},
                    priority=90, estimated_seconds=600,
                )
            with ProgressEventStore(database) as events:
                events.record("test", "suite-1", "completed", summary="must not appear in report")
                events.record("research", "scan-1", "progress", summary="private research notes")
                events._connection.execute(
                    "UPDATE progress_events SET created_at=? WHERE subject_id='scan-1'",
                    ((now - timedelta(minutes=45)).isoformat(),),
                )
                events._connection.commit()
            report = create_half_hour_report(database, root / "reports", now=now)
            self.assertEqual(report["window_minutes"], 30)
            self.assertEqual(report["workload"]["window_seconds"], 1800)
            self.assertEqual(report["workload"]["estimated_work_seconds"], 600)
            self.assertEqual(report["runtime_calibration"]["source"], "configured_default")
            self.assertEqual(report["runtime_calibration"]["sample_count"], 0)
            self.assertEqual(report["activity_counts"], {"test.completed": 1})
            self.assertEqual(report["timeline"][0]["event"], "test.completed")
            self.assertEqual(len(report["timeline"]), 1)
            self.assertTrue(Path(report["path"]).is_file())
            content = Path(report["path"]).read_text(encoding="utf-8")
            self.assertIn("30-minute Muse report", content)
            self.assertIn("Recent event timeline", content)
            self.assertIn("Runtime estimate calibration: 900 seconds", content)
            self.assertIn("test.completed", content)
            self.assertIn("600", content)
            self.assertNotIn("private task title", content)
            self.assertNotIn("payload-secret", content)
            self.assertNotIn("private research notes", content)
            self.assertNotIn("must not appear", content)

    def test_report_includes_latest_project_progress_and_next_backlog_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "data" / "tasks" / "tasks.sqlite3"
            database.parent.mkdir(parents=True)
            with TaskQueue(database):
                pass
            docs = root / "docs"
            docs.mkdir()
            (docs / "PROGRESS.md").write_text(
                "## Previous\n- Old milestone\n\n## Latest milestone\n- Implemented report engine\n- 8 tests passed\n",
                encoding="utf-8",
            )
            (docs / "CAPABILITY_BACKLOG.md").write_text(
                "### B-001 Done\n- 狀態：第一階段完成。\n\n### B-002 Capability radar\n- 狀態：未實作。\n- 下一步：建立結構化提案。\n",
                encoding="utf-8",
            )
            report = create_half_hour_report(database, root / "reports")
            content = Path(report["path"]).read_text(encoding="utf-8")
            self.assertIn("Latest milestone", content)
            self.assertIn("Implemented report engine", content)
            self.assertIn("B-002 Capability radar", content)
            self.assertIn("建立結構化提案", content)

    def test_report_for_missing_database_does_not_create_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "missing.sqlite3"
            report = create_half_hour_report(database, root / "reports")
            self.assertEqual(report["status"], "not_initialized")
            self.assertFalse(database.exists())
            self.assertIsNone(report["path"])

    def test_report_rejects_non_positive_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                create_half_hour_report(
                    Path(directory) / "tasks.sqlite3", Path(directory), window_minutes=0
                )


if __name__ == "__main__":
    unittest.main()
