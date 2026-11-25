from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from muse.lease_recovery import build_migration_plan, diagnose_leases, migrate_lease_schema


@contextmanager
def db_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class LeaseRecoveryStepOneTests(unittest.TestCase):
    def _legacy_dbs(self, root: Path) -> tuple[Path, Path]:
        routine = root / "routines.sqlite3"
        runtime = root / "runtime.sqlite3"
        with db_connection(routine) as db:
            db.execute("""CREATE TABLE routines (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL, payload_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, next_run_at TEXT NOT NULL,
                last_run_at TEXT, last_status TEXT NOT NULL DEFAULT 'never',
                lease_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                lease_token TEXT
            )""")
            db.execute(
                "INSERT INTO routines (id,name,kind,interval_seconds,payload_json,enabled,next_run_at,last_status,lease_until,created_at,updated_at,lease_token) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("r1", "idle-autonomous-research", "autonomous_research", 3600, "{}", 1,
                 "2026-11-16T19:00:00+00:00", "running", "2026-11-16T19:26:00+00:00",
                 "2026-11-01", "2026-11-16", "old-owner"),
            )
        with db_connection(runtime) as db:
            db.execute("""CREATE TABLE autonomous_runtime (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                owner_id TEXT, pid INTEGER, heartbeat_at TEXT, started_at TEXT,
                updated_at TEXT NOT NULL, last_error TEXT
            )""")
            db.execute("INSERT INTO autonomous_runtime VALUES (1,'stopped',NULL,NULL,NULL,NULL,'2026-11-16',NULL)")
        return routine, runtime

    def test_migration_plan_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routine, runtime = self._legacy_dbs(Path(temp))
            before = (hashlib.sha256(routine.read_bytes()).hexdigest(), hashlib.sha256(runtime.read_bytes()).hexdigest())
            plans = build_migration_plan(routine, runtime)
            after = (hashlib.sha256(routine.read_bytes()).hexdigest(), hashlib.sha256(runtime.read_bytes()).hexdigest())
            self.assertEqual(before, after)
            self.assertIn("fencing_epoch", {name for name, _ in plans[0].add_columns})
            self.assertIn("desired_state", {name for name, _ in plans[1].add_columns})

    def test_migration_is_idempotent_and_preserves_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routine, runtime = self._legacy_dbs(root)
            handoff = root / "handoff"
            now = datetime(2026, 11, 25, tzinfo=timezone.utc)
            first = migrate_lease_schema(routine, runtime, apply=True, handoff_dir=handoff, now=now)
            self.assertEqual(first["status"], "migrated")
            with db_connection(routine) as db:
                row = db.execute("SELECT id,name,last_status,lease_token,desired_state,fencing_epoch FROM routines").fetchone()
                self.assertEqual(row, ("r1", "idle-autonomous-research", "running", "old-owner", "running", 0))
                schema = db.execute("PRAGMA table_info(routines)").fetchall()
            with db_connection(runtime) as db:
                self.assertEqual(db.execute("SELECT state,desired_state,fencing_epoch FROM autonomous_runtime").fetchone(), ("stopped", "stopped", 0))
            second = migrate_lease_schema(routine, runtime, apply=True, handoff_dir=handoff, now=now)
            self.assertEqual(second["status"], "already_current")
            with db_connection(routine) as db:
                self.assertEqual(schema, db.execute("PRAGMA table_info(routines)").fetchall())
                self.assertEqual(db.execute("SELECT count(*) FROM routines").fetchone()[0], 1)

    def test_backup_collision_aborts_without_mutating_databases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routine, runtime = self._legacy_dbs(root)
            handoff = root / "handoff"
            handoff.mkdir()
            collision = handoff / "lease-migration-20261125-routines.sqlite3"
            collision.write_bytes(b"keep-me")
            before = (hashlib.sha256(routine.read_bytes()).hexdigest(), hashlib.sha256(runtime.read_bytes()).hexdigest())
            with self.assertRaises(Exception):
                migrate_lease_schema(routine, runtime, apply=True, handoff_dir=handoff, now=datetime(2026, 11, 25, tzinfo=timezone.utc))
            after = (hashlib.sha256(routine.read_bytes()).hexdigest(), hashlib.sha256(runtime.read_bytes()).hexdigest())
            self.assertEqual(before, after)
            self.assertEqual(collision.read_bytes(), b"keep-me")

    def test_doctor_respects_stop_and_detects_suspected_hung(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routine, runtime = self._legacy_dbs(root)
            migrate_lease_schema(routine, runtime, apply=True, handoff_dir=root / "backup", now=datetime(2026, 11, 25, tzinfo=timezone.utc))
            stale = (datetime(2026, 11, 25, tzinfo=timezone.utc) - timedelta(minutes=20)).isoformat()
            with db_connection(routine) as db:
                db.execute("UPDATE routines SET heartbeat_at=?,ttl_seconds=60,pid=123,process_identity='old-identity',desired_state='running' WHERE id='r1'", (stale,))
            with db_connection(runtime) as db:
                db.execute("UPDATE autonomous_runtime SET desired_state='stopped'")
            report = diagnose_leases(
                routine, runtime, now=datetime(2026, 11, 25, tzinfo=timezone.utc),
                pid_alive=lambda pid: pid == 123,
                process_identity=lambda pid: "old-identity" if pid == 123 else None,
            )
            by_name = {item["name"]: item for item in report["leases"]}
            self.assertEqual(by_name["idle-autonomous-research"]["conclusion"], "suspected_hung")
            self.assertEqual(by_name["autonomous_runtime"]["conclusion"], "stopped_by_intent")

    def test_legacy_runtime_stopped_without_owner_is_inferred_as_user_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routine, runtime = self._legacy_dbs(Path(temp))
            report = diagnose_leases(
                routine, runtime, now=datetime(2026, 11, 25, tzinfo=timezone.utc),
                pid_alive=lambda _pid: False, process_identity=lambda _pid: None,
            )
            runtime_item = next(item for item in report["leases"] if item["kind"] == "runtime")
            self.assertEqual(runtime_item["conclusion"], "stopped_by_intent")
            self.assertIn("inferred", runtime_item["desired_state"])

    def test_doctor_flags_pid_reuse_as_orphaned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routine, runtime = self._legacy_dbs(root)
            migrate_lease_schema(routine, runtime, apply=True, handoff_dir=root / "backup", now=datetime(2026, 11, 25, tzinfo=timezone.utc))
            fresh = datetime(2026, 11, 25, tzinfo=timezone.utc).isoformat()
            with db_connection(routine) as db:
                db.execute("UPDATE routines SET heartbeat_at=?,ttl_seconds=300,pid=456,process_identity='recorded-old',desired_state='running' WHERE id='r1'", (fresh,))
            report = diagnose_leases(
                routine, runtime, now=datetime(2026, 11, 25, tzinfo=timezone.utc),
                pid_alive=lambda pid: pid == 456,
                process_identity=lambda pid: "different-process" if pid == 456 else None,
            )
            self.assertEqual(report["leases"][0]["conclusion"], "orphaned")


if __name__ == "__main__":
    unittest.main()
