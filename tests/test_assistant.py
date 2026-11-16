from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.assistant import MuseAssistant
from muse.conversations import ConversationStore


class DeterministicModel:
    def __init__(self, response: str = "Hello from Muse") -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.response


class MuseAssistantTests(unittest.TestCase):
    def test_reply_uses_and_persists_thread_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "threads.sqlite3"
            model = DeterministicModel()
            with ConversationStore(database) as store:
                assistant = MuseAssistant(store, model, system_prompt="You are Muse.")
                first = assistant.reply("thread-a", "Hi")
                second = assistant.reply("thread-a", "Remember me?")
                self.assertEqual(first["reply"], "Hello from Muse")
                self.assertEqual(second["revision"], 2)
                self.assertEqual(len(model.calls[1]), 4)
                self.assertEqual(model.calls[1][1]["content"], "Hi")
                self.assertEqual(model.calls[1][2]["content"], "Hello from Muse")
                self.assertEqual(model.calls[1][3]["content"], "Remember me?")
                self.assertEqual(len(store.load("thread-a")["messages"]), 5)

    def test_failed_model_call_does_not_overwrite_checkpoint(self) -> None:
        class BrokenModel:
            def chat(self, messages: list[dict[str, str]]) -> str:
                raise RuntimeError("model offline")

        with tempfile.TemporaryDirectory() as directory:
            with ConversationStore(Path(directory) / "threads.sqlite3") as store:
                store.save("thread-a", [{"role": "user", "content": "previous"}])
                assistant = MuseAssistant(store, BrokenModel())
                with self.assertRaisesRegex(RuntimeError, "model offline"):
                    assistant.reply("thread-a", "new message")
                self.assertEqual(store.load("thread-a")["messages"], [{"role": "user", "content": "previous"}])


    def test_reply_uses_local_memories_as_ephemeral_context(self) -> None:
        from muse.memory import MemoryStore

        with tempfile.TemporaryDirectory() as directory:
            conversations_db = Path(directory) / "threads.sqlite3"
            memories_db = Path(directory) / "memory.sqlite3"
            model = DeterministicModel()
            with MemoryStore(memories_db) as memories:
                memories.add(
                    "User prefers concise Cantonese replies",
                    project="voice",
                    source="user-approved preference",
                    confidence=0.95,
                )
                with ConversationStore(conversations_db) as conversations:
                    assistant = MuseAssistant(
                        conversations, model, memory=memories,
                        system_prompt="You are Muse.",
                    )
                    assistant.reply("thread-a", "Please reply in Cantonese")
                    model_messages = model.calls[0]
                    saved_messages = conversations.load("thread-a")["messages"]

            self.assertTrue(any("User-approved local memories" in item["content"] for item in model_messages))
            self.assertTrue(any("concise Cantonese replies" in item["content"] for item in model_messages))
            self.assertFalse(any("concise Cantonese replies" in item["content"] for item in saved_messages))
            self.assertEqual([item["role"] for item in saved_messages], ["system", "user", "assistant"])

    def test_agent_track_is_never_injected_into_user_chat_context(self) -> None:
        from muse.memory import MemoryStore

        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memories:
                memories.add("User prefers calm concise replies", source="confirmed", track="user", kind="preference")
                memories.add("Internal agent workflow: run tests before release", source="runbook", track="agent", kind="skill")
                model = DeterministicModel()
                with ConversationStore(Path(directory) / "threads.sqlite3") as conversations:
                    assistant = MuseAssistant(conversations, model, memory=memories)
                    assistant.reply("thread", "Please reply with calm concise replies")
            context = "\\n".join(item["content"] for item in model.calls[0])
            self.assertIn("calm concise replies", context)
            self.assertNotIn("Internal agent workflow", context)



if __name__ == "__main__":
    unittest.main()
