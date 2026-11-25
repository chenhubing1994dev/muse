from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from muse.candidate_enqueuer import CandidateEnqueuer
from muse.task_queue import TaskQueue


@contextmanager
def db_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class CandidateEnqueuerTests(unittest.TestCase):
    def _fixtures(self, root: Path, count: int = 1) -> tuple[Path, Path, dict[str, dict[str, str]]]:
        knowledge = root / "knowledge.sqlite3"
        tasks = root / "tasks.sqlite3"
        resolver: dict[str, dict[str, str]] = {}
        with db_connection(knowledge) as db:
            db.execute("""CREATE TABLE knowledge_nodes (
                id TEXT PRIMARY KEY, node_type TEXT NOT NULL, title TEXT NOT NULL,
                summary TEXT NOT NULL, status TEXT NOT NULL, external_ref TEXT,
                metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            for index in range(count):
                node_id = f"claim-{index}"
                content_sha = hashlib.sha256(f"claim-content-{index}".encode()).hexdigest()
                metadata = {
                    "origin": "creator_inspiration_inbox",
                    "epistemic_status": "creator_inspired_hypothesis_only",
                    "creator_context_is_evidence": False,
                    "requires_support_and_counter_evidence": True,
                    "review_state": "not_researched",
                    "allow_external_research": True,
                    "content_sha256": content_sha,
                    "support_search_direction": f"support direction {index}",
                    "counter_search_direction": f"counter direction {index}",
                    "tags": ["tag-a", "tag-b"],
                    "research_domains": ["domain-a", "domain-b"],
                }
                db.execute(
                    "INSERT INTO knowledge_nodes VALUES (?,?,?,?,?,?,?,?,?)",
                    (node_id, "claim", f"Claim {index}", f"Summary {index}", "proposed",
                     f"external-{index}", json.dumps(metadata), "2026-11-25", "2026-11-25"),
                )
                resolver[node_id] = {
                    "key": "creator-hypothesis-review:" + node_id,
                    "creator_inspiration_node_id": node_id,
                    "support_query": f"support query {index}",
                    "counter_query": f"counter query {index}",
                }
        with TaskQueue(tasks):
            pass
        return knowledge, tasks, resolver

    def _enqueuer(self, root: Path, knowledge: Path, tasks: Path, resolver: dict[str, dict[str, str]]) -> CandidateEnqueuer:
        return CandidateEnqueuer(
            knowledge_db=knowledge, task_db=tasks,
            topic_resolver=lambda node_id: resolver.get(node_id),
            handoff_dir=root / "handoff",
        )

    def test_dry_run_has_zero_writes_and_no_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            knowledge, tasks, resolver = self._fixtures(root)
            before = (hashlib.sha256(knowledge.read_bytes()).hexdigest(), hashlib.sha256(tasks.read_bytes()).hexdigest())
            plan = self._enqueuer(root, knowledge, tasks, resolver).build_plan()
            after = (hashlib.sha256(knowledge.read_bytes()).hexdigest(), hashlib.sha256(tasks.read_bytes()).hexdigest())
            self.assertEqual(before, after)
            self.assertEqual(plan.preview()["database_writes"], 0)
            self.assertEqual(plan.preview()["snapshots_created"], 0)
            self.assertFalse((root / "handoff").exists())

    def test_apply_creates_one_parent_two_children_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            knowledge, tasks, resolver = self._fixtures(root)
            enqueuer = self._enqueuer(root, knowledge, tasks, resolver)
            plan = enqueuer.build_plan()
            first = enqueuer.apply_plan(plan)
            self.assertEqual(first["status"], "applied")
            self.assertEqual(len(first["created_tasks"]), 3)
            self.assertEqual({item["type"] for item in first["created_tasks"]},
                             {"evidence_search", "counter_evidence_search", "research_parent"})
            with TaskQueue(tasks) as queue:
                counts = queue._connection.execute("SELECT status,count(*) FROM tasks GROUP BY status").fetchall()
                self.assertEqual(sum(row[1] for row in counts), 3)
                parent = queue._connection.execute(
                    "SELECT payload_json,depends_on_json FROM tasks WHERE json_extract(payload_json,'$.kind')='research_parent'"
                ).fetchone()
                parent_payload = json.loads(parent[0])
                self.assertEqual(len(parent_payload["child_tasks"]), 2)
                self.assertEqual(len(json.loads(parent[1])), 2)
            with db_connection(knowledge) as db:
                metadata = json.loads(db.execute("SELECT metadata_json FROM knowledge_nodes WHERE id='claim-0'").fetchone()[0])
                self.assertEqual(metadata["research_status"], "queued")
                self.assertEqual(metadata["review_state"], "not_researched")
            second = enqueuer.apply_plan(plan)
            self.assertEqual(second["status"], "applied")
            with TaskQueue(tasks) as queue:
                self.assertEqual(queue._connection.execute("SELECT count(*) FROM tasks").fetchone()[0], 3)

    def test_failed_tasks_are_not_requeued_without_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            knowledge, tasks, resolver = self._fixtures(root)
            enqueuer = self._enqueuer(root, knowledge, tasks, resolver)
            plan = enqueuer.build_plan()
            candidate = plan.candidates[0]
            with TaskQueue(tasks) as queue:
                failed = queue.enqueue(
                    "failed evidence task", {"kind": "research_subtask"},
                    idempotency_key=candidate.task_keys["evidence_search"],
                    workload_class="research", recovery_policy="manual",
                )
                queue.claim_next(worker_id="test-worker", task_id=failed["id"])
                queue.finish(
                    failed["id"], status="failed", worker_id="test-worker",
                    result={"error_code": "test"},
                )
            skipped = enqueuer.build_plan()
            self.assertEqual(skipped.candidates, ())
            self.assertIn("failed_task_requires_retry_failed", {item["reason"] for item in skipped.skipped})
            retry = enqueuer.build_plan(retry_failed=True)
            self.assertEqual(retry.candidates[0].research_round, 2)
            self.assertNotEqual(
                retry.candidates[0].task_keys["evidence_search"],
                candidate.task_keys["evidence_search"],
            )

    def test_limit_is_respected_and_node_status_is_never_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            knowledge, tasks, resolver = self._fixtures(root, count=3)
            plan = self._enqueuer(root, knowledge, tasks, resolver).build_plan(max_candidates=2)
            self.assertEqual(len(plan.candidates), 2)
            with db_connection(knowledge) as db:
                statuses = db.execute("SELECT DISTINCT status FROM knowledge_nodes").fetchall()
                self.assertEqual([row[0] for row in statuses], ["proposed"])


if __name__ == "__main__":
    unittest.main()
