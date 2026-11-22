from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.business_opportunities import BusinessOpportunityStore
from muse.research_business_bridge import create_candidate_from_research_report


class ResearchBusinessBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.business_db = self.root / "business" / "opportunities.sqlite3"
        self.report = {
            "topic_key": "verified-ai-workflows",
            "evolution_cycle_id": "cycle-001",
            "evidence_coverage_score": 90,
            "sources": [
                {
                    "title": "Primary research",
                    "url": "https://example.com/research",
                    "source_kind": "peer_reviewed_research_article",
                    "sha256": "a" * 64,
                },
                {
                    "title": "Counter evidence",
                    "url": "https://example.org/limitations",
                    "source_kind": "peer_reviewed_research_article",
                    "sha256": "b" * 64,
                },
            ],
            "synthesis": {
                "executive_summary": "A research summary, not market validation.",
                "testable_experiments": ["Compare a manual and automated workflow."],
                "counter_evidence": ["The measured benefit may not generalize."],
            },
        }
        self.brief = {
            "title": "AI 内容工作流小规模试点",
            "customer_segment": "本地小型商户",
            "customer_problem": "商户没有稳定制作短视频的时间。",
            "proposed_offer": "先提供一条人工复核的粤语短片样片。",
            "validation_test": "向三家自愿参与的商户展示样片并记录反馈。",
            "success_signal": "至少一位商户明确提出下一步试点需求。",
        }
        self.ratings = {
            "demand_signal": 1,
            "willingness_to_pay": 1,
            "delivery_fit": 3,
            "competition_gap": 2,
            "repeatability": 2,
            "risk": 4,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_creates_only_a_candidate_and_keeps_research_as_assumption(self) -> None:
        result = create_candidate_from_research_report(
            self.report,
            self.brief,
            self.ratings,
            business_database=self.business_db,
            report_path="data/research/reports/cycle-001.json",
        )

        opportunity = result["opportunity"]
        self.assertEqual(opportunity["status"], "candidate")
        self.assertEqual(result["status"], "opportunity_resolved")
        self.assertEqual(result["opportunity_status"], "candidate")
        self.assertEqual(opportunity["customer_segment"], self.brief["customer_segment"])
        self.assertEqual(opportunity["ratings"], self.ratings)
        self.assertEqual(opportunity["evidence_summary"]["assumptions"], 1)
        self.assertEqual(
            opportunity["evidence_summary"]["validation_state"], "needs_more_evidence"
        )
        self.assertEqual(len(opportunity["evidence"]), 1)
        self.assertEqual(opportunity["evidence"][0]["evidence_type"], "estimate")
        self.assertEqual(opportunity["evidence"][0]["epistemic_status"], "assumption")
        self.assertIn("do not establish customer demand", opportunity["evidence"][0]["summary"])
        self.assertEqual(result["research_evaluation"]["epistemic_status"], "hypothesis_only")
        self.assertEqual(
            result["research_evaluation"]["recurrence_policy"],
            "single_research_cycle_is_not_recurrence_evidence",
        )
        self.assertFalse(result["market_demand_inferred"])
        self.assertFalse(result["willingness_to_pay_inferred"])
        self.assertTrue(result["customer_validation_required"])
        self.assertFalse(result["initiative_created"])
        self.assertFalse(result["dispatch_started"])
        self.assertFalse(result["publishing_started"])
        self.assertFalse(result["gpu_work_started"])
        self.assertEqual(result["pricing_state"], "not_provided")
        self.assertFalse((self.root / "initiatives.sqlite3").exists())
        self.assertFalse((self.root / "tasks" / "muse.sqlite3").exists())

    def test_replay_is_idempotent_for_candidate_and_assumption_note(self) -> None:
        first = create_candidate_from_research_report(
            self.report, self.brief, self.ratings, business_database=self.business_db
        )
        second = create_candidate_from_research_report(
            self.report, self.brief, self.ratings, business_database=self.business_db
        )

        self.assertEqual(first["opportunity"]["id"], second["opportunity"]["id"])
        self.assertEqual(first["status"], "opportunity_resolved")
        self.assertEqual(second["status"], "opportunity_resolved")
        self.assertEqual(
            first["research_assumption_evidence_id"],
            second["research_assumption_evidence_id"],
        )
        with BusinessOpportunityStore(self.business_db) as store:
            current = store.get(first["opportunity"]["id"])
        self.assertEqual(len(current["evidence"]), 1)
        self.assertEqual(current["status"], "candidate")

    def test_new_research_cycle_reuses_same_opportunity_and_appends_one_assumption(self) -> None:
        first = create_candidate_from_research_report(
            self.report, self.brief, self.ratings, business_database=self.business_db
        )
        later_report = dict(self.report)
        later_report["evolution_cycle_id"] = "cycle-002"
        later_report["synthesis"] = dict(
            self.report["synthesis"],
            executive_summary="Second research cycle with a distinct evidence snapshot.",
        )
        second = create_candidate_from_research_report(
            later_report, self.brief, self.ratings, business_database=self.business_db
        )
        replay = create_candidate_from_research_report(
            later_report, self.brief, self.ratings, business_database=self.business_db
        )

        self.assertEqual(first["opportunity"]["id"], second["opportunity"]["id"])
        self.assertEqual(second["opportunity"]["id"], replay["opportunity"]["id"])
        self.assertNotEqual(
            first["research_assumption_evidence_id"],
            second["research_assumption_evidence_id"],
        )
        self.assertEqual(
            second["research_assumption_evidence_id"],
            replay["research_assumption_evidence_id"],
        )
        with BusinessOpportunityStore(self.business_db) as store:
            current = store.get(first["opportunity"]["id"])
        self.assertEqual(len(current["evidence"]), 2)
        self.assertEqual(current["evidence_summary"]["assumptions"], 2)
        self.assertEqual(
            current["evidence_summary"]["validation_state"], "needs_more_evidence"
        )

    def test_invalid_customer_brief_or_business_ratings_fail_before_database_creation(self) -> None:
        with self.assertRaises(ValueError):
            create_candidate_from_research_report(
                self.report,
                {"title": "Incomplete"},
                self.ratings,
                business_database=self.business_db,
            )
        with self.assertRaises(ValueError):
            create_candidate_from_research_report(
                self.report,
                self.brief,
                dict(self.ratings, willingness_to_pay=99),
                business_database=self.business_db,
            )
        self.assertFalse(self.business_db.exists())

    def test_rejected_opportunity_does_not_accept_another_research_cycle(self) -> None:
        first = create_candidate_from_research_report(
            self.report, self.brief, self.ratings, business_database=self.business_db
        )
        with BusinessOpportunityStore(self.business_db) as store:
            store.set_status(first["opportunity"]["id"], "rejected")
        later_report = dict(self.report)
        later_report["evolution_cycle_id"] = "cycle-rejected-follow-up"
        with self.assertRaises(ValueError):
            create_candidate_from_research_report(
                later_report, self.brief, self.ratings, business_database=self.business_db
            )
        with BusinessOpportunityStore(self.business_db) as store:
            current = store.get(first["opportunity"]["id"])
        self.assertEqual(current["status"], "rejected")
        self.assertEqual(len(current["evidence"]), 1)

    def test_unsafe_urls_are_not_carried_forward_as_research_references(self) -> None:
        report = dict(self.report)
        report["sources"] = [
            {"url": "https://user:secret@example.com/private"},
            {"url": "file:///private/report"},
            {"url": "https://example.net/public"},
        ]
        result = create_candidate_from_research_report(
            report, self.brief, self.ratings, business_database=self.business_db
        )
        self.assertEqual(result["research_refs"], ["https://example.net/public"])
        evaluation_refs = [
            ref
            for candidate in result["research_evaluation"]["candidates"]
            for ref in candidate["evidence_refs"]
        ]
        self.assertNotIn("https://user:secret@example.com/private", evaluation_refs)
        self.assertNotIn("file:///private/report", evaluation_refs)
        self.assertFalse(result["market_demand_inferred"])


if __name__ == "__main__":
    unittest.main()
