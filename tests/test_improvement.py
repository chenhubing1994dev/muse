from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.improvement import ImprovementStore


class ImprovementStoreTests(unittest.TestCase):
    def test_proposal_and_research_sources_are_persisted_with_audit_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            with ImprovementStore(database) as store:
                proposal = store.create_proposal(
                    title="Add self-review loop",
                    problem="Completed tasks do not create follow-up improvements",
                    hypothesis="A durable review queue can generate testable next steps",
                )
                store.add_source(
                    proposal["id"], title="Architecture guide", url="https://example.org/guide",
                    license_status="not_applicable", security_status="reviewed",
                    notes="Design reference only; no code copied",
                )
                store.transition(proposal["id"], "researching", actor="scheduler")
                restored = store.get(proposal["id"])
                self.assertEqual(restored["status"], "researching")
                self.assertEqual(len(restored["sources"]), 1)
                self.assertGreaterEqual(len(restored["events"]), 3)
            with ImprovementStore(database) as reopened:
                self.assertEqual(reopened.get(proposal["id"])["title"], "Add self-review loop")

    def test_initiative_feedback_becomes_idempotent_unapproved_proposal(self) -> None:
        feedback = {
            "id": "feedback-001", "initiative_id": "initiative-001",
            "feedback": "原型缺少失败路径和恢复测试。",
            "decision": "revise", "reviewer": "creator",
            "source": "prototype_review", "created_at": "2026-11-09T00:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            with ImprovementStore(Path(directory) / "improvements.sqlite3") as store:
                first = store.create_feedback_proposal(feedback)
                repeated = store.create_feedback_proposal(feedback)
                self.assertEqual(first["id"], repeated["id"])
                self.assertEqual(first["status"], "proposed")
                self.assertEqual(first["problem"], feedback["feedback"])
                self.assertEqual(first["metadata"]["source_feedback_id"], feedback["id"])
                self.assertEqual(first["metadata"]["epistemic_status"], "unverified_user_feedback_signal")
                self.assertTrue(first["metadata"]["requires_research_and_independent_review"])
                self.assertEqual(first["events"][0]["to_status"], "proposed")
                invalid = dict(feedback, decision="accept", id="feedback-002")
                with self.assertRaisesRegex(ValueError, "only revise/reject"):
                    store.create_feedback_proposal(invalid)

    def test_approval_requires_reviewed_sources_and_implementation_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ImprovementStore(Path(directory) / "improvements.sqlite3") as store:
                proposal = store.create_proposal("Safe adapter", "No adapter contract", "Define a contract")
                store.transition(proposal["id"], "researching", actor="researcher")
                store.add_source(
                    proposal["id"], title="Unknown repository", url="https://example.org/repo",
                    license_status="unknown", security_status="needs_review",
                )
                store.transition(proposal["id"], "ready_for_review", actor="researcher")
                with self.assertRaises(ValueError):
                    store.approve(proposal["id"], approved_by="user")
                with self.assertRaises(ValueError):
                    store.transition(proposal["id"], "implemented", actor="worker")

    def test_human_can_review_source_license_and_security_before_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ImprovementStore(Path(directory) / "improvements.sqlite3") as store:
                proposal = store.create_proposal("Evidence review", "Research gap", "Test a bounded improvement")
                store.transition(proposal["id"], "researching", actor="researcher")
                source = store.add_source(
                    proposal["id"], title="Project documentation", url="https://example.org/docs",
                    license_status="unknown", security_status="needs_review",
                )
                store.transition(proposal["id"], "ready_for_review", actor="researcher")
                with self.assertRaises(ValueError):
                    store.approve(proposal["id"], approved_by="owner")
                reviewed = store.review_source(
                    source["id"], license_status="permissive", security_status="reviewed",
                    reviewed_by="owner", notes="Terms and source safety checked",
                )
                self.assertEqual(reviewed["license_status"], "permissive")
                self.assertEqual(reviewed["security_status"], "reviewed")
                approved = store.approve(proposal["id"], approved_by="owner")
                self.assertEqual(approved["status"], "approved")
                self.assertTrue(any(event["actor"] == "owner" and "source review" in event["note"]
                                    for event in approved["events"]))

    def test_only_approved_proposals_can_be_promoted_and_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ImprovementStore(Path(directory) / "improvements.sqlite3") as store:
                proposal = store.create_proposal("Tested module", "Gap", "Add tested module")
                store.transition(proposal["id"], "researching", actor="researcher")
                store.add_source(
                    proposal["id"], title="Official documentation", url="https://example.org/docs",
                    license_status="not_applicable", security_status="reviewed",
                )
                store.transition(proposal["id"], "ready_for_review", actor="reviewer")
                approved = store.approve(proposal["id"], approved_by="user")
                self.assertEqual(approved["status"], "approved")
                with self.assertRaisesRegex(ValueError, "verified experiment runs"):
                    store.transition(proposal["id"], "implemented", actor="release-gate")
                self.assertEqual(store.get(proposal["id"])["status"], "approved")

    def test_invalid_state_transition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ImprovementStore(Path(directory) / "improvements.sqlite3") as store:
                proposal = store.create_proposal("Idea", "Problem", "Hypothesis")
                with self.assertRaises(ValueError):
                    store.transition(proposal["id"], "implemented", actor="scheduler")


    def test_proposal_priority_and_metadata_survive_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            with ImprovementStore(database) as store:
                proposal = store.create_proposal(
                    "Ranked gap", "Evidence-backed problem", "Bounded fix", priority=87,
                    metadata={"signal_key": "task_failure", "confidence": 0.82,
                              "acceptance_criteria": ["Add regression coverage"],
                              "rollback_plan": "Revert the isolated change"},
                )
                self.assertEqual(proposal["priority"], 87)
                self.assertEqual(proposal["metadata"]["confidence"], 0.82)
            with ImprovementStore(database) as reopened:
                restored = reopened.get(proposal["id"])
                self.assertEqual(restored["priority"], 87)
                self.assertEqual(restored["metadata"]["signal_key"], "task_failure")

    def test_legacy_proposal_database_migrates_priority_and_metadata_defaults(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("""CREATE TABLE improvement_proposals (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, problem TEXT NOT NULL,
                hypothesis TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            connection.execute(
                "INSERT INTO improvement_proposals VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("legacy-id", "Legacy", "Old schema", "Keep compatibility",
                 "proposed", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            connection.commit()
            connection.close()
            with ImprovementStore(database) as store:
                restored = store.get("legacy-id")
                self.assertEqual(restored["priority"], 50)
                self.assertEqual(restored["metadata"], {})

    def test_radar_snapshots_compare_non_overlapping_windows_and_persist(self) -> None:
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            start = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
            with ImprovementStore(database) as store:
                first = store.record_radar_snapshot({"task_failure": 5, "test_failure": 2}, observed_at=start)
                second = store.record_radar_snapshot(
                    {"task_failure": 2, "test_failure": 2},
                    observed_at=start + timedelta(hours=24),
                )
                third = store.record_radar_snapshot(
                    {"task_failure": 4, "test_failure": 3},
                    observed_at=start + timedelta(hours=48),
                )
            with ImprovementStore(database) as reopened:
                fourth = reopened.record_radar_snapshot(
                    {"task_failure": 1}, observed_at=start + timedelta(hours=72),
                )
            self.assertEqual(first["direction"], "insufficient_data")
            self.assertEqual(second["direction"], "improving")
            self.assertEqual(second["delta"], -3)
            self.assertEqual(second["signals"]["task_failure"]["delta"], -3)
            self.assertEqual(third["direction"], "worsening")
            self.assertEqual(fourth["previous_total"], 7)

    def test_radar_snapshot_rejects_unbounded_or_malformed_signal_counts(self) -> None:
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as directory:
            with ImprovementStore(Path(directory) / "improvements.sqlite3") as store:
                now = datetime(2026, 11, 1, tzinfo=timezone.utc)
                for counts in ({"bad key": 1}, {"task_failure": True}, {"task_failure": -1}):
                    with self.assertRaises(ValueError):
                        store.record_radar_snapshot(counts, observed_at=now)
                with self.assertRaises(ValueError):
                    store.record_radar_snapshot({}, observed_at=now, lookback_hours=0)

    def test_proposal_rejects_invalid_priority_and_oversized_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ImprovementStore(Path(directory) / "improvements.sqlite3") as store:
                with self.assertRaises(ValueError):
                    store.create_proposal("Bad priority", "Problem", "Fix", priority=101)
                with self.assertRaises(ValueError):
                    store.create_proposal("Bad metadata", "Problem", "Fix", metadata={"large": "x" * 17000})

    def test_implementation_requires_comparable_runs_and_independent_review(self) -> None:
        import json
        from muse.capabilities import CapabilityRegistry
        from muse.experiment_bridge import ExperimentBridge
        from muse.initiative_engine import InitiativeStore
        from muse.research_knowledge import ResearchKnowledgeStore
        from muse.review_gate import IndependentReviewGate, ReviewStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                baseline_manifest = bridge.register_manifest(
                    title="Baseline tests", kind="test", argv=("python", "-m", "unittest"),
                    code_fingerprints={"src/muse/example.py": "a" * 64},
                )
                baseline = bridge.record_result(
                    baseline_manifest["manifest_id"], status="failed", duration_seconds=2,
                    metrics={"tests_run": 10, "failures": 2, "errors": 0},
                    summary="Baseline has two failures.", raw_evidence="FAILED (failures=2)",
                )
                mismatched_manifest = bridge.register_manifest(
                    title="Mismatched candidate tests", kind="test", argv=("pytest",),
                    code_fingerprints={"src/muse/example.py": "c" * 64},
                )
                mismatched = bridge.record_result(
                    mismatched_manifest["manifest_id"], status="succeeded", duration_seconds=2,
                    metrics={"tests_run": 10, "failures": 0, "errors": 0},
                    summary="Different test command passed.", raw_evidence="10 passed",
                )
                candidate_manifest = bridge.register_manifest(
                    title="Candidate tests", kind="test", argv=("python", "-m", "unittest"),
                    code_fingerprints={"src/muse/example.py": "b" * 64},
                )
                candidate = bridge.record_result(
                    candidate_manifest["manifest_id"], status="succeeded", duration_seconds=2,
                    metrics={"tests_run": 10, "failures": 0, "errors": 0},
                    summary="Candidate passes all tests.", raw_evidence="OK (10 tests)",
                )
                initiatives = InitiativeStore(root / "data" / "initiatives" / "muse.sqlite3")
                initiative = initiatives.propose(
                    "Feedback research initiative", kind="research",
                    creator_value="Validate feedback with independent experiments",
                    concept="Use a reproducible test and an independent review before upgrading capability metadata.",
                    first_step="Compare baseline and candidate runs",
                    expected_output="A linked, independently reviewed validation decision",
                )
                with ImprovementStore(root / "improvements.sqlite3") as store:
                    proposal = store.create_proposal(
                        "Fix regression", "Baseline fails", "Candidate fixes failures",
                        metadata={"initiative_id": initiative["id"], "source_feedback_id": "b" * 32,
                                  "evolution_cycle_id": "cycle_" + "c" * 32},
                    )
                    store.transition(proposal["id"], "researching", actor="researcher")
                    store.add_source(proposal["id"], title="Design reference", url="https://example.org/ref",
                                     license_status="not_applicable", security_status="reviewed")
                    store.transition(proposal["id"], "ready_for_review", actor="researcher")
                    store.approve(proposal["id"], approved_by="user")
                    reviews = ReviewStore(root / "reviews.sqlite3")
                    try:
                        gate = IndependentReviewGate(
                            reviews, reviewer_id="independent-reviewer",
                            call=lambda _: json.dumps({"approved": True, "summary": "Evidence verified.", "findings": []}),
                        )
                        with self.assertRaisesRegex(ValueError, "same test command"):
                            store.validate_and_promote(
                                proposal["id"], experiment_bridge=bridge,
                                baseline_run_ref=baseline["run_ref"], candidate_run_ref=mismatched["run_ref"],
                                review_gate=gate, implementer_id="builder",
                            )
                        with CapabilityRegistry(root / "capabilities.sqlite3") as capabilities:
                            capabilities.register(
                                capability_id="research.regression", domain="research",
                                name="Regression research", description="Validated regression workflow",
                                status="unverified", capabilities=("regression_testing",),
                                trust="local_test", license="not_applicable", version="0.1",
                            )
                            result = store.validate_and_promote(
                                proposal["id"], experiment_bridge=bridge,
                                baseline_run_ref=baseline["run_ref"], candidate_run_ref=candidate["run_ref"],
                                review_gate=gate, implementer_id="builder",
                                capability_registry=capabilities, capability_id="research.regression",
                                initiative_store=initiatives,
                            )
                            upgraded = capabilities.get("research.regression")
                            self.assertEqual(upgraded["status"], "ready")
                            self.assertEqual(upgraded["version"], result["capability_upgrade"]["version"])
                            self.assertEqual(upgraded["metadata"]["last_improvement_validation"]["reviewer_id"], "independent-reviewer")
                        self.assertEqual(result["status"], "implemented")
                        validation_node = knowledge.get_node(result["validation_node_id"])
                        self.assertEqual(validation_node["metadata"]["decision_kind"], "improvement_validation")
                        self.assertEqual(validation_node["metadata"]["source_feedback_id"], "b" * 32)
                        self.assertEqual(validation_node["metadata"]["capability_upgrade"]["status"], "ready")
                        self.assertTrue(result["initiative_linked"])
                        from muse.status_api import build_initiative_timeline
                        timeline = build_initiative_timeline(root / "data" / "initiatives" / "muse.sqlite3", initiative["id"])
                        self.assertTrue(any(
                            item["event_type"] == "knowledge_linked"
                            and item["details"].get("knowledge_node_id") == result["validation_node_id"]
                            for item in timeline["events"]
                        ))
                        self.assertEqual(result["validation"]["baseline_run_ref"], baseline["run_ref"])
                        self.assertEqual(result["validation"]["candidate_run_ref"], candidate["run_ref"])
                        self.assertEqual(result["validation"]["reviewer_id"], "independent-reviewer")
                        rejected_proposal = store.create_proposal(
                            "Rejected improvement", "Review found a mismatch", "Do not promote the candidate",
                            metadata={"initiative_id": initiative["id"], "source_feedback_id": "d" * 32},
                        )
                        store.transition(rejected_proposal["id"], "researching", actor="researcher")
                        store.add_source(rejected_proposal["id"], title="Counter reference", url="https://example.org/counter",
                                         license_status="not_applicable", security_status="reviewed")
                        store.transition(rejected_proposal["id"], "ready_for_review", actor="researcher")
                        store.approve(rejected_proposal["id"], approved_by="user")
                        rejecting_gate = IndependentReviewGate(
                            reviews, reviewer_id="independent-reviewer-2",
                            call=lambda _: json.dumps({
                                "approved": False, "summary": "The candidate does not meet the declared acceptance criteria.",
                                "findings": [{"severity": "high", "title": "Acceptance mismatch", "detail": "Observed output differs from the declared contract."}],
                            }),
                        )
                        rejected = store.validate_and_promote(
                            rejected_proposal["id"], experiment_bridge=bridge,
                            baseline_run_ref=baseline["run_ref"], candidate_run_ref=candidate["run_ref"],
                            review_gate=rejecting_gate, implementer_id="builder",
                            initiative_store=initiatives,
                        )
                        self.assertEqual(rejected["status"], "review_rejected")
                        rejected_node = knowledge.get_node(rejected["validation_node_id"])
                        self.assertFalse(rejected_node["metadata"]["approved"])
                        self.assertEqual(rejected_node["metadata"]["decision_kind"], "improvement_validation_review")
                        self.assertEqual(rejected_node["metadata"]["findings"][0]["title"], "Acceptance mismatch")
                        self.assertTrue(rejected["initiative_linked"])
                        rejected_timeline = build_initiative_timeline(root / "data" / "initiatives" / "muse.sqlite3", initiative["id"])
                        self.assertTrue(any(
                            item["event_type"] == "knowledge_linked"
                            and item["details"].get("knowledge_node_id") == rejected["validation_node_id"]
                            for item in rejected_timeline["events"]
                        ))
                    finally:
                        reviews.close()
                        initiatives.close()


    def test_implementation_validation_cannot_be_used_by_a_different_actor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ImprovementStore(Path(directory) / "improvements.sqlite3") as store:
                proposal = store.create_proposal("Validated change", "A gap", "A bounded fix")
                store.transition(proposal["id"], "researching", actor="researcher")
                store.add_source(proposal["id"], title="Reference", url="https://example.org/ref",
                                 license_status="not_applicable", security_status="reviewed")
                store.transition(proposal["id"], "ready_for_review", actor="researcher")
                store.approve(proposal["id"], approved_by="user")
                store.connection.execute(
                    "INSERT INTO improvement_validations VALUES (?,?,?,?,?,?,?,?,?)",
                    (proposal["id"], "experiment-run:base", "experiment-run:candidate", "a" * 64,
                     "reviewer", "builder", "review-id", "{}", store._now()),
                )
                store.connection.commit()
                with self.assertRaisesRegex(ValueError, "validated implementer"):
                    store.transition(proposal["id"], "implemented", actor="different-actor")
                self.assertEqual(store.get(proposal["id"])["status"], "approved")

if __name__ == "__main__":
    unittest.main()