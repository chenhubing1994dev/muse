from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.chat_cli import run_turn


class FixedModel:
    def chat(self, messages: list[dict[str, str]]) -> str:
        return f"received {messages[-1]['content']}"


class ChatCliTests(unittest.TestCase):
    def test_run_turn_persists_context_between_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "conversations.sqlite3"
            first = run_turn(database, "main", "first", FixedModel())
            second = run_turn(database, "main", "second", FixedModel())
            self.assertEqual(first["reply"], "received first")
            self.assertEqual(second["revision"], 2)
            self.assertEqual(second["messages"][1]["content"], "first")
            self.assertEqual(second["messages"][-2]["content"], "second")
            self.assertEqual(second["messages"][-1]["content"], "received second")

    def test_run_turn_retrieves_existing_local_memories_without_persisting_them(self) -> None:
        from muse.memory import MemoryStore

        class RecordingModel:
            def __init__(self) -> None:
                self.messages: list[dict[str, str]] = []

            def chat(self, messages: list[dict[str, str]]) -> str:
                self.messages = messages
                return "Cantonese reply"

        with tempfile.TemporaryDirectory() as directory:
            conversations_db = Path(directory) / "conversations.sqlite3"
            memories_db = Path(directory) / "memory.sqlite3"
            with MemoryStore(memories_db) as memory:
                memory.add("User prefers concise Cantonese replies", project="voice", source="confirmed")
            model = RecordingModel()
            result = run_turn(conversations_db, "main", "Please reply in Cantonese", model, memory_database=memories_db)
            self.assertTrue(any("concise Cantonese replies" in item["content"] for item in model.messages))
            self.assertFalse(any("concise Cantonese replies" in item["content"] for item in result["messages"]))

    def test_run_turn_only_extracts_candidates_when_explicitly_enabled(self) -> None:
        from muse.memory import MemoryStore

        class TwoStageModel:
            def __init__(self) -> None:
                self.calls = 0

            def chat(self, messages: list[dict[str, str]]) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "Normal reply"
                return json.dumps({"candidates": [
                    {"content": "User prefers concise Cantonese", "project": "voice", "confidence": 0.9}
                ]})

        with tempfile.TemporaryDirectory() as directory:
            conversations_db = Path(directory) / "conversations.sqlite3"
            memories_db = Path(directory) / "memory.sqlite3"
            result = run_turn(
                conversations_db, "thread-a", "I prefer concise Cantonese", TwoStageModel(),
                memory_database=memories_db, propose_memories=True,
            )
            self.assertEqual(result["memory_candidates_created"], 1)
            self.assertEqual(result["memory_extraction_status"], "completed")
            with MemoryStore(memories_db) as memory:
                self.assertEqual(len(memory.list_pending()), 1)
                self.assertEqual(memory.search("Cantonese"), [])


if __name__ == "__main__":
    unittest.main()