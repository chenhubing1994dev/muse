from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.capability_radar import scan_capabilities
from muse.improvement import ImprovementStore
from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue
from muse.worker import run_once


class CapabilityRadarTests(unittest.TestCase):
    def test_failed_task_aggregate_creates_one_redacted_deduplicated_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            improvement_db = root / "improvements.sqlite3"
            with TaskQueue(task_db) as queue:
                queue.enqueue("PRIVATE TASK TITLE", {"kind": "explode", "secret": "PRIVATE PAYLOAD"})
            run_once(task_db, {"explode": lambda _: (_ for _ in ()).throw(RuntimeError("PRIVATE EXCEPTION"))})

            first = scan_capabilities(task_db, improvement_db)
            second = scan_capabilities(task_db, improvement_db)

            self.assertEqual(len(first["created"]), 1)
            self.assertEqual(first["created"][0]["status"], "proposed")
            self.assertEqual(second["created"], [])
            self.assertEqual(len(second["existing"]), 1)
            with ImprovementStore(improvement_db) as store:
                proposal = store.get(first["created"][0]["id"])
            serialized = str(proposal)
            self.assertNotIn("PRIVATE TASK TITLE", serialized)
            self.assertNotIn("PRIVATE PAYLOAD", serialized)
            self.assertNotIn("PRIVATE EXCEPTION", serialized)
            self.assertIn("failed", proposal["problem"])
            with ProgressEventStore(task_db) as progress:
                radar_events = progress.list_events(category="system", subject_id="capability-radar")
            self.assertEqual(len(radar_events), 2)
            self.assertIn("created=0", radar_events[0]["summary"])

    def test_repeated_classified_failure_code_creates_redacted_root_cause_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            with TaskQueue(task_db) as queue:
                for index in range(2):
                    task = queue.enqueue(f"PRIVATE TASK {index}", {"secret": "PRIVATE PAYLOAD"})
                    queue.claim_next(worker_id=f"worker-{index}", task_id=task["id"])
                    queue.finish(task["id"], status="failed", worker_id=f"worker-{index}", result={
                        "error_code": "provider.unavailable", "failure_category": "provider",
                        "retryable": True, "recovery_guidance": "Check the configured endpoint.",
                        "private_detail": "PRIVATE EXCEPTION",
                    })
            result = scan_capabilities(task_db, root / "improvements.sqlite3")
            proposal = next(item for item in result["created"] if item["title"] == "Resolve recurring classified task failure patterns")
            self.assertEqual(proposal["evidence"]["classified_failures"]["by_code"], {"provider.unavailable": 2})
            self.assertEqual(proposal["evidence"]["classified_failures"]["by_category"], {"provider": 2})
            self.assertNotIn("PRIVATE TASK", str(result))
            self.assertNotIn("PRIVATE PAYLOAD", str(result))
            self.assertNotIn("PRIVATE EXCEPTION", str(result))
            self.assertTrue(result["approval_required"])

    def test_repeated_classified_failure_creates_linked_research_wiki_records(self) -> None:
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            knowledge_db = root / "research" / "knowledge.sqlite3"
            with TaskQueue(task_db) as queue:
                for index in range(3):
                    task = queue.enqueue(f"PRIVATE TASK {index}", {"secret": "PRIVATE PAYLOAD"})
                    queue.claim_next(worker_id=f"worker-{index}", task_id=task["id"])
                    queue.finish(task["id"], status="failed", worker_id=f"worker-{index}", result={
                        "error_code": "provider.unavailable", "failure_category": "provider",
                        "retryable": index != 2, "recovery_guidance": "Inspect provider configuration.",
                        "private_detail": "PRIVATE EXCEPTION",
                    })
            with ResearchKnowledgeStore(knowledge_db):
                pass

            first = scan_capabilities(
                task_db, root / "improvements.sqlite3", research_knowledge_database=knowledge_db,
            )
            proposal = next(item for item in first["created"] if item["title"] == "Resolve recurring classified task failure patterns")
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                failure_nodes = knowledge.list_nodes(node_type="failure")
                hypotheses = knowledge.list_nodes(node_type="idea")
                self.assertEqual(len(failure_nodes), 1)
                self.assertEqual(len(hypotheses), 1)
                hypothesis = hypotheses[0]
                self.assertEqual(hypothesis["metadata"]["improvement_proposal_id"], proposal["id"])
                self.assertEqual(hypothesis["metadata"]["evidence_snapshot"]["retryable"], 2)
                self.assertEqual(hypothesis["metadata"]["evidence_snapshot"]["non_retryable"], 1)
                self.assertEqual(hypothesis["metadata"]["counter_evidence"]["status"], "not_collected")
                self.assertTrue(any(
                    edge["metadata"].get("improvement_proposal_id") == proposal["id"]
                    for edge in knowledge.list_edges(node_id=hypothesis["id"])
                ))
                before = knowledge.stats()

            second = scan_capabilities(
                task_db, root / "improvements.sqlite3", research_knowledge_database=knowledge_db,
            )
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                after = knowledge.stats()
                self.assertEqual(after["nodes"], before["nodes"])
                self.assertEqual(after["edges"], before["edges"])
            self.assertEqual(second["created"], [])
            self.assertTrue(second["approval_required"])
            self.assertNotIn("PRIVATE TASK", str(first))
            self.assertNotIn("PRIVATE PAYLOAD", str(first))
            self.assertNotIn("PRIVATE EXCEPTION", str(first))

    def test_improvement_review_lifecycle_syncs_into_research_wiki(self) -> None:
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            knowledge_db = root / "research" / "knowledge.sqlite3"
            with ResearchKnowledgeStore(knowledge_db):
                pass
            with TaskQueue(task_db) as queue:
                for index in range(2):
                    task = queue.enqueue(f"PRIVATE REVIEW TASK {index}", {"secret": "PRIVATE REVIEW PAYLOAD"})
                    queue.claim_next(worker_id=f"review-worker-{index}", task_id=task["id"])
                    queue.finish(task["id"], status="failed", worker_id=f"review-worker-{index}", result={
                        "error_code": "provider.unavailable", "failure_category": "provider",
                        "retryable": True, "recovery_guidance": "Inspect endpoint configuration.",
                    })
            first = scan_capabilities(
                task_db, root / "improvements.sqlite3", research_knowledge_database=knowledge_db,
            )
            proposal = next(item for item in first["created"] if item["title"] == "Resolve recurring classified task failure patterns")
            self.assertEqual(len(proposal["knowledge_links"]), 1)

            with ImprovementStore(root / "improvements.sqlite3") as improvements:
                improvements.transition(proposal["id"], "researching", actor="human-reviewer", note="PRIVATE APPROVAL NOTE")
                source = improvements.add_source(
                    proposal["id"], title="Provider contract", url="https://example.test/provider",
                    license_status="not_applicable", security_status="reviewed",
                )
                improvements.transition(proposal["id"], "ready_for_review", actor="human-reviewer")
                approved = improvements.approve(proposal["id"], approved_by="human-reviewer")
                self.assertEqual(approved["status"], "approved")
            quiet_task_db = root / "quiet-tasks.sqlite3"
            with TaskQueue(quiet_task_db):
                pass
            second = scan_capabilities(
                quiet_task_db, root / "improvements.sqlite3", research_knowledge_database=knowledge_db,
            )
            self.assertEqual(second["signals_seen"], [])
            self.assertEqual(second["knowledge_link_errors"], [])
            self.assertEqual(second["knowledge_state_sync"]["updated_nodes"], 1)
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                hypothesis = next(
                    node for node in knowledge.list_nodes(node_type="idea")
                    if node["metadata"].get("improvement_proposal_id") == proposal["id"]
                )
                self.assertEqual(hypothesis["metadata"]["improvement_status"], "approved")
                self.assertEqual(hypothesis["metadata"]["review_state"], "human_approved")
                self.assertFalse(hypothesis["metadata"]["approval_required"])
                self.assertEqual(
                    [event["to_status"] for event in hypothesis["metadata"]["review_history"]],
                    ["proposed", "researching", "ready_for_review", "approved"],
                )
                self.assertNotIn("PRIVATE APPROVAL NOTE", str(hypothesis["metadata"]))
                self.assertNotIn("human-reviewer", str(hypothesis["metadata"]))
            self.assertTrue(source["id"])

    def test_recent_test_failure_creates_a_separate_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            with TaskQueue(task_db):
                pass
            with ProgressEventStore(task_db) as progress:
                progress.record("test", "suite", "failed", summary="private test output")
            result = scan_capabilities(task_db, root / "improvements.sqlite3")
            titles = [proposal["title"] for proposal in result["created"]]
            self.assertIn("Add regression triage and benchmark gate", titles)
            self.assertNotIn("private test output", str(result))

    def test_empty_healthy_queue_creates_no_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            with TaskQueue(task_db):
                pass
            result = scan_capabilities(task_db, root / "improvements.sqlite3")
            self.assertEqual(result["created"], [])
            self.assertEqual(result["signals_seen"], [])
            self.assertEqual(result["trend"]["direction"], "insufficient_data")

    def test_missing_task_database_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "missing.sqlite3"
            with self.assertRaises(FileNotFoundError):
                scan_capabilities(task_db, root / "improvements.sqlite3")
            self.assertFalse(task_db.exists())


    def test_generated_proposals_include_ranked_evidence_and_safe_recovery_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            with TaskQueue(task_db) as queue:
                task = queue.enqueue("private task title", {"secret": "private payload"})
                queue.claim_next(worker_id="worker-a")
                queue._connection.execute("UPDATE tasks SET attempts=3 WHERE id=?", (task["id"],))
                queue._connection.commit()
            with ProgressEventStore(task_db) as progress:
                progress.record("task", task["id"], "failed", summary="private exception text", error_code="handler.unexpected")
                progress.record("test", "suite", "failed", summary="private test output", error_code="test.assertion_failed",)
            result = scan_capabilities(task_db, root / "improvements.sqlite3")
            self.assertTrue(result["created"])
            priorities = [item["priority"] for item in result["created"]]
            self.assertEqual(priorities, sorted(priorities, reverse=True))
            for item in result["created"]:
                self.assertGreaterEqual(item["confidence"], 0.0)
                self.assertLessEqual(item["confidence"], 1.0)
                self.assertTrue(item["acceptance_criteria"])
                self.assertTrue(item["rollback_plan"])
                self.assertTrue(item["evidence"])
            self.assertNotIn("private payload", str(result))
            self.assertNotIn("private exception", str(result))
            self.assertEqual(result["created"][0]["evidence"]["failure_codes"]["handler.unexpected"], 1)
            self.assertNotIn("private test output", str(result))

    def test_scan_ignores_failure_signals_outside_the_lookback_window(self) -> None:
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
            old = (now - timedelta(days=2)).isoformat()
            with TaskQueue(task_db) as queue:
                task = queue.enqueue("old failure", {"private": "payload"})
                queue._connection.execute(
                    "UPDATE tasks SET status='failed', updated_at=? WHERE id=?",
                    (old, task["id"]),
                )
                queue._connection.commit()
            with ProgressEventStore(task_db) as progress:
                progress.record("test", "old-suite", "failed", summary="old private test output")
                progress._connection.execute(
                    "UPDATE progress_events SET created_at=? WHERE subject_id=?",
                    (old, "old-suite"),
                )
                progress._connection.commit()
            result = scan_capabilities(
                task_db, root / "improvements.sqlite3", now=now, lookback_hours=24
            )
            self.assertEqual(result["signals_seen"], [])
            self.assertEqual(result["created"], [])

    def test_explicit_feedback_and_repeated_corrections_create_ranked_proposals(self) -> None:
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            improvement_db = root / "improvements.sqlite3"
            with TaskQueue(task_db):
                pass
            with ImprovementStore(improvement_db) as store:
                store.record_feedback("reliability", "repeated_manual_fix", severity=4)
                store.record_feedback("reliability", "repeated_manual_fix", severity=5)
                store.record_feedback("capability", "missing_tool", severity=5)
            result = scan_capabilities(
                task_db, improvement_db, now=datetime.now(timezone.utc), lookback_hours=24
            )
            titles = [item["title"] for item in result["created"]]
            self.assertIn("Address recurring user-reported workflow friction", titles)
            self.assertIn("Eliminate repeated manual correction loops", titles)
            repeated = next(item for item in result["created"] if item["title"] == "Eliminate repeated manual correction loops")
            self.assertEqual(repeated["evidence"]["feedback"][0]["tag"], "repeated_manual_fix")
            self.assertEqual(repeated["evidence"]["feedback"][0]["count"], 2)
            self.assertTrue(result["approval_required"])

    def test_old_feedback_is_excluded_from_the_radar_window(self) -> None:
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            improvement_db = root / "improvements.sqlite3"
            now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
            with TaskQueue(task_db):
                pass
            with ImprovementStore(improvement_db) as store:
                item = store.record_feedback("reliability", "repeated_manual_fix", severity=5)
                old = (now - timedelta(days=2)).isoformat()
                store.connection.execute(
                    "UPDATE improvement_feedback SET created_at=? WHERE id=?",
                    (old, item["id"]),
                )
                store.connection.commit()
            result = scan_capabilities(
                task_db, improvement_db, now=now, lookback_hours=24
            )
            self.assertEqual(result["signals_seen"], [])
            self.assertEqual(result["created"], [])

    def test_research_knowledge_signals_create_approval_gated_meta_optimize_proposals(self) -> None:
        from muse.research_knowledge import ResearchKnowledgeStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "tasks.sqlite3"
            knowledge_db = root / "knowledge.sqlite3"
            improvement_db = root / "improvements.sqlite3"
            with TaskQueue(task_db):
                pass
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                knowledge.record_failure(
                    "Repeated fetch timeout", summary="fetch TimeoutError", signature="fetch:TimeoutError",
                    reason_to_avoid="Use bounded recovery", task_id="ticket-1",
                )
                knowledge.record_failure(
                    "Repeated fetch timeout", summary="fetch TimeoutError", signature="fetch:TimeoutError",
                    reason_to_avoid="Use bounded recovery", task_id="ticket-2",
                )
                for index in range(5):
                    knowledge.upsert_node(
                        "claim", f"Unverified claim {index}", external_ref=f"claim:{index}",
                        status="proposed",
                    )
            result = scan_capabilities(
                task_db, improvement_db, research_knowledge_database=knowledge_db,
            )
            titles = {item["title"] for item in result["created"]}
            self.assertIn("Break repeated autonomous research failure loops", titles)
            self.assertIn("Reduce the unverified research claim backlog", titles)
            self.assertTrue(result["approval_required"])
            self.assertEqual(result["research_knowledge_signals"]["repeated_failure_groups"], 1)
            self.assertEqual(result["research_knowledge_signals"]["proposed_claim_backlog"], 5)
            self.assertNotIn("fetch TimeoutError", str(result))
            second = scan_capabilities(
                task_db, improvement_db, research_knowledge_database=knowledge_db,
            )
            self.assertEqual(second["created"], [])
            self.assertGreaterEqual(len(second["existing"]), 2)

if __name__ == "__main__":
    unittest.main()
