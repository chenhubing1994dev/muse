"""C11: the unit-test stage's all-store real-data guard (scripts/diag_unit_stage.py).

Everything runs on throw-away SQLite stores in a temporary directory; the real data/ tree is never passed in.
The guard must ignore only rows the live self-check appends after the pre-run snapshot, and report (fail
closed) every other change: new rows, rewritten / deleted / back-dated history, schema changes, NULL
timestamps, new or vanished stores and unreadable files. Restore must bring a store back and keep evidence.
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import diag_unit_stage as stage

TASKS = "tasks/muse.sqlite3"
OLD = "2026-01-01T00:00:00+00:00"


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()


class StoreGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="muse store guard test ")
        self.data = Path(self._tmp.name) / "data"
        for sub in ("tasks", "improvements", "handoff", "memory"):
            (self.data / sub).mkdir(parents=True)
        self.db = self.data / "tasks" / "muse.sqlite3"
        self._sql(self.db,
                  "CREATE TABLE watchdog_checks(id TEXT PRIMARY KEY, checked_at TEXT, status TEXT);"
                  "CREATE TABLE progress_events(id TEXT PRIMARY KEY, category TEXT, subject_id TEXT, created_at TEXT);"
                  "CREATE TABLE tasks(id TEXT PRIMARY KEY, status TEXT);"
                  f"INSERT INTO watchdog_checks VALUES('w0', '{OLD}', 'ok');"
                  f"INSERT INTO progress_events VALUES('p0', 'system', 'watchdog', '{OLD}');"
                  "INSERT INTO tasks VALUES('t1', 'succeeded');")
        self._sql(self.data / "improvements" / "muse.sqlite3",
                  "CREATE TABLE capability_radar_snapshots(id TEXT PRIMARY KEY, observed_at TEXT);")
        self._sql(self.data / "handoff" / "frozen.sqlite3", "CREATE TABLE t(x);")
        self._sql(self.data / "memory" / "muse.sqlite3", "CREATE TABLE memories(x);")
        time.sleep(0.01)
        self.since = datetime.now(timezone.utc).isoformat()
        self.before = stage.snapshot_stores(self.since, self.data)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _sql(db: Path, script: str) -> None:
        con = sqlite3.connect(db)
        try:
            con.executescript(script)
            con.commit()
        finally:
            con.close()

    def _diff(self) -> dict:
        return stage.diff_store_fingerprints(self.before, stage.snapshot_stores(self.since, self.data))

    # ------------------------------------------------------------------ scope
    def test_handoff_and_memory_are_not_in_this_guard_but_live_stores_are(self) -> None:
        self.assertEqual(sorted(self.before), ["improvements/muse.sqlite3", TASKS])

    def test_untouched_stores_produce_no_diff(self) -> None:
        self.assertEqual(self._diff(), {})

    # ------------------------------------------------------------------ allowed runtime appends
    def test_self_check_rows_appended_after_snapshot_are_ignored(self) -> None:
        t = _later()
        self._sql(self.db, f"INSERT INTO watchdog_checks VALUES('w1', '{t}', 'warning');"
                           f"INSERT INTO progress_events VALUES('p1', 'system', 'watchdog', '{t}');"
                           f"INSERT INTO progress_events VALUES('p2', 'system', 'capability-radar', '{t}');")
        self._sql(self.data / "improvements" / "muse.sqlite3",
                  f"INSERT INTO capability_radar_snapshots VALUES('r1', '{t}');")
        self.assertEqual(self._diff(), {})

    # ------------------------------------------------------------------ must be reported
    def test_new_task_row_is_reported(self) -> None:
        self._sql(self.db, "INSERT INTO tasks VALUES('t2', 'queued');")
        self.assertEqual(self._diff(), {TASKS: ["tasks"]})

    def test_rewriting_watchdog_history_is_reported(self) -> None:
        self._sql(self.db, "UPDATE watchdog_checks SET status = 'ok-forged' WHERE id = 'w0';")
        self.assertEqual(self._diff(), {TASKS: ["watchdog_checks"]})

    def test_deleting_watchdog_history_is_reported(self) -> None:
        self._sql(self.db, "DELETE FROM watchdog_checks WHERE id = 'w0';")
        self.assertEqual(self._diff(), {TASKS: ["watchdog_checks"]})

    def test_back_dated_self_check_row_is_reported(self) -> None:
        self._sql(self.db, f"INSERT INTO watchdog_checks VALUES('w9', '{OLD}', 'ok');")
        self.assertEqual(self._diff(), {TASKS: ["watchdog_checks"]})

    def test_null_timestamp_row_is_never_exempt(self) -> None:
        self._sql(self.db, "INSERT INTO watchdog_checks VALUES('wn', NULL, 'ok');")
        self.assertEqual(self._diff(), {TASKS: ["watchdog_checks"]})

    def test_non_self_check_progress_event_is_reported(self) -> None:
        self._sql(self.db, f"INSERT INTO progress_events VALUES('p3', 'task', 'watchdog', '{_later()}');"
                           f"INSERT INTO progress_events VALUES('p4', 'system', 'research', '{_later()}');")
        self.assertEqual(self._diff(), {TASKS: ["progress_events"]})

    def test_schema_change_is_reported(self) -> None:
        self._sql(self.db, "CREATE INDEX ix_tasks_status ON tasks(status);")
        self.assertEqual(self._diff(), {TASKS: ["__schema__"]})

    def test_new_store_created_by_tests_is_reported(self) -> None:
        (self.data / "commander").mkdir()
        self._sql(self.data / "commander" / "tasks.sqlite3", "CREATE TABLE t(x);")
        self.assertEqual(self._diff(), {"commander/tasks.sqlite3": ["__created__"]})

    def test_vanished_store_is_reported(self) -> None:
        moved = Path(self._tmp.name) / "moved.sqlite3"
        self.db.replace(moved)
        self.assertEqual(self._diff(), {TASKS: ["__deleted__"]})

    def test_unreadable_store_fails_closed(self) -> None:
        self.db.write_bytes(b"this is not a sqlite database" * 200)
        fingerprint = stage.store_fingerprint(self.db, self.since, stage.RUNTIME_APPEND_RULES[TASKS])
        self.assertIn("__error__", fingerprint)
        self.assertEqual(self._diff(), {TASKS: ["__unreadable__"]})

    # ------------------------------------------------------------------ backup / restore
    def test_restore_returns_store_to_pre_run_state_and_keeps_evidence(self) -> None:
        backups = stage.backup_stores(Path(self._tmp.name) / "bk", self.data)
        self.assertTrue(all(isinstance(v, Path) and v.is_file() for v in backups.values()))
        self._sql(self.db, "UPDATE tasks SET status = 'tampered';")
        changed = self._diff()
        self.assertEqual(changed, {TASKS: ["tasks"]})
        evidence = Path(self._tmp.name) / "evidence"
        result = stage.restore_stores(changed, backups, evidence, self.data)
        self.assertEqual(result, {TASKS: "restored"})
        self.assertEqual(self._diff(), {})
        kept = evidence / "tasks" / "muse.sqlite3.after-tests"
        self.assertTrue(kept.is_file())
        con = sqlite3.connect(kept)
        try:
            self.assertEqual(con.execute("SELECT status FROM tasks").fetchone()[0], "tampered")
        finally:
            con.close()

    def test_store_without_backup_is_left_in_place_and_reported(self) -> None:
        (self.data / "commander").mkdir()
        new_db = self.data / "commander" / "tasks.sqlite3"
        self._sql(new_db, "CREATE TABLE t(x);")
        changed = self._diff()
        result = stage.restore_stores(changed, {}, Path(self._tmp.name) / "evidence", self.data)
        self.assertTrue(new_db.is_file())
        self.assertIn("no pre-run backup", result["commander/tasks.sqlite3"])


if __name__ == "__main__":
    unittest.main()
