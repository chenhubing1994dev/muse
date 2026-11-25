from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from muse.research_knowledge import ResearchKnowledgeStore
from muse.research_state_machine import ResearchStateError, ResearchStateMachine


class ResearchStateMachineTests(unittest.TestCase):
    def _claim(self, root: Path) -> tuple[Path, str]:
        path = root / "knowledge.sqlite3"
        with ResearchKnowledgeStore(path) as store:
            claim = store.upsert_node(
                "claim", "A falsifiable hypothesis", summary="A test claim",
                status="proposed",
                metadata={
                    "content_sha256": hashlib.sha256(b"claim").hexdigest(),
                    "review_state": "not_researched",
                    "epistemic_status": "creator_inspired_hypothesis_only",
                    "allow_external_research": True,
                },
            )
        return path, claim["id"]

    def test_illegal_transition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, claim_id = self._claim(root)
            machine = ResearchStateMachine(path, handoff_dir=root / "handoff")
            with self.assertRaises(ResearchStateError):
                machine.transition(claim_id, "evaluated")
            self.assertEqual(machine.get_status(claim_id), "not_researched")

    def test_failed_state_requires_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, claim_id = self._claim(root)
            machine = ResearchStateMachine(path, handoff_dir=root / "handoff")
            machine.transition(claim_id, "queued")
            machine.record_failure(claim_id, reason="Search provider unavailable", error_code="search_unavailable")
            self.assertEqual(machine.get_status(claim_id), "failed")
            with self.assertRaises(ResearchStateError):
                machine.transition(claim_id, "queued")
            machine.transition(claim_id, "queued", allow_retry=True, metadata_updates={"research_round": 2})
            self.assertEqual(machine.get_status(claim_id), "queued")

    def test_insufficient_evidence_forces_inconclusive_and_keeps_claim_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, claim_id = self._claim(root)
            machine = ResearchStateMachine(path, handoff_dir=root / "handoff")
            machine.transition(claim_id, "queued")
            machine.transition(claim_id, "researching")
            machine.transition(claim_id, "evidence_collected")
            result = machine.evaluate_claim(claim_id)
            self.assertEqual(result["conclusion"], "inconclusive")
            self.assertIn("support_or_counter_search_sources_missing", result["reasons"])
            with ResearchKnowledgeStore(path) as store:
                claim = store.get_node(claim_id)
                self.assertEqual(claim["status"], "proposed")
                self.assertEqual(claim["metadata"]["research_status"], "evaluated")
                self.assertEqual(claim["metadata"]["research_assessment"]["conclusion"], "inconclusive")

    def test_supported_requires_both_directions_two_independent_verified_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, claim_id = self._claim(root)
            machine = ResearchStateMachine(path, handoff_dir=root / "handoff")
            machine.transition(claim_id, "queued")
            machine.transition(claim_id, "researching")

            with ResearchKnowledgeStore(path) as store:
                evidence_ids = []
                source_specs = [
                    ("https://alpha.example/source", "support_search_candidate", "supports"),
                    ("https://beta.example/source", "support_search_candidate", "supports"),
                    ("https://counter.example/source", "counter_search_candidate", "insufficient"),
                ]
                for index, (url, direction, verdict) in enumerate(source_specs):
                    source_hash = hashlib.sha256(f"source-{index}".encode()).hexdigest()
                    source = store.upsert_node(
                        "source", f"Source {index}", summary="A stored source excerpt",
                        external_ref=url,
                        metadata={
                            "content_hash": source_hash,
                            "chars": 800,
                            "source_identity": {"hostname": url.split("/")[2], "registrable_domain": url.split("/")[2]},
                        },
                    )
                    evidence = store.record_claim_evidence(
                        claim_id, source["id"], direction=direction,
                        evidence_type="web_source_extract", search_query=f"query-{index}",
                    )
                    evidence_ids.append((evidence["id"], url, source_hash, verdict))
                for index, (evidence_id, url, source_hash, verdict) in enumerate(evidence_ids):
                    excerpt = f"Verified source excerpt number {index} with relevant context."
                    citation = {
                        "verified": True,
                        "source_url": url,
                        "source_sha256": source_hash,
                        "excerpt": excerpt,
                        "start_char": 0,
                        "end_char": len(excerpt),
                        "excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                        "fetched_at": "2026-11-25T00:00:00+00:00",
                        "content_type": "text/html",
                    }
                    store.record_claim_evidence_review(
                        evidence_id,
                        reviewer_id="independent-reviewer",
                        verdict=verdict,
                        rationale="Review based on the stored source excerpt.",
                        quality="high",
                        independence="independent",
                        citation_anchor=citation,
                    )

            machine.transition(claim_id, "evidence_collected")
            result = machine.evaluate_claim(claim_id)
            self.assertEqual(result["conclusion"], "supported")
            self.assertEqual(result["verified_support_review_count"], 2)
            self.assertEqual(result["counter_search_source_count"], 1)
            with ResearchKnowledgeStore(path) as store:
                self.assertEqual(store.get_node(claim_id)["status"], "proposed")


if __name__ == "__main__":
    unittest.main()
