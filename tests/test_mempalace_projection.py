import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.memory import MemoryStore
from muse.mempalace_projection import MemPalaceProjectionOutbox


class FakeCheckpointAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def checkpoint(self, items, *, approved=False, dedup_threshold=0.9):
        self.calls.append({
            "items": items,
            "approved": approved,
            "dedup_threshold": dedup_threshold,
        })
        if self.fail:
            raise RuntimeError("synthetic MCP failure")
        return {"structuredContent": {"added": [{"drawer_id": "synthetic-drawer"} for _ in items], "duplicates": [], "errors": []}}


class MemPalaceProjectionOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "memory.sqlite3"
        with MemoryStore(self.database) as memory:
            self.agent_id = memory.add(
                "A reusable engineering lesson: keep Muse MemoryStore canonical and use semantic recall as a sidecar.",
                project="muse",
                source="synthetic-test",
                confidence=0.94,
                kind="skill",
                track="agent",
            )["id"]
            self.user_id = memory.add(
                "A private user preference that must never be projected.",
                project="muse",
                source="synthetic-user-test",
                confidence=0.99,
                kind="preference",
                track="user",
            )["id"]
            self.fact_id = memory.add(
                "A generic fact is not an approved agent skill or case.",
                project="muse",
                source="synthetic-test",
                confidence=0.99,
                kind="fact",
                track="agent",
            )["id"]
            self.low_confidence_id = memory.add(
                "A weak and unverified engineering guess.",
                project="muse",
                source="synthetic-test",
                confidence=0.4,
                kind="skill",
                track="agent",
            )["id"]
            self.credential_id = memory.add(
                "Do not export this api_key=sk-test-0123456789abcdef0123456789abcdef",
                project="muse",
                source="synthetic-test",
                confidence=0.99,
                kind="skill",
                track="agent",
            )["id"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_health_summary_reports_counts_without_projected_content(self) -> None:
        with MemPalaceProjectionOutbox(self.database) as outbox:
            empty = outbox.health_summary()
            self.assertEqual(empty["status"], "healthy")
            self.assertEqual(empty["counts"], {"pending": 0, "failed": 0, "delivered": 0})
            queued = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
            pending = outbox.health_summary()
            self.assertEqual(pending["status"], "pending")
            self.assertEqual(pending["counts"]["pending"], 1)
            self.assertEqual(pending["oldest_pending_at"], queued["created_at"])
            self.assertFalse(pending["content_included"])
            self.assertNotIn("payload", pending)

    def test_queue_requires_per_record_approval_and_auditor(self) -> None:
        with MemPalaceProjectionOutbox(self.database) as outbox:
            with self.assertRaises(PermissionError):
                outbox.queue(self.agent_id)
            with self.assertRaises(ValueError):
                outbox.queue(self.agent_id, approved=True)
            with self.assertRaises(ValueError):
                outbox.queue(self.agent_id, approved=True, approved_by="api_key=sk-test-0123456789abcdef")
            self.assertEqual(outbox.list_pending(), [])

    def test_queue_rejects_user_track_non_projectable_kind_and_low_confidence(self) -> None:
        with MemPalaceProjectionOutbox(self.database) as outbox:
            with self.assertRaises(PermissionError):
                outbox.queue(self.user_id, approved=True, approved_by="operator")
            with self.assertRaises(ValueError):
                outbox.queue(self.fact_id, approved=True, approved_by="operator")
            with self.assertRaises(ValueError):
                outbox.queue(self.low_confidence_id, approved=True, approved_by="operator")
            self.assertEqual(outbox.list_pending(), [])

    def test_queue_is_idempotent_and_keeps_original_content_and_provenance(self) -> None:
        with MemPalaceProjectionOutbox(self.database) as outbox:
            first = outbox.queue(self.agent_id, approved=True, approved_by="reviewer-1")
            second = outbox.queue(self.agent_id, approved=True, approved_by="reviewer-2")
            self.assertEqual(first["outbox_id"], second["outbox_id"])
            self.assertEqual(first["idempotency_key"], second["idempotency_key"])
            self.assertEqual(first["status"], "pending")
            self.assertEqual(first["approved_by"], "reviewer-1")
            item = first["payload"]["items"][0]
            self.assertEqual(item["wing"], "muse")
            self.assertEqual(item["room"], "skill")
            self.assertIn("A reusable engineering lesson", item["content"])
            self.assertIn("[Muse Provenance]", item["content"])
            self.assertIn(self.agent_id.__str__(), item["content"])
            self.assertEqual(len(outbox.list_pending()), 1)

    def test_queue_rejects_credential_like_content(self) -> None:
        with MemPalaceProjectionOutbox(self.database) as outbox:
            with self.assertRaises(ValueError):
                outbox.queue(self.credential_id, approved=True, approved_by="reviewer")

    def test_delivery_requires_a_second_per_call_approval(self) -> None:
        adapter = FakeCheckpointAdapter()
        with MemPalaceProjectionOutbox(self.database) as outbox:
            queued = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
            with self.assertRaises(PermissionError):
                outbox.deliver(queued["outbox_id"], adapter)
            self.assertEqual(adapter.calls, [])

    def test_delivery_marks_success_and_never_replays_a_delivered_record(self) -> None:
        adapter = FakeCheckpointAdapter()
        with MemPalaceProjectionOutbox(self.database) as outbox:
            queued = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
            delivered = outbox.deliver(
                queued["outbox_id"], adapter, approved=True, approved_by="publisher",
            )
            self.assertEqual(delivered["status"], "delivered")
            self.assertEqual(delivered["delivered_by"], "publisher")
            self.assertEqual(delivered["attempts"], 1)
            self.assertEqual(adapter.calls[0]["approved"], True)
            replay = outbox.deliver(
                queued["outbox_id"], adapter, approved=True, approved_by="publisher",
            )
            self.assertEqual(replay["status"], "delivered")
            self.assertEqual(len(adapter.calls), 1)

    def test_stale_or_deleted_source_is_not_delivered(self) -> None:
        adapter = FakeCheckpointAdapter()
        with MemPalaceProjectionOutbox(self.database) as outbox:
            queued = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
            with MemoryStore(self.database) as memory:
                memory.revise(self.agent_id, "A newer revision of the engineering lesson.")
            with self.assertRaises(RuntimeError):
                outbox.deliver(
                    queued["outbox_id"], adapter, approved=True, approved_by="publisher",
                )
            failed = outbox.get(queued["outbox_id"])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["last_error"], "StaleSourceRevision")
            self.assertEqual(adapter.calls, [])

    def test_delivery_failure_is_recorded_and_can_be_retried_explicitly(self) -> None:
        adapter = FakeCheckpointAdapter(fail=True)
        with MemPalaceProjectionOutbox(self.database) as outbox:
            queued = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
            with self.assertRaises(RuntimeError):
                outbox.deliver(
                    queued["outbox_id"], adapter, approved=True, approved_by="publisher",
                )
            failed = outbox.get(queued["outbox_id"])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["attempts"], 1)
            self.assertEqual(failed["last_error"], "RuntimeError")
            self.assertEqual(len(outbox.list_pending(include_failed=True)), 1)
            adapter.fail = False
            delivered = outbox.deliver(
                queued["outbox_id"], adapter, approved=True, approved_by="publisher",
            )
            self.assertEqual(delivered["status"], "delivered")
            self.assertEqual(delivered["attempts"], 2)


    def test_delivery_requires_matching_persisted_commander_approval_ticket(self) -> None:
        from muse.task_queue import TaskQueue

        adapter = FakeCheckpointAdapter()
        with tempfile.TemporaryDirectory() as task_dir:
            task_database = Path(task_dir) / "tasks.sqlite3"
            with MemPalaceProjectionOutbox(self.database) as outbox:
                queued = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
                with TaskQueue(task_database) as tasks:
                    ticket = tasks.enqueue(
                        "Approve projection",
                        {
                            "kind": "mempalace_projection_approval",
                            "outbox_id": queued["outbox_id"],
                            "idempotency_key": queued["idempotency_key"],
                        },
                        require_approval=True,
                    )
                    with self.assertRaises(PermissionError):
                        outbox.deliver_with_approval_ticket(
                            queued["outbox_id"], adapter,
                            task_database=task_database, approval_task_id=ticket["id"],
                        )
                    self.assertEqual(adapter.calls, [])
                    import json
                    tasks.approve(ticket["id"], approved_by="human-reviewer")
                    approved = tasks.get(ticket["id"])
                    approved_at = approved["payload"]["_approval"]["approved_at"]
                    approved["payload"]["_approval"]["approved_at"] = "2000-01-01T00:00:00+00:00"
                    tasks._connection.execute(
                        "UPDATE tasks SET payload_json=? WHERE id=?",
                        (json.dumps(approved["payload"]), ticket["id"]),
                    )
                    tasks._connection.commit()
                with self.assertRaises(PermissionError):
                    outbox.deliver_with_approval_ticket(
                        queued["outbox_id"], adapter,
                        task_database=task_database, approval_task_id=ticket["id"],
                    )
                with TaskQueue(task_database) as tasks:
                    approved = tasks.get(ticket["id"])
                    approved["payload"]["_approval"]["approved_at"] = approved_at
                    tasks._connection.execute(
                        "UPDATE tasks SET payload_json=? WHERE id=?",
                        (json.dumps(approved["payload"]), ticket["id"]),
                    )
                    tasks._connection.commit()

                delivered = outbox.deliver_with_approval_ticket(
                    queued["outbox_id"], adapter,
                    task_database=task_database, approval_task_id=ticket["id"],
                )
                self.assertEqual(delivered["status"], "delivered")
                self.assertEqual(delivered["delivered_by"], "human-reviewer")
                self.assertEqual(len(adapter.calls), 1)

    def test_approval_ticket_cannot_authorize_another_projection(self) -> None:
        from muse.task_queue import TaskQueue

        adapter = FakeCheckpointAdapter()
        with tempfile.TemporaryDirectory() as task_dir:
            task_database = Path(task_dir) / "tasks.sqlite3"
            with MemPalaceProjectionOutbox(self.database) as outbox:
                first = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
                with MemoryStore(self.database) as memory:
                    other_id = memory.add(
                        "A second synthetic engineering case for ticket binding.",
                        project="muse", source="synthetic-test", confidence=0.95,
                        kind="case", track="agent",
                    )["id"]
                second = outbox.queue(other_id, approved=True, approved_by="reviewer")
                with TaskQueue(task_database) as tasks:
                    ticket = tasks.enqueue(
                        "Approve first projection",
                        {"kind": "mempalace_projection_approval",
                         "outbox_id": first["outbox_id"],
                         "idempotency_key": first["idempotency_key"]},
                        require_approval=True,
                    )
                    tasks.approve(ticket["id"], approved_by="human-reviewer")
                with self.assertRaises(PermissionError):
                    outbox.deliver_with_approval_ticket(
                        second["outbox_id"], adapter,
                        task_database=task_database, approval_task_id=ticket["id"],
                    )
                self.assertEqual(adapter.calls, [])

    def test_pending_projection_survives_outbox_reopen_and_delivers_once(self) -> None:
        with MemPalaceProjectionOutbox(self.database) as outbox:
            queued = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
            outbox_id = queued["outbox_id"]

        # Reopening simulates a process restart between staging and delivery.
        with MemPalaceProjectionOutbox(self.database) as recovered:
            pending = recovered.get(outbox_id)
            self.assertEqual(pending["status"], "pending")
            self.assertEqual(pending["payload"]["items"][0]["wing"], "muse")
            adapter = FakeCheckpointAdapter()
            delivered = recovered.deliver(
                outbox_id, adapter, approved=True, approved_by="publisher",
            )
            self.assertEqual(delivered["status"], "delivered")
            self.assertEqual(delivered["attempts"], 1)

        # A second restart must preserve the terminal state and prevent replay.
        with MemPalaceProjectionOutbox(self.database) as reopened:
            persisted = reopened.get(outbox_id)
            replay = reopened.deliver(
                outbox_id, adapter, approved=True, approved_by="publisher",
            )
            self.assertEqual(persisted["status"], "delivered")
            self.assertEqual(replay["status"], "delivered")
            self.assertEqual(len(adapter.calls), 1)

    def test_delivery_recovers_after_remote_acceptance_before_local_commit(self) -> None:
        class IdempotentRemote:
            def __init__(self) -> None:
                self.calls = []

            def checkpoint(self, items, *, approved=False, dedup_threshold=0.9):
                self.calls.append(items)
                expected = items[0]["content"]
                if len(self.calls) == 1:
                    return {"structuredContent": {
                        "added": [{"drawer_id": "remote-drawer-1"}],
                        "duplicates": [], "errors": [],
                    }}
                return {"structuredContent": {
                    "added": [],
                    "duplicates": [{"matches": [{
                        "id": "remote-drawer-1", "content": expected,
                    }]}],
                    "errors": [],
                }}

        adapter = IdempotentRemote()
        with MemPalaceProjectionOutbox(self.database) as outbox:
            queued = outbox.queue(self.agent_id, approved=True, approved_by="reviewer")
            outbox_id = queued["outbox_id"]
            outbox._connection.execute(
                """CREATE TRIGGER simulate_local_crash
                   BEFORE UPDATE OF status ON mempalace_projection_outbox
                   WHEN NEW.status = 'delivered'
                   BEGIN SELECT RAISE(ABORT, 'simulated crash after remote acceptance'); END"""
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated crash"):
                outbox.deliver(
                    outbox_id, adapter, approved=True, approved_by="publisher",
                )
            self.assertEqual(outbox.get(outbox_id)["status"], "pending")
            self.assertEqual(len(adapter.calls), 1)

        # A new connection models process restart. The remote already has the
        # item, so the retry must receive an exact duplicate with the same ID.
        with MemPalaceProjectionOutbox(self.database) as recovered:
            recovered._connection.execute("DROP TRIGGER simulate_local_crash")
            delivered = recovered.deliver(
                outbox_id, adapter, approved=True, approved_by="publisher",
            )
            self.assertEqual(delivered["status"], "delivered")
            self.assertEqual(delivered["remote_result"], {
                "outcome": "exact_duplicate", "drawer_id": "remote-drawer-1",
            })
            self.assertEqual(len(adapter.calls), 2)


if __name__ == "__main__":
    unittest.main()
