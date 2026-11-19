"""Tests for the claim-grounded narration orchestration boundary."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.narration_evidence_pipeline import persist_claim_grounded_narration_run
from muse.narration_sync import NarrationSyncStore

CLAIM = "a" * 32
EVIDENCE = "b" * 32


class FakeResearchStore:
    def __init__(self, dossier):
        self.dossier = dossier
        self.calls = []

    def claim_dossier(self, claim_id, *, evidence_limit):
        self.calls.append((claim_id, evidence_limit))
        if claim_id != self.dossier["claim_id"]:
            raise KeyError(claim_id)
        return self.dossier


class NarrationEvidencePipelineTests(unittest.TestCase):
    def test_fetches_dossier_builds_blocked_contract_and_persists_same_snapshot(self):
        dossier = {
            "claim_id": CLAIM,
            "evidence": [{
                "id": EVIDENCE,
                "direction": "support_search_candidate",
                "source_ref": "https://example.org/candidate",
                "latest_review": None,
                "reviews": [],
            }],
        }
        research = FakeResearchStore(dossier)
        narration = {
            "topic": "Muse evidence narration",
            "style": "yujie_canto",
            "segments": [{
                "index": 1,
                "text": "检索候选仍然不是审核结论。",
                "claim_ids": [CLAIM],
                "factual": True,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            with NarrationSyncStore(Path(directory) / "narration.sqlite3") as sync:
                result = persist_claim_grounded_narration_run(narration, research, sync)
                loaded = sync.get_run(result["run"]["id"])
        self.assertEqual(research.calls, [(CLAIM, 50)])
        self.assertEqual(result["claim_count"], 1)
        self.assertEqual(result["evidence_count"], 1)
        self.assertFalse(result["evidence_contract"]["publishable"])
        self.assertEqual(result["evidence_contract"]["segments"][0]["blocking_reasons"], ["no_verified_support"])
        self.assertEqual(loaded["evidence_contract"], result["evidence_contract"])

    def test_missing_dossier_and_missing_claim_link_fail_closed(self):
        research = FakeResearchStore({"claim_id": CLAIM, "evidence": []})
        narration = {
            "topic": "Missing evidence",
            "style": "yujie_canto",
            "segments": [{"text": "没有 Claim ID 的事实句。", "factual": True, "claim_ids": []}],
        }
        with tempfile.TemporaryDirectory() as directory:
            with NarrationSyncStore(Path(directory) / "narration.sqlite3") as sync:
                result = persist_claim_grounded_narration_run(narration, research, sync)
        self.assertFalse(result["evidence_contract"]["publishable"])
        self.assertIn("missing_claim_link", result["evidence_contract"]["segments"][0]["blocking_reasons"])

    def test_nonexistent_claim_node_is_treated_as_missing_evidence(self):
        research = FakeResearchStore({"claim_id": "c" * 32, "evidence": []})
        narration = {
            "topic": "Nonexistent claim",
            "style": "yujie_canto",
            "segments": [{"text": "不存在的 Claim 不能通过。", "factual": True, "claim_ids": [CLAIM]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            with NarrationSyncStore(Path(directory) / "narration.sqlite3") as sync:
                result = persist_claim_grounded_narration_run(narration, research, sync)
        self.assertFalse(result["evidence_contract"]["publishable"])
        self.assertIn("no_verified_support", result["evidence_contract"]["segments"][0]["blocking_reasons"])

    def test_reviewed_support_without_freshness_still_blocks(self):
        dossier = {
            "claim_id": CLAIM,
            "evidence": [{
                "id": EVIDENCE,
                "source_ref": "https://example.org/reviewed",
                "latest_review": {
                    "verdict": "supports",
                    "citations": [{
                        "source_url": "https://example.org/reviewed",
                        "source_sha256": "c" * 64,
                        "excerpt_sha256": "d" * 64,
                    }],
                },
            }],
        }
        narration = {
            "topic": "Freshness required",
            "style": "yujie_canto",
            "segments": [{"text": "已审核但未核验时效。", "claim_ids": [CLAIM], "factual": True}],
        }
        with tempfile.TemporaryDirectory() as directory:
            with NarrationSyncStore(Path(directory) / "narration.sqlite3") as sync:
                result = persist_claim_grounded_narration_run(
                    narration, FakeResearchStore(dossier), sync
                )
        self.assertFalse(result["evidence_contract"]["publishable"])
        self.assertIn(
            "supporting_source_freshness_unknown",
            result["evidence_contract"]["segments"][0]["blocking_reasons"],
        )


if __name__ == "__main__":
    unittest.main()
