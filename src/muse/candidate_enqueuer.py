"""Idempotently connect creator-inspired claims to the existing Commander TaskQueue.

Dry-run is strictly read-only. The real AutonomousResearchEngine._next_topic
selector runs against a disposable SQLite backup, never the formal knowledge DB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from muse.lease_recovery import MigrationError, _backup_database, _integrity
from muse.task_queue import TaskQueue


class EnqueueError(RuntimeError):
    """Candidate planning or enqueueing failed closed."""


@dataclass(frozen=True)
class CandidatePlan:
    node_id: str
    title: str
    content_sha256: str
    research_status: str
    research_round: int
    support_direction: str
    counter_direction: str
    support_query: str
    counter_query: str
    tags: tuple[str, ...]
    research_domains: tuple[str, ...]
    task_keys: dict[str, str]
    existing: dict[str, dict[str, Any] | None]

    def preview(self) -> dict[str, Any]:
        missing = [kind for kind, task in self.existing.items() if task is None]
        metadata_change = self.research_status != "queued" or bool(missing)
        return {
            "node_id": self.node_id,
            "title": self.title,
            "content_sha256": self.content_sha256,
            "research_status": self.research_status,
            "research_round": self.research_round,
            "subtasks": [
                {
                    "type": "evidence_search",
                    "idempotency_key": self.task_keys["evidence_search"],
                    "search_direction": self.support_direction,
                    "search_query": self.support_query,
                },
                {
                    "type": "counter_evidence_search",
                    "idempotency_key": self.task_keys["counter_evidence_search"],
                    "search_direction": self.counter_direction,
                    "search_query": self.counter_query,
                },
            ],
            "parent_idempotency_key": self.task_keys["research_parent"],
            "existing_tasks": self.existing,
            "tasks_to_create": missing,
            "knowledge_metadata_rows_to_update": int(metadata_change),
        }


@dataclass(frozen=True)
class EnqueuePlan:
    candidates: tuple[CandidatePlan, ...]
    skipped: tuple[dict[str, Any], ...]
    max_candidates: int
    retry_failed: bool

    def preview(self) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "apply_required": True,
            "max_candidates": self.max_candidates,
            "retry_failed": self.retry_failed,
            "candidate_count": len(self.candidates),
            "task_rows_to_create": sum(
                sum(task is None for task in item.existing.values())
                for item in self.candidates
            ),
            "knowledge_rows_to_update": sum(
                item.preview()["knowledge_metadata_rows_to_update"]
                for item in self.candidates
            ),
            "objects": [item.preview() for item in self.candidates],
            "skipped": list(self.skipped),
            "database_writes": 0,
            "snapshots_created": 0,
        }


def _readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _valid_sha(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _task_key(node_sha256: str, task_type: str, research_round: int) -> str:
    raw = f"{node_sha256}:{task_type}:{research_round}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _topic_from_engine(engine: Any, node_id: str) -> dict[str, Any] | None:
    """Delegate eligibility and ranking to the real _next_topic implementation."""
    preferred_key = "creator-hypothesis-review:" + node_id
    topic = engine._next_topic(preferred_key=preferred_key)
    if not isinstance(topic, dict):
        return None
    if topic.get("key") != preferred_key:
        return None
    if topic.get("creator_inspiration_node_id") != node_id:
        return None
    return topic


def _make_engine(
    knowledge_copy: Path, *, workspace: Path, evidence_db: Path,
    progress_db: Path, reminder_db: Path,
) -> Any:
    """Construct the real selector without enabling network/browser research."""
    from muse.autonomous_research import AutonomousResearchEngine

    class NoBrowser:
        def probe_runtime(self) -> dict[str, Any]:
            return {"runtime_ready": False, "available": False}

    return AutonomousResearchEngine(
        evidence_db=evidence_db,
        knowledge_db=knowledge_copy,
        progress_db=progress_db,
        reminder_db=reminder_db,
        search=object(),
        reader=object(),
        ego_browser=NoBrowser(),
        workspace=workspace,
        max_sources=1,
        max_workers=1,
        auto_build_products=False,
        improvement_db=None,
        philosophy_db=workspace / "data" / "philosophy" / "muse.sqlite3",
    )


class CandidateEnqueuer:
    """Plan and enqueue a parent Ticket plus two dependent research subtasks."""

    def __init__(
        self, *, knowledge_db: str | Path, task_db: str | Path,
        topic_resolver: Callable[[str], dict[str, Any] | None],
        handoff_dir: str | Path,
    ) -> None:
        self.knowledge_db = Path(knowledge_db)
        self.task_db = Path(task_db)
        self.topic_resolver = topic_resolver
        self.handoff_dir = Path(handoff_dir)

    def build_plan(self, *, max_candidates: int = 5, retry_failed: bool = False) -> EnqueuePlan:
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= 100:
            raise ValueError("max_candidates must be between 1 and 100")
        if not isinstance(retry_failed, bool):
            raise ValueError("retry_failed must be boolean")
        for path in (self.knowledge_db, self.task_db):
            result = _integrity(path)
            if result != "ok":
                raise EnqueueError(f"database integrity check failed: {path}: {result}")

        knowledge = _readonly(self.knowledge_db)
        tasks = _readonly(self.task_db)
        candidates: list[CandidatePlan] = []
        skipped: list[dict[str, Any]] = []
        try:
            rows = knowledge.execute(
                "SELECT id,title,status,metadata_json FROM knowledge_nodes "
                "WHERE node_type='claim' ORDER BY created_at,id"
            ).fetchall()
            for row in rows:
                if len(candidates) >= max_candidates:
                    break
                node_id = str(row["id"])
                topic = self.topic_resolver(node_id)
                if topic is None:
                    continue
                try:
                    metadata = json.loads(row["metadata_json"])
                except (TypeError, ValueError):
                    skipped.append({"node_id": node_id, "reason": "invalid_metadata_json"})
                    continue
                if not isinstance(metadata, dict):
                    skipped.append({"node_id": node_id, "reason": "metadata_not_object"})
                    continue
                if row["status"] != "proposed":
                    skipped.append({"node_id": node_id, "reason": "claim_not_proposed"})
                    continue
                if metadata.get("epistemic_status") != "creator_inspired_hypothesis_only":
                    skipped.append({"node_id": node_id, "reason": "not_creator_inspired_hypothesis"})
                    continue
                if metadata.get("review_state") != "not_researched":
                    skipped.append({"node_id": node_id, "reason": "review_state_not_researched"})
                    continue
                if metadata.get("allow_external_research") is not True:
                    skipped.append({"node_id": node_id, "reason": "external_research_not_allowed"})
                    continue
                research_status = metadata.get("research_status", "not_researched")
                if research_status != "not_researched" and not (
                    retry_failed and research_status == "failed"
                ):
                    skipped.append({"node_id": node_id, "reason": f"research_status_{research_status}"})
                    continue
                node_sha = metadata.get("content_sha256")
                if not _valid_sha(node_sha):
                    skipped.append({"node_id": node_id, "reason": "missing_or_invalid_content_sha256"})
                    continue
                support_direction = metadata.get("support_search_direction")
                counter_direction = metadata.get("counter_search_direction")
                if not isinstance(support_direction, str) or not support_direction.strip():
                    skipped.append({"node_id": node_id, "reason": "missing_support_search_direction"})
                    continue
                if not isinstance(counter_direction, str) or not counter_direction.strip():
                    skipped.append({"node_id": node_id, "reason": "missing_counter_search_direction"})
                    continue
                support_query = topic.get("support_query")
                counter_query = topic.get("counter_query")
                if not isinstance(support_query, str) or not support_query.strip():
                    skipped.append({"node_id": node_id, "reason": "selector_missing_support_query"})
                    continue
                if not isinstance(counter_query, str) or not counter_query.strip():
                    skipped.append({"node_id": node_id, "reason": "selector_missing_counter_query"})
                    continue

                round_value = metadata.get("research_round", 1)
                if isinstance(round_value, bool) or not isinstance(round_value, int) or round_value < 1:
                    round_value = 1

                def keys_for(research_round: int) -> dict[str, str]:
                    return {
                        "evidence_search": _task_key(node_sha, "evidence_search", research_round),
                        "counter_evidence_search": _task_key(node_sha, "counter_evidence_search", research_round),
                        "research_parent": _task_key(node_sha, "research_parent", research_round),
                    }

                keys = keys_for(round_value)
                existing_rows = tasks.execute(
                    "SELECT id,idempotency_key,status FROM tasks WHERE idempotency_key IN (?,?,?)",
                    (keys["evidence_search"], keys["counter_evidence_search"], keys["research_parent"]),
                ).fetchall()
                existing_by_key = {item["idempotency_key"]: dict(item) for item in existing_rows}
                has_failed = any(item["status"] in {"failed", "cancelled"} for item in existing_by_key.values())
                if has_failed and not retry_failed:
                    skipped.append({"node_id": node_id, "reason": "failed_task_requires_retry_failed"})
                    continue
                if has_failed:
                    round_value += 1
                    while True:
                        keys = keys_for(round_value)
                        collision = tasks.execute(
                            "SELECT 1 FROM tasks WHERE idempotency_key IN (?,?,?) LIMIT 1",
                            (keys["evidence_search"], keys["counter_evidence_search"], keys["research_parent"]),
                        ).fetchone()
                        if collision is None:
                            break
                        round_value += 1
                    existing_by_key = {}

                by_type = {
                    task_type: existing_by_key.get(key)
                    for task_type, key in keys.items()
                }
                tags = metadata.get("tags", [])
                domains = metadata.get("research_domains", [])
                candidates.append(CandidatePlan(
                    node_id=node_id,
                    title=str(row["title"]),
                    content_sha256=node_sha,
                    research_status=research_status,
                    research_round=round_value,
                    support_direction=support_direction.strip()[:3000],
                    counter_direction=counter_direction.strip()[:3000],
                    support_query=support_query.strip()[:1000],
                    counter_query=counter_query.strip()[:1000],
                    tags=tuple(value for value in tags if isinstance(value, str) and value.strip())[:32]
                    if isinstance(tags, list) else (),
                    research_domains=tuple(value for value in domains if isinstance(value, str) and value.strip())[:32]
                    if isinstance(domains, list) else (),
                    task_keys=keys,
                    existing=by_type,
                ))
        finally:
            knowledge.close()
            tasks.close()
        return EnqueuePlan(tuple(candidates), tuple(skipped), max_candidates, retry_failed)

    def apply_plan(self, plan: EnqueuePlan) -> dict[str, Any]:
        if not isinstance(plan, EnqueuePlan):
            raise TypeError("plan must be an EnqueuePlan")
        actionable = [
            candidate for candidate in plan.candidates
            if candidate.preview()["tasks_to_create"]
            or candidate.preview()["knowledge_metadata_rows_to_update"]
        ]
        if not actionable:
            return {**plan.preview(), "status": "no_changes", "snapshots": []}

        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        nonce = uuid.uuid4().hex[:10]
        backups = {
            "knowledge": self.handoff_dir / f"research-enqueue-{stamp}-{nonce}-knowledge.sqlite3",
            "tasks": self.handoff_dir / f"research-enqueue-{stamp}-{nonce}-tasks.sqlite3",
        }
        if any(path.exists() for path in backups.values()):
            raise MigrationError("research enqueue backup target already exists; refusing overwrite")
        for path in (self.knowledge_db, self.task_db):
            result = _integrity(path)
            if result != "ok":
                raise EnqueueError(f"database integrity check failed before backup: {path}: {result}")
        _backup_database(self.knowledge_db, backups["knowledge"])
        _backup_database(self.task_db, backups["tasks"])

        created: list[dict[str, Any]] = []
        with TaskQueue(self.task_db) as queue:
            for candidate in actionable:
                child_ids: dict[str, str] = {}
                for task_type, direction, query in (
                    ("evidence_search", candidate.support_direction, candidate.support_query),
                    ("counter_evidence_search", candidate.counter_direction, candidate.counter_query),
                ):
                    existing = candidate.existing.get(task_type)
                    if existing is not None:
                        child_ids[task_type] = existing["id"]
                        continue
                    task = queue.enqueue(
                        f"{task_type}: {candidate.title[:100]}",
                        {
                            "kind": task_type,
                            "subtask_type": task_type,                            "source_node_id": candidate.node_id,
                            "node_sha256": candidate.content_sha256,
                            "research_round": candidate.research_round,
                            "search_direction": direction,
                            "search_query": query,
                            "tags": list(candidate.tags),
                            "research_domains": list(candidate.research_domains),
                            "evidence_policy": "public_sources_only",
                            "claim_status_unchanged": True,
                        },
                        priority=45,
                        estimated_seconds=600,
                        recovery_policy="manual",
                        idempotency_key=candidate.task_keys[task_type],
                        workload_class="research",
                    )
                    child_ids[task_type] = task["id"]
                    created.append({"task_id": task["id"], "type": task_type, "status": task["status"]})

                parent_existing = candidate.existing.get("research_parent")
                if parent_existing is None:
                    parent = queue.enqueue(
                        f"Research candidate: {candidate.title[:110]}",
                        {
                            "kind": "research_parent",
                            "source_node_id": candidate.node_id,
                            "node_sha256": candidate.content_sha256,
                            "research_round": candidate.research_round,
                            "child_tasks": [
                                {"type": "evidence_search", "task_id": child_ids["evidence_search"],
                                 "idempotency_key": candidate.task_keys["evidence_search"]},
                                {"type": "counter_evidence_search", "task_id": child_ids["counter_evidence_search"],
                                 "idempotency_key": candidate.task_keys["counter_evidence_search"]},
                            ],
                            "evaluation_policy": "both_directions_and_verifiable_sources_required",
                            "claim_status_unchanged": True,
                        },
                        priority=50,
                        estimated_seconds=120,
                        recovery_policy="manual",
                        idempotency_key=candidate.task_keys["research_parent"],
                        depends_on=(child_ids["evidence_search"], child_ids["counter_evidence_search"]),
                        workload_class="research",
                    )
                    parent_id = parent["id"]
                    created.append({"task_id": parent_id, "type": "research_parent", "status": parent["status"]})
                else:
                    parent_id = parent_existing["id"]

                self._set_queued_metadata(candidate, child_ids, parent_id, allow_failed=plan.retry_failed)

        return {
            **plan.preview(),
            "status": "applied",
            "created_tasks": created,
            "snapshots": {key: str(value) for key, value in backups.items()},
            "database_writes": len(created) + len(actionable),
        }

    def _set_queued_metadata(
        self, candidate: CandidatePlan, child_ids: dict[str, str],
        parent_id: str, *, allow_failed: bool,
    ) -> None:
        db = sqlite3.connect(self.knowledge_db, timeout=10)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT metadata_json,status FROM knowledge_nodes WHERE id=? AND node_type='claim'",
                (candidate.node_id,),
            ).fetchone()
            if row is None or row[1] != "proposed":
                raise EnqueueError("claim changed before research metadata update")
            metadata = json.loads(row[0])
            if metadata.get("content_sha256") != candidate.content_sha256:
                raise EnqueueError("claim content hash changed before metadata update")
            current_status = metadata.get("research_status", "not_researched")
            allowed = {"not_researched", "queued"}
            if allow_failed:
                allowed.add("failed")
            if current_status not in allowed:
                raise EnqueueError(f"claim research status changed to {current_status}")
            metadata.update({
                "research_status": "queued",
                "research_round": candidate.research_round,
                "research_task_ids": {
                    "evidence_search": child_ids["evidence_search"],
                    "counter_evidence_search": child_ids["counter_evidence_search"],
                    "research_parent": parent_id,
                },
                "research_task_keys": candidate.task_keys,
                "research_status_updated_at": datetime.now(timezone.utc).isoformat(),
            })
            cursor = db.execute(
                "UPDATE knowledge_nodes SET metadata_json=?,updated_at=? "
                "WHERE id=? AND node_type='claim' AND status='proposed' AND metadata_json=?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                 datetime.now(timezone.utc).isoformat(), candidate.node_id, row[0]),
            )
            if cursor.rowcount != 1:
                raise EnqueueError("claim metadata compare-and-swap failed")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def build_plan_with_real_selector(
    *, project_root: str | Path, knowledge_db: str | Path, task_db: str | Path,
    evidence_db: str | Path, progress_db: str | Path, reminder_db: str | Path,
    handoff_dir: str | Path, max_candidates: int = 5, retry_failed: bool = False,
) -> tuple[CandidateEnqueuer, EnqueuePlan]:
    """Use the actual selector against a disposable copy, then discard it."""
    root = Path(project_root).resolve()
    source = Path(knowledge_db)
    with tempfile.TemporaryDirectory(prefix="muse-candidate-plan-") as temp:
        copy_path = Path(temp) / "knowledge.sqlite3"
        _backup_database(source, copy_path)
        engine = _make_engine(
            copy_path, workspace=root, evidence_db=Path(evidence_db),
            progress_db=Path(progress_db), reminder_db=Path(reminder_db),
        )
        enqueuer = CandidateEnqueuer(
            knowledge_db=source,
            task_db=task_db,
            topic_resolver=lambda node_id: _topic_from_engine(engine, node_id),
            handoff_dir=handoff_dir,
        )
        plan = enqueuer.build_plan(max_candidates=max_candidates, retry_failed=retry_failed)
        return enqueuer, plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enqueue creator-inspired claims into Muse Commander")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--knowledge-db", default="data/research/knowledge.sqlite3")
    parser.add_argument("--task-db", default="data/tasks/muse.sqlite3")
    parser.add_argument("--evidence-db", default="data/research/evidence.sqlite3")
    parser.add_argument("--progress-db", default="data/tasks/muse.sqlite3")
    parser.add_argument("--reminder-db", default="data/reminders/muse.sqlite3")
    parser.add_argument("--handoff-dir", default="data/handoff")
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        enqueuer, plan = build_plan_with_real_selector(
            project_root=args.project_root,
            knowledge_db=args.knowledge_db,
            task_db=args.task_db,
            evidence_db=args.evidence_db,
            progress_db=args.progress_db,
            reminder_db=args.reminder_db,
            handoff_dir=args.handoff_dir,
            max_candidates=args.max_candidates,
            retry_failed=args.retry_failed,
        )
        result = enqueuer.apply_plan(plan) if args.apply else plan.preview()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
