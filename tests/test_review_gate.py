from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.review_gate import IndependentReviewGate, ReviewStore


class IndependentReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.temp.name) / "reviews.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def gate(self, payload):
        return IndependentReviewGate(self.store, reviewer_id="reviewer-2",
                                     call=lambda _: json.dumps(payload))

    def request(self, gate):
        return gate.review(artifact_id="artifact-1", builder_id="builder-1",
                           artifact="patch", test_evidence="12 tests passed", lineage="run-42")

    def test_independent_reviewer_can_approve_and_audit_is_persisted(self):
        result = self.request(self.gate({"approved": True, "summary": "Looks sound", "findings": []}))
        self.assertTrue(result.approved)
        self.assertEqual(result.reviewer_id, "reviewer-2")
        self.assertEqual(len(self.store.list_for_artifact("artifact-1")), 1)

    def test_single_fenced_json_response_is_accepted_but_preamble_is_rejected(self):
        payload = {"approved": True, "summary": "Candidate passed review", "findings": []}
        raw = "```json\n" + json.dumps(payload) + "\n```"
        gate = IndependentReviewGate(self.store, reviewer_id="reviewer-2", call=lambda _: raw)
        result = self.request(gate)
        self.assertTrue(result.approved)
        self.assertEqual(len(self.store.list_for_artifact("artifact-1")), 1)

        invalid = "Review result:\n" + raw
        gate = IndependentReviewGate(self.store, reviewer_id="reviewer-2", call=lambda _: invalid)
        with self.assertRaisesRegex(ValueError, "plain JSON"):
            self.request(gate)
        self.assertEqual(len(self.store.list_for_artifact("artifact-1")), 1)

    def test_fenced_json_does_not_bypass_critical_or_high_finding_veto(self):
        payload = {"approved": True, "summary": "A high-risk issue remains", "findings": [
            {"severity": "high", "title": "Unresolved risk", "detail": "The candidate is not safe to promote."}
        ]}
        raw = "```json\n" + json.dumps(payload) + "\n```"
        gate = IndependentReviewGate(self.store, reviewer_id="reviewer-2", call=lambda _: raw)
        self.assertFalse(self.request(gate).approved)

    def test_builder_cannot_review_own_artifact(self):
        gate = IndependentReviewGate(self.store, reviewer_id="builder-1", call=lambda _: "{}")
        with self.assertRaises(PermissionError):
            self.request(gate)

    def test_critical_or_high_finding_vetoes_approval(self):
        result = self.request(self.gate({"approved": True, "summary": "Risk found", "findings": [
            {"severity": "high", "title": "Unsafe path", "detail": "Path is not constrained"}
        ]}))
        self.assertFalse(result.approved)

    def test_invalid_or_unstructured_reviewer_output_fails_closed(self):
        gate = IndependentReviewGate(self.store, reviewer_id="reviewer-2", call=lambda _: "approved")
        with self.assertRaises(ValueError):
            self.request(gate)
        self.assertEqual(self.store.list_for_artifact("artifact-1"), [])

    def test_missing_evidence_is_rejected_before_call(self):
        called = []
        gate = IndependentReviewGate(self.store, reviewer_id="reviewer-2", call=lambda _: called.append(True) or "{}")
        with self.assertRaises(ValueError):
            gate.review(artifact_id="a", builder_id="b", artifact="x", test_evidence="", lineage="r")
        self.assertEqual(called, [])


    def test_review_rejects_oversized_artifact_before_call(self):
        called = []
        gate = IndependentReviewGate(self.store, reviewer_id="reviewer-2",
                                     call=lambda _: called.append(True) or "{}")
        with self.assertRaises(ValueError):
            gate.review(artifact_id="a", builder_id="b", artifact="x" * (256 * 1024 + 1),
                        test_evidence="tests", lineage="run")
        self.assertEqual(called, [])

    def test_review_rejects_oversized_model_response_and_findings(self):
        gate = IndependentReviewGate(self.store, reviewer_id="reviewer-2",
                                     call=lambda _: "x" * (64 * 1024 + 1))
        with self.assertRaises(ValueError):
            self.request(gate)
        too_many = {"approved": True, "summary": "ok", "findings": [
            {"severity": "info", "title": "note", "detail": "bounded"} for _ in range(33)
        ]}
        gate = IndependentReviewGate(self.store, reviewer_id="reviewer-2",
                                     call=lambda _: json.dumps(too_many))
        with self.assertRaises(ValueError):
            self.request(gate)
        self.assertEqual(self.store.list_for_artifact("artifact-1"), [])

    def test_review_records_privacy_minimal_progress_events(self):
        from muse.progress import ProgressEventStore
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressEventStore(Path(directory) / "progress.sqlite3")
            try:
                gate = IndependentReviewGate(
                    self.store, reviewer_id="reviewer-2",
                    call=lambda _: json.dumps({"approved": True, "summary": "private summary", "findings": []}),
                    progress_events=progress,
                )
                result = self.request(gate)
                self.assertTrue(result.approved)
                events = progress.list_events(category="review", limit=10)
                self.assertEqual([item["event"] for item in reversed(events)], ["started", "completed"])
                self.assertEqual(events[0]["summary"], "Independent review completed.")
                self.assertNotIn("private summary", repr(events))
                self.assertNotIn("patch", repr(events))
            finally:
                progress.close()

    def test_invalid_reviewer_output_records_failed_progress_without_leaking_output(self):
        from muse.progress import ProgressEventStore
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressEventStore(Path(directory) / "progress.sqlite3")
            try:
                gate = IndependentReviewGate(
                    self.store, reviewer_id="reviewer-2", call=lambda _: "secret raw reviewer output",
                    progress_events=progress,
                )
                with self.assertRaises(ValueError):
                    self.request(gate)
                events = progress.list_events(category="review", limit=10)
                self.assertEqual([item["event"] for item in reversed(events)], ["started", "failed"])
                self.assertEqual(events[0]["error_code"], "review.failed")
                self.assertNotIn("secret raw reviewer output", repr(events))
            finally:
                progress.close()

    def test_progress_store_failure_does_not_change_review_result(self):
        from unittest.mock import Mock
        progress = Mock()
        progress.record.side_effect = OSError("telemetry unavailable")
        gate = IndependentReviewGate(
            self.store, reviewer_id="reviewer-2",
            call=lambda _: json.dumps({"approved": True, "summary": "ok", "findings": []}),
            progress_events=progress,
        )
        self.assertTrue(self.request(gate).approved)

    def test_review_result_is_written_back_to_builder_effectiveness(self):
        from muse.agent_registry import AgentRegistry
        agents = AgentRegistry(Path(self.temp.name) / "agents.sqlite3")
        try:
            agents.register("builder-1", role="developer")
            agents.record_outcome("builder-1", task_kind="coding", succeeded=True)
            gate = IndependentReviewGate(
                self.store, reviewer_id="reviewer-2", call=lambda _: json.dumps(
                    {"approved": False, "summary": "Needs work", "findings": []}
                ), agent_registry=agents,
            )
            self.request(gate)
            metrics = agents.effectiveness("builder-1")
            self.assertEqual(metrics["completed"], 1)
            self.assertEqual(metrics["reviewed"], 1)
            self.assertEqual(metrics["review_pass_rate"], 0.0)
        finally:
            agents.close()

if __name__ == "__main__":
    unittest.main()
