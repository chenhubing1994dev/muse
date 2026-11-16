"""Versioned workflow profiles and ticket-backed lifecycle for Muse Commander.

This module deliberately does not schedule work. It binds a validated Commander
DAG to existing TaskQueue tickets and records workflow state in task_events.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

from muse.commander_planner import CommanderPlan
from muse.task_queue import TaskQueue

PROFILE_SCHEMA_VERSION = 1
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


@dataclass(frozen=True)
class VerificationPolicy:
    """Hard ceilings for a future verify/fix executor; not an executor itself."""
    max_attempts: int = 2
    max_runtime_seconds: int = 1800

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be an integer from 1 to 3")
        if isinstance(self.max_runtime_seconds, bool) or not isinstance(self.max_runtime_seconds, int) or not 1 <= self.max_runtime_seconds <= 3600:
            raise ValueError("max_runtime_seconds must be an integer from 1 to 3600")

    def to_dict(self) -> dict[str, int]:
        return {"max_attempts": self.max_attempts, "max_runtime_seconds": self.max_runtime_seconds}


@dataclass(frozen=True)
class WorkflowProfile:
    """Portable, versioned composition of Commander stages and bounded verification."""
    name: str
    stages: tuple[str, ...]
    verification: VerificationPolicy = VerificationPolicy()
    schema_version: int = PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int)
                or self.schema_version != PROFILE_SCHEMA_VERSION):
            raise ValueError(f"unsupported workflow profile schema_version: {self.schema_version}")
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", self.name):
            raise ValueError("profile name must be a lowercase identifier of 2-48 characters")
        if not isinstance(self.stages, tuple) or not 2 <= len(self.stages) <= 12:
            raise ValueError("stages must be a tuple containing 2-12 stages")
        if any(not isinstance(stage, str) or not _STAGE_RE.fullmatch(stage) for stage in self.stages):
            raise ValueError("each stage must be a lowercase identifier of at most 48 characters")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("workflow stages must be unique")
        if not isinstance(self.verification, VerificationPolicy):
            raise TypeError("verification must be a VerificationPolicy")

    @classmethod
    def team(cls, *, max_attempts: int = 2, max_runtime_seconds: int = 1800) -> "WorkflowProfile":
        return cls(
            name="team",
            stages=("plan", "architect", "critic", "execute", "verify", "fix", "complete"),
            verification=VerificationPolicy(max_attempts, max_runtime_seconds),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "stages": list(self.stages),
            "verification": self.verification.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowProfile":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "name", "stages", "verification"}:
            raise ValueError("workflow profile must contain exactly schema_version, name, stages and verification")
        verification = value["verification"]
        if not isinstance(verification, Mapping) or set(verification) != {"max_attempts", "max_runtime_seconds"}:
            raise ValueError("verification policy has an invalid shape")
        stages = value["stages"]
        if not isinstance(stages, list):
            raise ValueError("stages must be a list in serialized profiles")
        return cls(
            name=value["name"],
            stages=tuple(stages),
            verification=VerificationPolicy(
                max_attempts=verification["max_attempts"],
                max_runtime_seconds=verification["max_runtime_seconds"],
            ),
            schema_version=value["schema_version"],
        )


class WorkflowController:
    """Ticket-backed lifecycle controls; TaskQueue remains the only durable store."""

    def __init__(self, queue: TaskQueue) -> None:
        if not isinstance(queue, TaskQueue):
            raise TypeError("queue must be the existing TaskQueue")
        self.queue = queue

    def bind_plan(
        self, plan: CommanderPlan, ticket_ids: Mapping[str, str], *,
        profile: WorkflowProfile | None = None, actor: str = "commander",
    ) -> str:
        if not isinstance(plan, CommanderPlan):
            raise TypeError("plan must be a validated CommanderPlan")
        profile = profile or WorkflowProfile.team()
        if not isinstance(profile, WorkflowProfile):
            raise TypeError("profile must be a WorkflowProfile")
        if not isinstance(ticket_ids, Mapping) or set(ticket_ids) != {step.step_id for step in plan.steps}:
            raise ValueError("ticket_ids must map every Commander step exactly once")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 128
               for value in ticket_ids.values()):
            raise ValueError("each Commander step must map to a non-empty Ticket ID")
        if len(set(ticket_ids.values())) != len(ticket_ids):
            raise ValueError("each Commander step must map to a distinct Ticket")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 120:
            raise ValueError("actor must be non-empty text of at most 120 characters")
        items = []
        for step in plan.steps:
            ticket_id = ticket_ids[step.step_id]
            task = self.queue.get(ticket_id)
            if task is None:
                raise KeyError(ticket_id)
            if task["status"] != "queued":
                raise ValueError("workflow plans can only bind to queued Tickets")
            if self.queue.workflow_revision(ticket_id) != 0:
                raise ValueError("Ticket is already bound to a workflow")
            items.append({
                "task_id": ticket_id,
                "event_type": "workflow.bound",
                "payload": {
                    "workflow_id": plan.plan_id,
                    "profile": profile.to_dict(),
                    "commander_plan_id": plan.plan_id,
                    "step_id": step.step_id,
                    "depends_on_ticket_ids": [ticket_ids[item] for item in step.depends_on],
                },
            })
        self.queue.record_workflow_events(items, actor=actor)
        return plan.plan_id

    def bind_plan_to_ticket(
        self, plan: CommanderPlan, ticket_id: str, *, worker_id: str,
        profile: WorkflowProfile | None = None,
    ) -> str:
        """Bind the existing Commander DAG to its already-claimed parent Ticket."""
        if not isinstance(plan, CommanderPlan):
            raise TypeError("plan must be a validated CommanderPlan")
        profile = profile or WorkflowProfile.team()
        if not isinstance(profile, WorkflowProfile):
            raise TypeError("profile must be a WorkflowProfile")
        task = self._require_running_lease(ticket_id, worker_id)
        if self.queue.workflow_revision(ticket_id) != 0:
            raise ValueError("Ticket is already bound to a workflow")
        payload = {
            "workflow_id": plan.plan_id,
            "commander_plan_id": plan.plan_id,
            "profile": profile.to_dict(),
            "plan_source": plan.source,
            "step_ids": [step.step_id for step in plan.steps],
            "steps": [
                {"step_id": step.step_id, "role": step.role, "depends_on": list(step.depends_on)}
                for step in plan.steps
            ],
            "parallel_waves": [list(wave) for wave in plan.parallel_waves],
        }
        self.queue.record_workflow_event(
            ticket_id, "workflow.bound", actor="commander", payload=payload,
            expected_worker_id=worker_id,
        )
        return plan.plan_id

    def snapshot(self, ticket_id: str) -> dict[str, Any]:
        task = self._require_task(ticket_id)
        events = self.queue.workflow_events(ticket_id)
        bound = next((event for event in events if event["event_type"] == "workflow.bound"), None)
        if bound is None:
            raise ValueError("Ticket is not bound to a Commander workflow")
        status = self.queue.workflow_status(ticket_id)
        stage_event = next(
            (event for event in reversed(events) if event["event_type"] == "workflow.stage_changed"),
            None,
        )
        stage_payload = stage_event["payload"] if stage_event else {}
        terminal_payload = events[-1]["payload"]
        stage = "complete" if status == "completed" else stage_payload.get("stage", "plan")
        return {
            "ticket_id": ticket_id,
            "workflow_id": bound["payload"]["workflow_id"],
            "profile": bound["payload"]["profile"],
            "status": status,
            "revision": self.queue.workflow_revision(ticket_id),
            "stage": stage,
            "attempt": stage_payload.get("attempt", 0),
            "checkpoint": stage_payload.get("checkpoint"),
            "evidence_refs": terminal_payload.get("evidence_refs", stage_payload.get("evidence_refs", [])),
            "stop_reason": terminal_payload.get("stop_reason", stage_payload.get("stop_reason")),
            "next_action": terminal_payload.get("next_action", stage_payload.get("next_action")),
            "ticket_status": task["status"],
            "lease_owner": task["lease_owner"],
        }

    def pause(self, ticket_id: str, *, actor: str, expected_revision: int) -> dict[str, Any]:
        task = self._require_task(ticket_id)
        if task["status"] != "queued":
            raise ValueError("only queued workflow Tickets can be paused; running handlers are not forcibly interrupted")
        if self.queue.workflow_status(ticket_id) != "active":
            raise ValueError("only active workflow Tickets can be paused")
        return self.queue.record_workflow_event(
            ticket_id, "workflow.paused", actor=actor,
            payload={"stop_reason": "operator_pause", "next_action": "resume"},
            expected_revision=expected_revision,
        )

    def resume(self, ticket_id: str, *, actor: str, expected_revision: int) -> dict[str, Any]:
        task = self._require_task(ticket_id)
        if task["status"] != "queued" or self.queue.workflow_status(ticket_id) != "paused":
            raise ValueError("only paused queued workflow Tickets can be resumed")
        return self.queue.record_workflow_event(
            ticket_id, "workflow.resumed", actor=actor,
            payload={"next_action": "dispatch"},
            expected_revision=expected_revision,
        )

    def cancel(self, ticket_id: str, *, actor: str, reason: str = "", expected_revision: int) -> dict[str, Any]:
        task = self._require_task(ticket_id)
        if task["status"] != "queued":
            raise ValueError("workflow cancellation currently requires a queued Ticket; running handlers need cooperative cancellation")
        if self.queue.workflow_status(ticket_id) == "cancelled":
            raise ValueError("workflow Ticket is already cancelled")
        self.queue.cancel(
            ticket_id, cancelled_by=actor, reason=reason,
            workflow_expected_revision=expected_revision,
        )
        return self.queue.workflow_events(ticket_id)[-1]

    def record_stage(
        self, ticket_id: str, *, worker_id: str, stage: str, attempt: int,
        expected_revision: int, checkpoint: Mapping[str, Any] | None = None,
        evidence_refs: tuple[str, ...] = (), next_action: str | None = None,
        stop_reason: str | None = None, step_id: str | None = None,
    ) -> dict[str, Any]:
        task = self._require_task(ticket_id)
        self._assert_current_lease(task, worker_id)
        events = self.queue.workflow_events(ticket_id)
        bound = next((event for event in events if event["event_type"] == "workflow.bound"), None)
        if bound is None:
            raise ValueError("Ticket is not bound to a Commander workflow")
        profile = WorkflowProfile.from_dict(bound["payload"]["profile"])
        declared_steps = bound["payload"].get("step_ids", [])
        if step_id is not None and (not isinstance(step_id, str) or step_id not in declared_steps):
            raise ValueError("step_id is not part of the bound Commander plan")
        if stage not in profile.stages:
            raise ValueError("stage is not declared by the bound workflow profile")
        if (isinstance(attempt, bool) or not isinstance(attempt, int)
                or not 1 <= attempt <= profile.verification.max_attempts):
            raise ValueError("attempt exceeds the workflow verification policy")
        started_at = datetime.fromisoformat(bound["created_at"])
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - started_at.astimezone(timezone.utc)).total_seconds()
        if elapsed > profile.verification.max_runtime_seconds:
            raise TimeoutError("workflow exceeded its declared maximum runtime")
        if not isinstance(evidence_refs, tuple) or len(evidence_refs) > 16 or any(
            not isinstance(ref, str) or not ref.strip() or len(ref) > 240 for ref in evidence_refs
        ):
            raise ValueError("evidence_refs must be a tuple of at most 16 bounded references")
        if checkpoint is not None:
            if not isinstance(checkpoint, Mapping):
                raise TypeError("checkpoint must be a mapping")
            encoded = json.dumps(checkpoint, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > 4096:
                raise ValueError("checkpoint exceeds 4 KiB")
        if next_action is not None and (not isinstance(next_action, str) or len(next_action) > 160):
            raise ValueError("next_action must be at most 160 characters")
        if stop_reason is not None and (not isinstance(stop_reason, str) or len(stop_reason) > 240):
            raise ValueError("stop_reason must be at most 240 characters")
        return self.queue.record_workflow_event(
            ticket_id, "workflow.stage_changed", actor=worker_id,
            expected_revision=expected_revision, expected_worker_id=worker_id,
            payload={
                "stage": stage, "attempt": attempt,
                "checkpoint": dict(checkpoint) if checkpoint is not None else None,
                "evidence_refs": list(evidence_refs),
                "next_action": next_action, "stop_reason": stop_reason,
                "profile_version": profile.schema_version,
                "step_id": step_id,
            },
        )

    def complete(
        self, ticket_id: str, *, worker_id: str, expected_revision: int,
        evidence_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        return self.queue.record_workflow_event(
            ticket_id, "workflow.completed", actor=worker_id,
            expected_revision=expected_revision, expected_worker_id=worker_id,
            payload={"evidence_refs": list(evidence_refs), "next_action": None, "stop_reason": None},
        )

    def fail(
        self, ticket_id: str, *, worker_id: str, expected_revision: int,
        stop_reason: str, next_action: str = "manual_review",
    ) -> dict[str, Any]:
        if not isinstance(stop_reason, str) or not stop_reason.strip() or len(stop_reason) > 240:
            raise ValueError("stop_reason must be non-empty text of at most 240 characters")
        if not isinstance(next_action, str) or len(next_action) > 160:
            raise ValueError("next_action must be text of at most 160 characters")
        return self.queue.record_workflow_event(
            ticket_id, "workflow.failed", actor=worker_id,
            expected_revision=expected_revision, expected_worker_id=worker_id,
            payload={"stop_reason": stop_reason, "next_action": next_action},
        )

    def verify_and_fix(
        self, ticket_id: str, *, worker_id: str, artifact: Any,
        verify, fix, idempotent: bool,
    ):
        """Run the profile-bounded verification loop and persist each verify/fix stage."""
        from muse.verification import BoundedVerificationLoop, VerificationResult

        bound = next(
            (event for event in self.queue.workflow_events(ticket_id)
             if event["event_type"] == "workflow.bound"),
            None,
        )
        if bound is None:
            raise ValueError("Ticket is not bound to a Commander workflow")
        profile = WorkflowProfile.from_dict(bound["payload"]["profile"])
        loop = BoundedVerificationLoop(profile.verification)

        def persist_attempt(attempt: int, result: VerificationResult) -> None:
            can_fix = (
                not result.approved and result.retryable and idempotent
                and attempt < profile.verification.max_attempts
            )
            self.record_stage(
                ticket_id, worker_id=worker_id, stage="verify", attempt=attempt,
                expected_revision=self.queue.workflow_revision(ticket_id),
                evidence_refs=result.evidence_refs,
                next_action="fix" if can_fix else ("complete" if result.approved else "manual_review"),
                stop_reason=None if result.approved or can_fix else "verification_blocked",
            )
            if can_fix:
                self.record_stage(
                    ticket_id, worker_id=worker_id, stage="fix", attempt=attempt,
                    expected_revision=self.queue.workflow_revision(ticket_id),
                    evidence_refs=result.evidence_refs, next_action="reverify",
                )

        return loop.run(
            artifact, verify=verify, fix=fix, idempotent=idempotent,
            on_attempt=persist_attempt,
        )

    def _require_running_lease(self, ticket_id: str, worker_id: str) -> dict[str, Any]:
        task = self.queue.get(ticket_id)
        if task is None:
            raise KeyError(ticket_id)
        self._assert_current_lease(task, worker_id)
        return task

    def _require_task(self, ticket_id: str) -> dict[str, Any]:
        task = self.queue.get(ticket_id)
        if task is None:
            raise KeyError(ticket_id)
        if not self.queue.workflow_events(ticket_id):
            raise ValueError("Ticket is not bound to a Commander workflow")
        return task

    @staticmethod
    def _assert_current_lease(task: dict[str, Any], worker_id: str) -> None:
        if task["status"] != "running" or task["lease_owner"] != worker_id:
            raise PermissionError("workflow update rejected by stale or foreign Worker lease")
        expiry = datetime.fromisoformat(task["lease_expires_at"])
        if expiry.tzinfo is None or expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise PermissionError("workflow update rejected because Worker lease has expired")
