from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.research_knowledge import ResearchKnowledgeStore


class ResearchKnowledgeStoreTests(unittest.TestCase):
    def test_upsert_is_idempotent_and_merges_attributions_without_source_body(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "knowledge.sqlite3"
            with ResearchKnowledgeStore(path) as store:
                first = store.upsert_node(
                    "source", "Muse docs", external_ref="https://example.com/docs",
                    metadata={"attributions": [{"query": "Muse"}], "relevance_score": 90},
                )
                second = store.upsert_node(
                    "source", "Muse docs", external_ref="https://example.com/docs",
                    metadata={"attributions": [{"query": "Agent"}], "relevance_score": 50},
                )
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(len(second["metadata"]["attributions"]), 2)
                self.assertNotIn("text", second)
                self.assertEqual(store.stats()["nodes"], 1)
                self.assertEqual(store.stats()["events"], 2)
            with ResearchKnowledgeStore(path) as reopened:
                self.assertEqual(reopened.get_node(first["id"])["metadata"]["relevance_score"], 50)

    def test_typed_edges_are_idempotent_and_referentially_integral(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                source = store.upsert_node("source", "Evidence", external_ref="https://example.com/e")
                idea = store.upsert_node("idea", "Hypothesis", summary="Try a new approach")
                edge = store.add_edge(source["id"], idea["id"], "inspired_by")
                again = store.add_edge(source["id"], idea["id"], "inspired_by")
                self.assertEqual(edge["id"], again["id"])
                self.assertEqual(store.stats()["edges"], 1)
                with self.assertRaisesRegex(ValueError, "active node endpoints"):
                    store.add_edge("missing", idea["id"], "supports")

    def test_classified_failure_observation_links_redacted_hypothesis_and_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                recorded = store.record_classified_failure_research(
                    error_code="provider.unavailable", category="provider", occurrences=3,
                    retryable=2, non_retryable=1, lookback_hours=24,
                    observed_at="2026-11-10T00:00:00+00:00", proposal_id="proposal-safe-123",
                )
                failure = store.get_node(recorded["failure_id"])
                hypothesis = store.get_node(recorded["hypothesis_id"])
                self.assertEqual(failure["node_type"], "failure")
                self.assertEqual(failure["metadata"]["occurrences"], 3)
                self.assertEqual(hypothesis["node_type"], "idea")
                self.assertEqual(hypothesis["metadata"]["epistemic_status"], "hypothesis")
                self.assertEqual(hypothesis["metadata"]["improvement_proposal_id"], "proposal-safe-123")
                self.assertEqual(hypothesis["metadata"]["review_state"], "pending_human_review")
                self.assertTrue(hypothesis["metadata"]["approval_required"])
                self.assertEqual(hypothesis["metadata"]["counter_evidence"]["status"], "not_collected")
                edges = store.list_edges(node_id=hypothesis["id"])
                self.assertTrue(any(edge["metadata"].get("improvement_proposal_id") == "proposal-safe-123" for edge in edges))
                self.assertNotIn("task_id", failure["metadata"])
                self.assertNotIn("payload", str(recorded).casefold())

    def test_improvement_review_state_sync_is_redacted_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                recorded = store.record_classified_failure_research(
                    error_code="provider.unavailable", category="provider", occurrences=2,
                    retryable=2, non_retryable=0, lookback_hours=24,
                    observed_at="2026-11-10T00:00:00+00:00", proposal_id="proposal-review-1",
                )
                events = [
                    {"id": "event-1", "from_status": "proposed", "to_status": "researching",
                     "actor": "PRIVATE REVIEWER", "note": "PRIVATE REVIEW NOTE", "created_at": "2026-11-10T00:01:00+00:00"},
                    {"id": "event-2", "from_status": "researching", "to_status": "ready_for_review",
                     "actor": "PRIVATE REVIEWER", "note": "PRIVATE REVIEW NOTE", "created_at": "2026-11-10T00:02:00+00:00"},
                    {"id": "event-3", "from_status": "ready_for_review", "to_status": "approved",
                     "actor": "PRIVATE REVIEWER", "note": "PRIVATE REVIEW NOTE", "created_at": "2026-11-10T00:03:00+00:00"},
                ]
                result = store.sync_improvement_proposal_state(
                    "proposal-review-1", proposal_status="approved", events=events,
                )
                self.assertEqual(result, {"updated_nodes": 1, "unchanged_nodes": 0})
                node = store.get_node(recorded["hypothesis_id"])
                self.assertEqual(node["metadata"]["improvement_status"], "approved")
                self.assertEqual(node["metadata"]["review_state"], "human_approved")
                self.assertFalse(node["metadata"]["approval_required"])
                self.assertEqual([item["event_id"] for item in node["metadata"]["review_history"]],
                                 ["event-1", "event-2", "event-3"])
                self.assertNotIn("PRIVATE REVIEWER", str(node["metadata"]))
                self.assertNotIn("PRIVATE REVIEW NOTE", str(node["metadata"]))
                updated_at = node["updated_at"]
                repeated = store.sync_improvement_proposal_state(
                    "proposal-review-1", proposal_status="approved", events=events,
                )
                self.assertEqual(repeated, {"updated_nodes": 0, "unchanged_nodes": 1})
                self.assertEqual(store.get_node(recorded["hypothesis_id"])["updated_at"], updated_at)

    def test_improvement_review_state_sync_rejects_unknown_status_and_event(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                store.record_classified_failure_research(
                    error_code="provider.unavailable", category="provider", occurrences=2,
                    retryable=2, non_retryable=0, lookback_hours=24,
                    observed_at="2026-11-10T00:00:00+00:00", proposal_id="proposal-review-2",
                )
                with self.assertRaisesRegex(ValueError, "proposal_status"):
                    store.sync_improvement_proposal_state(
                        "proposal-review-2", proposal_status="mystery", events=[],
                    )
                with self.assertRaisesRegex(ValueError, "event status"):
                    store.sync_improvement_proposal_state(
                        "proposal-review-2", proposal_status="approved", events=[
                            {"id": "event-bad", "from_status": "proposed", "to_status": "hacked",
                             "created_at": "2026-11-10T00:00:00+00:00"},
                        ],
                    )
                rejected = store.record_classified_failure_research(
                    error_code="worker.timeout", category="worker", occurrences=2,
                    retryable=2, non_retryable=0, lookback_hours=24,
                    observed_at="2026-11-10T00:00:00+00:00", proposal_id="proposal-review-rejected",
                )
                store.sync_improvement_proposal_state(
                    "proposal-review-rejected", proposal_status="rejected", events=[],
                )
                rejected_node = store.get_node(rejected["hypothesis_id"])
                self.assertEqual(rejected_node["metadata"]["review_state"], "human_rejected")
                self.assertEqual(rejected_node["metadata"]["human_decision"], "rejected")
                self.assertFalse(rejected_node["metadata"]["approval_required"])

    def test_classified_failure_experiment_records_counter_evidence_without_raw_output(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                recorded = store.record_classified_failure_research(
                    error_code="provider.unavailable", category="provider", occurrences=2,
                    retryable=2, non_retryable=0, lookback_hours=24,
                    observed_at="2026-11-10T00:00:00+00:00", proposal_id="proposal-1",
                )
                source = store.upsert_node("source", "Provider contract", external_ref="https://example.test/provider")
                experiment = store.record_classified_failure_experiment(
                    recorded["hypothesis_id"], run_id="test-run-001", outcome="refutes",
                    test_count=4, passed_count=4, failed_count=0, evidence_refs=(source["id"],),
                )
                repeated = store.record_classified_failure_experiment(
                    recorded["hypothesis_id"], run_id="test-run-001", outcome="refutes",
                    test_count=4, passed_count=4, failed_count=0, evidence_refs=(source["id"],),
                )
                self.assertEqual(repeated["experiment_id"], experiment["experiment_id"])
                with self.assertRaisesRegex(ValueError, "already recorded"):
                    store.record_classified_failure_experiment(
                        recorded["hypothesis_id"], run_id="test-run-001", outcome="supports",
                        test_count=4, passed_count=3, failed_count=1, evidence_refs=(source["id"],),
                    )
                self.assertEqual(experiment["outcome"], "refutes")
                node = store.get_node(experiment["experiment_id"])
                self.assertEqual(node["node_type"], "experiment")
                self.assertEqual(node["metadata"]["failed_count"], 0)
                self.assertEqual(node["metadata"]["evidence_refs"], [source["id"]])
                edges = store.list_edges(node_id=recorded["hypothesis_id"])
                self.assertTrue(any(edge["edge_type"] == "refutes" and edge["target_id"] == node["id"] for edge in edges))
                updated_hypothesis = store.get_node(recorded["hypothesis_id"])
                self.assertEqual(updated_hypothesis["metadata"]["counter_evidence"]["status"], "recorded")
                self.assertIn(node["id"], updated_hypothesis["metadata"]["counter_evidence"]["experiment_ids"])
                self.assertEqual(updated_hypothesis["metadata"]["epistemic_status"], "hypothesis")
                self.assertEqual(updated_hypothesis["metadata"]["improvement_proposal_id"], "proposal-1")
                self.assertEqual(updated_hypothesis["metadata"]["review_state"], "pending_human_review")
                self.assertTrue(updated_hypothesis["metadata"]["approval_required"])
                store.record_classified_failure_research(
                    error_code="provider.unavailable", category="provider", occurrences=3,
                    retryable=2, non_retryable=1, lookback_hours=24,
                    observed_at="2026-11-10T01:00:00+00:00", proposal_id="proposal-1",
                )
                preserved = store.get_node(recorded["hypothesis_id"])
                self.assertEqual(preserved["metadata"]["counter_evidence"]["status"], "recorded")
                self.assertNotIn("stdout", str(node["metadata"]))

    def test_classified_failure_experiment_rejects_unknown_evidence_and_invalid_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                recorded = store.record_classified_failure_research(
                    error_code="provider.unavailable", category="provider", occurrences=2,
                    retryable=2, non_retryable=0, lookback_hours=24,
                    observed_at="2026-11-10T00:00:00+00:00", proposal_id="proposal-1",
                )
                with self.assertRaisesRegex(ValueError, "evidence reference"):
                    store.record_classified_failure_experiment(
                        recorded["hypothesis_id"], run_id="test-run-002", outcome="supports",
                        test_count=1, passed_count=1, failed_count=0, evidence_refs=("missing",),
                    )
                with self.assertRaisesRegex(ValueError, "counts"):
                    store.record_classified_failure_experiment(
                        recorded["hypothesis_id"], run_id="test-run-003", outcome="supports",
                        test_count=1, passed_count=2, failed_count=0,
                    )

    def test_claim_cannot_be_supported_without_evidence_and_reviewer(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node("claim", "Claim X", status="proposed")
                with self.assertRaisesRegex(ValueError, "evidence reference"):
                    store.transition_claim(claim["id"], "supported", evidence_refs=(), reviewer_id="critic")
                with self.assertRaisesRegex(ValueError, "stored source or experiment"):
                    store.transition_claim(claim["id"], "supported", evidence_refs=("https://missing",), reviewer_id="critic")
                source = store.upsert_node(
                    "source", "Evidence", external_ref="https://example.com/e",
                    metadata={"structured_extract": {
                        "content_trust": "untrusted_page_derived",
                        "verification_status": "not_independently_verified",
                        "instruction_authority": False, "promotion_eligible": False,
                    }},
                )
                with self.assertRaisesRegex(ValueError, "reviewed claim-evidence candidate"):
                    store.transition_claim(
                        claim["id"], "supported", evidence_refs=("https://example.com/e",),
                        reviewer_id="independent-reviewer",
                    )
                candidate = store.record_claim_evidence(
                    claim["id"], source["id"], direction="support_search_candidate",
                )
                store.record_claim_evidence_review(
                    candidate["id"], reviewer_id="evidence-reviewer", verdict="supports",
                    quality="moderate", independence="unassessed",
                    rationale="A separately reviewed citation supports the claim.",
                    citation_anchor={
                        "verified": True, "source_url": "https://example.com/e",
                        "source_sha256": "a" * 64, "excerpt": "support evidence",
                        "start_char": 0, "end_char": 16, "excerpt_sha256": "b" * 64,
                        "fetched_at": "2026-11-20T00:00:00+00:00", "content_type": "text/html",
                    },
                )
                updated = store.transition_claim(
                    claim["id"], "supported", evidence_refs=("https://example.com/e",), reviewer_id="independent-reviewer"
                )
                self.assertEqual(updated["status"], "supported")
                self.assertEqual(updated["metadata"]["reviewer_id"], "independent-reviewer")
                self.assertEqual(store.stats()["edges"], 2)
                self.assertEqual(store.lint(), [])

    def test_claim_reviewer_must_be_independent_and_evidence_must_be_a_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node("claim", "Claim Y", metadata={"author_id": "builder"})
                idea = store.upsert_node("idea", "Unverified idea", external_ref="idea:1")
                with self.assertRaisesRegex(ValueError, "independent"):
                    store.transition_claim(claim["id"], "supported", evidence_refs=("idea:1",), reviewer_id="builder")
                with self.assertRaisesRegex(ValueError, "source or experiment"):
                    store.transition_claim(claim["id"], "supported", evidence_refs=("idea:1",), reviewer_id="critic")
                source = store.upsert_node("source", "Paper", external_ref="https://example.com/paper")
                candidate = store.record_claim_evidence(
                    claim["id"], source["id"], direction="counter_search_candidate",
                )
                store.record_claim_evidence_review(
                    candidate["id"], reviewer_id="evidence-reviewer", verdict="refutes",
                    quality="low", independence="unassessed",
                    rationale="A reviewed citation contradicts the claim.",
                    citation_anchor={
                        "verified": True, "source_url": "https://example.com/paper",
                        "source_sha256": "c" * 64, "excerpt": "counter evidence",
                        "start_char": 0, "end_char": 16, "excerpt_sha256": "d" * 64,
                        "fetched_at": "2026-11-20T00:00:00+00:00", "content_type": "text/html",
                    },
                )
                updated = store.transition_claim(
                    claim["id"], "refuted", evidence_refs=("https://example.com/paper",), reviewer_id="critic"
                )
                self.assertEqual(updated["status"], "refuted")
                self.assertEqual(store.stats()["edges"], 2)

    def test_claim_promotion_rejects_review_verdict_from_wrong_search_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node("claim", "A claim requiring supporting evidence")
                source = store.upsert_node("source", "Paper", external_ref="https://example.com/wrong-direction")
                candidate = store.record_claim_evidence(
                    claim["id"], source["id"], direction="counter_search_candidate",
                )
                store.record_claim_evidence_review(
                    candidate["id"], reviewer_id="evidence-reviewer", verdict="supports",
                    quality="moderate", independence="unassessed",
                    rationale="The review verdict conflicts with the search direction.",
                    citation_anchor={
                        "verified": True, "source_url": "https://example.com/wrong-direction",
                        "source_sha256": "e" * 64, "excerpt": "support evidence",
                        "start_char": 0, "end_char": 16, "excerpt_sha256": "f" * 64,
                        "fetched_at": "2026-11-20T00:00:00+00:00", "content_type": "text/html",
                    },
                )
                with self.assertRaisesRegex(ValueError, "direction-matched reviewed claim-evidence"):
                    store.transition_claim(
                        claim["id"], "supported", evidence_refs=("https://example.com/wrong-direction",),
                        reviewer_id="final-reviewer",
                    )

    def test_contested_claim_requires_both_reviewed_source_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node("claim", "A disputed claim")
                source_specs = (
                    ("https://support.example/paper", "support_search_candidate", "supports", "support evidence", "a", "b"),
                    ("https://counter.example/check", "counter_search_candidate", "refutes", "counter evidence", "c", "d"),
                )
                refs = []
                for url, direction, verdict, excerpt, source_hash, excerpt_hash in source_specs:
                    source = store.upsert_node("source", verdict, external_ref=url)
                    refs.append(url)
                    evidence = store.record_claim_evidence(claim["id"], source["id"], direction=direction)
                    store.record_claim_evidence_review(
                        evidence["id"], reviewer_id=f"reviewer-{verdict}", verdict=verdict,
                        quality="moderate", independence="unassessed",
                        rationale=f"Reviewed {verdict} citation.",
                        citation_anchor={
                            "verified": True, "source_url": url,
                            "source_sha256": source_hash * 64, "excerpt": excerpt,
                            "start_char": 0, "end_char": len(excerpt),
                            "excerpt_sha256": excerpt_hash * 64,
                            "fetched_at": "2026-11-20T00:00:00+00:00", "content_type": "text/html",
                        },
                    )
                updated = store.transition_claim(
                    claim["id"], "contested", evidence_refs=refs, reviewer_id="final-reviewer",
                )
                self.assertEqual(updated["status"], "contested")

    def test_lint_surfaces_contradictory_claim_links(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                left = store.upsert_node("claim", "Claim A")
                right = store.upsert_node("claim", "Claim B")
                store.add_edge(left["id"], right["id"], "contradicts")
                findings = store.lint()
                self.assertIn("contradictory_claims", {item["code"] for item in findings})

    def test_search_context_and_research_run_persist_linked_sources_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                failure = store.record_failure(
                    "Alpha search timeout", summary="alpha source endpoint timeout",
                    signature="alpha-timeout", reason_to_avoid="Use bounded retries only.",
                )
                source = store.upsert_node("source", "Alpha source", external_ref="https://example.com/alpha")
                context = store.search_context("alpha research timeout")
                self.assertEqual(context[0]["id"], failure["id"])
                recorded = store.record_research_run(
                    "alpha research", run_id="run-alpha", source_refs=("https://example.com/alpha",),
                    status="partial", failures=({"stage": "fetch", "error": "TimeoutError"},), task_id="ticket-alpha",
                )
                self.assertEqual(recorded["linked_source_count"], 1)
                self.assertEqual(store.get_node(recorded["experiment_id"])["metadata"]["task_id"], "ticket-alpha")
                run_node = store.get_node(recorded["experiment_id"])
                self.assertEqual(run_node["metadata"]["execution_class"], "research_source_retrieval")
                self.assertEqual(run_node["metadata"]["epistemic_status"], "research_observation_not_claim_verdict")
                self.assertFalse(run_node["metadata"]["verdict_assigned"])

                self.assertEqual(len(recorded["failure_ids"]), 1)
                self.assertGreaterEqual(store.stats()["edges"], 2)
                failure_edges = store.list_edges(node_id=recorded["failure_ids"][0])
                self.assertEqual({edge["edge_type"] for edge in failure_edges}, {"relates", "addresses_gap"})
                context = store.search_context("TimeoutError", limit=8)
                origins = {item["id"]: item.get("context_origin") for item in context}
                self.assertEqual(origins[recorded["experiment_id"]], "graph_neighbor")

    def test_commander_failure_is_linked_to_run_and_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                recorded = store.record_commander_outcome(
                    task_id="ticket-failed", goal="Research failed approach", run_id="run-failed",
                    plan_id="plan-failed", status="failed", review_approved=False, reviewer_id=None,
                )
                failure = store.search_failures("Commander execution failed")[0]
                edges = store.list_edges(node_id=failure["id"])
                self.assertEqual({edge["edge_type"] for edge in edges}, {"relates", "addresses_gap"})
                self.assertIn(recorded["experiment_id"], {edge["source_id"] for edge in edges})
                self.assertEqual(store.lint(), [])

    def test_commander_notes_remain_proposed_until_evidence_and_independent_review(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                recorded = store.record_commander_outcome(
                    task_id="ticket-1", goal="Research alpha", run_id="run-1", plan_id="plan-1",
                    status="succeeded", review_approved=True, reviewer_id="critic-1",
                    researcher_outputs=({"step_id": "researcher-1", "output": "A potentially useful finding requiring primary-source verification."},),
                )
                self.assertEqual(len(recorded["proposed_claim_ids"]), 1)
                claim = store.get_node(recorded["proposed_claim_ids"][0])
                self.assertEqual(claim["status"], "proposed")
                self.assertEqual(claim["metadata"]["review_state"], "proposed_not_fact_checked")
                with self.assertRaisesRegex(ValueError, "stored source or experiment"):
                    store.transition_claim(claim["id"], "supported", evidence_refs=("ticket:ticket-1",), reviewer_id="critic-1")

    def test_failure_memory_is_deduplicated_searchable_and_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                one = store.record_failure(
                    "Repeated timeout", summary="Search endpoint stalls", signature="timeout-search",
                    reason_to_avoid="Do not retry without new evidence", task_id="ticket-1",
                )
                two = store.record_failure(
                    "Repeated timeout", summary="Search endpoint stalls", signature="timeout-search",
                    reason_to_avoid="Use bounded backoff", task_id="ticket-2",
                )
                self.assertEqual(one["id"], two["id"])
                hits = store.search_failures("search timeout")
                self.assertEqual(hits[0]["id"], one["id"])
                self.assertEqual(store.stats()["nodes"], 1)
                self.assertEqual(len(store.list_events(entity_id=one["id"])), 2)
                self.assertEqual(set(two["metadata"]["task_ids"]), {"ticket-1", "ticket-2"})

    def test_lint_detects_advanced_claim_with_reviewer_but_missing_evidence(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node("claim", "Corrupt claim")
                with store.connection:
                    store.connection.execute(
                        "UPDATE knowledge_nodes SET status='supported',metadata_json=? WHERE id=?",
                        (json.dumps({"reviewer_id": "critic"}), claim["id"]),
                    )
                findings = store.lint()
                self.assertIn("claim_missing_evidence_or_reviewer", {item["code"] for item in findings})

    def test_tombstones_preserve_audit_history_across_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "knowledge.sqlite3"
            with ResearchKnowledgeStore(path) as store:
                node = store.upsert_node("idea", "Retired hypothesis")
                tombstone = store.tombstone_node(
                    node["id"], actor="maintainer", reason="Superseded by stronger evidence"
                )
                self.assertEqual(tombstone["status"], "deleted")
                self.assertIsNone(store.get_node(node["id"]))
                self.assertEqual(store.list_events(entity_id=node["id"])[0]["event_type"], "node_tombstoned")
            with ResearchKnowledgeStore(path) as reopened:
                self.assertIsNone(reopened.get_node(node["id"]))
                self.assertEqual(reopened.list_events(entity_id=node["id"])[0]["event_type"], "node_tombstoned")

    def test_schema_version_mismatch_fails_closed(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE research_knowledge_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO research_knowledge_meta VALUES('schema_version','99')")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(RuntimeError, "unsupported research knowledge schema"):
                ResearchKnowledgeStore(path)

    def test_tombstoned_nodes_cannot_be_resurrected_or_receive_new_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                retired = store.upsert_node("source", "Old source", external_ref="https://example.com/old")
                idea = store.upsert_node("idea", "New idea")
                store.tombstone_node(retired["id"], actor="maintainer", reason="Source withdrawn")
                with self.assertRaisesRegex(ValueError, "cannot be silently resurrected"):
                    store.upsert_node("source", "Old source", external_ref="https://example.com/old")
                with self.assertRaisesRegex(ValueError, "active node endpoints"):
                    store.add_edge(retired["id"], idea["id"], "inspired_by")

    def test_events_are_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                node = store.upsert_node("decision", "Keep Commander unique")
                import sqlite3
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    store.connection.execute("DELETE FROM knowledge_events")
                    store.connection.commit()
                self.assertIsNotNone(store.get_node(node["id"]))


    def test_claim_evidence_dossier_preserves_original_claim_and_counter_search(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node(
                    "claim", "争议叙事", summary="模型整理后的简述",
                    metadata={
                        "author_id": "researcher",
                        "original_claim": "原始说法：某项秘密计划已经实施",
                        "original_claim_source": "https://origin.example/story",
                    },
                )
                support = store.upsert_node(
                    "source", "支持方向材料", external_ref="https://support.example/paper",
                )
                counter = store.upsert_node(
                    "source", "反面方向材料", external_ref="https://counter.example/check",
                )
                first = store.record_claim_evidence(
                    claim["id"], support["id"], direction="support_search_candidate",
                    evidence_type="peer_reviewed_paper", search_query="support query",
                )
                duplicate = store.record_claim_evidence(
                    claim["id"], support["id"], direction="support_search_candidate",
                    evidence_type="peer_reviewed_paper", search_query="support query",
                )
                store.record_claim_evidence(
                    claim["id"], counter["id"], direction="counter_search_candidate",
                    evidence_type="fact_check", search_query="counter query",
                )
                self.assertEqual(first["id"], duplicate["id"])

                store.record_claim_review(
                    claim["id"], reviewer_id="reviewer-1",
                    assessment={"verdict": "insufficient", "rationale": "目前证据不足"},
                    evidence_refs=("https://support.example/paper",),
                    confidence_level="low",
                )
                store.record_claim_review(
                    claim["id"], reviewer_id="reviewer-2",
                    assessment={"verdict": "contested", "rationale": "出现相互冲突的材料"},
                    evidence_refs=("https://counter.example/check",),
                    confidence_level="moderate",
                )
                dossier = store.claim_dossier(claim["id"])
                self.assertEqual(dossier["original_claim"], "原始说法：某项秘密计划已经实施")
                self.assertEqual(dossier["original_source"], "https://origin.example/story")
                self.assertEqual(dossier["claim_status"], "proposed")
                self.assertEqual(dossier["evidence_counts"]["support_search_candidate"], 1)
                self.assertEqual(dossier["evidence_counts"]["counter_search_candidate"], 1)
                self.assertTrue(all(item["quality"] == "unassessed" for item in dossier["evidence"]))
                self.assertTrue(all(item["independence"] == "unassessed" for item in dossier["evidence"]))
                self.assertEqual(dossier["confidence"]["level"], "moderate")
                self.assertFalse(dossier["confidence"]["automatic_scoring"])
                self.assertEqual(len(dossier["confidence_history"]), 2)
                limited = store.claim_dossier(claim["id"], evidence_limit=1)
                self.assertEqual(len(limited["evidence"]), 1)
                self.assertEqual(limited["evidence_counts"]["support_search_candidate"], 1)
                self.assertEqual(limited["evidence_counts"]["counter_search_candidate"], 1)
                self.assertIn("source_metadata", dossier["evidence"][0])
                import sqlite3
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    with store.connection:
                        store.connection.execute(
                            "UPDATE claim_evidence SET quality='high' WHERE id=?",
                            (first["id"],),
                        )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    with store.connection:
                        store.connection.execute(
                            "DELETE FROM claim_evidence WHERE id=?", (first["id"],)
                        )

                self.assertEqual(store.stats()["edges"], 2)
                self.assertEqual(
                    {edge["edge_type"] for edge in store.list_edges(node_id=claim["id"])},
                    {"relates"},
                )

    def test_claim_review_records_completion_without_promoting_proposed_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node(
                    "claim", "Creator experiment hypothesis",
                    metadata={"review_state": "researched_needs_independent_review"},
                )
                source = store.upsert_node(
                    "source", "Unrelated source",
                    external_ref="https://example.org/unrelated",
                )
                store.record_claim_evidence(
                    claim["id"], source["id"], direction="support_search_candidate",
                    evidence_type="retrieved_web_source", search_query="creator title A/B test",
                )
                reviewed = store.record_claim_review(
                    claim["id"], reviewer_id="external_web_audit",
                    assessment={
                        "verdict": "insufficient",
                        "rationale": "The source does not study title variants or watch-time share.",
                    },
                    evidence_refs=(source["id"],),
                    confidence_level="unassessed",
                )
                self.assertEqual(reviewed["status"], "proposed")
                self.assertEqual(reviewed["metadata"]["research_claim_review_status"], "completed")
                self.assertEqual(reviewed["metadata"]["research_claim_review_verdict"], "insufficient")
                self.assertEqual(reviewed["metadata"]["review_state"], "reviewed_insufficient_evidence")
                self.assertTrue(reviewed["metadata"]["claim_review"]["does_not_promote_claim"])
                dossier = store.claim_dossier(claim["id"])
                self.assertEqual(dossier["claim_status"], "proposed")
                self.assertEqual(dossier["confidence"]["level"], "unassessed")
                self.assertEqual(dossier["confidence_history"][-1]["verdict"], "insufficient")

    def test_evidence_reviews_and_source_lineage_are_append_only_and_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node(
                    "claim", "A testable claim", metadata={"author_id": "claim-author"}
                )
                source = store.upsert_node(
                    "source", "Primary source", external_ref="https://example.org/primary"
                )
                derivative = store.upsert_node(
                    "source", "Syndicated copy", external_ref="https://mirror.example/copy"
                )
                evidence = store.record_claim_evidence(
                    claim["id"], source["id"], direction="context_candidate"
                )
                with self.assertRaisesRegex(ValueError, "independent from claim author"):
                    store.record_claim_evidence_review(
                        evidence["id"], reviewer_id="claim-author", verdict="supports",
                        rationale="Self-review must be rejected.",
                    )
                review = store.record_claim_evidence_review(
                    evidence["id"], reviewer_id="reviewer-a", verdict="mentions",
                    quality="moderate", independence="unassessed",
                    rationale="The page discusses the topic but does not establish the claim.",
                )
                relation = store.record_source_relationship(
                    derivative["id"], source["id"], relationship="syndicated_from",
                    reviewer_id="reviewer-b", confidence="moderate",
                    basis="The copy attributes the text to the primary publisher.",
                )
                dossier = store.claim_dossier(claim["id"])
                item = dossier["evidence"][0]
                self.assertEqual(item["reviews"][0]["id"], review["id"])
                self.assertEqual(item["reviews"][0]["verdict"], "mentions")
                self.assertEqual(item["source_relationships"][0]["id"], relation["id"])
                self.assertEqual(dossier["claim_status"], "proposed")
                import sqlite3
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    with store.connection:
                        store.connection.execute(
                            "DELETE FROM claim_evidence_reviews WHERE id=?", (review["id"],)
                        )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    with store.connection:
                        store.connection.execute(
                            "UPDATE source_relationships SET confidence='high' WHERE id=?",
                            (relation["id"],),
                        )

    def test_claim_dossier_reports_direction_review_quality_and_lineage_coverage_without_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node(
                    "claim", "A disputed hypothesis",
                    metadata={"author_id": "claim-author", "original_claim": "Original disputed wording"},
                    status="proposed",
                )
                source_a = store.upsert_node(
                    "source", "Primary paper", external_ref="https://journal.example.com/paper",
                    metadata={
                        "content_hash": "a" * 64, "chars": 900,
                        "source_identity": {"hostname": "journal.example.com", "registrable_domain": "example.com"},
                        "source_signals": {"https": True, "content_nontrivial": True,
                                           "publication_date": {"status": "parsed"}},
                    },
                )
                source_b = store.upsert_node(
                    "source", "Syndicated report", external_ref="https://mirror.example.com/report",
                    metadata={
                        "content_hash": "b" * 64, "chars": 600,
                        "source_identity": {"hostname": "mirror.example.com", "registrable_domain": "example.com"},
                        "source_signals": {"https": True, "content_nontrivial": True,
                                           "publication_date": {"status": "parsed"}},
                    },
                )
                source_c = store.upsert_node(
                    "source", "Official dataset", external_ref="http://agency.gov/data",
                    metadata={
                        "source_identity": {"hostname": "agency.gov", "registrable_domain": "agency.gov"},
                        "source_signals": {"https": False, "content_nontrivial": False,
                                           "publication_date": {"status": "missing"}},
                    },
                )
                evidence_a = store.record_claim_evidence(
                    claim["id"], source_a["id"], direction="support_search_candidate",
                )
                evidence_b = store.record_claim_evidence(
                    claim["id"], source_b["id"], direction="counter_search_candidate",
                )
                evidence_c = store.record_claim_evidence(
                    claim["id"], source_c["id"], direction="context_candidate",
                )
                anchor_a = {"verified": True, "source_url": "https://journal.example.com/paper",
                            "source_sha256": "a" * 64, "excerpt": "claim text",
                            "start_char": 10, "end_char": 20, "excerpt_sha256": "b" * 64,
                            "fetched_at": "2026-11-18T00:00:00+00:00", "content_type": "text/html"}
                anchor_b = {**anchor_a, "source_url": "https://mirror.example.com/report"}
                store.record_claim_evidence_review(
                    evidence_a["id"], reviewer_id="reviewer-a", verdict="supports",
                    quality="high", independence="independent", rationale="Primary methods and data are traceable.",
                    citation_anchor=anchor_a,
                )
                store.record_claim_evidence_review(
                    evidence_b["id"], reviewer_id="reviewer-b", verdict="refutes",
                    quality="low", independence="dependent", rationale="This is a derivative copy of the primary paper.",
                    citation_anchor=anchor_b,
                )
                anchor_c = {**anchor_a, "source_url": "http://agency.gov/data"}
                store.record_claim_evidence_review(
                    evidence_c["id"], reviewer_id="reviewer-c", verdict="refutes",
                    quality="moderate", independence="independent",
                    rationale="The independent official dataset contradicts the claim.",
                    citation_anchor=anchor_c,
                )
                store.record_source_relationship(
                    source_b["id"], source_a["id"], relationship="syndicated_from",
                    reviewer_id="lineage-reviewer", confidence="high", basis="The article carries the original publisher attribution.",
                )
                store.record_source_relationship(
                    source_c["id"], source_a["id"], relationship="independent_of",
                    reviewer_id="lineage-reviewer", confidence="moderate", basis="Separate official dataset and custodian verified.",
                )

                dossier = store.claim_dossier(claim["id"])
                coverage = dossier["evidence_coverage"]
                self.assertEqual(dossier["claim_status"], "proposed")
                self.assertEqual(coverage["evidence_count"], 3)
                self.assertEqual(coverage["direction_counts"]["support_search_candidate"], 1)
                self.assertEqual(coverage["direction_counts"]["counter_search_candidate"], 1)
                self.assertTrue(coverage["both_support_and_counter_present"])
                self.assertEqual(coverage["reviewed_evidence_count"], 3)
                self.assertEqual(coverage["unreviewed_evidence_count"], 0)
                self.assertEqual(coverage["quality_counts"]["high"], 1)
                self.assertEqual(coverage["quality_counts"]["low"], 1)
                self.assertEqual(coverage["quality_counts"]["moderate"], 1)
                effective_by_source = {item["source_ref"]: item for item in dossier["evidence"]}
                self.assertEqual(effective_by_source["https://journal.example.com/paper"]["effective_quality"], "high")
                self.assertEqual(effective_by_source["https://journal.example.com/paper"]["latest_verdict"], "supports")
                self.assertEqual(effective_by_source["https://mirror.example.com/report"]["effective_independence"], "dependent")
                self.assertEqual(coverage["quality_counts"]["unassessed"], 0)
                self.assertEqual(coverage["independence_counts"]["independent"], 2)
                self.assertEqual(coverage["independence_counts"]["dependent"], 1)
                self.assertEqual(coverage["independence_counts"]["unassessed"], 0)
                self.assertEqual(coverage["source_integrity_signals"]["content_hash_present_count"], 2)
                self.assertEqual(coverage["source_integrity_signals"]["https_count"], 2)
                self.assertEqual(coverage["distinct_host_count"], 3)
                self.assertEqual(coverage["heuristic_registrable_domain_count"], 2)
                self.assertFalse(coverage["domain_grouping_is_ownership_proof"])
                self.assertEqual(coverage["source_lineage"]["dependency_relation_count"], 1)
                self.assertEqual(coverage["source_lineage"]["explicit_independence_pair_count"], 1)
                self.assertEqual(coverage["evidence_conflict_state"], "contested")
                self.assertTrue(coverage["conflict_requires_human_resolution"])
                self.assertEqual(coverage["reviewed_support_source_count"], 1)
                self.assertEqual(coverage["reviewed_refute_source_count"], 2)
                self.assertEqual(coverage["explicit_independent_cross_verdict_pair_count"], 1)
                self.assertEqual(coverage["independence_assessment"], "partially_assessed")
                lineage_sources = {item["url"]: item for item in coverage["source_lineage"]["sources"]}
                self.assertEqual(lineage_sources["https://journal.example.com/paper"]["registrable_domain"], "example.com")
                self.assertFalse(lineage_sources["https://journal.example.com/paper"]["grouping_is_ownership_proof"])
                self.assertEqual(coverage["overall_quality"], "not_automatically_scored")
                self.assertTrue(coverage["retrieval_direction_is_not_evidence_verdict"])


    def test_dossier_does_not_mislabel_related_provenance_as_original_claim_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node(
                    "claim", "Generated hypothesis",
                    summary="A generated claim", provenance=("https://related.example/source",),
                )
                dossier = store.claim_dossier(claim["id"])
                self.assertIsNone(dossier["original_source"])
                self.assertEqual(dossier["related_source_refs"], ["https://related.example/source"])

    def test_source_relationship_requires_two_existing_distinct_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                source = store.upsert_node("source", "Only source", external_ref="https://one.example")
                with self.assertRaisesRegex(ValueError, "two stored source nodes"):
                    store.record_source_relationship(
                        source["id"], "https://missing.example", relationship="cites",
                        reviewer_id="reviewer", basis="Unverified",
                    )
                with self.assertRaisesRegex(ValueError, "endpoints must differ"):
                    store.record_source_relationship(
                        source["id"], source["id"], relationship="cites",
                        reviewer_id="reviewer", basis="Self-citation",
                    )

    def test_claim_evidence_requires_a_stored_source_node(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node("claim", "待验证主张")
                with self.assertRaisesRegex(ValueError, "stored source"):
                    store.record_claim_evidence(
                        claim["id"], "https://missing.example/source",
                        direction="counter_search_candidate",
                    )

    def test_schema_v1_migrates_additively_to_schema_v5(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE research_knowledge_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO research_knowledge_meta(key,value) VALUES('schema_version','1')"
            )
            connection.commit()
            connection.close()
            with ResearchKnowledgeStore(path) as store:
                version = store.connection.execute(
                    "SELECT value FROM research_knowledge_meta WHERE key='schema_version'"
                ).fetchone()["value"]
                self.assertEqual(version, "5")
                claim = store.upsert_node("claim", "Legacy claim")
                source = store.upsert_node(
                    "source", "Legacy source", external_ref="https://legacy.example/source"
                )
                self.assertEqual(
                    store.record_claim_evidence(
                        claim["id"], source["id"], direction="context_candidate"
                    )["direction"],
                    "context_candidate",
                )

    def test_schema_v2_migrates_additively_to_schema_v5(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE research_knowledge_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO research_knowledge_meta(key,value) VALUES('schema_version','2')"
            )
            connection.commit()
            connection.close()
            with ResearchKnowledgeStore(path) as store:
                version = store.connection.execute(
                    "SELECT value FROM research_knowledge_meta WHERE key='schema_version'"
                ).fetchone()["value"]
                self.assertEqual(version, "5")
                tables = {
                    row["name"] for row in store.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("claim_evidence_reviews", tables)
                self.assertIn("source_relationships", tables)


    def test_source_identity_candidates_are_pending_deduplicated_and_only_promoted_after_review(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                source = store.upsert_node("source", "Mirror article", external_ref="https://mirror.example/story")
                original = store.upsert_node("source", "Original article", external_ref="https://original.example/story")
                candidate = store.record_source_identity_candidate(
                    source["id"], original["id"], candidate_type="same_publisher",
                    confidence=0.8, evidence={"rule": "exact publisher name match"},
                )
                duplicate = store.record_source_identity_candidate(
                    source["id"], original["id"], candidate_type="same_publisher",
                    confidence=0.8, evidence={"rule": "exact publisher name match"},
                )
                self.assertEqual(candidate["id"], duplicate["id"])
                self.assertEqual(candidate["status"], "pending")
                self.assertEqual(store.list_source_identity_candidates(status="pending")[0]["id"], candidate["id"])
                self.assertEqual(
                    store.connection.execute("SELECT count(*) FROM source_relationships").fetchone()[0], 0
                )
                store.review_source_identity_candidate(
                    candidate["id"], reviewer_id="human-reviewer", decision="accepted",
                    rationale="Publisher imprint verified manually.",
                )
                self.assertEqual(store.list_source_identity_candidates(status="pending"), [])
                accepted = store.list_source_identity_candidates(status="accepted")
                self.assertEqual(len(accepted), 1)
                self.assertEqual(accepted[0]["reviews"][0]["reviewer_id"], "human-reviewer")
                relationship = store.connection.execute(
                    "SELECT relationship,reviewer_id FROM source_relationships"
                ).fetchone()
                self.assertEqual(relationship["relationship"], "same_publisher")
                self.assertEqual(relationship["reviewer_id"], "human-reviewer") 
                older = store.upsert_node("source", "Older source", external_ref="https://older.example")
                newer = store.upsert_node("source", "Newer source", external_ref="https://newer.example")
                pending = store.record_source_identity_candidate(
                    source["id"], older["id"], candidate_type="canonical_equivalent",
                    confidence=0.95, evidence={"rule": "older canonical"},
                )
                store.record_source_identity_candidate(
                    source["id"], newer["id"], candidate_type="canonical_equivalent",
                    confidence=0.95, evidence={"rule": "newer canonical"},
                )
                self.assertEqual(len(store.list_source_identity_candidates(status="accepted", limit=1)), 1)
                self.assertEqual(
                    store.list_source_identity_candidates(status="accepted", limit=1)[0]["id"],
                    candidate["id"],
                )
                self.assertEqual(
                    store.record_source_identity_candidate(
                        source["id"], original["id"], candidate_type="same_publisher",
                        confidence=0.8, evidence={"rule": "repeat discovery"},
                    )["status"],
                    "accepted",
                )


    def test_source_identity_candidate_and_review_records_are_append_only(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                source = store.upsert_node("source", "Source A", external_ref="https://a.example")
                other = store.upsert_node("source", "Source B", external_ref="https://b.example")
                candidate = store.record_source_identity_candidate(
                    source["id"], other["id"], candidate_type="canonical_equivalent",
                    confidence=0.95, evidence={"rule": "canonical"},
                )
                review = store.review_source_identity_candidate(
                    candidate["id"], reviewer_id="reviewer", decision="rejected",
                    rationale="Canonical metadata is inconsistent with page content.",
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    with store.connection:
                        store.connection.execute(
                            "DELETE FROM source_identity_candidates WHERE id=?", (candidate["id"],)
                        )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    with store.connection:
                        store.connection.execute(
                            "UPDATE source_identity_candidate_reviews SET decision='accepted' WHERE id=?",
                            (review["id"],),
                        )
                self.assertEqual(store.list_source_identity_candidates(status="rejected")[0]["id"], candidate["id"])

    def test_source_identity_candidate_rejects_missing_self_and_invalid_confidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                source = store.upsert_node("source", "Source A", external_ref="https://a.example")
                with self.assertRaisesRegex(ValueError, "two stored source nodes"):
                    store.record_source_identity_candidate(
                        source["id"], "https://missing.example", candidate_type="cites",
                        confidence=0.9, evidence={},
                    )
                with self.assertRaisesRegex(ValueError, "endpoints must differ"):
                    store.record_source_identity_candidate(
                        source["id"], source["id"], candidate_type="cites",
                        confidence=0.9, evidence={},
                    )
                with self.assertRaisesRegex(ValueError, "between 0 and 1"):
                    store.record_source_identity_candidate(
                        source["id"], source["id"], candidate_type="cites",
                        confidence=2, evidence={},
                    )

    def test_schema_v3_migrates_additively_to_schema_v5(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v3.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE research_knowledge_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO research_knowledge_meta(key,value) VALUES('schema_version','3')"
            )
            connection.commit()
            connection.close()
            with ResearchKnowledgeStore(path) as store:
                version = store.connection.execute(
                    "SELECT value FROM research_knowledge_meta WHERE key='schema_version'"
                ).fetchone()["value"]
                self.assertEqual(version, "5")
                tables = {
                    row["name"] for row in store.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("source_identity_candidates", tables)
                self.assertIn("source_identity_candidate_reviews", tables)


    def test_schema_v4_migrates_edge_constraint_to_alternative_without_losing_edges(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v4.sqlite3"
            with ResearchKnowledgeStore(path) as store:
                left = store.upsert_node("idea", "Existing left node")
                right = store.upsert_node("idea", "Existing right node")
                prior = store.add_edge(left["id"], right["id"], "relates")
            connection = sqlite3.connect(path)
            connection.execute("ALTER TABLE knowledge_edges RENAME TO knowledge_edges_new")
            connection.execute("""CREATE TABLE knowledge_edges (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                target_id TEXT NOT NULL REFERENCES knowledge_nodes(id),
                edge_type TEXT NOT NULL CHECK(edge_type IN
                    ('supports','refutes','relates','inspired_by','contradicts',
                     'supersedes','tested_by','addresses_gap')),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(source_id,target_id,edge_type),
                CHECK(source_id <> target_id)
            )""")
            connection.execute(
                "INSERT INTO knowledge_edges SELECT id,source_id,target_id,edge_type,metadata_json,created_at "
                "FROM knowledge_edges_new"
            )
            connection.execute("DROP TABLE knowledge_edges_new")
            connection.execute(
                "CREATE INDEX idx_knowledge_edges_target ON knowledge_edges(target_id,edge_type)"
            )
            connection.execute(
                "UPDATE research_knowledge_meta SET value='4' WHERE key='schema_version'"
            )
            connection.commit()
            connection.close()
            with ResearchKnowledgeStore(path) as store:
                version = store.connection.execute(
                    "SELECT value FROM research_knowledge_meta WHERE key='schema_version'"
                ).fetchone()["value"]
                self.assertEqual(version, "5")
                self.assertEqual(store.list_edges(edge_type="relates")[0]["id"], prior["id"])
                alternative = store.add_edge(left["id"], right["id"], "alternative", metadata={
                    "review_state": "pending_human_review",
                })
                self.assertEqual(alternative["edge_type"], "alternative")
                schema = store.connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='knowledge_edges'"
                ).fetchone()["sql"]
                self.assertIn("'alternative'", schema)

    def test_source_lineage_snapshot_clusters_only_reviewed_dependency_and_tracks_unknowns(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                def source(name, url, host, publisher="Shared Press"):
                    return store.upsert_node(
                        "source", name, external_ref=url,
                        metadata={"source_identity": {
                            "hostname": host, "publisher": publisher,
                            "authors": ["Ada Lovelace"],
                            "canonical_url": url,
                        }},
                    )

                original = source("Original", "https://a.example/original", "a.example")
                mirror = source("Mirror", "https://b.example/mirror", "b.example")
                derivative = source("Derivative", "https://c.example/derivative", "c.example")
                separate = source("Separate", "https://d.example/separate", "d.example")
                unknown = source("Unknown", "https://e.example/unknown", "e.example", "Another Press")

                canonical = store.record_source_identity_candidate(
                    mirror["id"], original["id"], candidate_type="canonical_equivalent",
                    confidence=0.95, evidence={"rule": "canonical URL match"},
                )
                store.review_source_identity_candidate(
                    canonical["id"], reviewer_id="reviewer", decision="accepted",
                    rationale="Both pages declare the same canonical article.",
                )
                derived = store.record_source_identity_candidate(
                    derivative["id"], mirror["id"], candidate_type="derived_from",
                    confidence=0.9, evidence={"rule": "isBasedOn metadata"},
                )
                store.review_source_identity_candidate(
                    derived["id"], reviewer_id="reviewer", decision="accepted",
                    rationale="The source explicitly declares the underlying article.",
                )
                store.record_source_relationship(
                    original["id"], separate["id"], relationship="independent_of",
                    reviewer_id="reviewer", basis="Different independently authored research records.",
                    confidence="moderate",
                )
                store.record_source_relationship(
                    separate["id"], unknown["id"], relationship="same_publisher",
                    reviewer_id="reviewer", basis="Same publishing imprint.",
                    confidence="high",
                )
                pending = store.record_source_identity_candidate(
                    unknown["id"], original["id"], candidate_type="same_publisher",
                    confidence=0.8, evidence={"rule": "publisher name overlap"},
                )

                snapshot = store.source_lineage_snapshot([
                    "https://a.example/original",
                    "https://b.example/mirror",
                    "https://c.example/derivative",
                    "https://d.example/separate",
                    "https://e.example/unknown",
                ])
                self.assertEqual(snapshot["source_count"], 5)
                self.assertEqual(snapshot["dependency_relation_count"], 2)
                self.assertEqual(snapshot["dependency_linked_source_count"], 3)
                self.assertEqual(snapshot["explicit_independence_pair_count"], 1)
                self.assertEqual(snapshot["explicitly_independent_source_count"], 2)
                self.assertEqual(snapshot["unassessed_source_count"], 1)
                self.assertEqual(snapshot["independence_assessment"], "partially_assessed")
                self.assertEqual(len(snapshot["dependency_clusters"]), 1)
                self.assertEqual(
                    {item["source_node_id"] for item in snapshot["dependency_clusters"][0]["members"]},
                    {original["id"], mirror["id"], derivative["id"]},
                )
                self.assertNotIn(
                    unknown["id"],
                    {item["source_node_id"] for item in snapshot["dependency_clusters"][0]["members"]},
                )
                self.assertIn(pending["id"], {item["id"] for item in snapshot["identity_candidates"]})
                self.assertTrue(any(
                    item["relationship"] == "same_publisher"
                    for item in snapshot["reviewed_relationships"]
                ))
                self.assertIn("不等于已证明来源独立", snapshot["interpretation"])

    def test_source_lineage_snapshot_preserves_unknown_when_no_reviewed_relations_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                first = store.upsert_node("source", "First", external_ref="https://first.example")
                second = store.upsert_node("source", "Second", external_ref="https://second.example")
                snapshot = store.source_lineage_snapshot([
                    first["external_ref"], second["external_ref"],
                ])
                self.assertEqual(snapshot["source_count"], 2)
                self.assertEqual(snapshot["dependency_clusters"], [])
                self.assertEqual(snapshot["explicit_independence_pair_count"], 0)
                self.assertEqual(snapshot["explicitly_independent_source_count"], 0)
                self.assertEqual(snapshot["unassessed_source_count"], 2)
                self.assertEqual(snapshot["independence_assessment"], "unassessed")

    def test_source_lineage_snapshot_enforces_report_source_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                with self.assertRaisesRegex(ValueError, "at most 32"):
                    store.source_lineage_snapshot([f"https://source{index}.example" for index in range(33)])

    def test_claim_evidence_coverage_without_evidence_is_unknown_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                claim = store.upsert_node("claim", "No evidence yet", status="proposed")
                coverage = store.claim_evidence_coverage(claim["id"])
                self.assertEqual(coverage["coverage_state"], "no_evidence")
                self.assertEqual(coverage["evidence_count"], 0)
                self.assertEqual(coverage["source_count"], 0)
                self.assertEqual(coverage["quality_assessment_state"], "unassessed")
                self.assertEqual(coverage["source_lineage"]["source_count"], 0)
                self.assertEqual(coverage["overall_quality"], "not_automatically_scored")
                with self.assertRaisesRegex(ValueError, "source_limit"):
                    store.claim_evidence_coverage(claim["id"], source_limit=33)
                self.assertEqual(store.get_node(claim["id"])["status"], "proposed")


    def test_knowledge_node_links_to_creator_initiative_and_cycle(self):
        from muse.initiative_engine import InitiativeStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge, InitiativeStore(root / "initiatives.sqlite3") as initiatives:
                idea = knowledge.upsert_node("idea", "跨域创意", metadata={"epistemic_status": "hypothesis"})
                initiative = initiatives.propose(
                    "跨域作品", kind="creative_output", creator_value="把研究变成作品",
                    concept="研究与创作融合", first_step="制作原型", expected_output="可验证产品",
                )
                cycle_id = "cycle_" + "c" * 32
                first = knowledge.link_node_to_initiative(
                    initiatives, initiative["id"], idea["id"],
                    relationship="inspired_by", evolution_cycle_id=cycle_id,
                )
                second = knowledge.link_node_to_initiative(
                    initiatives, initiative["id"], idea["id"],
                    relationship="inspired_by", evolution_cycle_id=cycle_id,
                )
                self.assertEqual(first["initiative_link"]["id"], second["initiative_link"]["id"])
                self.assertIn(initiative["id"], second["node"]["metadata"]["creator_initiative_ids"])
                self.assertIn(cycle_id, second["node"]["metadata"]["evolution_cycle_ids"])

if __name__ == "__main__":
    unittest.main()
