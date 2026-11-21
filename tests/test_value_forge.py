from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.business_opportunities import BusinessOpportunityStore
from muse.initiative_engine import InitiativeStore
from muse.value_forge import DIMENSIONS, SOURCE_REPOSITORY, ValueForge, score_value_opportunity


class ValueForgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.forge = ValueForge()
        self.ratings = {
            "impact": 4, "recurrence": 4, "capability_fit": 5,
            "evidence_quality": 2, "feasibility": 4, "risk": 2,
        }

    def test_catalog_contains_distinct_abstract_patterns_with_provenance(self) -> None:
        patterns = self.forge.list_patterns()
        self.assertGreaterEqual(len(patterns), 7)
        self.assertEqual(len({item["id"] for item in patterns}), len(patterns))
        self.assertTrue(all(item["title"] and item["creator_value"] for item in patterns))
        self.assertEqual(self.forge.evaluate("passive_watchtower", self.ratings)["source_refs"], [SOURCE_REPOSITORY])

    def test_scoring_is_explainable_bounded_and_risk_sensitive(self) -> None:
        result = score_value_opportunity(self.ratings)
        safer = score_value_opportunity(dict(self.ratings, risk=1))
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)
        self.assertGreater(safer["score"], result["score"])
        self.assertEqual(result["evidence_state"], "hypothesis_only")
        self.assertTrue(result["requires_validation"])
        self.assertEqual(set(result["ratings"]), set(DIMENSIONS))

    def test_scoring_rejects_missing_extra_and_non_integer_ratings(self) -> None:
        with self.assertRaises(ValueError):
            score_value_opportunity({"impact": 5})
        with self.assertRaises(ValueError):
            score_value_opportunity(dict(self.ratings, surprise=1))
        with self.assertRaises(ValueError):
            score_value_opportunity(dict(self.ratings, risk=True))
        with self.assertRaises(ValueError):
            score_value_opportunity(dict(self.ratings, impact=0))

    def test_unknown_pattern_and_unsafe_references_rejected(self) -> None:
        with self.assertRaises(KeyError):
            self.forge.get_pattern("not-a-pattern")
        with self.assertRaises(ValueError):
            self.forge.evaluate("passive_watchtower", self.ratings, evidence_refs=["bad\nreference"])

    def test_research_report_is_ranked_as_hypotheses_without_inventing_recurrence(self) -> None:
        result = self.forge.evaluate_research_report(
            {
                "evidence_coverage_score": 60,
                "claim_review_status": "unknown",
                "sources": [{"url": "https://example.com/source-1"}, {"url": "https://example.org/source-2"}],
                "synthesis": {
                    "executive_summary": "A bounded summary",
                    "testable_experiments": ["Compare baseline and treatment"],
                    "counter_evidence": ["A plausible alternative explanation"],
                },
            },
            report_path="reports/research-cycle.json",
        )
        self.assertEqual(result["epistemic_status"], "hypothesis_only")
        self.assertEqual(result["recurrence_policy"], "single_research_cycle_is_not_recurrence_evidence")
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertTrue(all(item["evaluation"]["ratings"]["evidence_quality"] == 2
                            for item in result["candidates"]))
        self.assertTrue(all(item["evaluation"]["ratings"]["recurrence"] == 1 for item in result["candidates"]))
        self.assertTrue(all(item["evaluation"]["evidence_state"] == "hypothesis_only" for item in result["candidates"]))
        self.assertTrue(all("reports/research-cycle.json" in item["evidence_refs"] for item in result["candidates"]))
        self.assertTrue(result["requires_human_review"])
        self.assertFalse(result["dispatch_started"])

    def test_research_report_handles_missing_and_untrusted_fields_conservatively(self) -> None:
        result = self.forge.evaluate_research_report(
            {"evidence_coverage_score": "not-a-number", "sources": "not-a-list", "synthesis": None}
        )
        self.assertEqual(result["source_count"], 0)
        self.assertEqual(result["experiment_count"], 0)
        self.assertEqual(result["counter_evidence_count"], 0)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["evaluation"]["ratings"]["evidence_quality"], 1)
        self.assertEqual(result["candidates"][0]["evaluation"]["ratings"]["recurrence"], 1)

    def test_duplicate_or_malformed_source_records_do_not_inflate_evidence_quality(self) -> None:
        result = self.forge.evaluate_research_report(
            {
                "evidence_coverage_score": 90,
                "sources": [
                    None,
                    "https://not-a-source-record.example",
                    {"url": "not-a-url"},
                    {"url": "https://example.com/one"},
                    {"url": "https://example.com/one"},
                ],
                "synthesis": {},
            }
        )
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(
            result["candidates"][0]["evaluation"]["ratings"]["evidence_quality"], 1
        )
        self.assertEqual(
            result["candidates"][0]["evidence_refs"], ["https://example.com/one"]
        )

    def test_capability_radar_adapter_scores_operational_recurrence_not_market_demand(self) -> None:
        result = self.forge.evaluate_capability_radar_result(
            {
                "signals_seen": [
                    {"key": "task_failure", "count": 4, "priority": 90, "confidence": 0.8}
                ],
                "created": [{"id": "proposal-1", "signal_key": "task_failure", "priority": 90}],
                "existing": [],
                "trend": {
                    "status": "compared",
                    "direction": "worsening",
                    "signals": {"task_failure": {"current": 4, "previous": 2, "delta": 2}},
                },
            }
        )
        self.assertEqual(result["epistemic_status"], "hypothesis_only")
        self.assertEqual(result["recurrence_scope"], "operational_signal_across_non_overlapping_windows")
        self.assertFalse(result["market_demand_inferred"])
        evaluation = result["evaluations"][0]
        self.assertEqual(evaluation["proposal_id"], "proposal-1")
        self.assertEqual(evaluation["signal_key"], "task_failure")
        self.assertTrue(evaluation["recurrence_observed"])
        self.assertEqual(evaluation["evaluation"]["ratings"]["recurrence"], 2)
        self.assertEqual(evaluation["evaluation"]["ratings"]["evidence_quality"], 3)
        self.assertEqual(evaluation["evaluation"]["evidence_state"], "hypothesis_only")

    def test_capability_radar_adapter_is_conservative_without_a_prior_window(self) -> None:
        result = self.forge.evaluate_capability_radar_result(
            {
                "signals_seen": [{"key": "test_failure", "count": 1, "priority": 85}],
                "created": [{"id": "proposal-2", "signal_key": "test_failure", "priority": 85}],
                "existing": [],
                "trend": {"status": "insufficient_data", "signals": {}},
            }
        )
        evaluation = result["evaluations"][0]
        self.assertFalse(evaluation["recurrence_observed"])
        self.assertEqual(evaluation["evaluation"]["ratings"]["recurrence"], 1)
        self.assertEqual(evaluation["evaluation"]["ratings"]["evidence_quality"], 2)
        self.assertFalse(result["market_demand_inferred"])
    def test_initiative_bridge_creates_draft_without_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                result = self.forge.propose_initiative(
                    store, "research_to_video", self.ratings,
                    evidence_refs=["research:topic-1", "https://example.com/source"],
                )
                initiative = result["initiative"]
                concept = json.loads(initiative["concept"])
                self.assertEqual(initiative["status"], "proposed")
                self.assertEqual(initiative["autonomy_level"], "draft")
                self.assertEqual(initiative["kind"], "creative_output")
                self.assertEqual(concept["epistemic_status"], "hypothesis_only")
                self.assertTrue(concept["problem"])
                self.assertTrue(concept["hypothesis"])
                self.assertEqual(concept["source_refs"], [SOURCE_REPOSITORY])
                self.assertIn("value-pattern:research_to_video", initiative["inspiration_refs"])
                self.assertFalse(result["dispatch_started"])
                self.assertEqual(initiative["priority"], round(result["evaluation"]["evaluation"]["score"]))

    def test_initiative_bridge_is_idempotent_through_existing_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                first = self.forge.propose_initiative(store, "workflow_to_microapp", self.ratings)
                second = self.forge.propose_initiative(store, "workflow_to_microapp", self.ratings)
                self.assertEqual(first["initiative"]["id"], second["initiative"]["id"])

    def test_business_bridge_delegates_to_existing_business_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with BusinessOpportunityStore(root / "business.sqlite3") as business:
                ratings = {
                    "demand_signal": 4, "willingness_to_pay": 3, "delivery_fit": 5,
                    "competition_gap": 3, "repeatability": 4, "risk": 2,
                }
                opportunity = business.create(
                    idempotency_key="value-forge-test", title="Pilot",
                    customer_segment="Local merchants", customer_problem="Need video",
                    proposed_offer="Short video package", validation_test="Show a sample",
                    success_signal="One paid pilot", ratings=ratings,
                )
                result = self.forge.promote_business_opportunity(
                    business, opportunity["id"], initiative_database=str(root / "initiatives.sqlite3")
                )
                self.assertEqual(result["opportunity"]["id"], opportunity["id"])
                self.assertIn(f"business-opportunity:{opportunity['id']}", result["initiative"]["inspiration_refs"])


if __name__ == "__main__":
    unittest.main()
