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

    def test_reported_agent_state_maps_herdr_lifecycle_without_approving_work(self) -> None:
        now = datetime(2026, 11, 21, tzinfo=timezone.utc)
        self.registry.register("coder", role="developer")
        self.assertTrue(self.registry.report_state(
            "coder", "blocked", message="waiting for operator", source="herdr-core", now=now,
        ))
        blocked = self.registry.get("coder", now=now)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["reported_state"], "blocked")
        self.assertEqual(blocked["reported_message"], "waiting for operator")
        self.assertEqual(blocked["reported_source"], "herdr-core")
        self.assertEqual(blocked["reported_at"], now.isoformat())
        stale = self.registry.get("coder", now=now + timedelta(seconds=91))
        self.assertEqual(stale["status"], "offline")
        self.assertEqual(stale["reported_state"], "blocked")

    def test_reported_idle_state_overrides_old_task_marker(self) -> None:
        now = datetime(2026, 11, 21, tzinfo=timezone.utc)
        self.registry.register("coder", role="developer")
        self.registry.heartbeat("coder", busy=True, current_task_id="old-task", now=now)
        self.registry.report_state("coder", "idle", now=now + timedelta(seconds=1))
        agent = self.registry.get("coder", now=now + timedelta(seconds=1))
        self.assertEqual(agent["status"], "online")
        self.assertEqual(agent["reported_state"], "idle")
        self.assertEqual(agent["current_task_id"], "old-task")

    def test_reported_state_rejects_untrusted_control_text(self) -> None:
        self.registry.register("coder", role="developer")
        with self.assertRaises(ValueError):
            self.registry.report_state("coder", "approved")
        with self.assertRaises(ValueError):
            self.registry.report_state("coder", "blocked", source="bad\x1b")
        with self.assertRaises(ValueError):
            self.registry.report_state("coder", "blocked", message="bad\x1b[31m")
        with self.assertRaises(ValueError):
            self.registry.report_state("coder", "blocked", message="x" * 161)

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

    def test_org_chart_tracks_reporting_lines_and_audits_changes(self) -> None:
        self.registry.register("ceo", role="executive")
        self.registry.register("cto", role="engineering")
        self.registry.register("coder", role="developer")
        self.registry.set_org_assignment("ceo", title="Chief Executive", actor="board")
        self.registry.set_org_assignment("cto", title="CTO", reports_to="ceo", actor="board")
        self.registry.set_org_assignment("coder", title="Backend Engineer", reports_to="cto", actor="board")

        chart = self.registry.org_chart()
        self.assertEqual(chart["total"], 3)
        self.assertFalse(chart["truncated"])
        self.assertEqual(chart["edges"], [
            {"manager_id": "ceo", "report_id": "cto"},
            {"manager_id": "cto", "report_id": "coder"},
        ])
        coder = self.registry.get("coder")
        self.assertEqual(coder["title"], "Backend Engineer")
        self.assertEqual(coder["reports_to"], "cto")
        events = self.registry.list_org_events(agent_id="coder")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"], "board")
        self.assertEqual(events[0]["payload"]["reports_to"], "cto")

    def test_org_assignment_rejects_missing_self_and_cyclic_managers(self) -> None:
        self.registry.register("ceo", role="executive")
        self.registry.register("cto", role="engineering")
        self.registry.register("coder", role="developer")
        with self.assertRaises(ValueError):
            self.registry.set_org_assignment("coder", title="Coder", reports_to="missing")
        with self.assertRaises(ValueError):
            self.registry.set_org_assignment("coder", title="Coder", reports_to="coder")
        self.registry.set_org_assignment("cto", title="CTO", reports_to="ceo")
        self.registry.set_org_assignment("coder", title="Coder", reports_to="cto")
        with self.assertRaises(ValueError):
            self.registry.set_org_assignment("ceo", title="CEO", reports_to="coder")
        self.assertIsNone(self.registry.get("ceo")["reports_to"])

    def test_org_chart_reports_truncation_and_rejects_invalid_limits(self) -> None:
        self.registry.register("alpha", role="research")
        self.registry.register("beta", role="writer")
        chart = self.registry.org_chart(limit=1)
        self.assertTrue(chart["truncated"])
        self.assertEqual(chart["total"], 2)
        with self.assertRaises(ValueError):
            self.registry.org_chart(limit=0)

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
