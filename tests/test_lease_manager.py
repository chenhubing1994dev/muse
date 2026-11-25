from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Thread
from typing import Iterator

from muse.lease_manager import LeaseError, LeaseManager, StaleFenceError


@contextmanager
def db_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class LeaseManagerTests(unittest.TestCase):
    def _db(self, root: Path, *, owner: str | None = None, pid: int | None = None,
            heartbeat: str | None = None, ttl: int = 30, epoch: int = 0,
            desired: str = "running", identity: str | None = None) -> Path:
        path = root / "routines.sqlite3"
        with db_connection(path) as db:
            db.execute("""CREATE TABLE routines (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, lease_until TEXT,
                lease_token TEXT, heartbeat_at TEXT, ttl_seconds INTEGER,
                pid INTEGER, fencing_epoch INTEGER NOT NULL DEFAULT 0,
                desired_state TEXT NOT NULL DEFAULT 'running', process_identity TEXT,
                updated_at TEXT NOT NULL
            )""")
            db.execute(
                "INSERT INTO routines VALUES ('r1','idle-autonomous-research',NULL,?,?,?,?,?,?,?,?)",
                (owner, heartbeat, ttl, pid, epoch, desired, identity, "2026-11-25T00:00:00+00:00"),
            )
        return path

    def _manager(self, root: Path, path: Path, *, alive=lambda _pid: False,
                 identity=lambda pid: f"process-{pid}", now=None) -> LeaseManager:
        return LeaseManager(
            path, resource_table="routines", resource_id="r1",
            handoff_dir=root / "handoff", pid_alive=alive,
            process_identity=identity, now=now,
        )

    def test_concurrent_acquire_only_one_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._db(root)
            barrier = Barrier(2)
            results: list[str] = []

            def worker(owner: str, pid: int) -> None:
                manager = self._manager(root, path)
                barrier.wait()
                try:
                    manager.acquire(owner, pid=pid, ttl_seconds=30)
                    results.append("ok")
                except LeaseError:
                    results.append("rejected")

            threads = [
                Thread(target=worker, args=("owner-a", 1001)),
                Thread(target=worker, args=("owner-b", 1002)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(sorted(results), ["ok", "rejected"])
            with db_connection(path) as db:
                self.assertEqual(db.execute("SELECT fencing_epoch FROM routines WHERE id='r1'").fetchone()[0], 1)

    def test_stale_takeover_increments_epoch_and_audits_old_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = datetime(2026, 11, 25, tzinfo=timezone.utc)
            stale = (now - timedelta(minutes=5)).isoformat()
            path = self._db(root, owner="old-owner", pid=None, heartbeat=stale, ttl=30, epoch=4)
            manager = self._manager(root, path, now=lambda: now)
            result = manager.takeover_if_stale("new-owner", pid=2002, ttl_seconds=60)
            self.assertEqual(result.status, "taken_over")
            self.assertEqual(result.epoch, 5)
            with db_connection(path) as db:
                audit = db.execute("SELECT old_owner,old_epoch,new_owner,new_epoch,reason FROM lease_audit").fetchone()
                self.assertEqual(audit, ("old-owner", 4, "new-owner", 5, "orphaned_process_absent"))

    def test_live_pid_with_stale_heartbeat_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = datetime(2026, 11, 25, tzinfo=timezone.utc)
            stale = (now - timedelta(minutes=5)).isoformat()
            path = self._db(root, owner="old-owner", pid=111, heartbeat=stale, ttl=30, epoch=2, identity="process-111")
            manager = self._manager(root, path, alive=lambda pid: pid == 111, now=lambda: now)
            result = manager.takeover_if_stale("new-owner", pid=222)
            self.assertEqual(result.status, "suspected_hung")
            forced = manager.takeover_if_stale("new-owner", pid=222, force_takeover=True)
            self.assertEqual(forced.epoch, 3)

    def test_stale_epoch_cannot_renew_after_new_owner_acquires(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._db(root)
            manager = self._manager(root, path)
            first = manager.acquire("owner-a", pid=1001)
            manager.release("owner-a", first.epoch)
            second = manager.acquire("owner-b", pid=1002)
            self.assertEqual(second.epoch, 2)
            with self.assertRaises(StaleFenceError):
                manager.renew("owner-a", first.epoch)

    def test_stopped_intent_blocks_acquire_and_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = datetime(2026, 11, 25, tzinfo=timezone.utc)
            stale = (now - timedelta(minutes=5)).isoformat()
            path = self._db(root, owner="old-owner", pid=None, heartbeat=stale, ttl=30, desired="stopped")
            manager = self._manager(root, path, now=lambda: now)
            self.assertEqual(manager.takeover_if_stale("new-owner", pid=333).status, "stopped_by_intent")
            with self.assertRaises(LeaseError):
                manager.acquire("new-owner", pid=333)

    def test_pid_reuse_is_orphaned_but_not_auto_taken_over(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = datetime(2026, 11, 25, tzinfo=timezone.utc)
            stale = (now - timedelta(minutes=5)).isoformat()
            path = self._db(root, owner="old-owner", pid=111, heartbeat=stale, ttl=30, identity="original-process")
            manager = self._manager(
                root, path, alive=lambda _pid: True,
                identity=lambda _pid: "reused-pid-process", now=lambda: now,
            )
            result = manager.takeover_if_stale("new-owner", pid=222)
            self.assertEqual(result.status, "orphaned")
            self.assertEqual(result.epoch, 0)


if __name__ == "__main__":
    unittest.main()
