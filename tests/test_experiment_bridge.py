from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.experiment_bridge import ExperimentBridge
from muse.research_knowledge import ResearchKnowledgeStore


class ExperimentBridgeTests(unittest.TestCase):
    def test_raw_evidence_is_independently_reviewed_before_claim_promotion(self):
        import json
        from muse.review_gate import IndependentReviewGate, ReviewStore
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                reviews = ReviewStore(Path(directory) / "reviews.sqlite3")
                try:
                    bridge = ExperimentBridge(knowledge)
                    manifest = bridge.register_manifest(
                        title="Unit test run", kind="test", argv=("python", "-m", "unittest", "discover"),
                        cwd=".", timeout_seconds=120, memory_budget_mb=1024,
                        code_fingerprints={"tests/test_example.py": "a" * 64}, task_id="ticket-1",
                    )
                    claim = knowledge.upsert_node("claim", "Feature passes tests", metadata={"author_id": "builder"})
                    run = bridge.record_result(
                        manifest["manifest_id"], status="succeeded", duration_seconds=2.5,
                        metrics={"tests_run": 12, "failures": 0}, summary="Unit test suite passed.", run_id="run-1",
                        raw_evidence="Ran 12 tests; OK",
                    )
                    captured = []
                    def reviewer(request):
                        captured.append(json.loads(request))
                        return json.dumps({"approved": True, "summary": "Raw output and run metadata checked.", "findings": []})
                    gate = IndependentReviewGate(reviews, reviewer_id="critic", call=reviewer)
                    result = bridge.review_claim(
                        claim["id"], run_ref=run["run_ref"], raw_evidence="Ran 12 tests; OK",
                        evidence_refs=(run["run_ref"],), review_gate=gate,
                    )
                    self.assertEqual(result["status"], "supported")
                    self.assertIn("Ran 12 tests; OK", captured[0]["test_evidence"])
                    self.assertEqual(result["claim"]["metadata"]["review_id"], result["review_id"])
                    self.assertEqual(result["claim"]["metadata"]["evidence_refs"], [run["run_ref"]])
                    with self.assertRaisesRegex(ValueError, "does not match the hash"):
                        bridge.review_claim(
                            claim["id"], run_ref=run["run_ref"], raw_evidence="forged output",
                            evidence_refs=(run["run_ref"],), review_gate=gate,
                        )
                finally:
                    reviews.close()

    def test_critical_review_finding_keeps_claim_proposed(self):
        import json
        from muse.review_gate import IndependentReviewGate, ReviewStore
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                reviews = ReviewStore(Path(directory) / "reviews.sqlite3")
                try:
                    bridge = ExperimentBridge(knowledge)
                    manifest = bridge.register_manifest(title="Review veto", kind="test", argv=("python", "-m", "unittest"))
                    run = bridge.record_result(manifest["manifest_id"], status="succeeded", duration_seconds=1,
                                               metrics={"tests_run": 3}, summary="Tests completed.", raw_evidence="Ran 3 tests")
                    claim = knowledge.upsert_node("claim", "Feature is safe", metadata={"author_id": "builder"})
                    gate = IndependentReviewGate(
                        reviews, reviewer_id="critic",
                        call=lambda _: json.dumps({"approved": True, "summary": "Evidence is incomplete.", "findings": [
                            {"severity": "critical", "title": "Missing original fixture", "detail": "Raw fixture was not independently verified."}
                        ]}),
                    )
                    result = bridge.review_claim(
                        claim["id"], run_ref=run["run_ref"], raw_evidence="Ran 3 tests",
                        evidence_refs=(run["run_ref"],), review_gate=gate,
                    )
                    self.assertEqual(result["status"], "review_rejected")
                    self.assertEqual(knowledge.get_node(claim["id"])["status"], "proposed")
                finally:
                    reviews.close()

    def test_claim_cannot_be_promoted_with_fabricated_reviewer_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                manifest = bridge.register_manifest(title="Benchmark", kind="benchmark", argv=("python", "benchmark.py"))
                run = bridge.record_result(manifest["manifest_id"], status="succeeded", duration_seconds=1,
                                           metrics={"iterations": 10}, summary="Benchmark completed.")
                claim = knowledge.upsert_node("claim", "Performance improved", metadata={"author_id": "builder"})
                with self.assertRaisesRegex(TypeError, "ReviewStore"):
                    bridge.link_claim(claim["id"], status="supported", run_ref=run["run_ref"],
                                      review_store=None, review_id="invented",
                                      evidence_refs=(run["run_ref"],), raw_evidence="fake")


    def test_manifest_rejects_shell_expressions_paths_and_invalid_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                with self.assertRaisesRegex(ValueError, "shell expressions"):
                    bridge.register_manifest(
                        title="Unsafe", kind="test", argv=("python", "x.py; del *"),
                    )
                with self.assertRaisesRegex(ValueError, "workspace-relative"):
                    bridge.register_manifest(
                        title="Unsafe path", kind="test", argv=("python",),
                        cwd="../outside",
                    )
                with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                    bridge.register_manifest(
                        title="No budget", kind="test", argv=("python",),
                        timeout_seconds=0,
                    )

    def test_failed_experiment_can_refute_but_cannot_support_a_claim(self):
        import json
        from muse.review_gate import IndependentReviewGate, ReviewStore
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                reviews = ReviewStore(Path(directory) / "reviews.sqlite3")
                try:
                    bridge = ExperimentBridge(knowledge)
                    manifest = bridge.register_manifest(title="Failing test", kind="test", argv=("python", "-m", "unittest"))
                    run = bridge.record_result(manifest["manifest_id"], status="failed", duration_seconds=2,
                                               metrics={"failures": 1}, summary="A regression test failed.",
                                               raw_evidence="FAILED: assertion mismatch")
                    claim = knowledge.upsert_node("claim", "Feature is correct", metadata={"author_id": "builder"})
                    gate = IndependentReviewGate(
                        reviews, reviewer_id="critic",
                        call=lambda _: json.dumps({"approved": True, "summary": "Failure output validates counter-evidence.", "findings": []}),
                    )
                    updated = bridge.review_claim(
                        claim["id"], run_ref=run["run_ref"], raw_evidence="FAILED: assertion mismatch",
                        evidence_refs=(run["run_ref"],), review_gate=gate,
                    )
                    self.assertEqual(updated["status"], "refuted")
                finally:
                    reviews.close()

    def test_successful_status_is_downgraded_when_runtime_budget_was_exceeded(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                manifest = bridge.register_manifest(
                    title="Overtime benchmark", kind="benchmark", argv=("python", "benchmark.py"),
                    timeout_seconds=5,
                )
                run = bridge.record_result(
                    manifest["manifest_id"], status="succeeded", duration_seconds=6,
                    metrics={"iterations": 100}, summary="Benchmark completed over budget.",
                )
                self.assertEqual(run["status"], "budget_exceeded")

    def test_result_metrics_reject_non_numeric_or_non_finite_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                manifest = bridge.register_manifest(
                    title="Metrics", kind="test", argv=("python",),
                )
                with self.assertRaisesRegex(ValueError, "finite numeric"):
                    bridge.record_result(
                        manifest["manifest_id"], status="succeeded", duration_seconds=1,
                        metrics={"tests_run": float("nan")}, summary="Invalid metrics.",
                    )


    def test_experiment_run_id_is_idempotent_and_rejects_conflicting_replay(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                manifest = bridge.register_manifest(
                    title="Stable experiment", kind="test", argv=("python", "-m", "unittest"),
                )
                first = bridge.record_result(
                    manifest["manifest_id"], status="succeeded", duration_seconds=2.0,
                    metrics={"tests_run": 5, "failures": 0}, summary="Five tests passed.",
                    run_id="stable-run", raw_evidence="Ran 5 tests; OK",
                )
                event_count = knowledge.connection.execute(
                    "SELECT count(*) FROM knowledge_events WHERE entity_id=?", (first["node_id"],)
                ).fetchone()[0]
                replay = bridge.record_result(
                    manifest["manifest_id"], status="succeeded", duration_seconds=2.0,
                    metrics={"tests_run": 5, "failures": 0}, summary="Five tests passed.",
                    run_id="stable-run", raw_evidence="Ran 5 tests; OK",
                )
                self.assertEqual(replay, first)
                self.assertEqual(
                    knowledge.connection.execute(
                        "SELECT count(*) FROM knowledge_events WHERE entity_id=?", (first["node_id"],)
                    ).fetchone()[0],
                    event_count,
                )
                with self.assertRaisesRegex(ValueError, "different result"):
                    bridge.record_result(
                        manifest["manifest_id"], status="failed", duration_seconds=2.0,
                        metrics={"tests_run": 5, "failures": 1}, summary="Five tests failed.",
                        run_id="stable-run", raw_evidence="FAILED: assertion mismatch",
                    )
                stored = knowledge.get_node(first["node_id"])
                self.assertEqual(stored["status"], "succeeded")
                self.assertEqual(stored["metadata"]["raw_evidence_sha256"], hashlib.sha256(
                    b"Ran 5 tests; OK"
                ).hexdigest())

    def test_exact_replay_repairs_missing_manifest_edge(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                manifest = bridge.register_manifest(
                    title="Recoverable experiment", kind="test", argv=("python", "-m", "unittest"),
                )
                first = bridge.record_result(
                    manifest["manifest_id"], status="succeeded", duration_seconds=1.0,
                    metrics={"tests_run": 2}, summary="Two tests passed.",
                    run_id="recover-edge-run", raw_evidence="Ran 2 tests; OK",
                )
                knowledge.connection.execute(
                    "DELETE FROM knowledge_edges WHERE source_id=? AND target_id=? AND edge_type='tested_by'",
                    (manifest["node_id"], first["node_id"]),
                )
                knowledge.connection.commit()

                replay = bridge.record_result(
                    manifest["manifest_id"], status="succeeded", duration_seconds=1.0,
                    metrics={"tests_run": 2}, summary="Two tests passed.",
                    run_id="recover-edge-run", raw_evidence="Ran 2 tests; OK",
                )

                self.assertEqual(replay, first)
                edges = knowledge.list_edges(node_id=first["node_id"], edge_type="tested_by")
                self.assertEqual(len(edges), 1)
                self.assertEqual(edges[0]["source_id"], manifest["node_id"])
                self.assertEqual(edges[0]["target_id"], first["node_id"])
                event_count = knowledge.connection.execute(
                    "SELECT count(*) FROM knowledge_events WHERE entity_id=?", (first["node_id"],)
                ).fetchone()[0]
                bridge.record_result(
                    manifest["manifest_id"], status="succeeded", duration_seconds=1.0,
                    metrics={"tests_run": 2}, summary="Two tests passed.",
                    run_id="recover-edge-run", raw_evidence="Ran 2 tests; OK",
                )
                self.assertEqual(
                    knowledge.connection.execute(
                        "SELECT count(*) FROM knowledge_events WHERE entity_id=?", (first["node_id"],)
                    ).fetchone()[0],
                    event_count,
                )

if __name__ == "__main__":
    unittest.main()
