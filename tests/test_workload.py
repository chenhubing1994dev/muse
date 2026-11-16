from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.task_queue import TaskQueue
from muse.workload import plan_hourly_work


class HourlyWorkloadPlannerTests(unittest.TestCase):
    def test_packs_high_priority_work_and_fills_remaining_capacity(self) -> None:
        tasks = [
            {"id": "research", "priority": 80, "estimated_seconds": 1200},
            {"id": "docs", "priority": 50, "estimated_seconds": 900},
            {"id": "benchmark", "priority": 95, "estimated_seconds": 2400},
        ]
        plan = plan_hourly_work(tasks, window_seconds=3600, reserve_seconds=300)
        self.assertEqual(plan["selected_ids"], ["benchmark", "docs"])
        self.assertEqual(plan["deferred_ids"], ["research"])
        self.assertEqual(plan["estimated_work_seconds"], 3300)
        self.assertEqual(plan["remaining_work_budget_seconds"], 0)

    def test_unknown_estimate_uses_conservative_default_and_reports_underfill(self) -> None:
        plan = plan_hourly_work(
            [{"id": "unknown", "priority": 1}],
            window_seconds=3600,
            reserve_seconds=300,
            default_estimate_seconds=900,
        )
        self.assertEqual(plan["selected_ids"], ["unknown"])
        self.assertEqual(plan["estimated_work_seconds"], 900)
        self.assertEqual(plan["remaining_work_budget_seconds"], 2400)
        self.assertEqual(plan["capacity_state"], "underfilled")

    def test_invalid_estimates_and_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            plan_hourly_work([{"id": "bad", "estimated_seconds": -1}])
        with self.assertRaises(ValueError):
            plan_hourly_work([{"id": "same"}, {"id": "same"}])

    def test_queue_persists_priority_and_estimate_for_hourly_planning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                low = queue.enqueue("docs", {"kind": "echo"}, priority=20, estimated_seconds=600)
                high = queue.enqueue("tests", {"kind": "echo"}, priority=90, estimated_seconds=1200)
                items = queue.queued_work_items()
                plan = plan_hourly_work(items, window_seconds=3600, reserve_seconds=300)
                self.assertEqual(plan["selected_ids"], [high["id"], low["id"]])
                claimed = queue.claim_next()
                self.assertEqual(claimed["id"], high["id"])

    def test_queue_rejects_invalid_priority_or_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                with self.assertRaises(ValueError):
                    queue.enqueue("bad priority", {}, priority=101)
                with self.assertRaises(ValueError):
                    queue.enqueue("bad estimate", {}, estimated_seconds=0)

    def test_task_larger_than_available_budget_is_deferred(self) -> None:
        plan = plan_hourly_work(
            [{"id": "large", "priority": 99, "estimated_seconds": 4000}],
            window_seconds=3600,
            reserve_seconds=300,
        )
        self.assertEqual(plan["selected_ids"], [])
        self.assertEqual(plan["deferred_ids"], ["large"])
        self.assertEqual(plan["capacity_state"], "no_fit")


if __name__ == "__main__":
    unittest.main()
