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
    def test_classify_is_advisory_and_records_project_usage_without_task_or_agent(self):
        from muse.usage_ledger import UsageLedger

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"

            def classify(_request):
                from muse.model_call_context import record_current_model_usage
                self.assertTrue(record_current_model_usage(
                    "lm-studio-local", "gemma-cli-model",
                    {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
                ))
                return {
                    "domain": "software", "capability": "coding",
                    "confidence": 0.95, "rationale": "A coding request.",
                }

            classifier = SimpleNamespace(classify=classify)
            args = SimpleNamespace(
                request="Build a Python helper", base_url="http://127.0.0.1:1234/v1",
                model="gemma-test", timeout=20,
            )
            output = io.StringIO()
            with (
                patch.object(chatgpt_bridge, "DEFAULT_DATABASE", database),
                patch.object(chatgpt_bridge, "LMStudioGemmaIntentClassifier", return_value=classifier),
                patch.dict("os.environ", {}, clear=True),
                redirect_stdout(output),
            ):
                code = chatgpt_bridge.command_classify(args)
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(result["advisory_only"])
            self.assertEqual(result["classification"]["capability"], "coding")
            self.assertEqual(result["execution"], "not_started")
            with UsageLedger(database.with_name("model_usage.sqlite3")) as ledger:
                events = ledger.list_events(limit=10)
            self.assertEqual(len(events), 1)
            self.assertIsNone(events[0]["task_id"])
            self.assertIsNone(events[0]["agent_id"])
            self.assertEqual(events[0]["project_id"], "muse")
            self.assertEqual(events[0]["input_tokens"], 13)
            self.assertEqual(events[0]["output_tokens"], 5)

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

    def test_submit_same_request_id_replays_existing_ticket_without_duplicate_events(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            args = SimpleNamespace(
                request="Research local agent orchestration", request_id="stable-replay-1",
                title=None, database=str(database), priority=50, estimated_seconds=900,
                base_url="http://127.0.0.1:1234/v1", model=None, timeout=20,
            )
            output = io.StringIO()
            with patch.object(chatgpt_bridge, "classify_request", side_effect=[
                {"domain": "research", "capability": "research", "confidence": 0.9,
                 "classifier_source": "deterministic_rules"},
                RuntimeError("classifier must not run for an existing request ID"),
            ]) as classifier, patch.dict("os.environ", {}, clear=True), redirect_stdout(output):
                first_code = chatgpt_bridge.command_submit(args)
                first = json.loads(output.getvalue())
                with TaskQueue(database) as queue:
                    approved = queue.approve(first["task_id"], approved_by="user")
                    self.assertEqual(approved["status"], "queued")
                    event_count_before_replay = len(queue.events(first["task_id"]))
                output.seek(0)
                output.truncate(0)
                second_code = chatgpt_bridge.command_submit(args)
                second = json.loads(output.getvalue())
                with TaskQueue(database) as queue:
                    claimed = queue.claim_next(worker_id="replay-worker", lease_seconds=60, task_id=first["task_id"])
                    self.assertIsNotNone(claimed)
                    queue.finish(first["task_id"], status="succeeded", result={"ok": True}, worker_id="replay-worker")
                    event_count_after_finish = len(queue.events(first["task_id"]))
                output.seek(0)
                output.truncate(0)
                third_code = chatgpt_bridge.command_submit(args)
                third = json.loads(output.getvalue())
                classifier.assert_called_once()
            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 0)
            self.assertEqual(third_code, 0)
            self.assertEqual(first["task_id"], second["task_id"])
            self.assertEqual(first["task_id"], third["task_id"])
            self.assertEqual(first["request_id"], args.request_id)
            self.assertEqual(second["request_id"], args.request_id)
            self.assertEqual(third["request_id"], args.request_id)
            self.assertEqual(second["status"], "queued")
            self.assertEqual(second["approval_required"], False)
            self.assertEqual(second["next_action"], "existing_ticket_reused")
            self.assertEqual(third["status"], "succeeded")
            self.assertEqual(third["approval_required"], False)
            self.assertEqual(third["next_action"], "existing_ticket_reused")
            self.assertEqual(first["intent_source"], "deterministic_rules")
            self.assertEqual(second["intent_source"], "deterministic_rules")
            self.assertEqual(third["intent_source"], "deterministic_rules")
            with TaskQueue(database) as queue:
                self.assertEqual(len(queue.events(first["task_id"])), event_count_after_finish)
                self.assertEqual(queue.snapshot(limit=10)["counts"]["succeeded"], 1)
                self.assertEqual(queue.get(first["task_id"])["payload"]["_result"], {"ok": True})

    def test_submit_rejects_request_id_reuse_for_different_request(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            args = SimpleNamespace(
                request="Research local agent orchestration", request_id="stable-conflict-1",
                title=None, database=str(database), priority=50, estimated_seconds=900,
                base_url="http://127.0.0.1:1234/v1", model=None, timeout=20,
            )
            changed = SimpleNamespace(**{**vars(args), "request": "Delete all local records"})
            output = io.StringIO()
            with patch.object(chatgpt_bridge, "classify_request", return_value={
                "domain": "research", "capability": "research", "confidence": 0.9,
            }), patch.dict("os.environ", {}, clear=True), redirect_stdout(output):
                self.assertEqual(chatgpt_bridge.command_submit(args), 0)
                output.seek(0)
                output.truncate(0)
                code = chatgpt_bridge.command_submit(changed)
            result = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(result["error"], "request_id_conflict")
            self.assertEqual(result["request_id"], args.request_id)
            self.assertEqual(result["execution"], "not_started")
            with TaskQueue(database) as queue:
                self.assertEqual(queue.snapshot(limit=10)["counts"]["waiting_approval"], 1)
                task = queue.get(result["task_id"])
                self.assertEqual(task["payload"]["goal"], args.request)

    def test_submit_rejects_blank_or_malformed_request_id_before_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            args = SimpleNamespace(
                request="Do safe research", request_id="  ", title=None, database=str(database),
                priority=50, estimated_seconds=900, base_url="http://127.0.0.1:1234/v1",
                model=None, timeout=20,
            )
            output = io.StringIO()
            with patch.object(chatgpt_bridge, "classify_request") as classify, redirect_stdout(output):
                code = chatgpt_bridge.command_submit(args)
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["error"], "invalid_request_id")
            classify.assert_not_called()
            self.assertFalse(database.exists())

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
