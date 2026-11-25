from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from muse.lease_manager import LeaseManager
from muse.lease_recovery import migrate_lease_schema
from muse.routines import RoutineStore


@contextmanager
def db_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class RoutineFencingIntegrationTests(unittest.TestCase):
    def test_claim_epoch_blocks_stale_finish_and_scheduler_does_not_reclaim_expired_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routine_db = root / "routines.sqlite3"
            runtime_db = root / "runtime.sqlite3"
            with RoutineStore(routine_db) as store:
                routine = store.add("idle-autonomous-research", "research", interval_seconds=3600)
            with db_connection(runtime_db) as db:
                db.execute("""CREATE TABLE autonomous_runtime (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                    owner_id TEXT, pid INTEGER, heartbeat_at TEXT, started_at TEXT,
                    updated_at TEXT NOT NULL, last_error TEXT
                )""")
                db.execute("INSERT INTO autonomous_runtime VALUES (1,'stopped',NULL,NULL,NULL,NULL,'2026-11-25',NULL)")
            migrate_lease_schema(
                routine_db, runtime_db, apply=True, handoff_dir=root / "handoff",
                now=datetime(2026, 11, 25, tzinfo=timezone.utc),
            )

            with RoutineStore(routine_db) as store:
                first = store.claim_due(lease_seconds=60)
                self.assertIsNotNone(first)
                self.assertEqual(first["fencing_epoch"], 1)
                store.connection.execute(
                    "UPDATE routines SET lease_until='2000-01-01T00:00:00+00:00',heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                    (routine["id"],),
                )
                store.connection.commit()
                self.assertIsNone(store.claim_due(lease_seconds=60))

            manager = LeaseManager(
                routine_db, resource_table="routines", resource_id=routine["id"],
                handoff_dir=root / "handoff", pid_alive=lambda _pid: False,
                process_identity=lambda _pid: "new-supervisor-process",
                now=lambda: datetime(2026, 11, 25, tzinfo=timezone.utc),
            )
            takeover = manager.takeover_if_stale("new-owner-token", pid=9001, ttl_seconds=60)
            self.assertEqual(takeover.status, "taken_over")
            self.assertEqual(takeover.epoch, 2)

            with RoutineStore(routine_db) as store:
                with self.assertRaisesRegex(RuntimeError, "lease was lost"):
                    store.finish(
                        routine["id"], status="succeeded",
                        lease_token=first["lease_token"], fencing_epoch=first["fencing_epoch"],
                    )


if __name__ == "__main__":
    unittest.main()
