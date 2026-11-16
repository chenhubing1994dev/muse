from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from muse.improvement import ImprovementStore
from muse.initiative_engine import InitiativeStore
from muse.initiative_feedback_bridge import sync_initiative_feedback
from muse.research_knowledge import ResearchKnowledgeStore
from muse.capability_radar import scan_capabilities
from muse.task_queue import TaskQueue


class InitiativeFeedbackBridgeTests(unittest.TestCase):
    def _initiative(self, store: InitiativeStore):
        return store.propose(
            "Video creator feedback target", kind="creative_output",
            creator_value="Turn video ideas into validated deliverables",
            concept="Generate a short video product",
            first_step="Build a rough cut", expected_output="A validated MP4 and ZIP",
        )

    def test_feedback_sync_is_idempotent_and_links_wiki_and_radar_without_promoting_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiative_db = root / "data" / "initiatives" / "muse.sqlite3"
            improvement_db = root / "data" / "improvements" / "muse.sqlite3"
            knowledge_db = root / "data" / "research" / "knowledge.sqlite3"
            with InitiativeStore(initiative_db) as initiatives:
                initiative = self._initiative(initiatives)
                cycle = "cycle_" + "a" * 32
                initiatives.record_knowledge_link(
                    initiative["id"], "b" * 32, node_type="source",
                    relationship="related", evolution_cycle_id=cycle,
                )
                feedback = initiatives.record_feedback(
                    initiative["id"], "镜头运动不足，需要更清晰的动态变化。",
                    decision="revise", reviewer="creator", source="video_review",
                    evolution_cycle_id=cycle,
                )
            with ResearchKnowledgeStore(knowledge_db):
                pass

            first = sync_initiative_feedback(root, improvement_db)
            second = sync_initiative_feedback(root, improvement_db)

            self.assertEqual(first["proposals_created"], 1)
            self.assertEqual(first["knowledge_nodes_linked"], 1)
            self.assertEqual(first["structured_labels_recorded"], 1)
            self.assertEqual(second["proposals_created"], 0)
            self.assertEqual(second["proposals_existing"], 1)
            self.assertEqual(second["knowledge_nodes_linked"], 1)
            self.assertEqual(second["structured_labels_recorded"], 0)

            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                nodes = [
                    node for node in knowledge.list_nodes(node_type="idea")
                    if node["external_ref"] == "initiative-feedback:" + feedback["id"]
                ]
                self.assertEqual(len(nodes), 1)
                node = nodes[0]
                self.assertEqual(node["metadata"]["epistemic_status"], "unverified_user_feedback_signal")
                self.assertEqual(node["metadata"]["evolution_cycle_id"], cycle)
                self.assertTrue(node["metadata"]["requires_research_and_independent_review"])
                self.assertFalse(node["node_type"] == "claim")
            with InitiativeStore(initiative_db) as initiatives:
                links = initiatives.connection.execute(
                    "SELECT * FROM initiative_knowledge_links WHERE knowledge_node_id=?",
                    (node["id"],),
                ).fetchall()
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0]["evolution_cycle_id"], cycle)
                self.assertEqual(links[0]["relationship"], "improvement_candidate")
            with ImprovementStore(improvement_db) as improvements:
                aggregates = improvements.aggregate_feedback(
                    since=datetime(2000, 1, 1, tzinfo=timezone.utc)
                )
                self.assertEqual(len(aggregates), 1)
                self.assertEqual(aggregates[0]["category"], "capability")
                self.assertEqual(aggregates[0]["tag"], "workflow_gap")
                proposal_row = improvements.connection.execute(
                    "SELECT id FROM improvement_proposals WHERE source_feedback_id=?", (feedback["id"],)
                ).fetchone()
                proposal = improvements.get(proposal_row["id"])
                self.assertEqual(proposal["status"], "proposed")
                self.assertTrue(proposal["metadata"]["requires_research_and_independent_review"])
                self.assertEqual(proposal["metadata"]["evolution_cycle_id"], cycle)

            task_db = root / "data" / "tasks" / "muse.sqlite3"
            with TaskQueue(task_db):
                pass
            radar = scan_capabilities(task_db, improvement_db, research_knowledge_database=knowledge_db)
            self.assertIn("user_feedback", {item["key"] for item in radar["signals_seen"]})
            radar_proposal = next(item for item in radar["created"] if item["title"] == "Address recurring user-reported workflow friction")
            self.assertEqual(radar_proposal["evidence"]["feedback"][0]["tag"], "workflow_gap")
            self.assertTrue(radar["approval_required"])

    def test_no_cycle_is_not_guessed_and_missing_knowledge_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiative_db = root / "data" / "initiatives" / "muse.sqlite3"
            improvement_db = root / "data" / "improvements" / "muse.sqlite3"
            knowledge_db = root / "data" / "research" / "knowledge.sqlite3"
            with InitiativeStore(initiative_db) as initiatives:
                initiative = self._initiative(initiatives)
                initiatives.record_feedback(
                    initiative["id"], "需要更清晰的交付说明。",
                    decision="reject", reviewer="creator", source="video_review",
                )
            result = sync_initiative_feedback(root, improvement_db)
            self.assertEqual(result["knowledge_status"], "not_initialized")
            self.assertFalse(knowledge_db.exists())
            self.assertEqual(result["structured_labels_recorded"], 1)

    def test_legacy_initiative_and_improvement_databases_migrate_feedback_cycle_and_dedupe_columns(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiative_db = root / "legacy-initiatives.sqlite3"
            connection = sqlite3.connect(initiative_db)
            connection.executescript("""
                CREATE TABLE creator_initiatives(
                    id TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,
                    creator_value TEXT NOT NULL,concept TEXT NOT NULL,first_step TEXT NOT NULL,
                    expected_output TEXT NOT NULL,interest_signals_json TEXT NOT NULL DEFAULT '[]',
                    inspiration_refs_json TEXT NOT NULL DEFAULT '[]',capability_gap TEXT NOT NULL DEFAULT '',
                    priority INTEGER NOT NULL DEFAULT 50,autonomy_level TEXT NOT NULL DEFAULT 'prototype',
                    status TEXT NOT NULL DEFAULT 'proposed',outcome TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(title,kind)
                );
                CREATE TABLE initiative_feedback(
                    id TEXT PRIMARY KEY,initiative_id TEXT NOT NULL,feedback TEXT NOT NULL,
                    decision TEXT NOT NULL,reviewer TEXT NOT NULL,source TEXT NOT NULL,created_at TEXT NOT NULL
                );
            """)
            connection.close()
            with InitiativeStore(initiative_db) as initiatives:
                item = self._initiative(initiatives)
                cycle = "cycle_" + "e" * 32
                feedback = initiatives.record_feedback(
                    item["id"], "旧数据库迁移反馈", decision="revise", evolution_cycle_id=cycle,
                )
                self.assertEqual(feedback["evolution_cycle_id"], cycle)

            improvement_db = root / "legacy-improvements.sqlite3"
            connection = sqlite3.connect(improvement_db)
            connection.execute("""
                CREATE TABLE improvement_feedback(
                    id TEXT PRIMARY KEY,category TEXT NOT NULL,tag TEXT NOT NULL,
                    severity INTEGER NOT NULL,created_at TEXT NOT NULL
                )
            """)
            connection.close()
            with ImprovementStore(improvement_db) as improvements:
                first = improvements.record_feedback(
                    "capability", "workflow_gap", severity=4, source_feedback_id="legacy-feedback-id",
                )
                second = improvements.record_feedback(
                    "capability", "workflow_gap", severity=4, source_feedback_id="legacy-feedback-id",
                )
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(
                    improvements.connection.execute("SELECT COUNT(*) FROM improvement_feedback").fetchone()[0],
                    1,
                )

    def test_video_failures_sync_to_wiki_and_trigger_radar_only_after_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiative_db = root / "data" / "initiatives" / "muse.sqlite3"
            improvement_db = root / "data" / "improvements" / "muse.sqlite3"
            knowledge_db = root / "data" / "research" / "knowledge.sqlite3"
            cycle = "cycle_" + "f" * 32
            with InitiativeStore(initiative_db) as initiatives:
                initiative = self._initiative(initiatives)
                for _ in range(2):
                    initiatives.record_product_failure(
                        initiative["id"], failure_code="video.product_job_failed",
                        error_type="RuntimeError", evolution_cycle_id=cycle,
                    )
                self.assertEqual(
                    initiatives.aggregate_failure_metrics(since=datetime(2000, 1, 1, tzinfo=timezone.utc))["total"],
                    2,
                )
            with ResearchKnowledgeStore(knowledge_db):
                pass
            first = sync_initiative_feedback(root, improvement_db)
            second = sync_initiative_feedback(root, improvement_db)
            self.assertEqual(first["failures_scanned"], 2)
            self.assertEqual(first["failure_nodes_created"], 2)
            self.assertEqual(second["failure_nodes_existing"], 2)
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                nodes = [
                    node for node in knowledge.list_nodes(node_type="failure")
                    if node["metadata"].get("origin") == "creator_video_job_failure"
                ]
                self.assertEqual(len(nodes), 2)
                self.assertTrue(all(
                    node["metadata"]["epistemic_status"] == "observed_execution_failure_not_claim_evidence"
                    for node in nodes
                ))
                self.assertTrue(all(node["metadata"]["evolution_cycle_id"] == cycle for node in nodes))
            task_db = root / "data" / "tasks" / "muse.sqlite3"
            with TaskQueue(task_db):
                pass
            radar = scan_capabilities(task_db, improvement_db, research_knowledge_database=knowledge_db)
            self.assertIn("creator_video_failure", {item["key"] for item in radar["signals_seen"]})
            proposal = next(
                item for item in radar["created"]
                if item["title"] == "Reduce recurring local video creation failures"
            )
            self.assertEqual(proposal["evidence"]["creator_video_failures"]["total"], 2)
            self.assertTrue(radar["approval_required"])

    def test_video_failure_radar_requires_repetition_of_the_same_stable_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiative_db = root / "data" / "initiatives" / "muse.sqlite3"
            improvement_db = root / "data" / "improvements" / "muse.sqlite3"
            task_db = root / "data" / "tasks" / "muse.sqlite3"
            with InitiativeStore(initiative_db) as initiatives:
                initiative = self._initiative(initiatives)
                initiatives.record_product_failure(
                    initiative["id"], failure_code="video.generation_failed", error_type="RuntimeError",
                )
                initiatives.record_product_failure(
                    initiative["id"], failure_code="video.packaging_failed", error_type="OSError",
                )
            with TaskQueue(task_db):
                pass
            radar = scan_capabilities(task_db, improvement_db)
            self.assertNotIn("creator_video_failure", {item["key"] for item in radar["signals_seen"]})

    def test_explicit_cycle_identifier_is_validated_when_feedback_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as initiatives:
                initiative = self._initiative(initiatives)
                with self.assertRaises(ValueError):
                    initiatives.record_feedback(
                        initiative["id"], "invalid cycle", decision="revise",
                        evolution_cycle_id="not-a-cycle",
                    )


if __name__ == "__main__":
    unittest.main()
