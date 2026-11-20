from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from muse.commander_planner import CommanderPlanner
from muse.contextual_inquiry import (
    ContextualInquiry,
    Hypothesis,
    Observation,
    SafetyRoute,
    SourceKind,
)
from muse.contextual_inquiry_bridge import (
    build_workflow_contract, enqueue_review_task, load_encrypted_case_for_review, persist_hypotheses,
)
from muse.contextual_inquiry_security import protect_contextual_inquiry, unprotect_case_bytes
from muse.research_knowledge import ResearchKnowledgeStore
from muse.task_queue import TaskQueue
from muse.workflow_profiles import WorkflowContract, WorkflowController


class ContextualInquiryBridgeTests(unittest.TestCase):
    def inquiry(self, *, consent=False, safety=SafetyRoute.NORMAL):
        observation = Observation(
            "obs-1", SourceKind.USER_QUOTE,
            "A private personal detail that must not be copied to the graph.",
            "conversation:private-turn",
        )
        hypothesis = Hypothesis(
            "hyp-1", "A possible explanation worth testing.", ("obs-1",),
            counter_refs=(), confidence=0.4,
            falsification_condition="A reliable independent record contradicts it.",
        )
        return ContextualInquiry(
            "case-bridge-1", "Understand a situation without assuming motives.",
            observations=(observation,), hypotheses=(hypothesis,),
            unknowns=("The other participant's perspective is not known.",),
            safety_route=safety, consent_to_store=consent,
        )
    def test_workflow_mapping_uses_existing_contract_without_writing(self):
        inquiry = self.inquiry()
        contract = build_workflow_contract(inquiry)
        self.assertIsInstance(contract, WorkflowContract)
        self.assertEqual(
            contract.goal,
            "Review a contextual inquiry using evidence provenance and safety boundaries.",
        )
        self.assertNotEqual(contract.goal, inquiry.user_goal)
        self.assertTrue(any("counter-evidence" in item for item in contract.acceptance_criteria))
        self.assertTrue(any("No diagnosis" in item for item in contract.exclusions))

    def test_user_goal_is_included_only_with_explicit_storage_consent(self):
        inquiry = self.inquiry()
        with self.assertRaises(PermissionError):
            build_workflow_contract(inquiry, include_user_goal=True)
        consented = self.inquiry(consent=True)
        contract = build_workflow_contract(consented, include_user_goal=True)
        self.assertEqual(contract.goal, consented.user_goal)
        self.assertTrue(any("No diagnosis" in item for item in contract.exclusions))

    def test_mapped_contract_binds_to_existing_commander_tickets(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("review a contextual inquiry")
                ticket_ids = {
                    step.step_id: queue.enqueue(step.step_id, {})["id"]
                    for step in plan.steps
                }
                contract = build_workflow_contract(self.inquiry())
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ticket_ids, contract=contract)
                snapshot = controller.snapshot(ticket_ids[plan.steps[0].step_id])
                self.assertEqual(snapshot["contract"], contract.to_dict())
                self.assertEqual(queue.workflow_revision(ticket_ids[plan.steps[0].step_id]), 1)

    def test_non_normal_safety_route_becomes_workflow_escalation(self):
        contract = build_workflow_contract(
            self.inquiry(safety=SafetyRoute.PROFESSIONAL_SUPPORT)
        )
        self.assertTrue(any("professional_support" in item for item in contract.escalation_conditions))

    def test_persistence_without_explicit_consent_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3")
            try:
                with self.assertRaises(PermissionError):
                    persist_hypotheses(store, self.inquiry(consent=False))
                self.assertEqual(store.list_nodes(node_type="claim"), [])
            finally:
                store.connection.close()

    def test_only_proposed_hypotheses_are_persisted_and_private_observations_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3")
            try:
                inquiry = self.inquiry(consent=True)
                ids = persist_hypotheses(store, inquiry)
                self.assertEqual(len(ids), 1)
                node = store.get_node(ids[0])
                self.assertEqual(node["node_type"], "claim")
                self.assertEqual(node["status"], "proposed")
                self.assertEqual(node["metadata"]["evidence_state"], "unverified_context_hypothesis")
                self.assertEqual(node["metadata"]["supporting_observation_refs"], ["obs-1"])
                self.assertNotIn("private personal detail", str(node))
                self.assertNotIn("conversation:private-turn", str(node))
                self.assertNotIn("The other participant", str(node))
            finally:
                store.connection.close()

    def test_hypothesis_evidence_links_only_exact_sources_as_unadjudicated_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                inquiry = ContextualInquiry(
                    "case-evidence-links", "Compare competing explanations.",
                    observations=(
                        Observation("obs-support", SourceKind.USER_QUOTE, "Private quote.", "source:primary"),
                        Observation("obs-counter", SourceKind.SYSTEM_RECORD, "Private record excerpt.", "source:counter"),
                    ),
                    hypotheses=(
                        Hypothesis(
                            "hyp-1", "A tentative hypothesis.",
                            source_refs=("obs-support",), counter_refs=("obs-counter",),
                            confidence=0.4,
                            falsification_condition="Independent evidence contradicts it.",
                        ),
                    ),
                    consent_to_store=True,
                )
                support = store.upsert_node("source", "Primary source", external_ref="source:primary")
                counter = store.upsert_node("source", "Counter source", external_ref="source:counter")
                claim_ids = persist_hypotheses(store, inquiry)
                rows = store.connection.execute(
                    "SELECT source_node_id,direction FROM claim_evidence WHERE claim_id=? ORDER BY direction",
                    (claim_ids[0],),
                ).fetchall()
                self.assertEqual(
                    {(row["source_node_id"], row["direction"]) for row in rows},
                    {
                        (support["id"], "support_search_candidate"),
                        (counter["id"], "counter_search_candidate"),
                    },
                )
                edges = store.list_edges(node_id=claim_ids[0])
                self.assertEqual({edge["edge_type"] for edge in edges}, {"relates"})
                self.assertNotIn("supports", {edge["edge_type"] for edge in edges})
                self.assertNotIn("refutes", {edge["edge_type"] for edge in edges})
                claim = store.get_node(claim_ids[0])
                self.assertEqual(claim["status"], "proposed")
                self.assertNotIn("Private quote", str(claim))
                self.assertNotIn("Private record excerpt", str(claim))

    def test_missing_ambiguous_inactive_and_non_source_references_are_not_linked(self):
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                observations = (
                    Observation("obs-good", SourceKind.USER_QUOTE, "Quote.", "ref:good"),
                    Observation("obs-missing", SourceKind.USER_QUOTE, "Quote.", "ref:missing"),
                    Observation("obs-ambiguous", SourceKind.USER_QUOTE, "Quote.", "ref:ambiguous"),
                    Observation("obs-inactive", SourceKind.USER_QUOTE, "Quote.", "ref:inactive"),
                    Observation("obs-not-source", SourceKind.USER_QUOTE, "Quote.", "ref:claim"),
                )
                inquiry = ContextualInquiry(
                    "case-evidence-resolution", "Resolve exact provenance.",
                    observations=observations,
                    hypotheses=(Hypothesis(
                        "hyp-1", "A tentative hypothesis.",
                        source_refs=tuple(item.observation_id for item in observations),
                        confidence=0.4,
                        falsification_condition="A verified source contradicts it.",
                    ),),
                    consent_to_store=True,
                )
                valid = store.upsert_node("source", "Valid source", external_ref="ref:good")
                store.upsert_node("source", "Ambiguous one", external_ref="ref:ambiguous")
                with store.connection:
                    stamp = store._now()
                    store.connection.execute(
                        "INSERT INTO knowledge_nodes(id,node_type,natural_key,title,summary,status,external_ref,metadata_json,provenance_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        ("ambiguous-source-duplicate", "source", "source:ambiguous-duplicate", "Ambiguous two", "", "active", "ref:ambiguous", "{}", "[]", stamp, stamp),
                    )
                inactive = store.upsert_node("source", "Inactive source", external_ref="ref:inactive")
                with store.connection:
                    store.connection.execute(
                        "UPDATE knowledge_nodes SET status='inactive' WHERE id=?", (inactive["id"],)
                    )
                store.upsert_node("claim", "Not a source", external_ref="ref:claim")
                claim_id = persist_hypotheses(store, inquiry)[0]
                rows = store.connection.execute(
                    "SELECT source_node_id,direction FROM claim_evidence WHERE claim_id=?",
                    (claim_id,),
                ).fetchall()
                self.assertEqual(len(rows), 1, [(row["source_node_id"], row["direction"]) for row in rows])
                self.assertEqual(rows[0]["source_node_id"], valid["id"])
                self.assertEqual(rows[0]["direction"], "support_search_candidate")

    def test_repeated_hypothesis_export_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3")
            try:
                inquiry = self.inquiry(consent=True)
                first = persist_hypotheses(store, inquiry)
                second = persist_hypotheses(store, inquiry)
                self.assertEqual(first, second)
                self.assertEqual(len(store.list_nodes(node_type="claim")), 1)
            finally:
                store.connection.close()

    def test_empty_hypothesis_set_does_not_create_empty_case_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3")
            try:
                inquiry = ContextualInquiry(
                    "case-empty", "Clarify a situation.", consent_to_store=True,
                )
                self.assertEqual(persist_hypotheses(store, inquiry), ())
                self.assertEqual(store.list_nodes(), [])
            finally:
                store.connection.close()


    def test_enqueue_review_task_stores_only_generic_contract_and_requires_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                inquiry = self.inquiry(consent=False, safety=SafetyRoute.PROFESSIONAL_SUPPORT)
                task = enqueue_review_task(queue, inquiry)
                self.assertEqual(task["status"], "waiting_approval")
                self.assertEqual(task["title"], "Contextual inquiry review - context required")
                self.assertEqual(task["payload"]["kind"], "contextual_inquiry_review")
                self.assertFalse(task["payload"]["case_context_attached"])
                self.assertEqual(task["payload"]["context_status"], "awaiting_user_reentry")
                self.assertTrue(task["payload"]["requires_context_reentry"])
                self.assertFalse(task["payload"]["long_term_storage_performed"])
                contract = task["payload"]["workflow_contract"]
                self.assertEqual(
                    contract["goal"],
                    "Review a contextual inquiry using evidence provenance and safety boundaries.",
                )
                self.assertTrue(any("professional_support" in item for item in contract["escalation_conditions"]))
                persisted = " ".join(
                    str(value)
                    for table in ("tasks", "task_events")
                    for row in queue._connection.execute(f"SELECT * FROM {table}").fetchall()
                    for value in tuple(row)
                )
                self.assertNotIn("A private personal detail", persisted)
                self.assertNotIn("conversation:private-turn", persisted)
                self.assertNotIn("Understand a situation without assuming motives", persisted)

    def test_enqueue_review_task_requires_consent_before_persisting_personal_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                with self.assertRaises(PermissionError):
                    enqueue_review_task(queue, self.inquiry(consent=False), include_user_goal=True)
                self.assertEqual(queue._connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
                task = enqueue_review_task(queue, self.inquiry(consent=True), include_user_goal=True)
                self.assertEqual(
                    task["payload"]["workflow_contract"]["goal"],
                    "Understand a situation without assuming motives.",
                )
                self.assertNotIn("A private personal detail", str(task["payload"]))

    def test_enqueue_review_task_is_idempotent_and_never_auto_dispatches(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                inquiry = self.inquiry(consent=True)
                first = enqueue_review_task(queue, inquiry)
                second = enqueue_review_task(queue, inquiry)
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(first["status"], "waiting_approval")
                self.assertEqual(queue._connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
                self.assertEqual(queue._connection.execute("SELECT COUNT(*) FROM task_events WHERE event_type='approval.requested'").fetchone()[0], 1)



    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration test")
    def test_encrypted_case_can_be_opened_only_for_pending_review_and_is_not_attached(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                inquiry = self.inquiry(consent=False)
                envelope = protect_contextual_inquiry(inquiry, consent=True)
                task = enqueue_review_task(
                    queue, inquiry, case_storage_consent=True,
                    encrypted_case_context=envelope,
                )
                restored = load_encrypted_case_for_review(queue, task["id"])
                self.assertEqual(restored.inquiry_id, inquiry.inquiry_id)
                self.assertEqual(restored.observations, inquiry.observations)
                self.assertEqual(restored.hypotheses, inquiry.hypotheses)
                self.assertFalse(restored.consent_to_store)
                unchanged = queue.get(task["id"])
                self.assertEqual(unchanged["status"], "waiting_approval")
                self.assertFalse(unchanged["payload"]["case_context_attached"])
                self.assertTrue(unchanged["payload"]["requires_context_reentry"])
                with self.assertRaises(PermissionError):
                    queue.approve(task["id"], approved_by="security-test")

    def test_encrypted_case_reader_rejects_wrong_task_kind_and_missing_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                ordinary = queue.enqueue("ordinary", {}, require_approval=True)
                with self.assertRaises(PermissionError):
                    load_encrypted_case_for_review(queue, ordinary["id"])
                placeholder = enqueue_review_task(queue, self.inquiry())
                with self.assertRaises(PermissionError):
                    load_encrypted_case_for_review(queue, placeholder["id"])

    def test_case_storage_consent_requires_matching_encrypted_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                with self.assertRaises(PermissionError):
                    enqueue_review_task(queue, self.inquiry(), case_storage_consent=True)
                with self.assertRaises(PermissionError):
                    enqueue_review_task(
                        queue, self.inquiry(), case_storage_consent=False,
                        encrypted_case_context={"not": "an envelope"},
                    )
                self.assertEqual(queue._connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration test")
    def test_same_inquiry_id_cannot_replace_an_existing_encrypted_case(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                original = self.inquiry()
                original_envelope = protect_contextual_inquiry(original, consent=True)
                first = enqueue_review_task(
                    queue, original, case_storage_consent=True,
                    encrypted_case_context=original_envelope,
                )
                changed = replace(original, user_goal="A different case goal.")
                changed_envelope = protect_contextual_inquiry(changed, consent=True)
                with self.assertRaisesRegex(ValueError, "different case content"):
                    enqueue_review_task(
                        queue, changed, case_storage_consent=True,
                        encrypted_case_context=changed_envelope,
                    )
                stored = queue.get(first["id"])
                self.assertEqual(
                    unprotect_case_bytes(stored["payload"]["case_context_envelope"]),
                    unprotect_case_bytes(original_envelope),
                )


if __name__ == "__main__":
    unittest.main()
