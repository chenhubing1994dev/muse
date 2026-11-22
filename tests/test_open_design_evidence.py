from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.open_design import OpenDesignTaskHandler
from muse.open_design_evidence import persist_open_design_artifact_evidence
from muse.open_design_recovery import persist_completed_open_design_run_evidence
from muse.research_knowledge import ResearchKnowledgeStore


class OpenDesignEvidencePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.knowledge_db = self.root / "data" / "research" / "knowledge.sqlite3"
        self.initiative_db = self.root / "data" / "initiatives" / "muse.sqlite3"
        with InitiativeStore(self.initiative_db) as store:
            item = store.propose(
                "Open Design output initiative", kind="creative_output",
                creator_value="Preserve a reviewed generated visual artifact",
                concept="A bounded Open Design output linked to Research Wiki",
                first_step="Generate a local design artifact",
                expected_output="Artifact metadata and hashes linked to the initiative",
            )
            self.initiative_id = item["id"]
        self.evidence = {
            "project_id": "project-42",
            "file_count": 2,
            "complete": True,
            "truncated": False,
            "skipped_file_count": 0,
            "files": [
                {
                    "path": "index.html", "mime": "text/html", "binary": False,
                    "bytes": 18, "sha256": "a" * 64,
                },
                {
                    "path": "assets/logo.png", "mime": "image/png", "binary": True,
                    "bytes": 128, "sha256": None,
                },
            ],
        }

    def tearDown(self):
        self.temp.cleanup()

    def _persist(self):
        return persist_open_design_artifact_evidence(

            knowledge_database=self.knowledge_db,
            initiative_database=self.initiative_db,
            task_id="b" * 32,
            run_id="run-42",
            evidence=self.evidence,
            initiative_id=self.initiative_id,
        )

    def test_persists_bundle_files_edges_and_initiative_link(self):
        result = self._persist()
        self.assertEqual(result["status"], "persisted")
        self.assertEqual(result["file_count"], 2)
        self.assertTrue(result["complete"])
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            bundle = knowledge.get_node(result["bundle_node_id"])
            self.assertEqual(bundle["metadata"]["kind"], "generated_artifact_bundle")
            self.assertFalse(bundle["metadata"]["content_copied"])
            self.assertEqual(len(knowledge.list_nodes(node_type="source", limit=10)), 3)
            self.assertEqual(len(knowledge.list_edges(node_id=result["bundle_node_id"])), 2)
            file_nodes = [node for node in knowledge.list_nodes(node_type="source", limit=10)
                          if node["metadata"].get("kind") == "generated_artifact_file"]
            self.assertEqual({node["metadata"]["path"] for node in file_nodes}, {"index.html", "assets/logo.png"})
            self.assertIsNone(next(node for node in file_nodes if node["metadata"]["binary"])["metadata"]["sha256"])
        with InitiativeStore(self.initiative_db) as initiatives:
            links = initiatives.connection.execute(
                "SELECT * FROM initiative_knowledge_links WHERE initiative_id=?",
                (self.initiative_id,),
            ).fetchall()
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["knowledge_node_id"], result["bundle_node_id"])

    def test_replay_is_idempotent(self):
        first = self._persist()
        second = self._persist()
        self.assertEqual(first["bundle_node_id"], second["bundle_node_id"])
        self.assertEqual(first["file_node_ids"], second["file_node_ids"])
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            self.assertEqual(len(knowledge.list_nodes(node_type="source", limit=10)), 3)
            self.assertEqual(len(knowledge.list_edges(node_id=first["bundle_node_id"])), 2)
        with InitiativeStore(self.initiative_db) as initiatives:
            links = initiatives.connection.execute(
                "SELECT * FROM initiative_knowledge_links WHERE initiative_id=?",
                (self.initiative_id,),
            ).fetchall()
            self.assertEqual(len(links), 1)

    def test_worker_handler_persists_artifact_evidence_after_successful_run(self):
        task_id = "c" * 32
        fake_result = {
            "status": "completed",
            "run_id": "run-42",
            "artifact_evidence": self.evidence,
        }
        with patch("muse.open_design.OpenDesignAdapter.from_installed") as factory:
            factory.return_value.__enter__.return_value = factory.return_value
            factory.return_value.__exit__.return_value = False
            factory.return_value.execute_task.return_value = dict(fake_result)
            handler = OpenDesignTaskHandler(
                workspace=self.root,
                database=self.root / "data" / "tasks" / "muse.sqlite3",
                knowledge_database=self.knowledge_db,
                initiative_database=self.initiative_db,
            )
            result = handler.execute_task({"initiative_id": self.initiative_id}, task_id=task_id)
        self.assertEqual(result["artifact_evidence_persistence"]["status"], "persisted")
        self.assertEqual(result["artifact_evidence_persistence"]["file_count"], 2)
    def test_worker_handler_marks_persisted_partial_bundle_incomplete(self):
        task_id = "d" * 32
        partial = dict(self.evidence)
        partial["truncated"] = True
        partial["complete"] = False
        fake_result = {
            "status": "completed",
            "run_id": "run-partial",
            "artifact_evidence": partial,
        }
        with patch("muse.open_design.OpenDesignAdapter.from_installed") as factory:
            factory.return_value.__enter__.return_value = factory.return_value
            factory.return_value.__exit__.return_value = False
            factory.return_value.execute_task.return_value = dict(fake_result)
            with patch(
                "muse.open_design_evidence.persist_open_design_artifact_evidence",
                return_value={"status": "persisted", "file_count": 2},
            ):
                handler = OpenDesignTaskHandler(
                    workspace=self.root,
                    database=self.root / "data" / "tasks" / "muse.sqlite3",
                    knowledge_database=self.knowledge_db,
                    initiative_database=self.initiative_db,
                )
                result = handler.execute_task({}, task_id=task_id)
        self.assertEqual(result["artifact_evidence_persistence"]["status"], "incomplete")
        self.assertEqual(result["artifact_evidence_persistence"]["error_type"], "IncompleteArtifactEvidence")

    def _completed_result_for_recovery(self, task_id: str, *, evidence=None, project_id="project-42"):
        return {
            "task_id": task_id,
            "status": "completed",
            "run_id": "run-42",
            "project_id": project_id,
            "artifact_evidence": dict(evidence or self.evidence),
        }

    def test_persistence_only_recovery_reuses_completed_run_without_starting_another(self):
        task_id = "e" * 32
        payload = {
            "kind": "open_design_run",
            "project_id": "project-42",
            "initiative_id": self.initiative_id,
        }
        completed = self._completed_result_for_recovery(task_id)
        with patch(
            "muse.open_design.OpenDesignAdapter.from_installed",
            side_effect=AssertionError("persistence-only recovery must not create an adapter or start a run"),
        ):
            result = persist_completed_open_design_run_evidence(
                knowledge_database=self.knowledge_db,
                initiative_database=self.initiative_db,
                payload=payload,
                task_id=task_id,
                completed_result=completed,
                completion_event_id="task-event-42",
            )
        self.assertEqual(result["status"], "persisted")
        self.assertEqual(result["recovery_mode"], "persistence_only")
        self.assertEqual(result["recovered_from_event_id"], "task-event-42")
        self.assertFalse(result["new_open_design_run_started"])
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            self.assertEqual(len(knowledge.list_nodes(node_type="source", limit=10)), 3)

    def test_persistence_only_recovery_is_idempotent(self):
        task_id = "f" * 32
        payload = {"kind": "open_design_run", "project_id": "project-42"}
        completed = self._completed_result_for_recovery(task_id)
        kwargs = {
            "knowledge_database": self.knowledge_db,
            "initiative_database": self.initiative_db,
            "payload": payload,
            "task_id": task_id,
            "completed_result": completed,
            "completion_event_id": "task-event-43",
        }
        first = persist_completed_open_design_run_evidence(**kwargs)
        second = persist_completed_open_design_run_evidence(**kwargs)
        self.assertEqual(first["bundle_node_id"], second["bundle_node_id"])
        self.assertEqual(first["file_node_ids"], second["file_node_ids"])
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            self.assertEqual(len(knowledge.list_nodes(node_type="source", limit=10)), 3)

    def test_persistence_only_recovery_rejects_untrusted_or_incomplete_results(self):
        task_id = "a" * 32
        payload = {"kind": "open_design_run", "project_id": "project-42"}
        base = self._completed_result_for_recovery(task_id)
        with self.assertRaisesRegex(ValueError, "completed Open Design run"):
            persist_completed_open_design_run_evidence(
                knowledge_database=self.knowledge_db,
                initiative_database=self.initiative_db,
                payload=payload, task_id=task_id,
                completed_result={**base, "status": "running"},
                completion_event_id="task-event-44",
            )
        with self.assertRaisesRegex(ValueError, "project does not match"):
            persist_completed_open_design_run_evidence(
                knowledge_database=self.knowledge_db,
                initiative_database=self.initiative_db,
                payload=payload, task_id=task_id,
                completed_result={**base, "project_id": "different-project"},
                completion_event_id="task-event-44",
            )
        partial = dict(self.evidence)
        partial["complete"] = False
        partial["truncated"] = True
        with self.assertRaisesRegex(ValueError, "complete artifact evidence"):
            persist_completed_open_design_run_evidence(
                knowledge_database=self.knowledge_db,
                initiative_database=self.initiative_db,
                payload=payload, task_id=task_id,
                completed_result=self._completed_result_for_recovery(task_id, evidence=partial),
                completion_event_id="task-event-44",
            )
        with self.assertRaisesRegex(ValueError, "completion event id"):
            persist_completed_open_design_run_evidence(
                knowledge_database=self.knowledge_db,
                initiative_database=self.initiative_db,
                payload=payload, task_id=task_id,
                completed_result=base,
                completion_event_id="",
            )
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            self.assertEqual(knowledge.list_nodes(node_type="source", limit=10), [])

    def test_rejects_inconsistent_complete_flag(self):
        self.evidence["complete"] = False
        with self.assertRaisesRegex(ValueError, "complete flag is inconsistent"):
            self._persist()
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            self.assertEqual(knowledge.list_nodes(node_type="source", limit=10), [])
    def test_rejects_traversal_before_persisting_any_nodes(self):
        self.evidence["files"][0]["path"] = "../outside.txt"
        with self.assertRaisesRegex(ValueError, "safe project-relative"):
            self._persist()
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            self.assertEqual(knowledge.list_nodes(node_type="source", limit=10), [])

    def test_rejects_text_file_without_verified_hash(self):
        self.evidence["files"][0]["sha256"] = None
        with self.assertRaisesRegex(ValueError, "verified SHA-256"):
            self._persist()
        with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
            self.assertEqual(knowledge.list_nodes(node_type="source", limit=10), [])


if __name__ == "__main__":
    unittest.main()
