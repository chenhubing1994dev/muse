from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from muse.agent_registry import AgentRegistry


class AgentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "agents.sqlite3"
        self.registry = AgentRegistry(self.database)

    def tearDown(self) -> None:
        self.registry.close()
        self.temp.cleanup()

    def test_register_and_restart_preserve_metadata_but_not_trust_callable(self) -> None:
        self.registry.register("researcher", role="research", capabilities=("web.search",), source="local:configured")
        self.registry.close()
        self.registry = AgentRegistry(self.database)
        agent = self.registry.get("researcher")
        self.assertEqual(agent["capabilities"], ["web.search"])
        self.assertEqual(agent["source"], "local:configured")
        self.assertEqual(agent["status"], "offline")

    def test_heartbeat_transitions_online_busy_and_offline(self) -> None:
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.registry.register("coder", role="developer", capabilities=("coding",))
        self.assertTrue(self.registry.heartbeat("coder", now=now))
        self.assertEqual(self.registry.get("coder", now=now)["status"], "online")
        self.registry.heartbeat("coder", busy=True, current_task_id="task-7", now=now + timedelta(seconds=1))
        busy = self.registry.get("coder", now=now + timedelta(seconds=1))
        self.assertEqual(busy["status"], "busy")
        self.assertEqual(busy["current_task_id"], "task-7")
        stale = self.registry.get("coder", now=now + timedelta(seconds=100), stale_after_seconds=30)
        self.assertEqual(stale["status"], "offline")

    def test_unknown_agent_heartbeat_returns_false(self) -> None:
        self.assertFalse(self.registry.heartbeat("missing"))

    def test_disabled_agent_cannot_become_available(self) -> None:
        self.registry.register("worker", role="developer", enabled=False)
        self.registry.heartbeat("worker")
        self.assertEqual(self.registry.get("worker")["status"], "disabled")

    def test_list_agents_is_bounded_and_sorted(self) -> None:
        self.registry.register("zeta", role="writer")
        self.registry.register("alpha", role="research")
        self.assertEqual([item["agent_id"] for item in self.registry.list_agents(limit=2)], ["alpha", "zeta"])
        with self.assertRaises(ValueError):
            self.registry.list_agents(limit=0)

    def test_record_outcome_and_effectiveness_are_evidence_based(self) -> None:
        self.registry.register("coder", role="developer")
        self.registry.record_outcome("coder", task_kind="coding", succeeded=True, reviewed=True, review_approved=True)
        self.registry.record_outcome("coder", task_kind="coding", succeeded=False, reviewed=True, review_approved=False)
        metrics = self.registry.effectiveness("coder")
        self.assertEqual(metrics["completed"], 2)
        self.assertEqual(metrics["succeeded"], 1)
        self.assertEqual(metrics["reviewed"], 2)
        self.assertEqual(metrics["review_approved"], 1)
        self.assertEqual(metrics["success_rate"], 0.5)
        self.assertEqual(metrics["review_pass_rate"], 0.5)

    def test_review_metrics_exclude_unreviewed_tasks(self) -> None:
        self.registry.register("agent", role="developer")
        self.registry.record_outcome("agent", task_kind="coding", succeeded=True, reviewed=False)
        metrics = self.registry.effectiveness("agent")
        self.assertIsNone(metrics["review_pass_rate"])
        self.assertEqual(metrics["reviewed"], 0)

    def test_reject_invalid_agent_and_metric_values(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.register("", role="developer")
        with self.assertRaises(ValueError):
            self.registry.record_outcome("missing", task_kind="coding", succeeded=True)
        with self.assertRaises(ValueError):
            self.registry.heartbeat("missing", busy=True)


    def test_review_outcome_updates_review_rate_without_double_counting_completion(self) -> None:
        self.registry.register("builder", role="developer")
        self.registry.record_outcome("builder", task_kind="coding", succeeded=True)
        self.registry.record_review_outcome("builder", task_kind="coding", approved=False)
        metrics = self.registry.effectiveness("builder")
        self.assertEqual(metrics["completed"], 1)
        self.assertEqual(metrics["reviewed"], 1)
        self.assertEqual(metrics["review_approved"], 0)
        self.assertEqual(metrics["review_pass_rate"], 0.0)

if __name__ == "__main__":
    unittest.main()
