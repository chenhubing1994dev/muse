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


if __name__ == "__main__":
    unittest.main()
