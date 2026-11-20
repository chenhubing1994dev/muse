from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse import chatgpt_bridge
from muse.task_queue import TaskQueue
from muse.conversations import ConversationStore


class ChatGPTBridgeTests(unittest.TestCase):
    def test_classify_is_advisory_and_never_executes(self):
        classifier = SimpleNamespace(classify=lambda _request: {
            "domain": "software", "capability": "coding",
            "confidence": 0.95, "rationale": "A coding request.",
        })
        args = SimpleNamespace(
            request="Build a Python helper", base_url="http://127.0.0.1:1234/v1",
            model="gemma-test", timeout=20,
        )
        output = io.StringIO()
        with patch.object(chatgpt_bridge, "LMStudioGemmaIntentClassifier", return_value=classifier), \
             patch.dict("os.environ", {}, clear=True), redirect_stdout(output):
            code = chatgpt_bridge.command_classify(args)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result["advisory_only"])
        self.assertEqual(result["classification"]["capability"], "coding")
        self.assertEqual(result["execution"], "not_started")

    def test_submit_creates_waiting_approval_ticket_in_existing_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            args = SimpleNamespace(
                request="Research local agent orchestration",
                request_id="stable-request-1", title=None, database=str(database),
                priority=50, estimated_seconds=900, base_url="http://127.0.0.1:1234/v1",
                model=None, timeout=20,
            )
            output = io.StringIO()
            with patch.object(chatgpt_bridge, "classify_request", return_value={
                "domain": "research", "capability": "research", "confidence": 0.9,
            }), patch.dict("os.environ", {}, clear=True), redirect_stdout(output):
                code = chatgpt_bridge.command_submit(args)
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(result["approval_required"])
            self.assertEqual(result["status"], "waiting_approval")
            with TaskQueue(database) as queue:
                task = queue.get(result["task_id"])
                self.assertEqual(task["status"], "waiting_approval")
                self.assertFalse(task["payload"]["execution_authorized"])
                self.assertEqual(task["payload"]["source"], "chatgpt_webcodex_bridge")
                self.assertEqual(
                    [event["event_type"] for event in queue.events(task["id"])],
                    ["ticket.created", "approval.requested"],
                )
                approved = queue.approve(task["id"], approved_by="user")
                self.assertEqual(approved["status"], "queued")
                self.assertTrue(approved["payload"]["_approved"])

    def test_chatgpt_export_import_is_local_opt_in_bounded_and_excludes_hidden_reasoning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "conversations.json"
            database = root / "conversations.sqlite3"
            mapping = {
                "u1": {"id": "u1", "parent": None, "message": {
                    "id": "message-u1", "author": {"role": "user"}, "create_time": 1,
                    "content": {"content_type": "text", "parts": ["Creator question about autonomous research."]},
                }},
                "a_hidden": {"id": "a_hidden", "parent": "u1", "message": {
                    "id": "hidden-analysis", "author": {"role": "assistant"}, "create_time": 2,
                    "metadata": {"is_visually_hidden_from_conversation": True},
                    "content": {"content_type": "text", "parts": ["private hidden reasoning"]},
                }},
                "tool": {"id": "tool", "parent": "a_hidden", "message": {
                    "id": "tool-message", "author": {"role": "tool"}, "create_time": 3,
                    "content": {"content_type": "text", "parts": ["tool output"]},
                }},
                "a_final": {"id": "a_final", "parent": "tool", "message": {
                    "id": "message-a1", "author": {"role": "assistant"}, "create_time": 4,
                    "content": {"content_type": "text", "parts": ["Visible answer: test every hypothesis."]},
                }},
            }
            export.write_text(json.dumps([{
                "id": "thread-123", "title": "Creator research discussion",
                "mapping": mapping, "current_node": "a_final",
            }]), encoding="utf-8")
            args = SimpleNamespace(
                input=str(export), database=str(database), confirm_local_import=True,
                overwrite=False, max_threads=200, max_messages_per_thread=200,
                max_total_chars=2_000_000,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = chatgpt_bridge.command_import_chatgpt_export(args)
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["imported_threads"], 1)
            self.assertEqual(result["imported_messages"], 2)
            self.assertEqual(result["network_upload"], "none_local_only")
            self.assertNotIn("private hidden reasoning", output.getvalue())
            with ConversationStore(database) as conversations:
                thread = conversations.load("chatgpt-export:thread-123")
                self.assertEqual([item["role"] for item in thread["messages"]], ["user", "assistant"])
                self.assertNotIn("private hidden reasoning", json.dumps(thread["messages"]))
                self.assertTrue(all(item["epistemic_status"] == "creator_dialogue_inspiration_only" for item in thread["messages"]))
                self.assertTrue(all(item["is_evidence"] is False for item in thread["messages"]))
            output = io.StringIO()
            with redirect_stdout(output):
                second_code = chatgpt_bridge.command_import_chatgpt_export(args)
            second = json.loads(output.getvalue())
            self.assertEqual(second_code, 0)
            self.assertEqual(second["imported_threads"], 0)
            self.assertEqual(second["skipped_existing"], 1)

    def test_chatgpt_export_import_requires_explicit_local_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "conversations.json"
            export.write_text("[]", encoding="utf-8")
            database = root / "conversations.sqlite3"
            args = SimpleNamespace(
                input=str(export), database=str(database), confirm_local_import=False,
                overwrite=False, max_threads=200, max_messages_per_thread=200,
                max_total_chars=2_000_000,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = chatgpt_bridge.command_import_chatgpt_export(args)
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["error"], "explicit_local_import_confirmation_required")
            self.assertFalse(database.exists())

    def test_health_and_classification_reject_remote_endpoint(self):
        args = SimpleNamespace(
            request="Classify this", base_url="http://example.com/v1",
            model=None, timeout=20,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = chatgpt_bridge.command_classify(args)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])


    def test_task_status_is_read_only_and_does_not_expose_payload_or_event_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("safe title", {"goal": "PRIVATE REQUEST", "secret": "DO_NOT_PRINT", "approval_required": True}, idempotency_key="test:status")
                queue.record_event(task["id"], "ticket.created", actor="commander", payload={"secret": "EVENT_SECRET"})
            args = SimpleNamespace(task_id=task["id"], database=str(database), events_limit=10)
            output = io.StringIO()
            with redirect_stdout(output):
                code = chatgpt_bridge.command_task_status(args)
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["ticket"]["status"], "queued")
            self.assertTrue(result["ticket"]["approval_required"])
            self.assertEqual(result["execution"], "not_started")
            self.assertNotIn("PRIVATE REQUEST", output.getvalue())
            self.assertNotIn("DO_NOT_PRINT", output.getvalue())
            self.assertNotIn("EVENT_SECRET", output.getvalue())

    def test_task_status_includes_safe_workflow_stage_summary_without_checkpoint_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("workflow title", {"goal": "PRIVATE REQUEST"})
                queue.claim_next(worker_id="status-worker", task_id=task["id"], lease_seconds=600)
                queue.record_workflow_event(
                    task["id"], "workflow.bound", actor="commander",
                    payload={
                        "workflow_id": "workflow-safe-id",
                        "profile": {"name": "team"},
                        "contract": {"goal": "PRIVATE WORKFLOW GOAL"},
                        "step_ids": ["research-1"],
                    }, expected_revision=0, expected_worker_id="status-worker",
                )
                queue.record_workflow_event(
                    task["id"], "workflow.stage_changed", actor="status-worker",
                    payload={
                        "stage": "research", "step_id": "research-1", "attempt": 1,
                        "checkpoint": {"secret": "CHECKPOINT_SECRET"},
                        "next_action": "PRIVATE NEXT ACTION",
                    }, expected_revision=1, expected_worker_id="status-worker",
                )
            args = SimpleNamespace(task_id=task["id"], database=str(database), events_limit=10)
            output = io.StringIO()
            with redirect_stdout(output):
                code = chatgpt_bridge.command_task_status(args)
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["workflow"]["status"], "active")
            self.assertEqual(result["workflow"]["stage"], "research")
            self.assertEqual(result["workflow"]["step_id"], "research-1")
            self.assertEqual(result["workflow"]["attempt"], 1)
            self.assertEqual(result["workflow"]["event_count"], 2)
            self.assertEqual(result["workflow"]["last_event_type"], "workflow.stage_changed")
            self.assertNotIn("PRIVATE REQUEST", output.getvalue())
            self.assertNotIn("PRIVATE WORKFLOW GOAL", output.getvalue())
            self.assertNotIn("CHECKPOINT_SECRET", output.getvalue())
            self.assertNotIn("PRIVATE NEXT ACTION", output.getvalue())

    def test_task_list_uses_payload_free_existing_queue_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                queue.enqueue("visible title", {"secret": "DO_NOT_PRINT"}, idempotency_key="test:list")
            args = SimpleNamespace(database=str(database), limit=10)
            output = io.StringIO()
            with redirect_stdout(output):
                code = chatgpt_bridge.command_task_list(args)
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["counts"]["queued"], 1)
            self.assertNotIn("DO_NOT_PRINT", output.getvalue())

    def test_task_status_reports_missing_ticket_without_mutating_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            args = SimpleNamespace(task_id="missing-ticket", database=str(database), events_limit=10)
            output = io.StringIO()
            with redirect_stdout(output):
                code = chatgpt_bridge.command_task_status(args)
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["error"], "task_not_found")
            with TaskQueue(database) as queue:
                self.assertEqual(sum(queue.snapshot(limit=10)["counts"].values()), 0)


if __name__ == "__main__":
    unittest.main()
