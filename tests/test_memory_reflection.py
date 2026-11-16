import tempfile
import unittest
from pathlib import Path

from muse.memory import MemoryStore
from muse.memory_reflection import MemoryReflection


class MemoryReflectionTests(unittest.TestCase):
    def test_related_memories_create_pending_not_active_insight(self):
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as store:
                a = store.add("Muse 项目采用任务调度和知识融合", project="muse", source="test")
                b = store.add("知识融合帮助 Muse 项目进行任务调度", project="muse", source="test")
                result = MemoryReflection(store).scan(project="muse", min_shared_terms=2)
                self.assertGreaterEqual(result["candidates_staged"], 1)
                self.assertTrue(result["approval_required"])
                self.assertEqual(len(store.list_pending()), result["candidates_staged"])
                self.assertEqual(len(store.search("任务调度", project="muse")), 2)

    def test_scan_does_not_mutate_existing_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as store:
                store.add("独立研究与安全审查", project="muse", source="test")
                before = store.list_memories(project="muse")
                MemoryReflection(store).scan(project="muse")
                after = store.list_memories(project="muse")
                self.assertEqual(before, after)


if __name__ == "__main__": unittest.main()
