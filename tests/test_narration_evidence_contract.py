"""Fail-closed claim/evidence contract tests for narration segments."""
from __future__ import annotations

import unittest

from muse.narration_evidence_contract import build_narration_evidence_contract

CLAIM = "a" * 32
SUPPORT = "b" * 32
REFUTE = "c" * 32


def _support(**overrides):
    record = {
        "evidence_id": SUPPORT,
        "verdict": "supports",
        "citation_anchor_verified": True,
        "source_current": True,
        "source_ref": "https://example.org/source",
    }
    return {**record, **overrides}


class NarrationEvidenceContractTests(unittest.TestCase):
    def test_verified_current_support_allows_factual_segment(self):
        result = build_narration_evidence_contract(
            [{"index": 1, "text": "可核验的事实句。", "claim_ids": [CLAIM], "start_seconds": 1, "end_seconds": 3}],
            {CLAIM: [_support()]},
        )
        self.assertTrue(result["publishable"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["segments"][0]["timecode"], {"start_seconds": 1.0, "end_seconds": 3.0})
        self.assertEqual(len(result["segments"][0]["sentence_sha256"]), 64)

    def test_unreviewed_candidate_never_counts_as_support(self):
        candidate = _support(verdict="unreviewed")
        result = build_narration_evidence_contract(
            [{"text": "候选证据尚未审核。", "claim_ids": [CLAIM]}], {CLAIM: [candidate]}
        )
        self.assertFalse(result["publishable"])
        self.assertIn("no_verified_support", result["segments"][0]["blocking_reasons"])

    def test_retrieval_direction_is_not_treated_as_verdict(self):
        candidate = {**_support(verdict="unreviewed"), "direction": "support_search_candidate"}
        result = build_narration_evidence_contract(
            [{"text": "检索方向不是结论。", "claim_ids": [CLAIM]}], {CLAIM: [candidate]}
        )
        self.assertFalse(result["publishable"])
        self.assertTrue(result["policy"]["retrieval_direction_is_not_verdict"])

    def test_verified_refutation_blocks_even_with_support(self):
        result = build_narration_evidence_contract(
            [{"text": "存在尚未解决的反证。", "claim_ids": [CLAIM]}],
            {CLAIM: [_support(), {"evidence_id": REFUTE, "verdict": "refutes", "citation_anchor_verified": True, "source_current": True}]},
        )
        self.assertFalse(result["publishable"])
        self.assertIn("verified_counter_evidence_unresolved", result["segments"][0]["blocking_reasons"])

    def test_stale_support_blocks(self):
        result = build_narration_evidence_contract(
            [{"text": "过期来源不能放行。", "claim_ids": [CLAIM]}],
            {CLAIM: [_support(source_current=False)]},
        )
        self.assertIn("supporting_source_stale", result["segments"][0]["blocking_reasons"])

    def test_unknown_freshness_blocks(self):
        result = build_narration_evidence_contract(
            [{"text": "未知时效不能放行。", "claim_ids": [CLAIM]}],
            {CLAIM: [_support(source_current=None)]},
        )
        self.assertIn("supporting_source_freshness_unknown", result["segments"][0]["blocking_reasons"])

    def test_factual_sentence_without_claim_is_blocked(self):
        result = build_narration_evidence_contract([{"text": "没有主张关联。"}], {})
        self.assertFalse(result["publishable"])
        self.assertIn("missing_claim_link", result["segments"][0]["blocking_reasons"])

    def test_non_factual_segment_does_not_require_claim(self):
        result = build_narration_evidence_contract(
            [{"text": "夜色渐深，镜头慢慢推进。", "factual": False}], {}
        )
        self.assertTrue(result["publishable"])
        self.assertEqual(result["segments"][0]["gate_status"], "not_applicable")

    def test_invalid_timecode_and_identifier_are_rejected(self):
        with self.assertRaises(ValueError):
            build_narration_evidence_contract(
                [{"text": "时间码倒退。", "claim_ids": [CLAIM], "start_seconds": 4, "end_seconds": 2}], {}
            )
        with self.assertRaises(ValueError):
            build_narration_evidence_contract([{"text": "坏 ID。", "claim_ids": ["not-an-id"]}], {})
        with self.assertRaises(ValueError):
            build_narration_evidence_contract(
                [{"text": "非布尔时效不能放行。", "claim_ids": [CLAIM]}],
                {CLAIM: [_support(source_current=1)]},
            )



    def test_dossier_adapter_keeps_unreviewed_candidates_unverified(self):
        from muse.narration_evidence_contract import build_evidence_by_claim_from_dossier

        dossier = {"claim_id": CLAIM, "evidence": [{
            "id": SUPPORT, "direction": "support_search_candidate",
            "source_ref": "https://example.org/source", "latest_review": None,
            "reviews": [], "source_metadata": {"published": "2026-11-01"},
        }]}
        adapted = build_evidence_by_claim_from_dossier(dossier)
        candidate = adapted[CLAIM][0]
        self.assertEqual(candidate["verdict"], "unreviewed")
        self.assertFalse(candidate["citation_anchor_verified"])
        self.assertIsNone(candidate["source_current"])
        result = build_narration_evidence_contract(
            [{"text": "检索候选不是证据结论。", "claim_ids": [CLAIM]}], adapted
        )
        self.assertFalse(result["publishable"])

    def test_dossier_adapter_uses_stored_review_but_keeps_freshness_unknown(self):
        from muse.narration_evidence_contract import build_evidence_by_claim_from_dossier

        dossier = {"claim_id": CLAIM, "evidence": [{
            "id": SUPPORT, "direction": "support_search_candidate",
            "source_ref": "https://example.org/source",
            "latest_review": {"verdict": "supports", "citations": [{
                "source_url": "https://example.org/source",
                "source_sha256": "d" * 64, "excerpt_sha256": "e" * 64,
            }]},
        }]}
        adapted = build_evidence_by_claim_from_dossier(dossier)
        record = adapted[CLAIM][0]
        self.assertEqual(record["verdict"], "supports")
        self.assertTrue(record["citation_anchor_verified"])
        self.assertIsNone(record["source_current"])
        result = build_narration_evidence_contract(
            [{"text": "审核过但时效未确认。", "claim_ids": [CLAIM]}], adapted
        )
        self.assertFalse(result["publishable"])
        self.assertIn("supporting_source_freshness_unknown",
                      result["segments"][0]["blocking_reasons"])

    def test_dossier_adapter_rejects_citation_for_another_source(self):
        from muse.narration_evidence_contract import build_evidence_by_claim_from_dossier

        dossier = {"claim_id": CLAIM, "evidence": [{
            "id": SUPPORT, "source_ref": "https://example.org/source",
            "latest_review": {"verdict": "supports", "citations": [{
                "source_url": "https://other.example/source",
                "source_sha256": "d" * 64, "excerpt_sha256": "e" * 64,
            }]},
        }]}
        record = build_evidence_by_claim_from_dossier(dossier)[CLAIM][0]
        self.assertFalse(record["citation_anchor_verified"])
        self.assertIsNone(record["source_current"])

if __name__ == "__main__":
    unittest.main()
