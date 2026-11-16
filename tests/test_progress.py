from pathlib import Path
import tempfile
import unittest

from muse.progress import ProgressEventStore, new_evolution_cycle_id, record_evolution_cycle_event


class ProgressEventStoreTests(unittest.TestCase):
    def test_persists_events_across_reopen_and_returns_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "progress.sqlite3"
            with ProgressEventStore(database) as store:
                first = store.record("task", "task-1", "started", summary="Started safe task")
                second = store.record("task", "task-1", "completed", summary="Task passed")
            with ProgressEventStore(database) as store:
                events = store.list_events()
            self.assertEqual([event["id"] for event in events], [second["id"], first["id"]])
            self.assertEqual(events[0]["summary"], "Task passed")

    def test_filters_by_category_and_subject_and_bounds_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ProgressEventStore(Path(directory) / "progress.sqlite3") as store:
                store.record("task", "task-1", "started")
                store.record("agent", "agent-1", "started")
                store.record("task", "task-2", "failed")
                events = store.list_events(category="task", subject_id="task-2", limit=1)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["subject_id"], "task-2")

    def test_rejects_unknown_categories_events_and_empty_subject(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ProgressEventStore(Path(directory) / "progress.sqlite3") as store:
                with self.assertRaises(ValueError):
                    store.record("private_chat", "x", "started")
                with self.assertRaises(ValueError):
                    store.record("task", "x", "thinking_forever")
                with self.assertRaises(ValueError):
                    store.record("task", "  ", "started")

    def test_rejects_oversized_or_non_text_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ProgressEventStore(Path(directory) / "progress.sqlite3") as store:
                with self.assertRaises(ValueError):
                    store.record("task", "x", "progress", summary="x" * 501)
                with self.assertRaises(ValueError):
                    store.record("task", "x", "progress", summary={"payload": "secret"})


    def test_evolution_cycle_id_correlates_append_only_lifecycle_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "progress.sqlite3"
            cycle_id = new_evolution_cycle_id()
            self.assertRegex(cycle_id, r"^cycle_[0-9a-f]{32}$")
            record_evolution_cycle_event(database, cycle_id, "started", summary="Cycle started")
            record_evolution_cycle_event(database, cycle_id, "progress", summary="Research completed")
            record_evolution_cycle_event(database, cycle_id, "completed", summary="Cycle completed")
            with ProgressEventStore(database) as store:
                events = list(reversed(store.list_events(category="evolution", subject_id=cycle_id)))
            self.assertEqual([item["event"] for item in events], ["started", "progress", "completed"])
            self.assertTrue(all(item["subject_id"] == cycle_id for item in events))

    def test_evolution_cycle_event_rejects_unstable_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "cycle_id"):
                record_evolution_cycle_event(Path(directory) / "progress.sqlite3", "not-a-cycle", "started")

    def test_worker_runtime_registration_heartbeat_and_stale_detection(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            with ProgressEventStore(Path(directory) / "progress.sqlite3") as store:
                store.register_worker("worker-a", pid=123, now=now)
                store.heartbeat_worker("worker-a", now=now + timedelta(seconds=20))
                fresh = store.list_workers(now=now + timedelta(seconds=30), stale_after_seconds=15)
                self.assertEqual(fresh[0]["state"], "active")
                self.assertFalse(fresh[0]["stale"])
                stale = store.list_workers(now=now + timedelta(seconds=60), stale_after_seconds=15)
                self.assertTrue(stale[0]["stale"])
                self.assertTrue(store.stop_worker("worker-a", now=now + timedelta(seconds=61)))
                stopped = store.list_workers(now=now + timedelta(seconds=62))
                self.assertEqual(stopped[0]["state"], "stopped")
                self.assertFalse(stopped[0]["stale"])

    def test_aggregate_event_counts_are_time_bounded_and_never_return_summaries(self) -> None:
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            with ProgressEventStore(Path(directory) / "progress.sqlite3") as store:
                old = datetime.now(timezone.utc) - timedelta(days=2)
                store.record("test", "old-suite", "failed", summary="private old output")
                store._connection.execute(
                    "UPDATE progress_events SET created_at=? WHERE subject_id=?",
                    (old.isoformat(), "old-suite"),
                )
                store._connection.commit()
                store.record("test", "suite", "failed", summary="private test output")
                store.record("task", "task", "failed", summary="private task output")
                counts = store.aggregate_event_counts(since=datetime.now(timezone.utc) - timedelta(hours=1))
            self.assertEqual(counts, {("task", "failed"): 1, ("test", "failed"): 1})

    def test_failure_codes_are_validated_and_aggregated_without_summaries(self) -> None:
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            with ProgressEventStore(Path(directory) / "progress.sqlite3") as store:
                store.record("task", "task-1", "failed", summary="private error detail",
                             error_code="handler.unexpected")
                store.record("task", "task-2", "failed", summary="private error detail",
                             error_code="handler.unexpected")
                store.record("task", "task-3", "failed", summary="private error detail",
                             error_code="provider.unavailable")
                with self.assertRaises(ValueError):
                    store.record("task", "task-4", "completed", error_code="handler.unexpected")
                with self.assertRaises(ValueError):
                    store.record("task", "task-4", "failed", error_code="invalid code")
                codes = store.aggregate_failure_codes(
                    since=datetime.now(timezone.utc) - timedelta(hours=1)
                )
            self.assertEqual(codes, {"handler.unexpected": 2, "provider.unavailable": 1})
            self.assertNotIn("private error detail", str(codes))

    def test_legacy_progress_database_migrates_error_code_column(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("""CREATE TABLE progress_events (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, subject_id TEXT NOT NULL,
                event TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            connection.execute(
                "INSERT INTO progress_events VALUES (?, ?, ?, ?, ?, ?)",
                ("old-event", "task", "task-old", "failed", "old summary", "2026-01-01T00:00:00+00:00"),
            )
            connection.commit()
            connection.close()
            with ProgressEventStore(database) as store:
                legacy = store.list_events(subject_id="task-old")
                self.assertIsNone(legacy[0]["error_code"])
                columns = {row["name"] for row in store._connection.execute("PRAGMA table_info(progress_events)")}
                self.assertIn("error_code", columns)
                saved = store.record("task", "task-new", "failed", error_code="handler.unexpected")
                self.assertEqual(saved["error_code"], "handler.unexpected")

    def test_watchdog_history_compares_two_windows_without_exposing_identifiers(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "progress.sqlite3"
            with ProgressEventStore(database) as store:
                store.record_watchdog_snapshot(status="warning", expired_leases=1, now=now - timedelta(hours=36))
                store.record_watchdog_snapshot(status="warning", stalled_tasks=2, now=now - timedelta(hours=30))
                store.record_watchdog_snapshot(status="healthy", now=now - timedelta(hours=12))
                store.record_watchdog_snapshot(status="warning", dead_workers=1, now=now - timedelta(hours=6))
                trend = store.watchdog_trend(now=now, window_hours=24)
            with ProgressEventStore(database) as store:
                reopened = store.watchdog_trend(now=now, window_hours=24)
            self.assertEqual(trend, reopened)
            self.assertEqual(trend["current"]["checks"], 2)
            self.assertEqual(trend["current"]["warning_checks"], 1)
            self.assertEqual(trend["current"]["dead_workers"], 1)
            self.assertEqual(trend["previous"]["checks"], 2)
            self.assertEqual(trend["previous"]["warning_checks"], 2)
            self.assertEqual(trend["trend"], "improving")
            self.assertNotIn("agent", str(trend))

    def test_watchdog_history_marks_worsening_and_requires_two_samples_per_window(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            with ProgressEventStore(Path(directory) / "progress.sqlite3") as store:
                insufficient = store.watchdog_trend(now=now)
                self.assertEqual(insufficient["trend"], "insufficient_data")
                store.record_watchdog_snapshot(status="healthy", now=now - timedelta(hours=36))
                store.record_watchdog_snapshot(status="healthy", now=now - timedelta(hours=30))
                store.record_watchdog_snapshot(status="warning", now=now - timedelta(hours=12))
                store.record_watchdog_snapshot(status="warning", now=now - timedelta(hours=6))
                trend = store.watchdog_trend(now=now)
                with self.assertRaises(ValueError):
                    store.watchdog_trend(now=now, window_hours=0)
            self.assertEqual(trend["trend"], "worsening")

    def test_watchdog_history_rejects_invalid_status_and_metric_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ProgressEventStore(Path(directory) / "progress.sqlite3") as store:
                with self.assertRaises(ValueError):
                    store.record_watchdog_snapshot(status="unknown")
                with self.assertRaises(ValueError):
                    store.record_watchdog_snapshot(status="healthy", stalled_tasks=True)
                with self.assertRaises(ValueError):
                    store.record_watchdog_snapshot(status="warning", expired_leases=-1)

if __name__ == "__main__":
    unittest.main()
