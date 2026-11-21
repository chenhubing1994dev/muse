from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.routines import (
    RoutineStore, run_due_once, run_forever, routine_handler_with_context,
)


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

    def test_trigger_makes_enabled_routine_due_without_claiming_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add(
                    "idle-autonomous-research", "autonomous_research",
                    interval_seconds=3600, run_immediately=False,
                )
                self.assertIsNone(store.claim_due())
                self.assertTrue(store.trigger("idle-autonomous-research"))
                triggered = store.get(routine["id"])
                self.assertLessEqual(triggered["next_run_at"], store._now().isoformat())
                claimed = store.claim_due()
                self.assertEqual(claimed["id"], routine["id"])
                store.finish(routine["id"], status="succeeded", lease_token=claimed["lease_token"])
                store.set_enabled(routine["id"], False)
                self.assertFalse(store.trigger("idle-autonomous-research"))

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


    def test_cooperative_handler_observes_stop_and_keeps_routine_due_for_resume(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add("cooperative", "research", interval_seconds=1800)
            stop_event = threading.Event()
            started = threading.Event()
            outcomes = []

            @routine_handler_with_context
            def handler(_payload, *, execution_context):
                started.set()
                self.assertTrue(execution_context.stop_event.wait(3))
                execution_context.raise_if_cancelled()

            runner = threading.Thread(target=lambda: outcomes.append(
                run_due_once(db, {"research": handler}, lease_seconds=3, stop_event=stop_event)
            ))
            runner.start()
            self.assertTrue(started.wait(2))
            stop_event.set()
            runner.join(timeout=5)
            self.assertFalse(runner.is_alive())
            self.assertEqual(outcomes[0]["outcomes"][0]["status"], "cancelled")
            with RoutineStore(db) as store:
                saved = store.get(routine["id"])
            self.assertEqual(saved["last_status"], "cancelled")
            self.assertIsNone(saved["lease_token"])
            self.assertLessEqual(saved["next_run_at"], RoutineStore._now().isoformat())

    def test_foreground_loop_passes_stop_event_into_cooperative_routine(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                routine = store.add("cooperative-loop", "research", interval_seconds=1800)
            stop_event = threading.Event()
            started = threading.Event()

            @routine_handler_with_context
            def handler(_payload, *, execution_context):
                started.set()
                self.assertTrue(execution_context.stop_event.wait(3))
                execution_context.raise_if_cancelled()

            results = []
            runner = threading.Thread(target=lambda: results.append(
                run_forever(db, {"research": handler}, stop_event=stop_event, poll_seconds=1)
            ))
            runner.start()
            self.assertTrue(started.wait(2))
            stop_event.set()
            runner.join(timeout=5)
            self.assertFalse(runner.is_alive())
            self.assertEqual(results, [{"processed": 1, "errors": 0}])
            with RoutineStore(db) as store:
                self.assertEqual(store.get(routine["id"])["last_status"], "cancelled")

    def test_stop_skips_already_claimed_routines_that_have_not_started(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "routines.sqlite3"
            with RoutineStore(db) as store:
                store.add("a-first", "first", interval_seconds=1800)
                second = store.add("b-second", "second", interval_seconds=1800)
            stop_event = threading.Event()
            calls = []

            def first_handler(_payload):
                calls.append("first")
                stop_event.set()

            def second_handler(_payload):
                calls.append("second")

            result = run_due_once(
                db, {"first": first_handler, "second": second_handler},
                max_routines=2, max_workers=1, stop_event=stop_event,
            )
            self.assertEqual(calls, ["first"])
            self.assertEqual([item["status"] for item in result["outcomes"]], ["succeeded", "cancelled"])
            with RoutineStore(db) as store:
                saved = store.get(second["id"])
            self.assertEqual(saved["last_status"], "cancelled")
            self.assertLessEqual(saved["next_run_at"], RoutineStore._now().isoformat())


    def test_parallel_handlers_inherit_existing_model_call_context(self) -> None:
        import threading
        from muse.model_call_context import model_call_scope, record_current_model_usage
        from muse.usage_ledger import UsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routines_db = root / "routines.sqlite3"
            usage_db = root / "tasks.sqlite3"
            with RoutineStore(routines_db) as store:
                store.add("usage-one", "parallel_usage", interval_seconds=1800)
                store.add("usage-two", "parallel_usage", interval_seconds=1800)
            barrier = threading.Barrier(2)

            def handler(_payload):
                barrier.wait(timeout=3)
                self.assertTrue(record_current_model_usage(
                    "lm-studio-local", "routine-context-model",
                    {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                ))

            with model_call_scope(usage_db, task_id="routine-task", agent_id="routine-agent", project_id="muse"):
                result = run_due_once(
                    routines_db, {"parallel_usage": handler}, max_routines=2, max_workers=2,
                )
            self.assertEqual(result["processed"], 2)
            with UsageLedger(usage_db.with_name("model_usage.sqlite3")) as ledger:
                events = ledger.list_events(limit=10)
            self.assertEqual(len(events), 2)
            self.assertTrue(all(event["task_id"] == "routine-task" for event in events))
            self.assertTrue(all(event["agent_id"] == "routine-agent" for event in events))

    def test_parallel_autonomy_routines_record_project_usage_without_outer_context(self) -> None:
        from unittest.mock import patch
        from muse.model_call_context import record_current_model_usage
        from muse.routines import _autonomy_handler
        from muse.usage_ledger import UsageLedger

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def chat(self, *args, **kwargs):
                record_current_model_usage(
                    "lm-studio-local", "routine-autonomy-model",
                    {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
                )
                return "{}"

        def fake_propose(_backlog, _task_db, _plan_db, model_call):
            model_call([{"role": "user", "content": "safe planning probe"}])
            return {"status": "proposal_saved"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routines_db = root / "routines.sqlite3"
            task_db = root / "tasks.sqlite3"
            with RoutineStore(routines_db) as store:
                for name in ("autonomy-one", "autonomy-two"):
                    store.add(name, "autonomy_plan", interval_seconds=1800, payload={"task_db": str(task_db)})
            with (
                patch("muse.model_client.LMStudioClient", FakeClient),
                patch("muse.autonomy.propose_next_step", side_effect=fake_propose),
            ):
                result = run_due_once(
                    routines_db, {"autonomy_plan": _autonomy_handler}, max_routines=2, max_workers=2,
                )
            self.assertEqual(result["processed"], 2)
            with UsageLedger(task_db.with_name("model_usage.sqlite3")) as ledger:
                events = ledger.list_events(limit=10)
            self.assertEqual(len(events), 2)
            self.assertTrue(all(event["project_id"] == "muse" for event in events))
            self.assertTrue(all(event["task_id"] is None and event["agent_id"] is None for event in events))


if __name__ == "__main__":
    unittest.main()