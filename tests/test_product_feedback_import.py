from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.product_builder import LocalProductBuilder
from muse.product_feedback_import import ProductFeedbackImportError, import_offline_product_feedback
from muse.product_feedback_import import parse_product_feedback_json
from muse.research_knowledge import ResearchKnowledgeStore


class OfflineProductFeedbackImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.initiative_db = self.root / "data" / "initiatives" / "muse.sqlite3"
        self.knowledge_db = self.root / "data" / "research" / "knowledge.sqlite3"
        self.knowledge_store = ResearchKnowledgeStore(self.knowledge_db)
        self.inspiration = self.knowledge_store.upsert_node(
            "claim", "Feedback source inspiration", summary="A hypothesis to test",
            external_ref="test-inspiration:feedback-source", status="proposed",
            metadata={"origin": "creator_inspiration_inbox", "epistemic_status": "creator_inspired_hypothesis_only"},
        )
        self.initiative_store = InitiativeStore(self.initiative_db)
        self.initiative = self.initiative_store.propose(
            "Muse offline worksheet fixture", kind="tool", creator_value="Creator worksheet test",
            concept=json.dumps({
                "research_question": "结构化记录能否提高重复任务的可审计性？",
                "creator_hypothesis_summary": "假设：结构化记录可减少遗漏；若重复试验没有稳定改善则证伪。",
                "key_discoveries": {
                    "creator_inspired_hypotheses": ["结构化记录可减少关键步骤遗漏。"],
                    "testable_experiments": ["在同一组10项任务上交替使用两种记录格式，比较遗漏数量。"],
                    "counter_evidence": ["小样本和操作者熟练度可能解释观察到的差异。"],
                    "sources": [],
                    "uncertainties": ["仍需扩大样本并独立复核。"],
                },
                "inspiration_productization": {"inspiration_id": self.inspiration["id"]},
            }, ensure_ascii=False),
            first_step="Run a reversible experiment", expected_output="An offline worksheet",
            capability_gap="Capture creator experiment feedback", priority=60, autonomy_level="draft",
        )
        builder = LocalProductBuilder(self.root)
        draft = builder.build(self.initiative_store, self.initiative["id"])
        self.assertFalse(draft["validated"])
        self.assertEqual(draft["status"], "draft")
        self.product = builder.approve_for_testing(
            self.initiative_store, self.initiative["id"], reviewed_by="test-reviewer",
        )
        self.assertTrue(self.product["validated"])
        self.assertEqual(self.product["status"], "testing")
        self.assertEqual(self.initiative_store.get_by_id(self.initiative["id"])["status"], "testing")
        self.payload = {
            "initiative_id": self.initiative["id"],            "title": self.initiative["title"],
            "objective": "Test whether structured notes improve repeatability.",
            "stable": "Keep the same test inputs.",
            "changing": "Try two note-taking formats.",
            "tensions": "The sample size is small.",
            "lessons": "The structured format was easier to audit.",
            "hypotheses": [{"title": "Structured notes", "statement": "May improve repeatability.", "test": "Compare two runs.", "falsifier": "No difference across repeated trials."}],
            "experiments": [{"title": "A/B note format", "method": "Alternate formats across 10 tasks.", "expected": "Fewer omissions.", "result": "Two fewer omissions; small sample."}],
        }

    def tearDown(self):
        self.initiative_store.close()
        self.knowledge_store.close()
        self.temp.cleanup()


    def test_json_parser_rejects_duplicate_keys_at_root_and_nested_levels(self):
        with self.assertRaises(ProductFeedbackImportError):
            parse_product_feedback_json(b'{"title":"first","title":"second"}')
        with self.assertRaises(ProductFeedbackImportError):
            parse_product_feedback_json(b'{"hypotheses":[{"title":"first","title":"second"}]}')
        with self.assertRaises(ProductFeedbackImportError):
            parse_product_feedback_json(b"x" * (1024 * 1024 + 1))

    def test_imports_feedback_as_unverified_experiment_linked_to_initiative(self):
        result = import_offline_product_feedback(self.root, self.payload)
        self.assertTrue(result["created"])
        self.assertFalse(result["claim_promotion_allowed"])
        self.assertFalse(result["dispatch_started"])
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            node = knowledge.get_node(result["knowledge_node_id"])
        self.assertEqual(node["node_type"], "experiment")
        self.assertEqual(node["metadata"]["epistemic_status"], "creator_recorded_observation_unverified")
        self.assertFalse(node["metadata"]["is_external_factual_evidence"])
        self.assertTrue(node["metadata"]["requires_independent_review"])
        self.assertIn(self.initiative["id"], node["metadata"]["creator_initiative_ids"])
        self.assertEqual(node["metadata"]["product_package_sha256"], self.product["package_sha256"])
        self.assertEqual(result["inspiration_node_id"], self.inspiration["id"])
        self.assertTrue(result["inspiration_linked"])
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            edges = knowledge.list_edges(node_id=self.inspiration["id"], edge_type="tested_by")
        self.assertTrue(any(edge["source_id"] == self.inspiration["id"] and edge["target_id"] == node["id"] for edge in edges))

    def test_identical_import_is_idempotent_and_changed_observation_is_append_only(self):
        first = import_offline_product_feedback(self.root, self.payload)
        duplicate = import_offline_product_feedback(self.root, self.payload)
        self.assertEqual(first["knowledge_node_id"], duplicate["knowledge_node_id"])
        self.assertFalse(duplicate["created"])
        changed_payload = {**self.payload, "lessons": "A later run found one additional limitation."}
        changed = import_offline_product_feedback(self.root, changed_payload)
        self.assertNotEqual(first["knowledge_node_id"], changed["knowledge_node_id"])
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            edges = knowledge.list_edges(node_id=self.inspiration["id"], edge_type="tested_by")
        self.assertEqual(len(edges), 2)
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            nodes = [node for node in knowledge.list_nodes(node_type="experiment", limit=20)
                     if node.get("metadata", {}).get("origin") == "offline_product_creator_feedback"]
        self.assertEqual(len(nodes), 2)

    def test_rejects_malformed_or_unrelated_worksheet(self):
        with self.assertRaises(ProductFeedbackImportError):
            import_offline_product_feedback(self.root, {**self.payload, "unexpected": True})
        with self.assertRaises(ProductFeedbackImportError):
            import_offline_product_feedback(self.root, {**self.payload, "initiative_id": "f" * 32})
        with self.assertRaises(ProductFeedbackImportError):
            import_offline_product_feedback(self.root, {**self.payload, "title": "different initiative"})
        with self.assertRaises(ProductFeedbackImportError):
            import_offline_product_feedback(self.root, {**self.payload, "experiments": [{"title": "bad"}]})

    def test_rejects_empty_observations_and_oversized_payload(self):
        empty = {**self.payload, "stable": "", "changing": "", "tensions": "", "lessons": "", "hypotheses": [], "experiments": []}
        with self.assertRaises(ProductFeedbackImportError):
            import_offline_product_feedback(self.root, empty)
        with self.assertRaises(ProductFeedbackImportError):
            import_offline_product_feedback(self.root, self.payload, max_bytes=10)
        deeply_nested = []
        for _ in range(1500):
            deeply_nested = [deeply_nested]
        with self.assertRaises(ProductFeedbackImportError):
            import_offline_product_feedback(self.root, {**self.payload, "stable": deeply_nested})

    def test_refuses_import_if_current_product_artifact_was_modified(self):
        artifact_path = self.root / self.product["relative_path"] / "index.html"
        artifact_path.write_text("tampered", encoding="utf-8")
        with self.assertRaises(ProductFeedbackImportError):
            import_offline_product_feedback(self.root, self.payload)


if __name__ == "__main__":
    unittest.main()
