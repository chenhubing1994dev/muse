import os
import unittest
from unittest.mock import patch

from muse.chatanywhere_smoke import run_smoke_test


class FakeRuntime:
    instances = []
    result = {
        "status": "succeeded",
        "task_id": "task-test",
        "agent_id": "agent-test",
        "result": {"success": True, "output": "MUSE_RUFLO_CHATANYWHERE_OK"},
    }

    def __init__(self, *args, **kwargs):
        self.init_args = args
        self.init_kwargs = kwargs
        self.enqueued = None
        self.dispatched = None
        self.env_seen = None
        type(self).instances.append(self)

    def enqueue(self, title, payload, *, priority=50):
        self.enqueued = (title, payload, priority)
        return {"id": "task-test"}

    def dispatch_once(self, *, task_id, approved=False):
        self.dispatched = (task_id, approved)
        self.env_seen = {
            "CHATANYWHERE_API_KEY": os.environ.get("CHATANYWHERE_API_KEY"),
            "MUSE_RUFLO_PROVIDER": os.environ.get("MUSE_RUFLO_PROVIDER"),
            "CHATANYWHERE_MODEL": os.environ.get("CHATANYWHERE_MODEL"),
        }
        return type(self).result


class ChatAnywhereSmokeTests(unittest.TestCase):
    def setUp(self):
        FakeRuntime.instances.clear()
        FakeRuntime.result = {
            "status": "succeeded",
            "task_id": "task-test",
            "agent_id": "agent-test",
            "result": {"success": True, "output": "MUSE_RUFLO_CHATANYWHERE_OK"},
        }

    def test_smoke_test_runs_one_approved_ticket_and_restores_secret_environment(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "keep-existing"}, clear=True):
            result = run_smoke_test("chatanywhere-test-secret", runtime_factory=FakeRuntime)
            self.assertEqual(os.environ, {"ANTHROPIC_API_KEY": "keep-existing"})
        runtime = FakeRuntime.instances[0]
        self.assertEqual(runtime.dispatched, ("task-test", True))
        self.assertEqual(runtime.env_seen, {
            "CHATANYWHERE_API_KEY": "chatanywhere-test-secret",
            "MUSE_RUFLO_PROVIDER": "chatanywhere",
            "CHATANYWHERE_MODEL": "gpt-4o-mini",
        })
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["agent_id"], "agent-test")
        self.assertEqual(result["output"], "MUSE_RUFLO_CHATANYWHERE_OK")

    def test_smoke_test_rejects_empty_key_before_creating_runtime(self):
        with self.assertRaisesRegex(ValueError, "API key is required"):
            run_smoke_test("  ", runtime_factory=FakeRuntime)
        self.assertEqual(FakeRuntime.instances, [])

    def test_smoke_test_does_not_report_success_when_agent_execution_fails(self):
        FakeRuntime.result = {"status": "failed", "task_id": "task-test"}
        with self.assertRaisesRegex(RuntimeError, "did not complete successfully"):
            run_smoke_test("chatanywhere-test-secret", runtime_factory=FakeRuntime)


    def test_smoke_test_rejects_successful_transport_without_expected_agent_reply(self):
        FakeRuntime.result = {
            "status": "succeeded",
            "task_id": "task-test",
            "agent_id": "agent-test",
            "result": {"success": True, "output": "unrelated response"},
        }
        with self.assertRaisesRegex(RuntimeError, "did not return the expected marker"):
            run_smoke_test("chatanywhere-test-secret", runtime_factory=FakeRuntime)

if __name__ == "__main__":
    unittest.main()
