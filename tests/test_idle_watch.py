from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from muse.idle_watch import adaptive_idle_threshold, check_idle
from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue


class IdleWatchTests(unittest.TestCase):
    def test_threshold_uses_recent_event_gaps_and_clamps(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = [
            {"created_at": (base + timedelta(minutes=n * 2)).isoformat()}
            for n in range(4)
        ]
        self.assertEqual(adaptive_idle_threshold(events), 300)
        self.assertEqual(adaptive_idle_threshold(events, minimum_seconds=1, maximum_seconds=1000), 180)

    def test_latest_event_uses_utc_chronology_not_offset_string_order(self) -> None:
        from muse.idle_watch import latest_event_timestamp
        events = [
            {"created_at": "2026-01-01T00:30:00+01:00"},
            {"created_at": "2025-12-31T23:45:00+00:00"},
        ]
        self.assertEqual(
            latest_event_timestamp(events),
            datetime(2025, 12, 31, 23, 45, tzinfo=timezone.utc),
        )

    def test_no_history_uses_fallback_and_invalid_bounds_rejected(self) -> None:
        self.assertEqual(adaptive_idle_threshold([]), 900)
        with self.assertRaises(ValueError):
            adaptive_idle_threshold([], minimum_seconds=20, maximum_seconds=10)


    def test_missing_database_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "missing.sqlite3"
            result = check_idle(
                database, progress_path=root / "PROGRESS.md",
                architecture_path=root / "ARCH.md", output_path=root / "resume.md",
            )
            self.assertEqual(result["status"], "not_initialized")
            self.assertFalse(database.exists())
            self.assertFalse((root / "resume.md").exists())
    def test_active_progress_does_not_generate_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "muse.sqlite3"
            with TaskQueue(db):
                pass
            with ProgressEventStore(db) as store:
                store.record("task", "task-1", "progress", summary="Working")
            result = check_idle(
                db, progress_path=root / "PROGRESS.md",
                architecture_path=root / "ARCH.md", output_path=root / "resume.md",
            )
            self.assertEqual(result["status"], "active")
            self.assertFalse((root / "resume.md").exists())

    def test_idle_progress_generates_prompt_and_cooldown_prevents_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "muse.sqlite3"
            with TaskQueue(db):
                pass
            progress = root / "PROGRESS.md"
            architecture = root / "ARCH.md"
            progress.write_text("Recent work", encoding="utf-8")
            architecture.write_text("Architecture", encoding="utf-8")
            old = datetime.now(timezone.utc) - timedelta(hours=1)
            with ProgressEventStore(db) as store:
                store.record("task", "task-1", "completed", summary="Done")
                store._connection.execute(
                    "UPDATE progress_events SET created_at=? WHERE category='task'",
                    (old.isoformat(),),
                )
                store._connection.commit()
            first = check_idle(
                db, progress_path=progress, architecture_path=architecture,
                output_path=root / "resume.md",
            )
            second = check_idle(
                db, progress_path=progress, architecture_path=architecture,
                output_path=root / "resume.md",
            )
            self.assertEqual(first["status"], "prompt_generated")
            self.assertEqual(second["status"], "cooldown")
            self.assertIn("Recent work", (root / "resume.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
