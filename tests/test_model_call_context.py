from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.model_call_context import (
    current_model_call_context,
    model_call_scope,
    record_current_model_usage,
)
from muse.model_client import LMStudioClient
from muse.providers import ChatRequest, LMStudioAdapter
from muse.task_queue import TaskQueue
from muse.usage_observer import UsageRecordingAdapter
from muse.usage_ledger import UsageLedger
from muse.worker import run_once


class ModelCallContextTests(unittest.TestCase):
    def test_scope_binds_task_and_project_and_resets_after_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_db = Path(directory) / "tasks.sqlite3"
            self.assertIsNone(current_model_call_context())
            with model_call_scope(task_db, task_id="task-123", project_id="muse"):
                context = current_model_call_context()
                self.assertIsNotNone(context)
                self.assertEqual(context.task_id, "task-123")
                self.assertEqual(context.project_id, "muse")
                self.assertTrue(record_current_model_usage(
                    "test-provider", "test-model",
                    {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
                ))
            self.assertIsNone(current_model_call_context())
            with UsageLedger(Path(directory) / "model_usage.sqlite3") as ledger:
                rows = ledger.list_events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["task_id"], "task-123")
            self.assertEqual(rows[0]["project_id"], "muse")
            self.assertEqual(rows[0]["attribution_status"], "partial")

    def test_lmstudio_client_automatically_records_inside_active_task_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_db = Path(directory) / "tasks.sqlite3"
            client = LMStudioClient(model="actual-local-model")
            client._request = lambda method, path, body=None: {
                "model": "actual-local-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
            }
            with model_call_scope(task_db, task_id="task-model", project_id="muse"):
                result = client.chat_with_usage([{"role": "user", "content": "not persisted"}])
            self.assertEqual(result.content, "ok")
            with UsageLedger(Path(directory) / "model_usage.sqlite3") as ledger:
                rows = ledger.list_events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["provider_id"], "lm-studio-local")
            self.assertEqual(rows[0]["model"], "actual-local-model")
            self.assertEqual(rows[0]["total_tokens"], 20)
            self.assertEqual(rows[0]["task_id"], "task-model")

    def test_lmstudio_adapter_observer_chain_records_active_scope_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            client = LMStudioClient(model="actual-local-model")
            client._request = lambda method, path, body=None: {
                "model": "actual-local-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            }
            with UsageLedger(root / "model_usage.sqlite3") as ledger:
                adapter = UsageRecordingAdapter(
                    LMStudioAdapter(client), ledger,
                    task_id="stale-static-task", project_id="stale-static-project",
                )
                with model_call_scope(task_db, task_id="active-task", project_id="muse"):
                    response = adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}]))
                rows = ledger.list_events()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["task_id"], "active-task")
                self.assertEqual(rows[0]["project_id"], "muse")
                self.assertEqual(rows[0]["total_tokens"], 10)
                self.assertTrue(response.usage_recorded)
                self.assertEqual(adapter.observer_snapshot()["record_failures"], 0)

    def test_observer_falls_back_when_low_level_context_recording_fails(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            client = LMStudioClient(model="actual-local-model")
            client._request = lambda method, path, body=None: {
                "model": "actual-local-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            }
            with UsageLedger(root / "fallback.sqlite3") as ledger:
                adapter = UsageRecordingAdapter(LMStudioAdapter(client), ledger, task_id="fallback-task")
                with model_call_scope(task_db, task_id="active-task", project_id="muse"):
                    with patch("muse.model_client.record_current_model_usage", return_value=False):
                        response = adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}]))
                rows = ledger.list_events()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["task_id"], "fallback-task")
                self.assertTrue(response.usage_recorded)


    def test_worker_installs_context_for_legacy_handler_without_changing_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_db = Path(directory) / "tasks.sqlite3"

            def handler(payload):
                context = current_model_call_context()
                self.assertIsNotNone(context)
                self.assertEqual(context.task_id, payload["expected_task_id"])
                record_current_model_usage(
                    "worker-test-provider", "worker-test-model",
                    {"total_tokens": 7},
                )
                return {"ok": True}

            with TaskQueue(task_db) as queue:
                queued = queue.enqueue(
                    "legacy-context", {"kind": "legacy-context", "expected_task_id": "placeholder"}
                )
            # The handler sees the queue-assigned id, not a caller-supplied payload claim.
            def task_handler(payload):
                payload = dict(payload)
                payload["expected_task_id"] = queued["id"]
                return handler(payload)

            result = run_once(task_db, {"legacy-context": task_handler})
            self.assertEqual(result["status"], "succeeded")
            with UsageLedger(Path(directory) / "model_usage.sqlite3") as ledger:
                rows = ledger.list_events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["task_id"], queued["id"])
            self.assertEqual(rows[0]["agent_id"], None)
            self.assertEqual(rows[0]["project_id"], "muse")
            self.assertEqual(rows[0]["attribution_status"], "partial")

    def test_explicit_usage_event_id_deduplicates_a_retried_logical_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_db = Path(directory) / "tasks.sqlite3"
            client = LMStudioClient(model="actual-local-model")
            client._request = lambda method, path, body=None: {
                "model": "actual-local-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }
            with model_call_scope(task_db, task_id="task-retry", project_id="muse"):
                client.chat_with_usage(
                    [{"role": "user", "content": "same logical request"}],
                    usage_event_id="logical-call-001",
                )
                client.chat_with_usage(
                    [{"role": "user", "content": "same logical request"}],
                    usage_event_id="logical-call-001",
                )
            with UsageLedger(Path(directory) / "model_usage.sqlite3") as ledger:
                rows = ledger.list_events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_id"], "logical-call-001")

    def test_reused_usage_event_id_with_different_usage_does_not_break_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_db = Path(directory) / "tasks.sqlite3"
            client = LMStudioClient(model="actual-local-model")
            responses = iter((
                {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
            ))
            client._request = lambda method, path, body=None: {
                "model": "actual-local-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": next(responses),
            }
            with model_call_scope(task_db, task_id="task-retry", project_id="muse"):
                first = client.chat_with_usage(
                    [{"role": "user", "content": "same logical request"}],
                    usage_event_id="logical-call-conflict",
                )
                second = client.chat_with_usage(
                    [{"role": "user", "content": "same logical request"}],
                    usage_event_id="logical-call-conflict",
                )
            self.assertEqual(first.content, "ok")
            self.assertEqual(second.content, "ok")
            with UsageLedger(Path(directory) / "model_usage.sqlite3") as ledger:
                rows = ledger.list_events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["total_tokens"], 14)

    def test_worker_attributes_usage_only_to_enabled_agent_assigned_to_exact_task(self) -> None:
        from muse.agent_registry import AgentRegistry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            agent_db = root / "agents.sqlite3"
            with TaskQueue(task_db) as queue:
                queued = queue.enqueue("agent-task", {"kind": "agent-task"})
            with AgentRegistry(agent_db) as agents:
                agents.register("agent-researcher-01", role="researcher")
                agents.heartbeat("agent-researcher-01", busy=True, current_task_id=queued["id"])

            def handler(payload):
                context = current_model_call_context()
                self.assertEqual(context.agent_id, "agent-researcher-01")
                record_current_model_usage("agent-test-provider", "agent-test-model", {"total_tokens": 19})
                return {"ok": True}

            result = run_once(
                task_db, {"agent-task": handler}, task_id=queued["id"],
                agent_id="agent-researcher-01", agent_registry_database=agent_db,
            )
            self.assertEqual(result["status"], "succeeded")
            with UsageLedger(root / "model_usage.sqlite3") as ledger:
                rows = ledger.list_events()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["agent_id"], "agent-researcher-01")
            self.assertEqual(rows[0]["attribution_status"], "complete")

    def test_worker_rejects_agent_attribution_when_registry_assignment_mismatches(self) -> None:
        from muse.agent_registry import AgentRegistry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            agent_db = root / "agents.sqlite3"
            with TaskQueue(task_db) as queue:
                queued = queue.enqueue("unassigned-task", {"kind": "unassigned-task"})
            with AgentRegistry(agent_db) as agents:
                agents.register("agent-reviewer-01", role="reviewer")
                agents.heartbeat("agent-reviewer-01", busy=True, current_task_id="different-task")
            with self.assertRaises(PermissionError):
                run_once(
                    task_db, {"unassigned-task": lambda payload: {"ok": True}},
                    task_id=queued["id"], agent_id="agent-reviewer-01",
                    agent_registry_database=agent_db,
                )
            with TaskQueue(task_db) as queue:
                self.assertEqual(queue.get(queued["id"])["status"], "queued")

    def test_no_active_scope_means_no_implicit_global_ledger_write(self) -> None:
        self.assertFalse(record_current_model_usage(
            "test-provider", "test-model", {"total_tokens": 1}
        ))


if __name__ == "__main__":
    unittest.main()
