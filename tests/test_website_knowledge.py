from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from muse.website_knowledge import WebsiteKnowledgeCatalog, normalize_public_url


class WebsiteKnowledgeCatalogTests(unittest.TestCase):
    def test_bookmarks_and_research_sources_are_persisted_with_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            with WebsiteKnowledgeCatalog(Path(d) / "sites.sqlite3") as catalog:
                self.assertEqual(catalog.ingest_bookmarks([{
                    "title": "易经资料库", "url": "https://example.org/yijing",
                    "read_url": "https://example.org/yijing/", "folder": "书签栏/文化",
                }]), 1)
                self.assertEqual(catalog.ingest_research_sources([
                    {"title": "反面研究", "url": "https://journal.example.org/study",
                     "query": "counter query", "chars": 2400, "relevance_score": 7,
                     "citation_id": "S1", "sha256": "a" * 64},
                ], counter_query="counter query", topic_key="topic:one"), 1)
                sites = catalog.list_sites()
                counter = next(item for item in sites if item["epistemic_role"] == "counter_candidate")
                self.assertEqual(counter["status"], "read")
                self.assertEqual(counter["metadata"]["evidence_quality"], "unassessed")
                self.assertEqual(catalog.stats()["counter_candidates"], 1)
                self.assertEqual(catalog.stats()["bookmarks"], 1)

    def test_unsafe_urls_are_rejected(self):
        for url in ("file:///etc/passwd", "http://127.0.0.1:8000/", "http://localhost/", "https://user:pass@example.org/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_public_url(url)

    def test_upsert_is_idempotent_and_retains_read_history(self):
        with tempfile.TemporaryDirectory() as d:
            with WebsiteKnowledgeCatalog(Path(d) / "sites.sqlite3") as catalog:
                catalog.upsert_site("Test", "https://example.org", status="candidate")
                catalog.upsert_site("Test updated", "https://example.org/", status="read")
                self.assertEqual(catalog.stats()["sites_total"], 1)
                self.assertEqual(catalog.list_sites()[0]["read_count"], 1)

    def test_domain_hints_are_unique_and_filterable(self):
        with tempfile.TemporaryDirectory() as d:
            with WebsiteKnowledgeCatalog(Path(d) / "sites.sqlite3") as catalog:
                catalog.upsert_site("One", "https://example.org/a", category="metaphysics")
                catalog.upsert_site("Two", "https://example.org/b", category="metaphysics")
                catalog.upsert_site("Three", "https://example.net", category="software_engineering")
                self.assertEqual(catalog.domain_hints(category="metaphysics"), ["example.org"])


if __name__ == "__main__":
    unittest.main()
