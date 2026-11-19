from __future__ import annotations

import tempfile
import ipaddress
import unittest
from unittest.mock import patch
from pathlib import Path

from muse.research_knowledge import ResearchKnowledgeStore
from muse.research_wiki_review_record import record_review


class ResearchWikiReviewWriterTests(unittest.TestCase):
    def _database_with_evidence(self, database: Path):
        with ResearchKnowledgeStore(database) as store:
            claim = store.upsert_node(
                "claim", "Reviewable claim", metadata={"author_id": "claim-author"}
            )
            source = store.upsert_node(
                "source", "Source to inspect", external_ref="https://example.org/source"
            )
            evidence = store.record_claim_evidence(
                claim["id"], source["id"], direction="context_candidate"
            )
            return claim, evidence

    def test_requires_explicit_write_opt_in_and_records_append_only_review(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "knowledge.sqlite3"
            claim, evidence = self._database_with_evidence(database)
            with self.assertRaises(PermissionError):
                record_review(
                    database, evidence_id=evidence["id"], reviewer_id="reviewer",
                    verdict="mentions", quality="moderate", independence="unclear",
                    rationale="Read source abstract; it mentions the topic only.",
                )
            result = record_review(
                database, evidence_id=evidence["id"], reviewer_id="reviewer",
                verdict="mentions", quality="moderate", independence="unclear",
                rationale="Read source abstract; it mentions the topic only.",
                confirm_write=True,
            )
            self.assertTrue(result["append_only"])
            self.assertEqual(result["review"]["verdict"], "mentions")
            with ResearchKnowledgeStore(database) as store:
                dossier = store.claim_dossier(claim["id"])
                self.assertEqual(dossier["claim_status"], "proposed")
                self.assertEqual(len(dossier["evidence"][0]["reviews"]), 1)

    def test_rejects_claim_author_self_review_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "knowledge.sqlite3"
            _, evidence = self._database_with_evidence(database)
            with self.assertRaisesRegex(ValueError, "unsupported verdict"):
                record_review(
                    database, evidence_id=evidence["id"], reviewer_id="reviewer",
                    verdict="probably_true", quality="high", independence="independent",
                    rationale="Read the original source.", confirm_write=True,
                )
            with self.assertRaisesRegex(ValueError, "independent from claim author"):
                record_review(
                    database, evidence_id=evidence["id"], reviewer_id="claim-author",
                    verdict="supports", quality="high", independence="independent",
                    rationale="Read the original source and verified the result.",
                    confirm_write=True,
                )


    @patch("muse.research_fetch._resolve_public_addresses", return_value=[ipaddress.ip_address("93.184.216.34")])
    def test_supports_refutes_require_verified_snapshot_and_store_citation(self, _resolve_addresses):
        import hashlib
        from muse.research_fetch import ResearchEvidenceStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge_db = root / "knowledge.sqlite3"
            evidence_db = root / "evidence.sqlite3"
            claim, evidence = self._database_with_evidence(knowledge_db)
            body = "Intro. Exact verified finding from the primary source. End."
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            with ResearchEvidenceStore(evidence_db) as evidence_store:
                evidence_store.save({
                    "url": "https://example.org/source",
                    "content_type": "text/html",
                    "text": body,
                    "sha256": digest,
                    "fetched_at": "2026-11-18T00:00:00+00:00",
                    "truncated": False,
                })
            with self.assertRaisesRegex(ValueError, "require --source-sha256"):
                record_review(
                    knowledge_db, evidence_id=evidence["id"], reviewer_id="reviewer",
                    verdict="supports", quality="high", independence="independent",
                    rationale="The exact source passage establishes the claim.",
                    confirm_write=True, evidence_database=evidence_db,
                )
            result = record_review(
                knowledge_db, evidence_id=evidence["id"], reviewer_id="reviewer",
                verdict="supports", quality="high", independence="independent",
                rationale="The exact source passage establishes the claim.",
                confirm_write=True, evidence_database=evidence_db,
                source_sha256=digest, excerpt="Exact verified finding from the primary source.",
            )
            self.assertTrue(result["review"]["citation_attached"])
            with ResearchKnowledgeStore(knowledge_db) as store:
                dossier = store.claim_dossier(claim["id"])
                review = dossier["evidence"][0]["reviews"][0]
                self.assertEqual(len(review["citations"]), 1)
                citation = review["citations"][0]
                self.assertEqual(citation["source_sha256"], digest)
                self.assertEqual(citation["excerpt"], "Exact verified finding from the primary source.")
                self.assertEqual(dossier["claim_status"], "proposed")

    @patch("muse.research_fetch._resolve_public_addresses", return_value=[ipaddress.ip_address("93.184.216.34")])
    def test_rejects_citation_from_another_source(self, _resolve_addresses):
        import hashlib
        from muse.research_fetch import ResearchEvidenceStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge_db = root / "knowledge.sqlite3"
            evidence_db = root / "evidence.sqlite3"
            _, evidence = self._database_with_evidence(knowledge_db)
            body = "A unique statement in a different source."
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            with ResearchEvidenceStore(evidence_db) as evidence_store:
                evidence_store.save({
                    "url": "https://example.org/other",
                    "content_type": "text/plain", "text": body, "sha256": digest,
                    "fetched_at": "2026-11-18T00:00:00+00:00", "truncated": False,
                })
            with self.assertRaisesRegex(ValueError, "not found"):
                record_review(
                    knowledge_db, evidence_id=evidence["id"], reviewer_id="reviewer",
                    verdict="refutes", quality="moderate", independence="independent",
                    rationale="Counter-evidence was inspected.", confirm_write=True,
                    evidence_database=evidence_db, source_sha256=digest,
                    excerpt="A unique statement in a different source.",
                )

if __name__ == "__main__":
    unittest.main()
