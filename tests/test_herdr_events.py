from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from muse.agent_registry import AgentRegistry
from muse.herdr_events import HerdrEventStream, bound_agent_snapshot_updates, bound_agent_status_updates


class HerdrEventStreamTests(unittest.TestCase):
    def test_subscription_snapshot_seeds_sequence_and_only_status_projection(self):
        stream = HerdrEventStream.from_snapshot({"seq": 7, "terminals": [
            {"id": "t1", "status": "working", "reported": True, "argv": ["secret"], "cwd": "C:/private"},
            {"id": "t2", "status": "idle", "reported": True},
            {"id": "t3", "status": "mystery", "reported": True},
            {"id": "t4", "status": "blocked", "reported": False},
        ]})
        self.assertEqual(stream.sequence, 7)
        self.assertEqual(stream.statuses, {"t1": "working", "t2": "idle", "t3": "unknown", "t4": "unknown"})
        self.assertNotIn("argv", repr(stream.statuses))

    def test_status_event_updates_only_one_terminal_and_explicit_agent_binding(self):
        stream = HerdrEventStream.from_snapshot({"seq": 2, "terminals": [{"id": "t1", "status": "idle", "reported": True}]})
        result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {
            "seq": 3, "type": "status", "terminal": "t1",
            "data": {"status": "blocked", "records": [], "reported": True},
        }})
        self.assertEqual(stream.statuses["t1"], "blocked")
        self.assertEqual(bound_agent_status_updates(result, {"t1": "coder"}), (("coder", "blocked"),))
        self.assertEqual(bound_agent_status_updates(result, {}), ())

    def test_unreported_or_unknown_status_never_becomes_an_agent_claim(self):
        stream = HerdrEventStream.from_snapshot({"seq": 0, "terminals": []})
        result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {
            "seq": 1, "type": "status", "terminal": "t1",
            "data": {"status": "error", "reported": True},
        }})
        self.assertEqual(result.agent_state, "unknown")
        self.assertEqual(bound_agent_status_updates(result, {"t1": "coder"}), ())

    def test_heartbeat_advances_sequence_without_state_change(self):
        stream = HerdrEventStream.from_snapshot({"seq": 10, "terminals": [{"id": "t1", "status": "working", "reported": True}]})
        result = stream.apply({"jsonrpc": "2.0", "method": "heartbeat", "params": {"seq": 10}})
        self.assertTrue(result.accepted)
        self.assertEqual(stream.sequence, 10)
        self.assertEqual(stream.statuses, {"t1": "working"})

    def test_duplicate_and_stale_events_are_ignored(self):
        stream = HerdrEventStream.from_snapshot({"seq": 4, "terminals": []})
        result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {
            "seq": 4, "type": "status", "terminal": "t1", "data": {"status": "blocked"}
        }})
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "duplicate_or_stale")
        self.assertEqual(stream.statuses, {})

    def test_missed_event_atomically_replaces_snapshot_and_suppresses_direct_mapping(self):
        stream = HerdrEventStream.from_snapshot({"seq": 2, "terminals": [{"id": "old", "status": "working", "reported": True}]})
        result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {
            "seq": 9, "type": "missed", "terminals": [{"id": "new", "status": "blocked", "reported": True}],
        }})
        self.assertTrue(result.snapshot_replaced)
        self.assertEqual(stream.statuses, {"new": "blocked"})
        self.assertEqual(bound_agent_status_updates(result, {"new": "coder"}), ())

    def test_reconnect_uses_new_core_sequence_snapshot(self):
        first = HerdrEventStream.from_snapshot({"seq": 90, "terminals": [{"id": "t1", "status": "working", "reported": True}]})
        second = HerdrEventStream.from_snapshot({"seq": 0, "terminals": [{"id": "t1", "status": "idle", "reported": True}]})
        self.assertEqual(first.sequence, 90)
        self.assertEqual(second.sequence, 0)
        self.assertEqual(second.statuses["t1"], "idle")

    def test_snapshot_projection_only_updates_explicitly_bound_reported_agents(self):
        stream = HerdrEventStream.from_snapshot({"seq": 3, "terminals": [
            {"id": "t1", "status": "working", "reported": True},
            {"id": "t2", "status": "error", "reported": True},
            {"id": "t3", "status": "blocked", "reported": False},
        ]})
        self.assertEqual(bound_agent_snapshot_updates(stream, {"t1": "coder", "t2": "reviewer", "t3": "writer"}), (("coder", "working"),))

    def test_core_missed_notification_method_replaces_snapshot(self):
        stream = HerdrEventStream.from_snapshot({"seq": 2, "terminals": [{"id": "old", "status": "working", "reported": True}]})
        result = stream.apply({"jsonrpc": "2.0", "method": "missed", "params": {
            "seq": 9, "terminals": [{"id": "new", "status": "blocked", "reported": True}],
        }})
        self.assertTrue(result.accepted)
        self.assertTrue(result.snapshot_replaced)
        self.assertEqual(stream.sequence, 9)
        self.assertEqual(stream.statuses, {"new": "blocked"})

    def test_missed_without_snapshot_requires_external_reconcile(self):
        stream = HerdrEventStream.from_snapshot({"seq": 2, "terminals": []})
        result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {"seq": 3, "type": "missed"}})
        self.assertFalse(result.accepted)
        self.assertTrue(result.needs_reconcile)
        self.assertEqual(stream.sequence, 2)

    def test_lost_event_clears_stale_working_state(self):
        stream = HerdrEventStream.from_snapshot({"seq": 1, "terminals": [{"id": "t1", "status": "working", "reported": True}]})
        result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {"seq": 2, "type": "lost", "terminal": "t1", "data": {"reason": "holder_lost"}}})
        self.assertEqual(result.agent_state, "unknown")
        self.assertNotIn("t1", stream.statuses)
        self.assertEqual(bound_agent_status_updates(result, {"t1": "coder"}), ())

    def test_malformed_or_unexpected_messages_fail_closed(self):
        stream = HerdrEventStream.from_snapshot({"seq": 1, "terminals": []})
        result = stream.apply({"jsonrpc": "1.0", "method": "event", "params": {"seq": 2, "type": "status"}})
        self.assertFalse(result.accepted)
        self.assertTrue(result.needs_reconcile)
        self.assertEqual(stream.sequence, 1)

    def test_explicit_event_binding_updates_existing_agent_registry_only(self):
        now = datetime(2026, 11, 21, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            registry = AgentRegistry(Path(directory) / "agents.sqlite3")
            try:
                registry.register("coder", role="developer")
                stream = HerdrEventStream.from_snapshot({"seq": 1, "terminals": [{"id": "t1", "status": "working", "reported": True}]})
                result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {
                    "seq": 2, "type": "status", "terminal": "t1",
                    "data": {"status": "blocked", "reported": True},
                }})
                for agent_id, state in bound_agent_status_updates(result, {"t1": "coder"}):
                    self.assertTrue(registry.report_state(agent_id, state, source="herdr-core", now=now))
                agent = registry.get("coder", now=now)
                self.assertEqual(agent["status"], "blocked")
                self.assertEqual(agent["reported_source"], "herdr-core")
            finally:
                registry.close()

    def test_reported_flag_must_be_boolean_true_and_mapping_must_be_one_to_one(self):
        stream = HerdrEventStream.from_snapshot({"seq": 1, "terminals": [
            {"id": "t1", "status": "working", "reported": "true"},
        ]})
        self.assertEqual(stream.statuses["t1"], "unknown")
        result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {
            "seq": 2, "type": "status", "terminal": "t1",
            "data": {"status": "working", "reported": True},
        }})
        self.assertEqual(bound_agent_status_updates(result, {"t1": "coder", "t2": "coder"}), ())
        self.assertEqual(bound_agent_snapshot_updates(stream, {"t1": "coder", "t2": "coder"}), ())

    def test_finished_or_removed_event_does_not_claim_task_success(self):
        stream = HerdrEventStream.from_snapshot({"seq": 1, "terminals": [{"id": "t1", "status": "working", "reported": True}]})
        result = stream.apply({"jsonrpc": "2.0", "method": "event", "params": {
            "seq": 2, "type": "finished", "terminal": "t1", "data": {"exit_code": 1}
        }})
        self.assertEqual(result.agent_state, "unknown")
        self.assertEqual(stream.statuses, {})
        self.assertEqual(bound_agent_status_updates(result, {"t1": "coder"}), ())


if __name__ == "__main__":
    unittest.main()
