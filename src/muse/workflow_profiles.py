"""Versioned workflow profiles and ticket-backed lifecycle for Muse Commander.

This module deliberately does not schedule work. It binds a validated Commander
DAG to existing TaskQueue tickets and records workflow state in task_events.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any, Mapping

from muse.artifact_evidence import ArtifactEvidenceError, LocalArtifactEvidenceVerifier
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
            stages=("plan", "plan_ready", "architect", "critic", "execute", "verify", "fix", "complete"),
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


@dataclass(frozen=True)
class WorkflowContract:
    """Versioned statement of the outcome a Commander workflow must achieve."""

    goal: str
    desired_outcome: str
    acceptance_criteria: tuple[str, ...]
    scope: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    source_precedence: tuple[str, ...] = ()
    escalation_conditions: tuple[str, ...] = ()
    feedback_metrics: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int)
                or self.schema_version != 1):
            raise ValueError(f"unsupported workflow contract schema_version: {self.schema_version}")
        for field_name, value, limit in (
            ("goal", self.goal, 1200),
            ("desired_outcome", self.desired_outcome, 1200),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{field_name} must be non-empty text of at most {limit} characters")
        for field_name, values, maximum in (
            ("acceptance_criteria", self.acceptance_criteria, 32),
            ("scope", self.scope, 8),
            ("exclusions", self.exclusions, 8),
            ("source_precedence", self.source_precedence, 8),
            ("escalation_conditions", self.escalation_conditions, 8),
            ("feedback_metrics", self.feedback_metrics, 8),
        ):
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} must be a tuple")
            if field_name == "acceptance_criteria" and not 1 <= len(values) <= maximum:
                raise ValueError("acceptance_criteria must contain 1-32 items")
            if len(values) > maximum or any(
                not isinstance(item, str) or not item.strip() or len(item) > 500 for item in values
            ):
                raise ValueError(f"{field_name} items must be non-empty text of at most 500 characters")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "goal": self.goal,
            "desired_outcome": self.desired_outcome,
            "acceptance_criteria": list(self.acceptance_criteria),
            "scope": list(self.scope),
            "exclusions": list(self.exclusions),
            "source_precedence": list(self.source_precedence),
            "escalation_conditions": list(self.escalation_conditions),
            "feedback_metrics": list(self.feedback_metrics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowContract":
        fields = {
            "schema_version", "goal", "desired_outcome", "acceptance_criteria",
            "scope", "exclusions", "source_precedence", "escalation_conditions",
            "feedback_metrics",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("workflow contract has an invalid shape")
        collections = fields - {"schema_version", "goal", "desired_outcome"}
        if any(not isinstance(value[name], list) for name in collections):
            raise ValueError("workflow contract collections must be serialized as lists")
        return cls(
            schema_version=value["schema_version"],
            goal=value["goal"],
            desired_outcome=value["desired_outcome"],
            acceptance_criteria=tuple(value["acceptance_criteria"]),
            scope=tuple(value["scope"]),
            exclusions=tuple(value["exclusions"]),
            source_precedence=tuple(value["source_precedence"]),
            escalation_conditions=tuple(value["escalation_conditions"]),
            feedback_metrics=tuple(value["feedback_metrics"]),
        )

    @classmethod
    def from_plan(cls, plan: CommanderPlan) -> "WorkflowContract":
        """Create a conservative goal contract from an already validated Commander DAG."""
        if not isinstance(plan, CommanderPlan):
            raise TypeError("plan must be a validated CommanderPlan")
        criteria = tuple(f"{step.step_id}: {step.objective}"[:500] for step in plan.steps)
        return cls(
            goal=plan.goal,
            desired_outcome=(
                "All declared Commander steps are completed with evidence, and the "
                "independent Reviewer verifies the requested outcome."
            ),
            acceptance_criteria=criteria,
            scope=(plan.goal[:500],),
        )


class WorkflowController:
    """Ticket-backed lifecycle controls; TaskQueue remains the only durable store."""

    def __init__(self, queue: TaskQueue, *, artifact_root: str | Path | None = None) -> None:
        if not isinstance(queue, TaskQueue):
            raise TypeError("queue must be the existing TaskQueue")
        self.queue = queue
        self.artifact_verifier = (
            LocalArtifactEvidenceVerifier(artifact_root) if artifact_root is not None else None
        )

    def bind_plan(
        self, plan: CommanderPlan, ticket_ids: Mapping[str, str], *,
        profile: WorkflowProfile | None = None, actor: str = "commander",
        contract: WorkflowContract | None = None,
    ) -> str:
        if not isinstance(plan, CommanderPlan):
            raise TypeError("plan must be a validated CommanderPlan")
        profile = profile or WorkflowProfile.team()
        if not isinstance(profile, WorkflowProfile):
            raise TypeError("profile must be a WorkflowProfile")
        contract = contract or WorkflowContract.from_plan(plan)
        if not isinstance(contract, WorkflowContract):
            raise TypeError("contract must be a WorkflowContract")
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
                    "contract": contract.to_dict(),
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
        contract: WorkflowContract | None = None,
    ) -> str:
        """Bind the existing Commander DAG to its already-claimed parent Ticket."""
        if not isinstance(plan, CommanderPlan):
            raise TypeError("plan must be a validated CommanderPlan")
        profile = profile or WorkflowProfile.team()
        if not isinstance(profile, WorkflowProfile):
            raise TypeError("profile must be a WorkflowProfile")
        contract = contract or WorkflowContract.from_plan(plan)
        if not isinstance(contract, WorkflowContract):
            raise TypeError("contract must be a WorkflowContract")
        task = self._require_running_lease(ticket_id, worker_id)
        if self.queue.workflow_revision(ticket_id) != 0:
            raise ValueError("Ticket is already bound to a workflow")
        payload = {
            "workflow_id": plan.plan_id,
            "commander_plan_id": plan.plan_id,
            "profile": profile.to_dict(),
            "contract": contract.to_dict(),
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
            "contract": bound["payload"].get("contract"),
            "outcome_verification": terminal_payload.get("outcome_verification"),
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

    def record_artifact_evidence(
        self, ticket_id: str, *, worker_id: str, expected_revision: int,
        orchestration_run_id: str, relative_path: str, expected_sha256: str,
        expected_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Hash a local deliverable and persist its evidence in this Ticket."""
        if self.artifact_verifier is None:
            raise ValueError("local artifact evidence requires an explicitly configured artifact_root")
        task = self._require_running_lease(ticket_id, worker_id)
        del task
        bound = next(
            (event for event in self.queue.workflow_events(ticket_id)
             if event["event_type"] == "workflow.bound"),
            None,
        )
        if bound is None:
            raise ValueError("Ticket is not bound to a Commander workflow")
        if not isinstance(orchestration_run_id, str) or not orchestration_run_id.strip() or len(orchestration_run_id) > 128:
            raise ValueError("orchestration_run_id must be non-empty bounded text")
        plan_id = bound["payload"].get("commander_plan_id")
        finished = [
            event for event in self.queue.events(ticket_id, limit=500)
            if event["event_type"] == "commander.execution_finished"
            and event["actor"] == "commander"
            and event["payload"].get("plan_id") == plan_id
        ]
        latest = finished[-1] if finished else None
        if not latest or any(latest["payload"].get(key) != value for key, value in (
            ("orchestration_run_id", orchestration_run_id), ("status", "succeeded"),
            ("orchestration_status", "succeeded"), ("review_approved", True),
        )):
            raise ValueError("artifact evidence must belong to the latest successful Commander run with Reviewer approval")
        evidence = self.artifact_verifier.verify(
            relative_path, expected_sha256, expected_bytes=expected_bytes,
        )
        evidence["plan_id"] = plan_id
        evidence["orchestration_run_id"] = orchestration_run_id
        return self.queue.record_workflow_event(
            ticket_id, "workflow.artifact_verified", actor=worker_id,
            expected_revision=expected_revision, expected_worker_id=worker_id,
            payload=evidence,
        )

    def record_video_product_evidence(
        self, ticket_id: str, *, worker_id: str, expected_revision: int,
        orchestration_run_id: str, initiative_id: str, version_id: str,
    ) -> dict[str, Any]:
        """Verify a VideoProductBuilder package, then bind its manifest and files to this Run."""
        if self.artifact_verifier is None:
            raise ValueError("video product evidence requires an explicitly configured artifact_root")
        from muse.video_product_builder import VideoProductBuilder

        products_root = self.artifact_verifier.root / "data" / "products"
        verified = VideoProductBuilder.verify_path(products_root, initiative_id, version_id)
        validation = verified.get("validation_summary")
        if (verified.get("validated") is not True or not isinstance(validation, Mapping)
                or validation.get("validated") is not True):
            raise ValueError("VideoProductBuilder did not validate this product package")
        relative_dir = verified.get("relative_path")
        product_path = Path(relative_dir) if isinstance(relative_dir, str) else Path()
        if (not isinstance(relative_dir, str) or not relative_dir
                or product_path.is_absolute()
                or product_path.parts[:2] != ("data", "products")
                or product_path.parts[-2:] != (initiative_id, version_id)
                or any(part in {"", ".", ".."} for part in product_path.parts)):
            raise ValueError("VideoProductBuilder returned an unsafe product-relative path")

        manifest_relative = (product_path / "manifest.json").as_posix()
        manifest_digest = verified.get("manifest_sha256")
        manifest_evidence = self.artifact_verifier.verify(manifest_relative, manifest_digest)
        manifest_path = self.artifact_verifier.root / Path(*product_path.parts) / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("validated video product manifest could not be read") from exc
        files = manifest.get("files") if isinstance(manifest, dict) else None
        allowed_names = {
            "README.md", "storyboard.json", "video.mp4", "product.zip",
            "qa-report.json", "subtitles.srt", "subtitle-review.json",
        }
        if (manifest.get("product_kind") != "video_creator"
                or manifest.get("initiative_id") != initiative_id
                or manifest.get("version_id") != version_id
                or not isinstance(files, dict)
                or not {"README.md", "storyboard.json", "video.mp4", "product.zip"}.issubset(files)
                or not set(files).issubset(allowed_names)):
            raise ValueError("VideoProductBuilder manifest has an unexpected artifact inventory")

        artifact_specs = [(manifest_relative, manifest_digest, manifest_evidence["bytes"])]
        for name in sorted(files):
            digest = files[name]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("video product manifest contains an invalid file digest")
            relative = (product_path / name).as_posix()
            candidate = self.artifact_verifier.root / Path(*product_path.parts) / name
            try:
                expected_bytes = candidate.stat().st_size
            except OSError as exc:
                raise ValueError("a manifest-declared video artifact is unavailable") from exc
            artifact_specs.append((relative, digest, expected_bytes))

        records = []
        for index, (relative, digest, byte_count) in enumerate(artifact_specs):
            event = self.record_artifact_evidence(
                ticket_id, worker_id=worker_id, expected_revision=expected_revision + index,
                orchestration_run_id=orchestration_run_id,
                relative_path=relative, expected_sha256=digest, expected_bytes=byte_count,
            )
            records.append(event["payload"])
        return {
            "status": "verified",
            "initiative_id": initiative_id,
            "version_id": version_id,
            "product_status": verified.get("status"),
            "quality_gate": verified.get("quality_gate"),
            "validation_summary": dict(validation),
            "artifacts": records,
            "evidence_refs": [item["evidence_ref"] for item in records],
            "workflow_revision": expected_revision + len(records),
        }

    def complete(
        self, ticket_id: str, *, worker_id: str, expected_revision: int,
        evidence_refs: tuple[str, ...], outcome_verified: bool,
        criteria_evidence: Mapping[str, tuple[str, ...]],
    ) -> dict[str, Any]:
        if outcome_verified is not True:
            raise ValueError("workflow completion requires an explicit verified outcome")
        if (not isinstance(evidence_refs, tuple) or not 1 <= len(evidence_refs) <= 32
                or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 240 for ref in evidence_refs)):
            raise ValueError("evidence_refs must contain 1-32 bounded evidence references")
        if len(set(evidence_refs)) != len(evidence_refs):
            raise ValueError("evidence_refs must not contain duplicates")
        events = self.queue.workflow_events(ticket_id)
        bound = next((event for event in events if event["event_type"] == "workflow.bound"), None)
        if bound is None or "contract" not in bound["payload"]:
            raise ValueError("workflow completion requires a persisted outcome contract")
        contract = WorkflowContract.from_dict(bound["payload"]["contract"])
        if not isinstance(criteria_evidence, Mapping) or set(criteria_evidence) != set(contract.acceptance_criteria):
            raise ValueError("criteria_evidence must cover every acceptance criterion exactly once")

        bound_payload = bound["payload"]
        plan_id = bound_payload.get("commander_plan_id")
        declared_steps = bound_payload.get("steps")
        if not isinstance(plan_id, str) or not plan_id or not isinstance(declared_steps, list) or not declared_steps:
            raise ValueError("workflow completion requires a bound Commander plan and declared steps")
        step_ids = []
        for step in declared_steps:
            if not isinstance(step, Mapping) or not isinstance(step.get("step_id"), str) or not step["step_id"]:
                raise ValueError("bound Commander steps are invalid")
            step_ids.append(step["step_id"])
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("bound Commander step IDs must be unique")

        ticket_events = self.queue.events(ticket_id, limit=500)
        plan_approved = any(
            event["event_type"] == "commander.plan_consensus"
            and event["actor"] == "commander"
            and event["payload"].get("plan_id") == plan_id
            and event["payload"].get("approved") is True
            for event in ticket_events
        )
        if not plan_approved:
            raise ValueError("workflow completion requires an approved Commander plan consensus event")

        run_refs = [ref for ref in evidence_refs if ref.startswith("orchestration-run:")]
        if len(run_refs) != 1 or not run_refs[0].partition(":")[2]:
            raise ValueError("workflow completion requires exactly one orchestration run reference")
        run_id = run_refs[0].partition(":")[2]
        run_finish_events = [
            event for event in ticket_events
            if event["event_type"] == "commander.execution_finished"
            and event["payload"].get("plan_id") == plan_id
        ]
        latest_run_finished = run_finish_events[-1] if run_finish_events else None
        run_finished = bool(
            latest_run_finished
            and latest_run_finished["actor"] == "commander"
            and latest_run_finished["payload"].get("orchestration_run_id") == run_id
            and latest_run_finished["payload"].get("status") == "succeeded"
            and latest_run_finished["payload"].get("orchestration_status") == "succeeded"
            and latest_run_finished["payload"].get("review_approved") is True
        )
        if not run_finished:
            raise ValueError("workflow completion requires a successful run with independent Reviewer approval")

        submitted_steps = {
            event["payload"].get("step_id")
            for event in ticket_events
            if event["event_type"] == "report.step_submitted"
            and event["actor"] == "ruflo"
            and isinstance(event["payload"].get("step_id"), str)
            and isinstance(event["payload"].get("output_chars"), int)
            and not isinstance(event["payload"].get("output_chars"), bool)
            and event["payload"]["output_chars"] > 0
        }
        if not set(step_ids).issubset(submitted_steps):
            raise ValueError("workflow completion requires a non-empty submitted output for every Commander step")

        artifact_refs = [ref for ref in evidence_refs if ref.startswith("artifact:")]
        artifact_records: list[dict[str, Any]] = []
        if artifact_refs:
            if self.artifact_verifier is None:
                raise ValueError("artifact evidence cannot be accepted without an explicitly configured artifact_root")
            for artifact_ref in artifact_refs:
                matches = [
                    event for event in ticket_events
                    if event["event_type"] == "workflow.artifact_verified"
                    and event["actor"] == worker_id
                    and event["payload"].get("plan_id") == plan_id
                    and event["payload"].get("orchestration_run_id") == run_id
                    and event["payload"].get("evidence_ref") == artifact_ref
                ]
                if not matches:
                    raise ValueError("artifact evidence reference has no verified event in this Ticket")
                recorded = matches[-1]["payload"]
                try:
                    current = self.artifact_verifier.verify(
                        recorded.get("relative_path"), recorded.get("sha256"),
                        expected_bytes=recorded.get("bytes"),
                    )
                except ArtifactEvidenceError as exc:
                    raise ValueError("local artifact failed completion-time revalidation") from exc
                if any(current.get(key) != recorded.get(key) for key in (
                    "relative_path", "bytes", "sha256", "evidence_ref", "verifier",
                )):
                    raise ValueError("local artifact evidence changed after its verification event")
                artifact_records.append({
                    "relative_path": current["relative_path"],
                    "bytes": current["bytes"],
                    "sha256": current["sha256"],
                    "evidence_ref": current["evidence_ref"],
                    "verified_at": current["verified_at"],
                })

        required_refs = {run_refs[0], "step:independent_review"}
        required_refs.update(f"step:{step_id}" for step_id in step_ids)
        required_refs.update(artifact_refs)
        if set(evidence_refs) != required_refs:
            raise ValueError("evidence_refs must exactly identify the verified run, all steps and independent review")

        normalized_evidence: dict[str, list[str]] = {}
        for criterion in contract.acceptance_criteria:
            refs = criteria_evidence[criterion]
            if not isinstance(refs, tuple) or not 1 <= len(refs) <= 16 or any(
                not isinstance(ref, str) or not ref.strip() or len(ref) > 240 for ref in refs
            ):
                raise ValueError("each acceptance criterion requires 1-16 bounded evidence references")
            if not set(refs).issubset(required_refs):
                raise ValueError("criterion evidence must reference verified Commander evidence")
            criterion_prefix = criterion.partition(":")[0]
            relevant_step = criterion_prefix if criterion_prefix in step_ids else None
            if run_refs[0] not in refs or "step:independent_review" not in refs:
                raise ValueError("each acceptance criterion requires run and independent review evidence")
            if relevant_step is not None and f"step:{relevant_step}" not in refs:
                raise ValueError("criterion evidence must include its declared Commander step")
            if relevant_step is None and not any(f"step:{step_id}" in refs for step_id in step_ids):
                raise ValueError("criterion evidence must include at least one submitted Commander step")
            normalized_evidence[criterion] = list(refs)

        criterion_artifacts = {
            ref for refs in normalized_evidence.values() for ref in refs if ref.startswith("artifact:")
        }
        if criterion_artifacts != set(artifact_refs):
            raise ValueError("every artifact reference must be attached to at least one acceptance criterion")

        verification = {
            "verified": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "criteria_evidence": normalized_evidence,
            "evidence_validation": {
                "source": "commander_event_ledger",
                "plan_id": plan_id,
                "orchestration_run_id": run_id,
                "verified_steps": step_ids,
                "independent_review_approved": True,
            },
            "artifact_validation": {
                "status": "verified" if artifact_records else "not_required",
                "verified_artifacts": artifact_records,
            },
        }
        return self.queue.record_workflow_event(
            ticket_id, "workflow.completed", actor=worker_id,
            expected_revision=expected_revision, expected_worker_id=worker_id,
            payload={
                "evidence_refs": list(evidence_refs),
                "outcome_verification": verification,
                "next_action": None,
                "stop_reason": None,
            },
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
