from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.research_knowledge import ResearchKnowledgeStore
from muse.research_wiki_audit import audit_database


class ResearchWikiAuditTests(unittest.TestCase):
    def test_audit_is_read_only_and_reports_lint_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "knowledge.sqlite3"
            with ResearchKnowledgeStore(database) as store:
                idea = store.upsert_node("idea", "Unlinked idea")
                claim = store.upsert_node("claim", "Proposed claim", status="proposed")
                source = store.upsert_node("source", "Standalone source")
                before = store.stats().copy()

            result = audit_database(database)
            self.assertTrue(result["read_only"])
            self.assertEqual(result["summary"]["active_nodes"], 3)
            self.assertEqual(result["summary"]["lint_scope_isolated_nodes"], 2)
            self.assertEqual(result["summary"]["claims_without_evidence"], 1)
            self.assertEqual(result["queue_count"], 3)
            self.assertEqual(
                {item["id"] for item in result["queue"] if item["kind"] == "isolated_knowledge_node"},
                {idea["id"], claim["id"]},
            )
            with ResearchKnowledgeStore(database) as store:
                self.assertEqual(store.stats(), before)

    def test_audit_rejects_invalid_limit_and_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                audit_database(Path(directory) / "missing.sqlite3", limit=0)
            with self.assertRaises(FileNotFoundError):
                audit_database(Path(directory) / "missing.sqlite3")


if __name__ == "__main__":
    unittest.main()
