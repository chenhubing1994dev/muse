from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from muse.autonomous_runtime import RuntimeStateStore
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


class RuntimeFencingTests(unittest.TestCase):
    def _databases(self, root: Path) -> tuple[Path, Path]:
        routine_db = root / "routines.sqlite3"
        runtime_db = root / "runtime.sqlite3"
        with RoutineStore(routine_db) as routines:
            routines.add("idle-autonomous-research", "research", interval_seconds=3600)
        with RuntimeStateStore(runtime_db) as runtime:
            runtime.set_control("stopping")
        migrate_lease_schema(routine_db, runtime_db, apply=True, handoff_dir=root / "handoff")
        return routine_db, runtime_db

    def test_runtime_lease_requires_explicit_resume_and_rejects_old_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, runtime_db = self._databases(root)
            with RuntimeStateStore(runtime_db) as runtime:
                with self.assertRaisesRegex(RuntimeError, "desired_state=stopped"):
                    runtime.acquire("owner-not-resumed")
                runtime.set_control("running")
                first = runtime.acquire("owner-a")
                epoch_a = first["fencing_epoch"]
                self.assertEqual(epoch_a, 1)
                self.assertTrue(runtime.heartbeat("owner-a", epoch_a))
                runtime.release("owner-a", epoch_a)
                second = runtime.acquire("owner-b")
                epoch_b = second["fencing_epoch"]
                self.assertEqual(epoch_b, 2)
                self.assertFalse(runtime.heartbeat("owner-a", epoch_a))
                self.assertTrue(runtime.heartbeat("owner-b", epoch_b))
                runtime.release("owner-b", epoch_b)
            with db_connection(runtime_db) as db:
                row = db.execute("SELECT state,owner_id,pid,desired_state,fencing_epoch FROM autonomous_runtime").fetchone()
                self.assertEqual(row, ("stopped", None, None, "running", 2))


if __name__ == "__main__":
    unittest.main()
