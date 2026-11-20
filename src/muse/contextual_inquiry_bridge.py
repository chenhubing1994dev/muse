"""Explicit bridge from contextual inquiry to existing Muse stores.

No new scheduler, database, memory track, or execution authority is introduced.
The bridge can place only a separately consented DPAPI ciphertext envelope in
TaskQueue; raw observations, stakeholder statements, and action narratives are
never persisted in plaintext by this bridge.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from muse.contextual_inquiry import ContextualInquiry, SafetyRoute
from muse.contextual_inquiry_security import (
    _serialize_contextual_inquiry,
    deserialize_contextual_inquiry,
    unprotect_case_bytes,
)

from muse.research_knowledge import ResearchKnowledgeStore
from muse.task_queue import TaskQueue
from muse.workflow_profiles import WorkflowContract


def build_workflow_contract(
    inquiry: ContextualInquiry, *, include_user_goal: bool = False
) -> WorkflowContract:
    """Map inquiry guardrails into the existing, versioned Commander contract.

    The default goal is generic so a caller cannot accidentally persist a
    personal case narrative into the TaskQueue workflow event log. Including
    the user's exact goal requires the same explicit storage-consent gate.
    This function itself remains an in-memory projection.
    """
    if not isinstance(inquiry, ContextualInquiry):
        raise TypeError("inquiry must be a ContextualInquiry")
    if not isinstance(include_user_goal, bool):
        raise TypeError("include_user_goal must be a boolean")
    if include_user_goal:
        inquiry.assert_persistable()

    goal = (
        inquiry.user_goal if include_user_goal
        else "Review a contextual inquiry using evidence provenance and safety boundaries."
    )
    escalation = ()
    if inquiry.safety_route is not SafetyRoute.NORMAL:
        escalation = (
            f"Safety route is {inquiry.safety_route.value}; do not dispatch ordinary action experiments.",
        )

    return WorkflowContract(
        goal=goal,
        desired_outcome=(
            "A contextual inquiry is reviewed with source provenance, competing "
            "hypotheses, explicit unknowns, and safety boundaries preserved."
        ),
        acceptance_criteria=(
            "Observations retain their declared source kind and source reference.",
            "Hypotheses retain supporting and counter-evidence references or an explicit unsourced label.",
            "Stakeholder perspectives are sourced statements or remain explicitly unknown.",
            "No interpretation is promoted to a verified fact without independent evidence review.",
            "Any proposed action is reversible and includes an observable signal and stop condition.",
        ),
        scope=(
            "Contextual inquiry and evidence-aware hypothesis review.",
            "Reuse the existing Muse Commander and ResearchKnowledgeStore.",
        ),
        exclusions=(
            "No diagnosis, mind-reading, or unsupported motive attribution.",
            "No automatic persistence of observation text or stakeholder narratives.",
            "No automatic execution of proposed action experiments.",
        ),
        source_precedence=(
            "Preserve the declared source kind and source reference for every observation.",
            "Model interpretations remain hypotheses, not source evidence.",
        ),
        escalation_conditions=escalation,
        feedback_metrics=(
            "Counter-evidence and falsification conditions remain attached to hypotheses.",
            "Action feedback records adverse or disconfirming outcomes without auto-promoting claims.",
        ),
    )


def enqueue_review_task(
    queue: TaskQueue,
    inquiry: ContextualInquiry,
    *,
    include_user_goal: bool = False,
    case_storage_consent: bool = False,
    encrypted_case_context: dict[str, Any] | None = None,
    priority: int = 50,
) -> dict[str, Any]:
    """Create an approval-gated Ticket with optional DPAPI ciphertext, never plaintext.

    The complete inquiry is stored only when the caller supplies a separately
    consented, valid envelope. The task remains non-approvable until a trusted
    review handler is registered and attaches validated context.
    """
    if not isinstance(queue, TaskQueue):
        raise TypeError("queue must be the existing Muse TaskQueue")
    if not isinstance(inquiry, ContextualInquiry):
        raise TypeError("inquiry must be a ContextualInquiry")
    if not isinstance(case_storage_consent, bool):
        raise TypeError("case_storage_consent must be a boolean")
    if case_storage_consent is not (encrypted_case_context is not None):
        raise PermissionError("case-storage consent and encrypted context envelope must be supplied together")

    canonical_context = None
    if encrypted_case_context is not None:
        canonical_context = _serialize_contextual_inquiry(inquiry)
        if unprotect_case_bytes(encrypted_case_context) != canonical_context:
            raise ValueError("encrypted case envelope does not match this contextual inquiry")

    contract = build_workflow_contract(inquiry, include_user_goal=include_user_goal)
    idempotency_digest = hashlib.sha256(inquiry.inquiry_id.encode("utf-8")).hexdigest()
    payload = {
        "kind": "contextual_inquiry_review",
        "workflow_contract": contract.to_dict(),
        "case_context_attached": False,
        "case_context_envelope_stored": encrypted_case_context is not None,
        "context_status": (
            "encrypted_pending_handler" if encrypted_case_context is not None
            else "awaiting_user_reentry"
        ),
        "requires_context_reentry": True,
        "long_term_storage_performed": False,
    }
    if encrypted_case_context is not None:
        payload["case_context_envelope"] = encrypted_case_context

    task = queue.enqueue(
        "Contextual inquiry review - context required",
        payload,
        priority=priority,
        workload_class="research",
        recovery_policy="manual",
        idempotency_key=f"contextual-inquiry-review:{idempotency_digest}",
        require_approval=True,
    )

    # TaskQueue's idempotency key is the case ID. Never silently reuse an old
    # Ticket when its stored consent/envelope differs from this submission.
    if task["payload"].get("workflow_contract") != contract.to_dict():
        raise ValueError("inquiry id already exists with a different workflow contract")
    stored_envelope = task["payload"].get("case_context_envelope")
    if (stored_envelope is not None) != (encrypted_case_context is not None):
        raise ValueError("inquiry id already exists with a different case-storage consent state")
    if stored_envelope is not None:
        if unprotect_case_bytes(stored_envelope) != canonical_context:
            raise ValueError("inquiry id already exists with different case content")
    return task




def load_encrypted_case_for_review(
    queue: TaskQueue, task_id: str, *, worker_id: str | None = None,
) -> ContextualInquiry:
    """Read one encrypted case for pre-approval review; never attaches or approves it.

    Before a review-only lease is claimed, the legacy read-only waiting_approval
    path remains available. During review execution, the caller must supply the
    exact live Worker lease owner and the Ticket must carry the separate human
    review-only authorization and matching audit event.
    """
    if not isinstance(queue, TaskQueue):
        raise TypeError("queue must be the existing Muse TaskQueue")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 128:
        raise ValueError("task_id must be non-empty text of at most 128 characters")
    if worker_id is not None and (
        not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 120
    ):
        raise ValueError("worker_id must be non-empty text of at most 120 characters")
    task = queue.get(task_id)
    if task is None:
        raise KeyError(task_id)
    payload = task["payload"]
    if payload.get("kind") != "contextual_inquiry_review":
        raise PermissionError("only contextual inquiry review Tickets can open case context")

    if task["status"] == "waiting_approval":
        if worker_id is not None:
            raise PermissionError("a Worker lease is not valid for a waiting_approval Ticket")
    elif task["status"] == "running":
        if worker_id is None or task.get("lease_owner") != worker_id:
            raise PermissionError("opening a running case requires the current Worker lease owner")
        try:
            expiry = datetime.fromisoformat(task["lease_expires_at"])
        except (TypeError, ValueError):
            raise PermissionError("running case has no valid Worker lease expiry") from None
        if expiry.tzinfo is None or expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise PermissionError("running case Worker lease has expired")
        authorization = payload.get("_review_authorization")
        if (
            not isinstance(authorization, dict)
            or authorization.get("scope") != "review_only"
            or not isinstance(authorization.get("authorized_by"), str)
            or not authorization["authorized_by"].strip()
        ):
            raise PermissionError("running case has no separate review-only authorization")
        authorization_event = next(
            (
                event for event in reversed(queue.events(task_id))
                if event["event_type"] == "contextual_inquiry.review_only_authorized"
            ),
            None,
        )
        if (
            authorization_event is None
            or authorization_event["actor"] != authorization["authorized_by"]
            or authorization_event["payload"] != {"scope": "review_only"}
        ):
            raise PermissionError("running case review-only authorization audit does not match")
    else:
        raise PermissionError("case context can be opened only for pending or leased review-only Tickets")

    if (
        payload.get("case_context_envelope_stored") is not True
        or payload.get("context_status") != "encrypted_pending_handler"
        or payload.get("case_context_attached") is not False
        or payload.get("requires_context_reentry") is not True
        or payload.get("long_term_storage_performed") is not False
    ):
        raise PermissionError("Ticket does not contain a valid pending encrypted case context")
    plaintext = unprotect_case_bytes(payload.get("case_context_envelope"))
    inquiry = deserialize_contextual_inquiry(plaintext)
    expected_key = (
        "contextual-inquiry-review:"
        + hashlib.sha256(inquiry.inquiry_id.encode("utf-8")).hexdigest()
    )
    if task.get("idempotency_key") != expected_key:
        raise ValueError("encrypted case context does not match the Ticket identity")
    if payload.get("workflow_contract") != build_workflow_contract(inquiry).to_dict():
        raise ValueError("encrypted case context does not match the Ticket workflow contract")
    if _serialize_contextual_inquiry(inquiry) != plaintext:
        raise ValueError("encrypted case context changed during validation")
    return inquiry


def _resolve_exact_active_source(
    store: ResearchKnowledgeStore, source_ref: str,
) -> str | None:
    """Resolve one exact, unique, active source node; ambiguity fails closed."""
    rows = store.connection.execute(
        "SELECT id,node_type,status FROM knowledge_nodes "
        "WHERE deleted_at IS NULL AND (id=? OR external_ref=?)",
        (source_ref, source_ref),
    ).fetchall()
    if len(rows) != 1:
        return None
    row = rows[0]
    if row["node_type"] != "source" or row["status"] != "active":
        return None
    return str(row["id"])


def persist_hypotheses(
    store: ResearchKnowledgeStore,
    inquiry: ContextualInquiry,
) -> tuple[str, ...]:
    """Persist proposed claims and only provenance-resolved evidence candidates.

    Observation IDs are resolved through the inquiry object, then source_ref must
    exactly identify one active source node in the existing graph. Supporting and
    counter references become unadjudicated claim-evidence candidates only;
    ResearchKnowledgeStore records a relates edge, never supports/refutes here.
    """
    if not isinstance(store, ResearchKnowledgeStore):
        raise TypeError("store must be the existing ResearchKnowledgeStore")
    if not isinstance(inquiry, ContextualInquiry):
        raise TypeError("inquiry must be a ContextualInquiry")
    inquiry.assert_persistable()

    observations = {item.observation_id: item for item in inquiry.observations}
    node_ids: list[str] = []
    for hypothesis in inquiry.hypotheses:
        node = store.upsert_node(
            "claim",
            hypothesis.statement,
            summary="Contextual hypothesis; unverified and not a factual conclusion.",
            external_ref=(
                f"contextual-inquiry:{inquiry.inquiry_id}:{hypothesis.hypothesis_id}"
            ),
            status="proposed",
            metadata={
                "origin": "muse.contextual_inquiry",
                "contextual_inquiry_id": inquiry.inquiry_id,
                "hypothesis_id": hypothesis.hypothesis_id,
                "confidence": float(hypothesis.confidence),
                "unsourced": hypothesis.unsourced,
                "falsification_condition": hypothesis.falsification_condition,
                "supporting_observation_refs": list(hypothesis.source_refs),
                "counter_observation_refs": list(hypothesis.counter_refs),
                "evidence_state": "unverified_context_hypothesis",
            },
        )
        node_ids.append(node["id"])

        for observation_id, direction in (
            *((ref, "support_search_candidate") for ref in hypothesis.source_refs),
            *((ref, "counter_search_candidate") for ref in hypothesis.counter_refs),
        ):
            observation = observations.get(observation_id)
            if observation is None:
                # The ContextualInquiry contract already rejects this; retain a
                # fail-closed guard at the persistence boundary as defense in depth.
                continue
            source_node_id = _resolve_exact_active_source(store, observation.source_ref)
            if source_node_id is None:
                continue
            store.record_claim_evidence(
                node["id"],
                source_node_id,
                direction=direction,
                evidence_type="contextual_observation_source",
                notes=(
                    f"Observation {observation_id} was explicitly associated by the "
                    "inquiry author; this is a candidate direction, not an evidence verdict."
                ),
            )
    return tuple(node_ids)
