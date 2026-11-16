from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from muse.local_planner import LocalModelCommanderPlanner
from muse.memory import MemoryStore


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages = None

    def chat(self, messages, *, temperature, max_tokens):
        self.messages = messages
        return self.content


class LocalModelCommanderPlannerTests(unittest.TestCase):
    def test_uses_local_model_and_only_user_track_memory_with_symbolic_lenses(self) -> None:
        steps = {"steps": [
            {"id": "research", "role": "researcher", "objective": "research", "depends_on": [], "required_capabilities": ["research"]},
            {"id": "build", "role": "developer", "objective": "build", "depends_on": ["research"], "required_capabilities": ["coding"]},
        ]}
        client = FakeClient(json.dumps(steps))
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "memory.sqlite3"
            with MemoryStore(db) as memory:
                memory.add("User wants Muse to be a strict Commander", project="muse", source="user", track="user")
                memory.add("Private agent execution trace", project="muse", source="agent", track="agent")
            planner = LocalModelCommanderPlanner(
                memory_database=db, project="muse", client=client
            )
            result = planner("Improve Muse Commander")
            self.assertEqual(result, steps)
            prompt = client.messages[1]["content"]
            self.assertIn("User wants Muse to be a strict Commander", prompt)
            self.assertNotIn("Private agent execution trace", prompt)
            self.assertIn("symbolic_lenses", prompt)
            self.assertIn("大六壬", prompt)
            self.assertIn('"researcher": ["research"]', client.messages[0]["content"])
            self.assertIn("必须恰好一个 independent reviewer", client.messages[0]["content"])
            self.assertIn("纯只读研究任务优先 researcher", client.messages[0]["content"])

    def test_default_timeout_allows_a_large_local_model_to_respond(self) -> None:
        planner = LocalModelCommanderPlanner()
        self.assertEqual(planner.client.timeout, 90.0)

    def test_timeout_can_be_configured_with_a_bounded_environment_value(self) -> None:
        with patch.dict("os.environ", {"MUSE_LMSTUDIO_TIMEOUT_SECONDS": "120"}):
            planner = LocalModelCommanderPlanner()
        self.assertEqual(planner.client.timeout, 120.0)

        with patch.dict("os.environ", {"MUSE_LMSTUDIO_TIMEOUT_SECONDS": "181"}):
            with self.assertRaisesRegex(ValueError, "between 1 and 180"):
                LocalModelCommanderPlanner()

    def test_rejects_non_loopback_and_credential_bearing_endpoints(self) -> None:
        for endpoint in (
            "https://example.com/v1",
            "http://192.168.1.20:1234/v1",
            "http://user:secret@127.0.0.1:1234/v1",
            "http://127.0.0.1:1234/v1?token=secret",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    LocalModelCommanderPlanner(base_url=endpoint, client=FakeClient("{}"))

    def test_rejects_invalid_model_json(self) -> None:
        planner = LocalModelCommanderPlanner(client=FakeClient("not-json"))
        with self.assertRaises(json.JSONDecodeError):
            planner("goal")

    def test_rejects_non_plan_json(self) -> None:
        planner = LocalModelCommanderPlanner(client=FakeClient('{"result":"no steps"}'))
        with self.assertRaisesRegex(ValueError, "containing steps"):
            planner("goal")


if __name__ == "__main__":
    unittest.main()
