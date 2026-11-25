"""Commander Worker handlers for evidence, counter-evidence and claim assessment."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from muse.lease_recovery import _backup_database, _integrity
from muse.research_fetch import PublicSourceReader, ResearchEvidenceStore
from muse.research_knowledge import ResearchKnowledgeStore
from muse.research_pipeline import ResearchPipeline
from muse.research_state_machine import ResearchStateError, ResearchStateMachine


class ResearchTaskError(RuntimeError):
    """A research task failed without manufacturing evidence or conclusions."""


class ResearchTaskHandler:
    """Execute research subtasks through the existing Worker and ResearchPipeline."""

    accepts_execution_context = True

    def __init__(
        self,
        *,
        knowledge_db: str | Path = "data/research/knowledge.sqlite3",
        evidence_db: str | Path = "data/research/evidence.sqlite3",
        task_db: str | Path = "data/tasks/muse.sqlite3",
        handoff_dir: str | Path = "data/handoff",
        search: Any | None = None,
        reader: Any | None = None,
        max_sources: int = 8,
        max_workers: int = 3,
    ) -> None:
        if not 1 <= max_sources <= 16:
            raise ValueError("max_sources must be between 1 and 16")
        if not 1 <= max_workers <= 8:
            raise ValueError("max_workers must be between 1 and 8")
        self.knowledge_db = Path(knowledge_db)
        self.evidence_db = Path(evidence_db)
        self.task_db = Path(task_db)
        self.handoff_dir = Path(handoff_dir)
        self.max_sources = max_sources
        self.max_workers = max_workers
        if search is None:
            from muse.research import DuckDuckGoHTMLSearchAdapter
            search = DuckDuckGoHTMLSearchAdapter()
        self.search = search
        self.reader = reader or PublicSourceReader()
        self.state = ResearchStateMachine(self.knowledge_db, handoff_dir=self.handoff_dir)

    def _snapshot_if_present(self, path: Path, label: str) -> str | None:
        if not path.exists():
            return None
        integrity = _integrity(path)
        if integrity != "ok":
            raise ResearchTaskError(f"{label} database integrity check failed: {integrity}")
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.handoff_dir / f"research-task-{stamp}-{uuid.uuid4().hex[:10]}-{label}.sqlite3"
        if target.exists():
            raise ResearchTaskError(f"backup target already exists; refusing overwrite: {target}")
        _backup_database(path, target)
        return str(target)

    @staticmethod
    def _check_lease(execution_context: Any) -> None:
        if execution_context is not None:
            execution_context.assert_current_lease()

    def execute_task(
        self, payload: dict[str, Any], *, task_id: str, execution_context: Any,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("research task payload must be an object")
        kind = payload.get("kind")
        if kind in {"evidence_search", "counter_evidence_search"}:
            return self._run_subtask(payload, task_id=task_id, execution_context=execution_context)
        if kind == "research_parent":
            return self._run_parent(payload, task_id=task_id, execution_context=execution_context)
        raise ValueError(f"unsupported research task kind: {kind}")

    def _run_subtask(
        self, payload: dict[str, Any], *, task_id: str, execution_context: Any,
    ) -> dict[str, Any]:
        task_type = payload.get("kind")
        direction = (
            "support_search_candidate" if task_type == "evidence_search"
            else "counter_search_candidate"
        )
        claim_id = payload.get("source_node_id")
        node_sha = payload.get("node_sha256")
        query = payload.get("search_query")
        search_direction = payload.get("search_direction")
        research_round = payload.get("research_round")
        if not isinstance(claim_id, str) or not claim_id:
            raise ValueError("research subtask is missing source_node_id")
        if not isinstance(node_sha, str) or len(node_sha) != 64:
            raise ValueError("research subtask is missing node SHA-256")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("research subtask is missing a bounded search_query")
        if not isinstance(search_direction, str) or not search_direction.strip():
            raise ValueError("research subtask is missing the original search direction")
        if isinstance(research_round, bool) or not isinstance(research_round, int) or research_round < 1:
            raise ValueError("research subtask has an invalid research_round")

        current = self.state.get_status(claim_id)
        if current == "queued":
            self.state.transition(claim_id, "researching")
        elif current != "researching":
            raise ResearchTaskError(f"claim is not researchable from state {current}")

        self._check_lease(execution_context)
        evidence_backup = self._snapshot_if_present(self.evidence_db, "evidence")
        pipeline_result: dict[str, Any]
        try:
            with ResearchEvidenceStore(self.evidence_db) as evidence_store:
                pipeline = ResearchPipeline(
                    self.search, self.reader, evidence_store,
                    max_workers=self.max_workers, resource_aware=False,
                    progress_events=None, knowledge_store=None,
                )
                pipeline_result = pipeline.run((query.strip(),), max_sources=self.max_sources, task_id=task_id)
        except Exception as exc:
            self.state.record_failure(
                claim_id, reason=f"{type(exc).__name__}: {exc}", error_code="research_pipeline_failed",
            )
            raise

        sources = pipeline_result.get("sources", [])
        failures = pipeline_result.get("failures", [])
        if pipeline_result.get("cancelled"):
            self.state.record_failure(
                claim_id, reason="Research pipeline was cancelled", error_code="research_cancelled",
            )
            raise ResearchTaskError("research pipeline was cancelled")
        if failures and not sources:
            reason = "; ".join(str(item.get("error", "unknown")) for item in failures[:5])
            self.state.record_failure(
                claim_id, reason=f"Search/read produced no sources: {reason}",
                error_code="research_no_sources_after_failure",
            )
            raise ResearchTaskError("search/read failed and produced no usable sources")

        self._check_lease(execution_context)
        if self.state.get_status(claim_id) != "researching":
            raise ResearchTaskError("claim research state changed while evidence was being fetched")
        knowledge_backup = self._snapshot_if_present(self.knowledge_db, "knowledge")
        recorded: list[dict[str, Any]] = []
        try:
            with ResearchKnowledgeStore(self.knowledge_db) as knowledge:
                claim = knowledge.get_node(claim_id)
                if claim is None or claim.get("status") != "proposed":
                    raise ResearchTaskError("source Claim is missing or no longer proposed")
                if claim.get("metadata", {}).get("content_sha256") != node_sha:
                    raise ResearchTaskError("Claim SHA-256 changed while research task was running")
                for source in sources:
                    self._check_lease(execution_context)
                    if self.state.get_status(claim_id) != "researching":
                        raise ResearchTaskError("claim entered a terminal research state during source recording")
                    url = source.get("url")
                    content_hash = source.get("sha256")
                    if not isinstance(url, str) or not isinstance(content_hash, str) or len(content_hash) != 64:
                        continue
                    source_metadata = {
                        "content_hash": content_hash,
                        "sha256": content_hash,
                        "chars": int(source.get("chars") or 0),
                        "fetched_at": source.get("fetched_at"),
                        "content_type": source.get("content_type"),
                        "truncated": bool(source.get("truncated")),
                        "source_identity": source.get("source_identity") or {},
                        "source_signals": source.get("source_signals") or {},
                        "capture_method": source.get("capture_method", "http"),
                        "citation_id": source.get("citation_id"),
                        "search_query": query.strip(),
                        "search_direction": direction,
                        "research_task_id": task_id,
                        "research_round": research_round,
                    }
                    knowledge.connection.execute("BEGIN IMMEDIATE")
                    source_node = knowledge.upsert_node(
                        "source",
                        str(source.get("title") or url)[:500],
                        summary=str(source.get("snippet") or "")[:2000],
                        external_ref=url,
                        metadata=source_metadata,
                    )
                    knowledge.connection.execute("BEGIN IMMEDIATE")
                    evidence = knowledge.record_claim_evidence(
                        claim_id,
                        source_node["id"],
                        direction=direction,
                        evidence_type="web_source_extract",
                        search_query=query.strip(),
                        notes=json.dumps({
                            "research_task_id": task_id,
                            "research_round": research_round,
                            "node_sha256": node_sha,
                            "search_direction": search_direction[:500],
                        }, ensure_ascii=False),
                    )
                    recorded.append({
                        "evidence_id": evidence["id"],
                        "source_ref": url,
                        "source_sha256": content_hash,
                        "direction": direction,
                    })
        except Exception as exc:
            self.state.record_failure(
                claim_id, reason=f"Evidence attribution failed: {type(exc).__name__}: {exc}",
                error_code="research_evidence_persistence_failed",
            )
            raise

        return {
            "status": "evidence_collected" if recorded else "search_completed_no_readable_sources",
            "task_id": task_id,
            "source_node_id": claim_id,
            "subtask_type": task_type,
            "research_round": research_round,
            "search_query": query.strip(),
            "search_direction": search_direction,
            "read_source_count": len(sources),
            "recorded_claim_evidence_count": len(recorded),
            "recorded_evidence": recorded,
            "search_failure_count": len(failures),
            "failures": failures[:10],
            "evidence_database_backup": evidence_backup,
            "knowledge_database_backup": knowledge_backup,
            "claim_status_unchanged": True,
            "no_verdict_assigned": True,
        }

    def _run_parent(
        self, payload: dict[str, Any], *, task_id: str, execution_context: Any,
    ) -> dict[str, Any]:
        claim_id = payload.get("source_node_id")
        node_sha = payload.get("node_sha256")
        children = payload.get("child_tasks")
        if not isinstance(claim_id, str) or not isinstance(node_sha, str):
            raise ValueError("research parent is missing claim identity")
        if not isinstance(children, list) or len(children) != 2:
            raise ValueError("research parent must contain exactly two child task references")
        child_ids = [item.get("task_id") for item in children if isinstance(item, dict)]
        if len(child_ids) != 2 or not all(isinstance(item, str) for item in child_ids):
            raise ValueError("research parent child task references are invalid")
        self._check_lease(execution_context)

        task_db = Path(getattr(execution_context, "database", self.task_db))
        db = sqlite3.connect(task_db.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            statuses = {
                row[0]: row[1] for row in db.execute(
                    "SELECT id,status FROM tasks WHERE id IN (?,?)", tuple(child_ids)
                ).fetchall()
            }
        finally:
            db.close()
        if any(statuses.get(child_id) != "succeeded" for child_id in child_ids):
            raise ResearchTaskError("research parent cannot evaluate until both child tasks succeeded")

        claim = self.state._read_claim(claim_id)[1]
        if claim.get("content_sha256") != node_sha:
            raise ResearchTaskError("Claim SHA-256 changed before parent evaluation")
        current = self.state.get_status(claim_id)
        if current == "researching":
            self.state.transition(
                claim_id, "evidence_collected",
                metadata_updates={"evidence_collected_at": datetime.now(timezone.utc).isoformat()},
            )
        elif current != "evidence_collected":
            raise ResearchTaskError(f"claim cannot be evaluated from research state {current}")
        self._check_lease(execution_context)
        result = self.state.evaluate_claim(claim_id)
        return {
            "status": "evaluated",
            "task_id": task_id,
            "source_node_id": claim_id,
            "research_conclusion": result["conclusion"],
            "evidence_count": result["evidence_count"],
            "support_search_source_count": result["support_search_source_count"],
            "counter_search_source_count": result["counter_search_source_count"],
            "reasons": result["reasons"],
            "claim_status_unchanged": True,
            "automatic_fact_promotion": False,
            "assessment_backup": result["backup"],
        }
