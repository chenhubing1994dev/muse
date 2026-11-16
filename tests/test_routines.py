from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.routines import RoutineStore, run_due_once, run_forever


class StopAfterWait:
    def is_set(self):
        return False
    def wait(self, _seconds):
        return True


class RoutineSchedulerTests(unittest.TestCase):
    def test_add_claim_finish_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add("planner", "autonomy_plan", interval_seconds=1800, payload={"x": 1})
                claimed = store.claim_due()
                self.assertEqual(claimed["id"], routine["id"])
                self.assertIsNone(store.claim_due())
                finished = store.finish(routine["id"], status="succeeded", lease_token=claimed["lease_token"])
                self.assertEqual(finished["last_status"], "succeeded")
                self.assertIsNone(store.claim_due())
            with RoutineStore(db) as store:
                self.assertEqual(store.list()[0]["payload"], {"x": 1})

    def test_existing_database_migrates_missing_lease_token_column(self) -> None:
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute("""CREATE TABLE routines (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL, payload_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, next_run_at TEXT NOT NULL,
                last_run_at TEXT, last_status TEXT NOT NULL DEFAULT 'never',
                lease_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            connection.commit()
            connection.close()
            with RoutineStore(db) as store:
                columns = {row[1] for row in store.connection.execute("PRAGMA table_info(routines)")}
                self.assertIn("lease_token", columns)
                routine = store.add("legacy-planner", "autonomy_plan", interval_seconds=1800)
                self.assertEqual(store.claim_due()["id"], routine["id"])

    def test_concurrent_openers_serialize_legacy_schema_migration(self) -> None:
        import sqlite3
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "legacy-concurrent.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute("""CREATE TABLE routines (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL, payload_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, next_run_at TEXT NOT NULL,
                last_run_at TEXT, last_status TEXT NOT NULL DEFAULT 'never',
                lease_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            connection.commit()
            connection.close()

            def open_and_close(_: int) -> None:
                with RoutineStore(db) as store:
                    self.assertIn("lease_token", {row[1] for row in store.connection.execute("PRAGMA table_info(routines)")})

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(open_and_close, range(16)))
            with RoutineStore(db) as store:
                columns = {row[1] for row in store.connection.execute("PRAGMA table_info(routines)")}
                self.assertEqual(sum(column == "lease_token" for column in columns), 1)

    def test_due_routine_executes_only_registered_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                store.add("planner", "autonomy_plan", interval_seconds=1800)
            calls = []
            result = run_due_once(db, {"autonomy_plan": lambda payload: calls.append(payload)})
            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["outcomes"][0]["status"], "succeeded")
            self.assertEqual(calls, [{}])

    def test_unknown_handler_is_recorded_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                store.add("unknown", "missing", interval_seconds=1800)
            result = run_due_once(db, {})
            self.assertEqual(result["outcomes"][0]["status"], "failed")
            self.assertIn("no handler", result["outcomes"][0]["error"])

    def test_disable_prevents_new_claims_and_preserves_inflight_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add("planner", "autonomy_plan", interval_seconds=1800)
                claimed = store.claim_due()
                store.set_enabled(routine["id"], False)
                self.assertIsNone(store.claim_due())
                store.finish(claimed["id"], status="succeeded", lease_token=claimed["lease_token"])
                self.assertIsNone(store.claim_due())

    def test_stale_worker_cannot_finish_after_lease_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add("planner", "autonomy_plan", interval_seconds=1800)
                first = store.claim_due(lease_seconds=1)
                store.connection.execute("UPDATE routines SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (routine["id"],))
                store.connection.commit()
                second = store.claim_due(lease_seconds=60)
                self.assertNotEqual(first["lease_token"], second["lease_token"])
                with self.assertRaisesRegex(RuntimeError, "lease was lost"):
                    store.finish(routine["id"], status="succeeded", lease_token=first["lease_token"])
                finished = store.finish(routine["id"], status="succeeded", lease_token=second["lease_token"])
                self.assertEqual(finished["last_status"], "succeeded")

    def test_expired_lease_cannot_be_finished_even_before_reclaim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add("planner", "autonomy_plan", interval_seconds=1800)
                claimed = store.claim_due(lease_seconds=60)
                store.connection.execute(
                    "UPDATE routines SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
                    (routine["id"],),
                )
                store.connection.commit()
                with self.assertRaisesRegex(RuntimeError, "lease was lost"):
                    store.finish(routine["id"], status="succeeded", lease_token=claimed["lease_token"])

    def test_renew_lease_requires_current_token_and_extends_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add("planner", "autonomy_plan", interval_seconds=1800)
                claimed = store.claim_due(lease_seconds=1)
                renewed = store.renew_lease(routine["id"], lease_token=claimed["lease_token"], lease_seconds=30)
                self.assertIsNotNone(renewed)
                self.assertGreater(renewed["lease_until"], claimed["lease_until"])
                self.assertIsNone(store.renew_lease(routine["id"], lease_token="stale-token", lease_seconds=30))

    def test_long_handler_heartbeats_lease_so_second_runner_cannot_claim(self) -> None:
        import threading
        import time
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add("planner", "autonomy_plan", interval_seconds=1800)
            started = threading.Event()
            outcomes = []
            def handler(_payload):
                started.set()
                time.sleep(1.4)
            runner = threading.Thread(target=lambda: outcomes.append(
                run_due_once(db, {"autonomy_plan": handler}, lease_seconds=3)
            ))
            runner.start()
            self.assertTrue(started.wait(2))
            time.sleep(1.1)
            with RoutineStore(db) as store:
                self.assertIsNone(store.claim_due(lease_seconds=3))
            runner.join(timeout=4)
            self.assertFalse(runner.is_alive())
            self.assertEqual(outcomes[0]["processed"], 1)
            with RoutineStore(db) as store:
                self.assertEqual(store.get(routine["id"])["last_status"], "succeeded")

    def test_due_routines_execute_concurrently_with_bounded_workers(self) -> None:
        import threading
        import time
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                for name in ("one", "two", "three"):
                    store.add(name, "parallel", interval_seconds=1800)
            barrier = threading.Barrier(3)
            started = []
            lock = threading.Lock()

            def handler(_payload):
                with lock:
                    started.append(threading.current_thread().name)
                barrier.wait(timeout=3)
                time.sleep(0.05)

            result = run_due_once(db, {"parallel": handler}, max_routines=3, max_workers=3)
            self.assertEqual(result["processed"], 3)
            self.assertEqual([item["status"] for item in result["outcomes"]], ["succeeded"] * 3)
            self.assertEqual(len(set(started)), 3)
            self.assertTrue(all(name.startswith("muse-routine") for name in started))

    def test_concurrency_worker_limit_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run_due_once(Path(directory) / "routines.sqlite3", {}, max_workers=0)
            with self.assertRaises(ValueError):
                run_due_once(Path(directory) / "routines.sqlite3", {}, max_routines=2, max_workers=3)

    def test_foreground_loop_stops_and_interval_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            result = run_forever(db, {}, stop_event=StopAfterWait(), poll_seconds=1)
            self.assertEqual(result, {"processed": 0, "errors": 0})
            with self.assertRaises(ValueError):
                run_forever(db, {}, poll_seconds=0)


if __name__ == "__main__":
    unittest.main()
