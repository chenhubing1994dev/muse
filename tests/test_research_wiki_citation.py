from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from muse.research_fetch import ResearchEvidenceStore
from muse.research_wiki_citation import verify_citation


class ResearchWikiCitationTests(unittest.TestCase):
    def _fixture(self, directory: str, *, truncated: bool = False):
        database = Path(directory) / "evidence.sqlite3"
        text = "Opening context. The source directly states the tested finding. Closing context."
        page = {
            "url": "https://93.184.216.34/citation-test",
            "content_type": "text/plain",
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "fetched_at": "2026-11-18T00:00:00+00:00",
            "truncated": truncated,
        }
        with ResearchEvidenceStore(database) as store:
            store.save(page)
        return database, page

    def test_verifies_exact_excerpt_and_character_offsets_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database, page = self._fixture(directory)
            before = database.read_bytes()
            result = verify_citation(
                database, source_url=page["url"], expected_sha256=page["sha256"],
                excerpt="The source directly states the tested finding.",
            )
            self.assertTrue(result["verified"])
            self.assertEqual(result["start_char"], page["text"].index("The source"))
            self.assertEqual(result["end_char"] - result["start_char"], len(result["excerpt"]))
            self.assertTrue(result["read_only"])
            self.assertEqual(before, database.read_bytes())

    def test_rejects_wrong_hash_missing_excerpt_and_duplicate_excerpt(self):
        with tempfile.TemporaryDirectory() as directory:
            database, page = self._fixture(directory)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                verify_citation(database, source_url=page["url"], expected_sha256="0" * 64, excerpt="Opening context.")
            with self.assertRaisesRegex(ValueError, "not found"):
                verify_citation(database, source_url=page["url"], expected_sha256=page["sha256"], excerpt="not present")
            duplicate = "repeat marker. middle. repeat marker."
            duplicate_page = {**page, "url": "https://93.184.216.34/duplicate",
                              "text": duplicate, "sha256": hashlib.sha256(duplicate.encode()).hexdigest()}
            with ResearchEvidenceStore(database) as store:
                store.save(duplicate_page)
            with self.assertRaisesRegex(ValueError, "more than once"):
                verify_citation(database, source_url=duplicate_page["url"],
                                expected_sha256=duplicate_page["sha256"], excerpt="repeat marker.")

    def test_rejects_truncated_or_missing_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            database, page = self._fixture(directory, truncated=True)
            with self.assertRaisesRegex(ValueError, "truncated"):
                verify_citation(database, source_url=page["url"],
                                expected_sha256=page["sha256"], excerpt="Opening context.")
            with self.assertRaisesRegex(ValueError, "not found"):
                verify_citation(database, source_url="https://93.184.216.34/missing",
                                expected_sha256=page["sha256"], excerpt="Opening context.")


if __name__ == "__main__":
    unittest.main()
