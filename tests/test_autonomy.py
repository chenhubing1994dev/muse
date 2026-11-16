from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.autonomy import AutonomyPlanStore, propose_next_step, run_planner_loop, validate_plan
from muse.task_queue import TaskQueue


VALID = {
    "title": "Inspect worker recovery gaps",
    "objective": "Review worker restart recovery and add regression tests.",
    "action_class": "code_change",
    "rationale": "The backlog identifies service-level recovery as unfinished.",
    "acceptance_criteria": ["Add a deterministic regression test", "Run the full suite"],
    "priority": 90,
    "estimated_seconds": 1800,
}


class AutonomyPlannerTests(unittest.TestCase):
    def test_valid_plan_is_normalized_and_always_requires_approval(self) -> None:
        plan = validate_plan(VALID)
        self.assertTrue(plan["approval_required"])
        self.assertEqual(plan["action_class"], "code_change")

    def test_rejects_unknown_action_and_bad_schema(self) -> None:
        with self.assertRaises(ValueError):
            validate_plan({**VALID, "action_class": "shell_command"})
        with self.assertRaises(ValueError):
            validate_plan({**VALID, "priority": True})
        with self.assertRaises(ValueError):
            validate_plan({k: v for k, v in VALID.items() if k != "objective"})

    def test_plan_store_deduplicates_identical_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plans.sqlite3"
            with AutonomyPlanStore(path) as store:
                first = store.save(validate_plan(VALID))
                second = store.save(validate_plan(VALID))
                self.assertTrue(first["created"])
                self.assertFalse(second["created"])
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(len(store.list_plans()), 1)

    def test_approval_dispatches_one_idempotent_intake_task_and_rejects_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            with TaskQueue(task_db):
                pass
            with AutonomyPlanStore(root / "plans.sqlite3") as store:
                saved = store.save(validate_plan(VALID))
                first = store.approve(saved["id"], approved_by="owner", task_database=task_db)
                second = store.approve(saved["id"], approved_by="owner", task_database=task_db)
                self.assertEqual(first["task"]["id"], second["task"]["id"])
                self.assertEqual(first["status"], "approved")
                self.assertEqual(first["task"]["payload"]["kind"], "approved_plan_intake")
                with TaskQueue(task_db) as queue:
                    self.assertEqual(queue.snapshot()["counts"]["queued"], 1)
                with self.assertRaises(ValueError):
                    store.reject(saved["id"], rejected_by="owner")

    def test_approved_plan_execution_receipt_tracks_authoritative_task_status(self) -> None:
        from unittest.mock import patch
        from muse.worker import run_once

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            with TaskQueue(task_db):
                pass
            with AutonomyPlanStore(root / "plans.sqlite3") as store:
                saved = store.save(validate_plan(VALID))
                approved = store.approve(saved["id"], approved_by="owner", task_database=task_db)
                queued = store.refresh_execution_status(task_db)
                self.assertEqual(queued[0]["execution_status"], "queued")
                packet_dir = root / "approved_plans"
                with patch("muse.worker.Path", return_value=packet_dir):
                    completed = run_once(task_db)
                self.assertEqual(completed["status"], "succeeded")
                receipt = store.refresh_execution_status(task_db)
                self.assertEqual(receipt[0]["plan_id"], saved["id"])
                self.assertEqual(receipt[0]["task_id"], approved["task"]["id"])
                self.assertEqual(receipt[0]["execution_status"], "succeeded")
                listed = store.list_plans()
                self.assertEqual(listed[0]["execution_status"], "succeeded")

    def test_execution_status_refresh_never_creates_missing_task_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing_db = root / "not-created.sqlite3"
            with AutonomyPlanStore(root / "plans.sqlite3") as store:
                saved = store.save(validate_plan(VALID))
                # Simulate a persisted approved plan with a stale task reference.
                store.connection.execute(
                    "UPDATE autonomy_plans SET status='approved', task_id=? WHERE id=?",
                    ("f" * 32, saved["id"]),
                )
                store.connection.commit()
                receipt = store.refresh_execution_status(missing_db)
                self.assertEqual(receipt[0]["execution_status"], "missing")
                self.assertFalse(missing_db.exists())

    def test_rejection_is_audited_and_cannot_be_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            with TaskQueue(task_db):
                pass
            with AutonomyPlanStore(root / "plans.sqlite3") as store:
                saved = store.save(validate_plan(VALID))
                rejected = store.reject(saved["id"], rejected_by="owner", reason="not now")
                self.assertEqual(rejected["status"], "rejected")
                with self.assertRaises(ValueError):
                    store.approve(saved["id"], approved_by="owner", task_database=task_db)

    def test_model_proposal_is_saved_but_never_enqueued_or_executed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backlog = root / "BACKLOG.md"
            backlog.write_text("# P0\n- Finish worker recovery tests\n", encoding="utf-8")
            task_db = root / "tasks.sqlite3"
            with TaskQueue(task_db):
                pass
            calls = []
            def fake_model(messages):
                calls.append(messages)
                return json.dumps(VALID)
            result = propose_next_step(backlog, task_db, root / "plans.sqlite3", fake_model)
            self.assertEqual(result["status"], "proposal_saved")
            self.assertTrue(result["approval_required"])
            self.assertFalse(result["execution_started"])
            self.assertEqual(len(calls), 1)
            with TaskQueue(task_db) as queue:
                self.assertEqual(queue.snapshot()["counts"]["queued"], 0)

    def test_periodic_loop_survives_one_failed_cycle_and_stops_cleanly(self) -> None:
        class StopAfterTwoWaits:
            def __init__(self):
                self.waits = 0
            def is_set(self):
                return False
            def wait(self, _seconds):
                self.waits += 1
                return self.waits >= 2
        calls = []
        def cycle():
            calls.append(len(calls))
            if len(calls) == 1:
                raise RuntimeError("temporary local model outage")
        stats = run_planner_loop(cycle, interval_seconds=60, stop_event=StopAfterTwoWaits())
        self.assertEqual(len(calls), 2)
        self.assertEqual(stats, {"cycles": 1, "errors": 1})

    def test_periodic_loop_rejects_too_short_interval(self) -> None:
        with self.assertRaises(ValueError):
            run_planner_loop(lambda: None, interval_seconds=10)

    def test_missing_inputs_do_not_create_databases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                propose_next_step(root / "missing.md", root / "missing.sqlite3", root / "plans.sqlite3", lambda _: "{}")
            self.assertFalse((root / "missing.sqlite3").exists())
            self.assertFalse((root / "plans.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
