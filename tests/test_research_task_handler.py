from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from muse.research_knowledge import ResearchKnowledgeStore
from muse.research_state_machine import ResearchStateMachine
from muse.research_task_handler import ResearchTaskError, ResearchTaskHandler


@contextmanager
def db_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class FakeSearch:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, str]]:
        if self.fail:
            raise RuntimeError("simulated provider outage")
        return [{
            "url": "https://example.com/research",
            "title": "Example research source",
            "snippet": "A source snippet about the test topic.",
            "engine": "test-search",
        }]


class FakeReader:
    def read(self, url: str) -> dict[str, object]:
        body = ("Verified readable source body about the research hypothesis. " * 12).strip()
        return {
            "url": url,
            "text": body,
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "content_type": "text/html",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "truncated": False,
            "source_metadata": {},
            "capture_method": "test_reader",
        }


class FakeExecutionContext:
    database: str

    def __init__(self, database: Path) -> None:
        self.database = str(database)

    def assert_current_lease(self) -> None:
        return None


class ResearchTaskHandlerTests(unittest.TestCase):
    def _claim(self, root: Path, *, status: str = "queued") -> tuple[Path, str, str]:
        knowledge_db = root / "knowledge.sqlite3"
        node_sha = hashlib.sha256(b"test-claim-content").hexdigest()
        with ResearchKnowledgeStore(knowledge_db) as store:
            claim = store.upsert_node(
                "claim", "Test research hypothesis", summary="A falsifiable test hypothesis",
                status="proposed",
                metadata={
                    "content_sha256": node_sha,
                    "research_status": status,
                    "review_state": "not_researched",
                    "epistemic_status": "creator_inspired_hypothesis_only",
                    "allow_external_research": True,
                },
            )
        return knowledge_db, claim["id"], node_sha

    def test_evidence_subtask_records_source_and_direction_without_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            knowledge_db, claim_id, node_sha = self._claim(root)
            evidence_db = root / "evidence.sqlite3"
            task_db = root / "tasks.sqlite3"
            handler = ResearchTaskHandler(
                knowledge_db=knowledge_db, evidence_db=evidence_db, task_db=task_db,
                handoff_dir=root / "handoff", search=FakeSearch(), reader=FakeReader(),
                max_sources=1, max_workers=1,
            )
            result = handler.execute_task(
                {
                    "kind": "evidence_search",
                    "source_node_id": claim_id,
                    "node_sha256": node_sha,
                    "research_round": 1,
                    "search_direction": "support direction for hypothesis",
                    "search_query": "test support query",
                },
                task_id="task-evidence-1",
                execution_context=FakeExecutionContext(task_db),
            )
            self.assertEqual(result["recorded_claim_evidence_count"], 1)
            self.assertTrue(result["no_verdict_assigned"])
            with ResearchKnowledgeStore(knowledge_db) as store:
                evidence = store.list_claim_evidence(claim_id)
                self.assertEqual(len(evidence), 1)
                self.assertEqual(evidence[0]["direction"], "support_search_candidate")
                self.assertEqual(evidence[0]["reviews"], [])
                self.assertEqual(store.get_node(claim_id)["status"], "proposed")

    def test_search_failure_marks_claim_failed_and_does_not_invent_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            knowledge_db, claim_id, node_sha = self._claim(root)
            task_db = root / "tasks.sqlite3"
            handler = ResearchTaskHandler(
                knowledge_db=knowledge_db, evidence_db=root / "evidence.sqlite3",
                task_db=task_db, handoff_dir=root / "handoff",
                search=FakeSearch(fail=True), reader=FakeReader(),
                max_sources=1, max_workers=1,
            )
            with self.assertRaises(ResearchTaskError):
                handler.execute_task(
                    {
                        "kind": "counter_evidence_search",
                        "source_node_id": claim_id,
                        "node_sha256": node_sha,
                        "research_round": 1,
                        "search_direction": "counter direction",
                        "search_query": "test counter query",
                    },
                    task_id="task-counter-1",
                    execution_context=FakeExecutionContext(task_db),
                )
            machine = ResearchStateMachine(knowledge_db, handoff_dir=root / "handoff")
            self.assertEqual(machine.get_status(claim_id), "failed")
            with ResearchKnowledgeStore(knowledge_db) as store:
                self.assertEqual(store.list_claim_evidence(claim_id), [])

    def test_parent_waits_for_both_children_and_records_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            knowledge_db, claim_id, node_sha = self._claim(root, status="researching")
            task_db = root / "tasks.sqlite3"
            with db_connection(task_db) as db:
                db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT NOT NULL)")
                db.executemany(
                    "INSERT INTO tasks VALUES(?,?)",
                    [("child-support", "succeeded"), ("child-counter", "succeeded")],
                )
            handler = ResearchTaskHandler(
                knowledge_db=knowledge_db, evidence_db=root / "evidence.sqlite3",
                task_db=task_db, handoff_dir=root / "handoff",
                search=FakeSearch(), reader=FakeReader(),
            )
            result = handler.execute_task(
                {
                    "kind": "research_parent",
                    "source_node_id": claim_id,
                    "node_sha256": node_sha,
                    "child_tasks": [
                        {"type": "evidence_search", "task_id": "child-support"},
                        {"type": "counter_evidence_search", "task_id": "child-counter"},
                    ],
                },
                task_id="parent-1",
                execution_context=FakeExecutionContext(task_db),
            )
            self.assertEqual(result["research_conclusion"], "inconclusive")
            with ResearchKnowledgeStore(knowledge_db) as store:
                claim = store.get_node(claim_id)
                self.assertEqual(claim["status"], "proposed")
                self.assertEqual(claim["metadata"]["research_status"], "evaluated")
                self.assertFalse(claim["metadata"]["research_assessment"]["automatic_fact_promotion"])


if __name__ == "__main__":
    unittest.main()
