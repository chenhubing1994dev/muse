from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from muse.narration_evidence_pipeline import persist_claim_grounded_narration_run
from muse.narration_sync import NarrationSyncStore
from muse.research_fetch import SourceReadError
from muse.research_knowledge import ResearchKnowledgeStore

CLAIM = "a" * 32
URL = "https://evidence.example.org/report"


class FakeReader:
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error
        self.calls = []

    def read(self, url, *, max_bytes, max_chars):
        self.calls.append((url, max_bytes, max_chars))
        if self.error:
            raise self.error
        return self.page


def make_page(text, *, url=URL, truncated=False):
    return {
        "url": url,
        "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "truncated": truncated,
        "status_code": 200,        "fetched_at": "2026-11-19T12:00:00+00:00",
        "etag": '"snapshot-v1"',
        "last_modified": "Wed, 18 Nov 2026 10:00:00 GMT",
    }


class SourceFreshnessTests(unittest.TestCase):
    def _seed(self, store):
        claim = store.upsert_node("claim", "A verifiable claim")
        source = store.upsert_node("source", "Evidence report", external_ref=URL)
        evidence = store.record_claim_evidence(
            claim["id"], source["id"], direction="support_search_candidate",
        )
        text = "Verified source supports this claim."
        excerpt = "supports this claim"
        start = text.index(excerpt)
        anchor = {
            "verified": True, "source_url": URL,
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "excerpt": excerpt, "start_char": start, "end_char": start + len(excerpt),
            "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "fetched_at": "2026-11-18T10:00:00+00:00", "content_type": "text/plain",
        }
        store.record_claim_evidence_review(
            evidence["id"], reviewer_id="independent-reviewer", verdict="supports",
            rationale="The cited passage directly supports the claim.",
            citation_anchor=anchor,
        )
        return claim["id"], evidence["id"], text

    def _run(self, store, reader, claim_id, directory):
        narration = {
            "topic": "Source freshness test",
            "style": "yujie_canto",
            "segments": [{
                "index": 1, "text": "这项主张有对应的已审核来源。",
                "claim_ids": [claim_id], "factual": True,
            }],
        }
        with NarrationSyncStore(Path(directory) / "narration.sqlite3") as sync:
            return persist_claim_grounded_narration_run(
                narration, store, sync, source_reader=reader,
            )

    def test_unchanged_complete_source_and_exact_anchor_are_persisted_as_current(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim_id, evidence_id, text = self._seed(store)
                reader = FakeReader(make_page(text))
                result = self._run(store, reader, claim_id, directory)
                dossier = store.claim_dossier(claim_id)
                citation = dossier["evidence"][0]["latest_review"]["citations"][0]
                self.assertTrue(result["evidence_contract"]["publishable"])
                self.assertEqual(result["freshness_checks"]["current"], 1)
                self.assertEqual(citation["freshness"]["outcome"], "unchanged")
                self.assertIs(citation["freshness"]["source_current"], True)
                self.assertEqual(citation["freshness"]["fetched_at"], "2026-11-19T12:00:00+00:00")
                self.assertEqual(reader.calls, [(URL, 2_000_000, 20_000)])
                event = store.connection.execute(
                    "SELECT event_type FROM knowledge_events WHERE entity_type='task' AND entity_id=?",
                    (evidence_id,),
                ).fetchone()
                self.assertEqual(event["event_type"], "citation_freshness_checked")

    def test_changed_source_is_stale_and_does_not_rewrite_human_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim_id, _, _ = self._seed(store)
                reader = FakeReader(make_page("The report has changed and no longer contains the passage."))
                result = self._run(store, reader, claim_id, directory)
                evidence = store.claim_dossier(claim_id)["evidence"][0]
                self.assertFalse(result["evidence_contract"]["publishable"])
                self.assertIn("supporting_source_stale", result["evidence_contract"]["segments"][0]["blocking_reasons"])
                self.assertEqual(evidence["latest_verdict"], "supports")
                self.assertEqual(evidence["latest_review"]["citations"][0]["freshness"]["outcome"], "content_changed")

    def test_fetch_failure_and_truncated_snapshot_remain_unknown(self):
        for reader in (
            FakeReader(error=SourceReadError("network unavailable")),
            FakeReader(make_page("Verified source supports this claim.", truncated=True)),
        ):
            with self.subTest(reader=reader), tempfile.TemporaryDirectory() as directory:
                with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                    claim_id, _, _ = self._seed(store)
                    result = self._run(store, reader, claim_id, directory)
                    citation = store.claim_dossier(claim_id)["evidence"][0]["latest_review"]["citations"][0]
                    self.assertFalse(result["evidence_contract"]["publishable"])
                    self.assertIn("supporting_source_freshness_unknown", result["evidence_contract"]["segments"][0]["blocking_reasons"])
                    self.assertIsNone(citation["freshness"]["source_current"])
                    self.assertIn(citation["freshness"]["outcome"], {"fetch_failed", "incomplete_snapshot"})

    def test_http_error_status_is_preserved_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim_id, _, _ = self._seed(store)
                reader = FakeReader(error=SourceReadError("HTTP 404", status_code=404))
                result = self._run(store, reader, claim_id, directory)
                citation = store.claim_dossier(claim_id)["evidence"][0]["latest_review"]["citations"][0]
                self.assertFalse(result["evidence_contract"]["publishable"])
                self.assertIsNone(citation["freshness"]["source_current"])
                self.assertEqual(citation["freshness"]["http_status"], 404)

    def test_outcome_cannot_claim_a_conflicting_freshness_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim_id, evidence_id, _ = self._seed(store)
                citation = store.claim_dossier(claim_id)["evidence"][0]["latest_review"]["citations"][0]
                with self.assertRaisesRegex(ValueError, "matching fail-closed states"):
                    store.record_citation_freshness_check(
                        evidence_id, citation["id"], outcome="fetch_failed", source_current=False,
                    )

    def test_freshness_events_are_append_only(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim_id, evidence_id, text = self._seed(store)
                citation = store.claim_dossier(claim_id)["evidence"][0]["latest_review"]["citations"][0]
                page = make_page(text)
                store.record_citation_freshness_check(
                    evidence_id, citation["id"], outcome="unchanged", source_current=True,
                    final_url=URL, http_status=200, content_sha256=page["sha256"],
                    excerpt_sha256=citation["excerpt_sha256"],
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    with store.connection:
                        store.connection.execute(
                            "UPDATE knowledge_events SET event_type='tampered' WHERE entity_id=?",
                            (evidence_id,),
                        )


if __name__ == "__main__":
    unittest.main()
