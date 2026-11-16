"""Idempotent bridge from creator review feedback to Research Wiki and capability signals.

Feedback is an unverified user signal, never a factual claim or an approval.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from muse.improvement import ImprovementStore
from muse.initiative_engine import InitiativeStore
from muse.research_knowledge import ResearchKnowledgeStore


_FEEDBACK_SIGNAL = {
    "revise": ("capability", "workflow_gap", 4),
    "reject": ("accuracy", "incorrect_result", 5),
}


def sync_initiative_feedback(
    workspace: str | Path,
    improvement_database: str | Path,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    """Synchronize bounded creator feedback into proposal, radar and knowledge ledgers."""
    root = Path(workspace).resolve()
    initiative_database = root / "data" / "initiatives" / "muse.sqlite3"
    knowledge_database = root / "data" / "research" / "knowledge.sqlite3"
    if not initiative_database.is_file() or initiative_database.is_symlink():
        return {
            "status": "not_initialized", "scanned": 0, "proposals_created": 0,
            "proposals_existing": 0, "knowledge_nodes_linked": 0,
            "structured_labels_recorded": 0, "knowledge_status": "not_initialized",
        }
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")

    with InitiativeStore(initiative_database) as initiatives:
        feedback_items = initiatives.list_feedback(limit=limit)
        failure_items = initiatives.list_failures(limit=limit)

    knowledge_available = knowledge_database.is_file() and not knowledge_database.is_symlink()
    result: dict[str, Any] = {
        "status": "completed", "scanned": len(feedback_items),
        "proposals_created": 0, "proposals_existing": 0,
        "knowledge_nodes_linked": 0, "structured_labels_recorded": 0,
        "failures_scanned": len(failure_items), "failure_nodes_linked": 0,
        "failure_nodes_created": 0, "failure_nodes_existing": 0,
        "knowledge_status": "ready" if knowledge_available else "not_initialized",
    }
    with ImprovementStore(improvement_database) as improvements:
        knowledge_context = ResearchKnowledgeStore(knowledge_database) if knowledge_available else None
        try:
            with InitiativeStore(initiative_database) as initiatives:
                for feedback in feedback_items:
                    feedback_id = feedback["id"]
                    initiative_id = feedback["initiative_id"]
                    decision = feedback["decision"]
                    proposal_before = improvements.connection.execute(
                        "SELECT id FROM improvement_proposals WHERE source_feedback_id=?",
                        (feedback_id,),
                    ).fetchone()
                    if decision in _FEEDBACK_SIGNAL:
                        improvements.create_feedback_proposal(feedback)
                        if proposal_before is None:
                            result["proposals_created"] += 1
                        else:
                            result["proposals_existing"] += 1
                        category, tag, severity = _FEEDBACK_SIGNAL[decision]
                        label_before = improvements.connection.execute(
                            "SELECT 1 FROM improvement_feedback WHERE source_feedback_id=?",
                            (feedback_id,),
                        ).fetchone()
                        improvements.record_feedback(
                            category, tag, severity=severity, source_feedback_id=feedback_id,
                        )
                        if label_before is None:
                            result["structured_labels_recorded"] += 1

                    if knowledge_context is None:
                        continue
                    cycle_id = feedback.get("evolution_cycle_id")
                    external_ref = f"initiative-feedback:{feedback_id}"
                    existing_node = knowledge_context.connection.execute(
                        "SELECT id FROM knowledge_nodes WHERE natural_key=? AND deleted_at IS NULL",
                        ("idea:" + external_ref,),
                    ).fetchone()
                    metadata = {
                        "origin": "initiative_review_feedback",
                        "source_feedback_id": feedback_id,
                        "initiative_id": initiative_id,
                        "decision": decision,
                        "reviewer": feedback.get("reviewer", "")[:200],
                        "source": feedback.get("source", "")[:200],
                        "feedback_created_at": feedback.get("created_at", "")[:80],
                        "epistemic_status": "unverified_user_feedback_signal",
                        "requires_research_and_independent_review": True,
                    }
                    if cycle_id:
                        metadata["evolution_cycle_id"] = cycle_id
                    node = knowledge_context.upsert_node(
                        "idea",
                        f"创作反馈待核验：{initiative_id[:12]}（{decision}）",
                        summary=feedback["feedback"][:2000],
                        external_ref=external_ref,
                        metadata=metadata,
                        provenance=(f"creator-initiative:{initiative_id}", f"initiative-feedback:{feedback_id}"),
                    )
                    knowledge_context.link_node_to_initiative(
                        initiatives, initiative_id, node["id"],
                        relationship="improvement_candidate" if decision in _FEEDBACK_SIGNAL else "related",
                        evolution_cycle_id=cycle_id,
                    )
                    result["knowledge_nodes_linked"] += 1
                    if existing_node is None:
                        result.setdefault("knowledge_nodes_created", 0)
                        result["knowledge_nodes_created"] += 1
                    else:
                        result.setdefault("knowledge_nodes_existing", 0)
                        result["knowledge_nodes_existing"] += 1
                for failure in failure_items:
                    if knowledge_context is None:
                        continue
                    failure_id = failure["id"]
                    initiative_id = failure["initiative_id"]
                    cycle_id = failure.get("evolution_cycle_id")
                    external_ref = f"initiative-video-failure:{failure_id}"
                    existing_node = knowledge_context.connection.execute(
                        "SELECT id FROM knowledge_nodes WHERE natural_key=? AND deleted_at IS NULL",
                        ("failure:" + external_ref,),
                    ).fetchone()
                    metadata = {
                        "origin": "creator_video_job_failure",
                        "source_failure_id": failure_id,
                        "initiative_id": initiative_id,
                        "failure_code": failure["failure_code"],
                        "error_type": failure["error_type"],
                        "epistemic_status": "observed_execution_failure_not_claim_evidence",
                        "requires_reproduction_before_root_cause_conclusion": True,
                    }
                    if cycle_id:
                        metadata["evolution_cycle_id"] = cycle_id
                    node = knowledge_context.upsert_node(
                        "failure",
                        f"视频创作失败记录：{failure['failure_code']}",
                        summary="A local video product job failed. This is an execution observation, not evidence for an external factual claim.",
                        external_ref=external_ref,
                        metadata=metadata,
                        provenance=(f"creator-initiative:{initiative_id}", f"initiative-video-failure:{failure_id}"),
                    )
                    knowledge_context.link_node_to_initiative(
                        initiatives, initiative_id, node["id"],
                        relationship="addresses_gap", evolution_cycle_id=cycle_id,
                    )
                    result["failure_nodes_linked"] += 1
                    if existing_node is None:
                        result["failure_nodes_created"] += 1
                    else:
                        result["failure_nodes_existing"] += 1
        finally:
            if knowledge_context is not None:
                knowledge_context.close()
    # Preserve the legacy autonomous-research result contract while exposing richer counters.
    result["created"] = result["proposals_created"]
    result["existing"] = result["proposals_existing"]
    return result
