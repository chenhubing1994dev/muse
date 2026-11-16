import json
from pathlib import Path
import tempfile
import unittest

from muse.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_add_and_get_preserves_project_source_confidence_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                saved = memory.add(
                    "User prefers concise Cantonese replies",
                    project="voice",
                    source="conversation:2026-11-02",
                    confidence=0.9,
                )
                restored = memory.get(saved["id"])

                self.assertEqual(restored["content"], "User prefers concise Cantonese replies")
                self.assertEqual(restored["project"], "voice")
                self.assertEqual(restored["source"], "conversation:2026-11-02")
                self.assertEqual(restored["confidence"], 0.9)
                self.assertTrue(restored["created_at"])
                self.assertEqual(restored["revision"], 1)

    def test_memory_supports_typed_user_and_agent_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                user_item = memory.add("User prefers concise answers", project="profile", source="confirmed",
                                       kind="preference", track="user")
                agent_item = memory.add("Use staged validation before release", project="Muse", source="runbook",
                                        kind="skill", track="agent")
                self.assertEqual(user_item["kind"], "preference")
                self.assertEqual(user_item["track"], "user")
                self.assertEqual(agent_item["kind"], "skill")
                self.assertEqual(agent_item["track"], "agent")
                self.assertEqual([item["id"] for item in memory.search("validation", track="agent")], [agent_item["id"]])
                self.assertEqual(memory.search("validation", track="user"), [])
                self.assertEqual([item["id"] for item in memory.list_memories(track="user")], [user_item["id"]])

    def test_memory_kind_and_track_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                with self.assertRaisesRegex(ValueError, "kind"):
                    memory.add("Bad kind", source="test", kind="unknown")
                with self.assertRaisesRegex(ValueError, "track"):
                    memory.add("Bad track", source="test", track="shared")

    def test_approved_candidate_preserves_kind_and_track(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                candidate = memory.propose("Keep this project procedure", project="Muse", source="chat",
                                           kind="case", track="agent")
                approved = memory.approve_candidate(candidate["id"], approved_by="user")
                saved = memory.get(approved["memory_id"])
                self.assertEqual(saved["kind"], "case")
                self.assertEqual(saved["track"], "agent")

    def test_revision_history_preserves_kind_and_track_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                saved = memory.add("An agent procedure", project="Muse", source="runbook", kind="skill", track="agent")
                memory.revise(saved["id"], "A refined agent procedure", kind="case")
                history = memory.history(saved["id"])
                self.assertEqual([item["kind"] for item in history], ["skill", "case"])
                self.assertEqual([item["track"] for item in history], ["agent", "agent"])

    def test_legacy_database_migrates_existing_rows_to_user_fact(self) -> None:
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE memories (id INTEGER PRIMARY KEY, project TEXT NOT NULL, content TEXT NOT NULL,
                    source TEXT NOT NULL, confidence REAL NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, deleted_at TEXT);
                CREATE TABLE memory_revisions (id INTEGER PRIMARY KEY, memory_id INTEGER NOT NULL,
                    revision INTEGER NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
                    confidence REAL NOT NULL, changed_at TEXT NOT NULL);
                CREATE TABLE memory_candidates (id INTEGER PRIMARY KEY, project TEXT NOT NULL,
                    content TEXT NOT NULL, source TEXT NOT NULL, confidence REAL NOT NULL,
                    status TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                    reviewed_at TEXT, reviewed_by TEXT, review_reason TEXT, memory_id INTEGER);
                INSERT INTO memories VALUES (1, 'legacy', 'Old memory', 'chat', 0.8, '2026-01-01', '2026-01-01', 1, NULL);
            """)
            connection.commit()
            connection.close()
            with MemoryStore(database) as memory:
                item = memory.get(1)
                self.assertEqual(item["kind"], "fact")
                self.assertEqual(item["track"], "user")

    def test_search_matches_keywords_and_can_filter_by_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                memory.add("Muse uses SQLite for durable memory", project="Muse", source="plan")
                memory.add("User prefers Cantonese voice", project="voice", source="chat")

                results = memory.search("sqlite durable", project="Muse")

                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["project"], "Muse")

    def test_search_uses_partial_terms_and_ranks_more_matches_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                memory.add("Cantonese voice preference", project="voice", source="chat")
                memory.add("Cantonese concise replies preference", project="voice", source="confirmed")

                results = memory.search("Cantonese concise")

                self.assertEqual([item["source"] for item in results], ["confirmed", "chat"])

    def test_search_supports_chinese_bigrams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                memory.add("使用者偏好以粵語回覆", project="voice", source="confirmed")

                results = memory.search("請用粵語回覆")

                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["project"], "voice")

    def test_revise_updates_content_and_increments_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                saved = memory.add("Old preference", project="voice", source="chat")

                revised = memory.revise(saved["id"], "Updated preference", confidence=0.8)

                self.assertEqual(revised["content"], "Updated preference")
                self.assertEqual(revised["confidence"], 0.8)
                self.assertEqual(revised["revision"], 2)
                self.assertEqual(revised["source"], "chat")

    def test_revision_history_preserves_previous_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                saved = memory.add("First version", project="Muse", source="chat")
                memory.revise(saved["id"], "Second version")
                memory.revise(saved["id"], "Third version", confidence=0.7)

                history = memory.history(saved["id"])

                self.assertEqual([item["content"] for item in history], ["First version", "Second version", "Third version"])
                self.assertEqual([item["revision"] for item in history], [1, 2, 3])
    def test_memory_survives_closing_and_reopening_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with MemoryStore(database) as memory:
                saved = memory.add("Survives restart", project="Muse", source="test")
                memory_id = saved["id"]

            with MemoryStore(database) as reopened:
                restored = reopened.get(memory_id)
                self.assertIsNotNone(restored)
                self.assertEqual(restored["content"], "Survives restart")
                self.assertEqual([item["content"] for item in reopened.history(memory_id)], ["Survives restart"])

    def test_delete_hides_memory_from_get_search_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                saved = memory.add("Temporary fact", project="Muse", source="test")

                self.assertTrue(memory.delete(saved["id"]))
                self.assertEqual([item["content"] for item in memory.history(saved["id"])], ["Temporary fact"])
                self.assertIsNone(memory.get(saved["id"]))
                self.assertEqual(memory.search("Temporary"), [])
                self.assertEqual(json.loads(memory.export_json()), [])

    def test_export_contains_active_records_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                memory.add("Keep this fact", project="Muse", source="decision")

                exported = json.loads(memory.export_json())

                self.assertEqual(len(exported), 1)
                self.assertEqual(exported[0]["content"], "Keep this fact")
                self.assertEqual(exported[0]["project"], "Muse")


if __name__ == "__main__":
    unittest.main()
