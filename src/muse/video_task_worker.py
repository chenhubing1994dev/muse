"""Bounded executor for durable Creator Initiative video attempts.

The worker claims at most one task per invocation. Wan generation obtains its own
cross-process GPU lease in wan_video; this module never starts or stops services.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from muse.initiative_engine import InitiativeStore
from muse.task_queue import TaskQueue
from muse.video_commander_admission import VideoAdmissionBlocked, VideoAdmissionRequest
from muse.video_checkpoint_adapter import cleanup_video_attempt_staging_directory


def _checkpoint_scene_fingerprint_inputs(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bind per-scene negative conditioning into the legacy checkpoint input digest.

    Adapter v1 accepts only prompt/duration/seed. For scenes with an additional
    negative prompt, replace only the digest-side prompt with a domain-separated
    SHA-256 of both prompt fields; the original text still goes to the Builder.
    """
    fingerprint_inputs = []
    for scene in scenes:
        prompt = scene["prompt"]
        scene_negative_prompt = scene.get("negative_prompt", "")
        if scene_negative_prompt:
            payload = json.dumps(
                {
                    "schema": "muse-video-scene-conditioning-v1",
                    "prompt": prompt,
                    "negative_prompt": scene_negative_prompt,
                },
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            prompt = "muse-scene-conditioning-v1:" + hashlib.sha256(payload).hexdigest()
        fingerprint_inputs.append({
            "prompt": prompt,
            "duration_seconds": scene["duration_seconds"],
            "seed": scene["seed"],
        })
    return fingerprint_inputs


def _video_generation_input_sha256(**fields: Any) -> str:
    payload = {"schema": "muse-video-worker-generation-input-v1", **fields}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _workspace_artifact_path(workspace: str | Path, relative_path: str, *, maximum_bytes: int) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("video package artifact path is missing")
    relative = Path(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("video package artifact path must be workspace-relative")
    root = Path(workspace).resolve()
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("video package artifacts may not traverse symlinks")
    target = cursor.resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size > maximum_bytes:
        raise ValueError("video package artifact is missing, outside workspace or oversized")
    return target


def _artifact_json(workspace: str | Path, relative_path: str, *, maximum_bytes: int) -> dict[str, Any]:
    path = _workspace_artifact_path(workspace, relative_path, maximum_bytes=maximum_bytes)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("video package artifact root must be a JSON object")
    return value


def _json_artifact_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _load_video_brief_reference(workspace: str | Path, reference: Mapping[str, Any]) -> dict[str, Any]:
    relative_path = reference.get("brief_path")
    expected = reference.get("brief_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("video brief reference digest is invalid")
    wrapper = _artifact_json(workspace, relative_path, maximum_bytes=262_144)
    brief = wrapper.get("video_brief")
    digest = _json_artifact_digest(brief)
    if wrapper.get("schema_version") != 1 or wrapper.get("video_brief_sha256") != digest or digest != expected:
        raise ValueError("video brief artifact digest does not match its immutable reference")
    if not isinstance(brief, dict):
        raise ValueError("video brief artifact does not contain an object")
    return brief


def load_prebuilt_narration_script(
    workspace: str | Path, narration: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Load an approved narration script only when its workspace path and SHA-256 match."""
    relative_path = narration.get("script_path")
    expected = narration.get("script_sha256")
    if relative_path is None and expected is None:
        return None
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("prebuilt narration script digest is invalid")
    wrapper = _artifact_json(workspace, relative_path, maximum_bytes=131_072)
    script = wrapper.get("script")
    digest = _json_artifact_digest(script)
    if (
        wrapper.get("schema_version") != 1
        or wrapper.get("script_sha256") != digest
        or digest != expected
        or not isinstance(script, dict)
        or not isinstance(script.get("segments"), list)
    ):
        raise ValueError("prebuilt narration script is missing, malformed or has a SHA-256 mismatch")
    return script


def verify_video_package_artifacts(
    workspace: str | Path, video_brief: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Verify the exact script/storyboard/story-bible files approved with a video brief."""
    reference_fields = (
        "script_path", "script_sha256", "storyboard_path", "storyboard_sha256",
        "story_bible_path", "story_bible_sha256",
    )
    present = [video_brief.get(field) is not None for field in reference_fields]
    if not any(present):
        return None
    if not all(present):
        raise ValueError("video package contains an incomplete artifact reference set")
    script = load_prebuilt_narration_script(workspace, video_brief.get("narration", {}))
    storyboard_wrapper = _artifact_json(
        workspace, video_brief["storyboard_path"], maximum_bytes=1_048_576,
    )
    story_bible_wrapper = _artifact_json(
        workspace, video_brief["story_bible_path"], maximum_bytes=131_072,
    )
    storyboard = storyboard_wrapper.get("storyboard")
    story_bible = story_bible_wrapper.get("story_bible")
    if (
        storyboard_wrapper.get("schema_version") != 1
        or story_bible_wrapper.get("schema_version") != 1
        or _json_artifact_digest(storyboard) != video_brief["storyboard_sha256"]
        or storyboard_wrapper.get("storyboard_sha256") != video_brief["storyboard_sha256"]
        or _json_artifact_digest(story_bible) != video_brief["story_bible_sha256"]
        or story_bible_wrapper.get("story_bible_sha256") != video_brief["story_bible_sha256"]
    ):
        raise ValueError("storyboard or story-bible artifact SHA-256 validation failed")
    from muse.story_bible import validate_story_continuity
    from muse.video_workflow import validate_storyboard
    normalized_storyboard = validate_storyboard(storyboard)
    validate_story_continuity(story_bible, normalized_storyboard)
    brief_scenes = video_brief.get("scenes")
    storyboard_scenes = normalized_storyboard.get("scenes")
    if not isinstance(brief_scenes, list) or not isinstance(storyboard_scenes, list) or len(brief_scenes) != len(storyboard_scenes):
        raise ValueError("approved video brief scene count differs from its storyboard")
    for index, (brief_scene, storyboard_scene) in enumerate(zip(brief_scenes, storyboard_scenes), start=1):
        if (
            not isinstance(brief_scene, dict)
            or brief_scene.get("prompt") != storyboard_scene.get("prompt")
            or float(brief_scene.get("duration_seconds", -1)) != float(storyboard_scene.get("duration_seconds", -2))
        ):
            raise ValueError(f"approved video brief scene {index} differs from its storyboard")
    return script



def _runtime_is_paused(workspace: Path) -> bool:
    from muse.autonomous_runtime import RuntimeStateStore
    with RuntimeStateStore(workspace / "data" / "autonomy" / "runtime.sqlite3") as state:
        return state.status().get("state") in {"paused", "stopping"}

class _VideoAttemptLeaseHeartbeat:
    """Renew one exact Attempt lease from a separate SQLite connection."""

    def __init__(
        self, database: Path, attempt_id: str, worker_id: str, lease_epoch: int,
        *, lease_seconds: int, interval_seconds: float,
    ) -> None:
        if (isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float))
                or not 0.001 <= interval_seconds < lease_seconds):
            raise ValueError("lease renewal interval must be positive and shorter than the lease")
        self.database = database
        self.attempt_id = attempt_id
        self.worker_id = worker_id
        self.lease_epoch = lease_epoch
        self.lease_seconds = lease_seconds
        self.interval_seconds = float(interval_seconds)
        self._stop_event = threading.Event()
        self._lease_lost = threading.Event()
        self._error_type: str | None = None
        self._thread: threading.Thread | None = None

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    @property
    def error_type(self) -> str | None:
        return self._error_type

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("lease heartbeat has already been started")
        self._thread = threading.Thread(
            target=self._run, name=f"video-lease-{self.attempt_id[:8]}", daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                if self.database.is_symlink():
                    raise ValueError("video Attempt database must not be a symlink")
                with InitiativeStore(self.database) as store:
                    store.renew_video_attempt_lease(
                        self.attempt_id, worker_id=self.worker_id,
                        lease_epoch=self.lease_epoch, lease_seconds=self.lease_seconds,
                    )
            except Exception as exc:
                self._error_type = type(exc).__name__
                self._lease_lost.set()
                return

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.ident is not None and thread is not threading.current_thread():
            thread.join(timeout=max(5.0, self.interval_seconds * 2))
            if thread.is_alive():
                self._error_type = self._error_type or "HeartbeatStopTimeout"
                self._lease_lost.set()


class _TaskTicketLeaseHeartbeat:
    """Renew a Runtime-owned TaskQueue lease while the video worker is busy."""

    def __init__(self, database: Path, task_id: str, worker_id: str, *, lease_seconds: int = 60, interval_seconds: float = 15.0):
        if not 0.1 <= interval_seconds < lease_seconds:
            raise ValueError("TaskQueue heartbeat interval must be shorter than its lease")
        self.database, self.task_id, self.worker_id = database, task_id, worker_id
        self.lease_seconds, self.interval_seconds = lease_seconds, interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self.error_type: str | None = None
        self._thread: threading.Thread | None = None

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("TaskQueue heartbeat already started")
        self._thread = threading.Thread(target=self._run, name=f"ticket-lease-{self.task_id[:8]}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from muse.task_queue import TaskQueue
        while not self._stop.wait(self.interval_seconds):
            try:
                if self.database.is_symlink():
                    raise ValueError("TaskQueue database must not be a symlink")
                with TaskQueue(self.database) as queue:
                    renewed = queue.heartbeat(self.task_id, worker_id=self.worker_id, lease_seconds=self.lease_seconds)
                if renewed is None:
                    raise RuntimeError("TaskQueue execution lease was lost or expired")
            except Exception as exc:
                self.error_type = type(exc).__name__
                self._lost.set()
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.ident is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(5.0, self.interval_seconds * 2))
            if self._thread.is_alive():
                self.error_type = self.error_type or "HeartbeatStopTimeout"
                self._lost.set()


class VideoTaskWorker:
    def __init__(
        self, workspace: str | Path, *, builder=None, worker_id: str | None = None,
        pause_check: Callable[[], bool] | None = None,
        admission_provider: Any | None = None,
        admission_request_factory: Callable[..., VideoAdmissionRequest] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.worker_id = worker_id or ("video-" + uuid.uuid4().hex[:16])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.worker_id):
            raise ValueError("worker_id must be 1-64 safe identifier characters")
        self.builder = builder
        self.pause_check = pause_check or (lambda: False)
        if (admission_provider is None) != (admission_request_factory is None):
            raise ValueError("Commander admission provider and request factory must be injected together")
        if admission_provider is not None and not callable(getattr(admission_provider, "issue_video_admission_grant", None)):
            raise ValueError("admission_provider must issue persisted Commander video grants")
        if admission_request_factory is not None and not callable(admission_request_factory):
            raise ValueError("admission_request_factory must be callable")
        self.admission_provider = admission_provider
        self.admission_request_factory = admission_request_factory
        self.database = self.workspace / "data" / "initiatives" / "muse.sqlite3"
        self.task_database = self.workspace / "data" / "tasks" / "muse.sqlite3"
        self.lease_seconds = 3600
        self.lease_renew_interval_seconds = 60.0

    @staticmethod
    def _commander_approval_evidence(store, queue, attempt, workspace: str | Path | None = None) -> dict[str, str] | None:
        """Return the exact live human-decision evidence bound to this video attempt."""
        task_id = attempt.get("approval_task_id")
        initiative_id = attempt.get("initiative_id")
        attempt_id = attempt.get("attempt_id")
        if (not isinstance(task_id, str) or not task_id
                or not isinstance(initiative_id, str) or not isinstance(attempt_id, str) or not attempt_id):
            return None
        initiative = store.get_by_id(initiative_id)
        if initiative is None or initiative.get("status") in {"rejected", "parked"}:
            return None
        try:
            concept = json.loads(initiative["concept"])
        except (TypeError, json.JSONDecodeError, KeyError):
            return None
        creator_brief = concept.get("creator_brief") if isinstance(concept, dict) else None
        brief = creator_brief.get("video") if isinstance(creator_brief, dict) else None
        if not isinstance(brief, dict):
            return None
        if "brief_path" in brief or "brief_sha256" in brief:
            if workspace is None:
                return None
            try:
                brief = _load_video_brief_reference(workspace, brief)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                return None

        digest = hashlib.sha256(json.dumps(brief, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        ticket = queue.get(task_id)
        if ticket is None or ticket.get("status") != "queued":
            return None
        payload = ticket.get("payload", {})
        if not isinstance(payload, dict):
            return None
        approval = payload.get("_approval", {})
        approved_by = approval.get("approved_by") if isinstance(approval, dict) else None
        approval_kind = payload.get("kind")
        if approval_kind not in {"business_opportunity_video_approval", "creator_initiative_video_approval"}:
            return None
        if approval_kind == "creator_initiative_video_approval":
            topic_key = payload.get("topic_key")
            if not isinstance(topic_key, str) or not topic_key.strip():
                return None
            expected_attempt_id = "research-" + hashlib.sha256(
                f"{topic_key}:{initiative_id}:{attempt.get('recipe_sha256') or ''}:{digest}".encode("utf-8")
            ).hexdigest()[:32]
            if attempt_id != expected_attempt_id:
                return None
        if (payload.get("initiative_id") != initiative_id
                or payload.get("attempt_id") != attempt_id
                or payload.get("approval_scope") != "one_video_attempt_only"
                or payload.get("_approved") is not True
                or payload.get("video_brief_sha256") != digest
                or not isinstance(payload.get("video_brief"), dict)
                or hashlib.sha256(json.dumps(payload["video_brief"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != digest
                or not isinstance(approved_by, str) or not approved_by.strip()):
            return None
        decisions = [
            event for event in queue.events(task_id, limit=500)
            if event.get("event_type") == "human.decision"
        ]
        if not decisions:
            return None
        latest_decision = decisions[-1]
        latest_payload = latest_decision.get("payload")
        event_id = latest_decision.get("event_id")
        if (latest_decision.get("actor") != approved_by
                or not isinstance(latest_payload, dict)
                or latest_payload.get("decision") != "approved"
                or not TaskQueue.approval_timestamp_matches_event(approval.get("approved_at"), latest_decision.get("created_at"))
                or not isinstance(event_id, str) or not event_id):
            return None
        return {
            "approval_task_id": task_id,
            "approval_event_id": event_id,
            "approved_by": approved_by,
            "video_brief_sha256": digest,
        }

    @staticmethod
    def _commander_approval_is_valid(store, queue, attempt, workspace: str | Path | None = None) -> bool:
        """Compatibility predicate for a live approval bound to this exact attempt."""
        return VideoTaskWorker._commander_approval_evidence(store, queue, attempt, workspace) is not None

    def _consume_commander_admission_grant(
        self, store, attempt, video_brief, *, step_id: str, input_sha256: str,
        approval_evidence: Mapping[str, str], actual_handler: Callable[..., Any],
        lease_lost: Callable[[], bool], commander_plan: Any, commander_batches: tuple[Any, ...],
        admission_batch: Any, scene_context: Mapping[str, Any],
        lease_context: Any | None = None,
        commander_plan_id: str = "",
        commander_plan_source: str = "",
        workflow_ticket_id: str = "",
    ) -> dict[str, str]:
        """Issue and atomically consume one least-privilege grant for one exact Wan dispatch."""
        if self.admission_provider is None or self.admission_request_factory is None:
            raise VideoAdmissionBlocked("Commander admission provider/request factory is not injected")
        if not isinstance(input_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
            raise VideoAdmissionBlocked("Wan dispatch input fingerprint is unavailable")
        if lease_context is None:
            raise VideoAdmissionBlocked("Wan dispatch did not provide its active GPU lease context")
        if lease_context is not None:
            assert_valid = getattr(lease_context, "assert_valid", None)
            if not callable(assert_valid):
                raise VideoAdmissionBlocked("Wan admission requires a live GpuLeaseSession")
            try:
                assert_valid()
            except Exception as exc:
                raise VideoAdmissionBlocked("GPU lease is not valid before Commander grant creation") from exc
        try:
            request = self.admission_request_factory(
                attempt=attempt, video_brief=video_brief, step_id=step_id,
                input_sha256=input_sha256, approval_evidence=dict(approval_evidence),
                actual_handler=actual_handler, workspace_root=self.workspace,
                commander_plan=commander_plan, commander_batches=commander_batches,
                admission_batch=admission_batch, scene_context=dict(scene_context),
                lease_context=lease_context,
                commander_plan_id=commander_plan_id,
                commander_plan_source=commander_plan_source,
                workflow_ticket_id=workflow_ticket_id,
            )
        except VideoAdmissionBlocked:
            raise
        except Exception as exc:
            raise VideoAdmissionBlocked(f"Commander admission request factory failed closed ({type(exc).__name__})") from exc
        if not isinstance(request, VideoAdmissionRequest):
            raise VideoAdmissionBlocked("Commander admission factory did not return VideoAdmissionRequest")
        resource_context = request.resource_context
        lease_binding = resource_context.get("gpu_lease") if isinstance(resource_context, Mapping) else None
        if lease_context is None:
            if isinstance(lease_binding, Mapping) and lease_binding.get("status") == "active":
                raise VideoAdmissionBlocked("admission factory cannot claim a GPU lease that Worker did not supply")
        else:
            expected_lease = {
                "status": "active",
                "lease_id": getattr(lease_context, "lease_id", None),
                "owner_id": getattr(lease_context, "owner_id", None),
                "coordination": dict(getattr(lease_context, "coordination", {}) or {}),
            }
            if not isinstance(lease_binding, Mapping) or dict(lease_binding) != expected_lease:
                raise VideoAdmissionBlocked("admission request GPU lease binding does not match the live Wan lease")
        try:
            supplied_batches = tuple(request.all_batches)
        except (TypeError, AttributeError):
            raise VideoAdmissionBlocked("Commander admission factory omitted the original batch sequence")
        if (request.plan is not commander_plan or request.batch is not admission_batch
                or len(supplied_batches) != len(commander_batches)
                or any(actual is not expected for actual, expected in zip(supplied_batches, commander_batches))):
            raise VideoAdmissionBlocked("Commander admission factory substituted the Worker-supplied DAG or batch identity")

        expected_approval = {
            "approval_task_id": approval_evidence.get("approval_task_id"),
            "approval_event_id": approval_evidence.get("approval_event_id"),
            "approved_by": approval_evidence.get("approved_by"),
        }
        expected_attempt = {
            "attempt_id": attempt.get("attempt_id"),
            "worker_id": attempt.get("worker_id"),
            "lease_epoch": int(attempt.get("attempt_count") or 0),
            "recipe_sha256": attempt.get("recipe_sha256"),
            "input_sha256": input_sha256,
            **expected_approval,
        }
        for field, expected in expected_attempt.items():
            if getattr(request, field, None) != expected:
                raise VideoAdmissionBlocked(f"admission request {field} does not match the live Worker context")
        if request.scope != "video_studio" or request.step_ids != (step_id,):
            raise VideoAdmissionBlocked("one-shot grant must be scoped to one video_studio step")
        request_approval = request.approval_evidence
        if (request_approval.get("approval_task_id") != expected_approval["approval_task_id"]
                or request_approval.get("approval_event_id") != expected_approval["approval_event_id"]
                or request_approval.get("approved_by") != expected_approval["approved_by"]):
            raise VideoAdmissionBlocked("request approval evidence does not match the latest TaskQueue decision")
        if request.actual_handler is not actual_handler or Path(request.workspace_root).resolve() != self.workspace:
            raise VideoAdmissionBlocked("request handler or workspace does not match the actual local executor")
        if not isinstance(request.expected_input_sha256, Mapping) or request.expected_input_sha256.get(step_id) != input_sha256:
            raise VideoAdmissionBlocked("request DAG does not bind this step to the exact generation input")
        if not re.fullmatch(r"[0-9a-f]{64}", str(request.recipe_sha256 or "")):
            raise VideoAdmissionBlocked("current Attempt has no valid recipe fingerprint")
        if not re.fullmatch(r"[0-9a-f]{64}", str(request.dag_sha256 or "")) or not re.fullmatch(
            r"[0-9a-f]{64}", str(request.batch_fingerprint or "")
        ):
            raise VideoAdmissionBlocked("request DAG/batch fingerprint is invalid")
        if not isinstance(request.required_capabilities, tuple) or (
            "comfyui_generation" not in request.required_capabilities
            or len(request.required_capabilities) != len(set(request.required_capabilities))
        ):
            raise VideoAdmissionBlocked("one-shot Wan grant must name unique comfyui_generation capability")
        plan_steps = tuple(
            step for step in getattr(request.plan, "steps", ())
            if getattr(step, "step_id", None) == step_id
        )
        if (len(plan_steps) != 1
                or getattr(plan_steps[0], "role", None) != request.role
                or tuple(getattr(plan_steps[0], "required_capabilities", ())) != request.required_capabilities):
            raise VideoAdmissionBlocked("request DAG must contain exactly one matching Commander step for this Wan dispatch")
        batch = request.batch
        if (getattr(batch, "batch_id", None) != request.batch_id
                or getattr(batch, "batch_fingerprint", None) != request.batch_fingerprint
                or getattr(batch, "dag_sha256", None) != request.dag_sha256
                or tuple(getattr(batch, "step_ids", ())) != (step_id,)
                or tuple(getattr(batch, "required_completed_steps", ()))):
            raise VideoAdmissionBlocked("request batch is not a dependency-free one-step dispatch batch")

        try:
            grant = self.admission_provider.issue_video_admission_grant(store, request)
        except VideoAdmissionBlocked:
            raise
        except Exception as exc:
            raise VideoAdmissionBlocked(f"Commander admission provider denied dispatch ({type(exc).__name__})") from exc
        if not isinstance(grant, Mapping) or grant.get("status") != "active" or not isinstance(grant.get("grant_id"), str):
            raise VideoAdmissionBlocked("Commander provider did not persist an active one-shot grant")
        grant_id = grant["grant_id"]

        def revoke(reason: str) -> None:
            try:
                store.revoke_video_admission_grant(grant_id, actor=self.worker_id, reason=reason)
            except (KeyError, ValueError, OSError):
                pass

        for field, expected in (
            ("attempt_id", request.attempt_id), ("worker_id", request.worker_id),
            ("lease_epoch", request.lease_epoch), ("input_sha256", request.input_sha256),
            ("dag_sha256", request.dag_sha256), ("batch_id", request.batch_id),
            ("batch_fingerprint", request.batch_fingerprint), ("approval_task_id", request.approval_task_id),
            ("approval_event_id", request.approval_event_id), ("approved_by", request.approved_by),
            ("scope", request.scope), ("role", request.role),
        ):
            if grant.get(field) != expected:
                revoke("worker_rejected_grant_binding")
                raise VideoAdmissionBlocked(f"persisted grant {field} differs from the requested dispatch")
        digests = {key: grant.get(key) for key in ("handler_digest", "resource_digest", "cost_digest")}
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in digests.values()):
            revoke("worker_rejected_grant_digest")
            raise VideoAdmissionBlocked("persisted grant lacks valid handler/resource/cost digests")
        if grant.get("required_capabilities") != list(request.required_capabilities):
            revoke("worker_rejected_grant_capabilities")
            raise VideoAdmissionBlocked("persisted grant capabilities differ from the one-step request")

        with TaskQueue(self.task_database) as live_approvals:
            latest_approval = self._commander_approval_evidence(store, live_approvals, attempt, self.workspace)
        if (lease_lost() or latest_approval is None
                or latest_approval.get("approval_task_id") != request.approval_task_id
                or latest_approval.get("approval_event_id") != request.approval_event_id
                or latest_approval.get("approved_by") != request.approved_by):
            revoke("approval_changed_before_consume" if not lease_lost() else "lease_lost_before_consume")
            raise VideoAdmissionBlocked("lease or human approval changed after grant issuance; grant revoked before dispatch")

        try:
            consumed = store.consume_video_admission_grant(
                grant_id, attempt_id=request.attempt_id, worker_id=request.worker_id,
                lease_epoch=request.lease_epoch, recipe_sha256=request.recipe_sha256,
                input_sha256=request.input_sha256, dag_sha256=request.dag_sha256,
                batch_id=request.batch_id, batch_fingerprint=request.batch_fingerprint,
                handler_digest=digests["handler_digest"], resource_digest=digests["resource_digest"],
                cost_digest=digests["cost_digest"], approval_task_id=request.approval_task_id,
                approval_event_id=request.approval_event_id, approved_by=request.approved_by,
                scope=request.scope, role=request.role, required_capabilities=request.required_capabilities,
            )
        except Exception as exc:
            revoke("grant_consume_failed")
            raise VideoAdmissionBlocked(f"InitiativeStore refused one-shot grant consumption ({type(exc).__name__})") from exc
        if not isinstance(consumed, Mapping) or consumed.get("status") != "consumed":
            raise VideoAdmissionBlocked("InitiativeStore did not confirm one-shot grant consumption")
        return {"grant_id": grant_id, "step_id": step_id, "status": "consumed"}
    def _builder(self):
        if self.builder is None:
            from muse.video_product_builder import VideoProductBuilder
            self.builder = VideoProductBuilder(self.workspace)
        return self.builder

    def run_once(self) -> dict[str, Any]:
        """Deprecated entrypoint: production video dispatch must be Runtime-owned."""
        return {
            "status": "blocked_entrypoint", "claimed": False,
            "reason": "use MuseRuntime.dispatch_video_pipeline_once and run_approved_ticket",
        }

    def run_approved_ticket(
        self, *, execution_task_id: str, commander_plan: Any, dag_sha256: str,
        approval_task_id: str, approval_event_id: str, approved_by: str, worker_id: str,
    ) -> dict[str, Any]:
        """Execute one exact Runtime-claimed Ticket and return independently signed completion evidence."""
        from muse.commander_planner import CommanderPlan
        from muse.artifact_evidence import LocalArtifactEvidenceVerifier
        from muse.unified_runtime import MuseRuntime
        from muse.task_queue import TaskQueue

        if (not isinstance(execution_task_id, str) or not execution_task_id.strip()
                or not isinstance(commander_plan, CommanderPlan)
                or not isinstance(dag_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", dag_sha256) is None
                or not all(isinstance(value, str) and value.strip() for value in
                           (approval_task_id, approval_event_id, approved_by, worker_id))
                or worker_id != self.worker_id):
            raise VideoAdmissionBlocked("run_approved_ticket requires the exact validated Runtime dispatch identity")
        if self.pause_check():
            return {"status": "paused", "claimed": False}
        if self.admission_provider is None or self.admission_request_factory is None:
            raise VideoAdmissionBlocked("Commander one-shot admission provider is not configured")

        with TaskQueue(self.task_database) as queue:
            ticket = queue.get(execution_task_id)
            if (not isinstance(ticket, dict) or ticket.get("status") != "running"
                    or ticket.get("lease_owner") != worker_id):
                raise VideoAdmissionBlocked("execution Ticket is not held by this exact live Worker lease")
            try:
                expires = datetime.fromisoformat(str(ticket.get("lease_expires_at", "")).replace("Z", "+00:00"))
            except ValueError as exc:
                raise VideoAdmissionBlocked("execution Ticket lease expiry is malformed") from exc
            if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                raise VideoAdmissionBlocked("execution Ticket lease has expired")
            payload = ticket.get("payload")
            if not isinstance(payload, dict) or payload.get("kind") != "video_pipeline":
                raise VideoAdmissionBlocked("run_approved_ticket accepts video_pipeline Tickets only")
            context = MuseRuntime._validate_video_pipeline_execution_ticket(queue, {**ticket, "status": "queued"})
            plan = context["commander_plan"]
            if (plan.source != "model" or plan.to_snapshot() != commander_plan.to_snapshot()
                    or payload.get("commander_plan_id") != plan.plan_id or payload.get("dag_sha256") != dag_sha256
                    or payload.get("approval_task_id") != approval_task_id
                    or payload.get("approval_event_id") != approval_event_id
                    or payload.get("approved_by") != approved_by):
                raise VideoAdmissionBlocked("Commander DAG or approval identity differs from the persisted Ticket")
            workflow = [e for e in queue.workflow_events(execution_task_id) if e.get("event_type") == "workflow.bound"]
            commander = [e for e in queue.events(execution_task_id, limit=500)
                         if e.get("event_type") == "commander.video_pipeline_bound"]
            step_ids = [step.step_id for step in plan.steps]
            steps = [{"step_id": step.step_id, "role": step.role, "depends_on": list(step.depends_on)}
                     for step in plan.steps]
            if (len(workflow) != 1 or len(commander) != 1
                    or workflow[0].get("actor") != "commander"
                    or workflow[0].get("payload", {}).get("workflow_id") != plan.plan_id
                    or workflow[0].get("payload", {}).get("commander_plan_id") != plan.plan_id
                    or workflow[0].get("payload", {}).get("plan_source") != "model"
                    or workflow[0].get("payload", {}).get("step_ids") != step_ids
                    or workflow[0].get("payload", {}).get("steps") != steps
                    or workflow[0].get("payload", {}).get("parallel_waves") != [list(w) for w in plan.parallel_waves]
                    or commander[0].get("actor") != "commander"
                    or commander[0].get("payload") != {
                        "plan_id": plan.plan_id, "plan_source": "model", "step_ids": step_ids,
                        "approval_task_id": approval_task_id, "approval_event_id": approval_event_id,
                        "attempt_id": payload["attempt_id"], "initiative_id": payload["initiative_id"],
                    }):
                raise VideoAdmissionBlocked("execution Ticket lacks its exact durable Commander workflow binding")

        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt(payload["attempt_id"])
            if (not isinstance(attempt, dict) or attempt.get("status") != "queued"
                    or attempt.get("initiative_id") != payload.get("initiative_id")
                    or attempt.get("approval_task_id") != approval_task_id):
                raise VideoAdmissionBlocked("persisted video Attempt does not match the approved execution Ticket")

        ticket_heartbeat = _TaskTicketLeaseHeartbeat(self.task_database, execution_task_id, worker_id)
        attempt_heartbeat: _VideoAttemptLeaseHeartbeat | None = None
        pending: dict[str, Any] | None = None
        try:
            ticket_heartbeat.start()
            pending = self._run_approved_attempt(execution_task_id=execution_task_id, defer_completion=True)
            if pending.get("status") != "awaiting_independent_review":
                return pending
            attempt_id, lease_epoch = pending["attempt_id"], pending["lease_epoch"]
            attempt_heartbeat = _VideoAttemptLeaseHeartbeat(
                self.database, attempt_id, worker_id, lease_epoch,
                lease_seconds=self.lease_seconds, interval_seconds=self.lease_renew_interval_seconds,
            )
            attempt_heartbeat.start()
            product_dir = Path(pending["relative_path"])
            if product_dir.is_absolute() or any(part in {"", ".", ".."} for part in product_dir.parts):
                raise VideoAdmissionBlocked("Worker product directory is not workspace-relative")
            manifest_relative = (product_dir / "manifest.json").as_posix()
            manifest = LocalArtifactEvidenceVerifier(self.workspace).verify(
                manifest_relative, pending["manifest_sha256"],
            )
            artifact = {
                "execution_task_id": execution_task_id, "attempt_id": attempt_id,
                "initiative_id": pending["initiative_id"], "version_id": pending["version_id"],
                "commander_plan_id": plan.plan_id, "dag_sha256": dag_sha256,
                "approval_task_id": approval_task_id, "approval_event_id": approval_event_id,
                "approved_by": approved_by, "worker_id": worker_id,
                "manifest_sha256": pending["manifest_sha256"],
                "manifest_relative_path": manifest_relative, "manifest_bytes": manifest["bytes"],
                "mp4_sha256": pending["mp4_sha256"], "package_sha256": pending["package_sha256"],
            }
            with TaskQueue(self.task_database) as queue:
                existing = [e for e in queue.events(execution_task_id, limit=500)
                            if e.get("event_type") == "video_pipeline.artifacts_verified"]
                if existing:
                    if len(existing) != 1 or existing[0].get("actor") != worker_id or existing[0].get("payload") != artifact:
                        raise VideoAdmissionBlocked("conflicting artifact evidence already exists")
                else:
                    queue.record_event(execution_task_id, "video_pipeline.artifacts_verified", actor=worker_id, payload=artifact)

            wait_seconds = int(os.environ.get("MUSE_VIDEO_REVIEWER_WAIT_SECONDS", "300"))
            if not 0 <= wait_seconds <= 1800:
                raise VideoAdmissionBlocked("Reviewer wait timeout must be between 0 and 1800 seconds")
            deadline = time.monotonic() + wait_seconds
            review_event = None
            while True:
                if ticket_heartbeat.lease_lost or attempt_heartbeat.lease_lost:
                    raise VideoAdmissionBlocked("execution or Attempt lease was lost while awaiting review")
                with TaskQueue(self.task_database) as queue:
                    current = queue.get(execution_task_id)
                    if not isinstance(current, dict) or current.get("status") != "running" or current.get("lease_owner") != worker_id:
                        raise VideoAdmissionBlocked("execution Ticket lease changed while awaiting Reviewer")
                    decisions = [e for e in queue.events(approval_task_id, limit=500) if e.get("event_type") == "human.decision"]
                    if not decisions or decisions[-1].get("event_id") != approval_event_id or decisions[-1].get("actor") != approved_by or decisions[-1].get("payload", {}).get("decision") != "approved":
                        raise VideoAdmissionBlocked("approval was revoked or changed while awaiting Reviewer")
                    reviews = [e for e in queue.events(execution_task_id, limit=500)
                               if e.get("event_type") == "video_pipeline.review_approved"]
                    if len(reviews) > 1:
                        raise VideoAdmissionBlocked("multiple Reviewer approvals exist for one execution Ticket")
                    if reviews:
                        review_event = reviews[0]
                        break
                if time.monotonic() >= deadline:
                    raise VideoAdmissionBlocked("independent Reviewer signer did not approve the artifact before timeout")
                time.sleep(0.5)

            review = review_event.get("payload")
            reviewer_id = review.get("reviewer_id") if isinstance(review, dict) else None
            attestation = review.get("reviewer_attestation") if isinstance(review, dict) else None
            if (not isinstance(reviewer_id, str) or not reviewer_id.strip() or reviewer_id == worker_id
                    or review_event.get("actor") != reviewer_id or not isinstance(review, dict)
                    or review.get("decision") != "approved"
                    or any(review.get(key) != value for key, value in artifact.items())
                    or not isinstance(attestation, dict) or attestation.get("reviewer_id") != reviewer_id
                    or attestation.get("review_record_sha256") != review.get("review_record_sha256")):
                raise VideoAdmissionBlocked("Reviewer event is not bound to this exact artifact")
            expected_binding = {
                "execution_task_id": execution_task_id, "attempt_id": attempt_id,
                "initiative_id": pending["initiative_id"], "commander_plan_id": plan.plan_id,
                "dag_sha256": dag_sha256, "approval_task_id": approval_task_id,
                "approval_event_id": approval_event_id, "approved_by": approved_by,
                "worker_id": worker_id, "manifest_sha256": pending["manifest_sha256"],
                "reviewer_id": reviewer_id, "decision": "approved",
                "manifest_relative_path": manifest_relative, "manifest_bytes": manifest["bytes"],
            }
            if attestation.get("binding") != expected_binding:
                raise VideoAdmissionBlocked("Reviewer signature binding differs from the approved artifact")
            try:
                from muse.video_reviewer_attestation import (
                    load_video_reviewer_public_keys_from_environment,
                    verify_video_reviewer_attestation_signature,
                )
                verify_video_reviewer_attestation_signature(
                    attestation, load_video_reviewer_public_keys_from_environment(), expected_binding,
                )
            except (OSError, TypeError, ValueError, RuntimeError) as exc:
                raise VideoAdmissionBlocked("independent Reviewer signature could not be verified before Attempt completion") from exc
            if ticket_heartbeat.lease_lost or attempt_heartbeat.lease_lost:
                raise VideoAdmissionBlocked("execution or Attempt lease was lost before completion")
            with InitiativeStore(self.database) as store, TaskQueue(self.task_database) as queue:
                current = queue.get(execution_task_id)
                current_attempt = store.get_video_attempt(attempt_id)
                decisions = [e for e in queue.events(approval_task_id, limit=500) if e.get("event_type") == "human.decision"]
                if (not isinstance(current, dict) or current.get("status") != "running" or current.get("lease_owner") != worker_id
                        or not decisions or decisions[-1].get("event_id") != approval_event_id
                        or decisions[-1].get("actor") != approved_by or decisions[-1].get("payload", {}).get("decision") != "approved"):
                    raise VideoAdmissionBlocked("approval or execution lease changed before Runtime validation")
                if (not isinstance(current_attempt, dict) or current_attempt.get("status") != "running"
                        or current_attempt.get("worker_id") != worker_id
                        or int(current_attempt.get("attempt_count") or 0) != lease_epoch):
                    raise VideoAdmissionBlocked("video Attempt lease changed before Runtime validation")
            completion_evidence = {
                "execution_task_id": execution_task_id, "attempt_id": attempt_id,
                "initiative_id": pending["initiative_id"], "commander_plan_id": plan.plan_id,
                "dag_sha256": dag_sha256, "approval_task_id": approval_task_id,
                "approval_event_id": approval_event_id, "approved_by": approved_by,
                "worker_id": worker_id, "manifest_sha256": pending["manifest_sha256"],
                "reviewer_id": reviewer_id, "review_event_id": review_event["event_id"],
                "reviewer_attestation": attestation,
            }
            attempt_commit = {
                "attempt_id": attempt_id, "lease_epoch": lease_epoch,
                "initiative_id": pending["initiative_id"], "version_id": pending["version_id"],
                "mp4_sha256": pending["mp4_sha256"], "package_sha256": pending["package_sha256"],
                "manifest_sha256": pending["manifest_sha256"],
            }
            return {
                "status": "succeeded", "attempt_id": attempt_id, "product_id": pending["initiative_id"],
                "version_id": pending["version_id"], "attempt_commit": attempt_commit,
                "quality_gate": pending.get("quality_gate", {"status": "not_reported"}),
                "content_review_required": False, "auto_publish": False,
                "narration_status": pending.get("narration_status", "not_requested"),
                "admission_grants_consumed": pending.get("admission_grants_consumed", []),
                "completion_evidence": completion_evidence,
            }
        except Exception:
            if pending is not None and attempt_heartbeat is not None and not attempt_heartbeat.lease_lost:
                try:
                    with InitiativeStore(self.database) as store:
                        store.release_video_attempt(attempt_id, worker_id=worker_id, lease_epoch=lease_epoch,
                                                    reason="independent_reviewer_not_completed")
                except (ValueError, KeyError):
                    pass
            raise
        finally:
            if attempt_heartbeat is not None:
                attempt_heartbeat.stop()
            ticket_heartbeat.stop()

    def finalize_approved_ticket(
        self, *, execution_task_id: str, worker_id: str,
        attempt_commit: dict[str, Any], completion_evidence: dict[str, Any],
        completion: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit the video Attempt only after Runtime verified and consumed the Reviewer nonce."""
        if worker_id != self.worker_id or not isinstance(attempt_commit, dict) or set(attempt_commit) != {
            "attempt_id", "lease_epoch", "initiative_id", "version_id",
            "mp4_sha256", "package_sha256", "manifest_sha256",
        }:
            raise VideoAdmissionBlocked("Runtime finalization requires the exact Worker lease and commit receipt")
        if (not isinstance(completion_evidence, dict)
                or completion_evidence.get("execution_task_id") != execution_task_id
                or completion_evidence.get("worker_id") != worker_id
                or completion_evidence.get("attempt_id") != attempt_commit.get("attempt_id")
                or completion_evidence.get("initiative_id") != attempt_commit.get("initiative_id")
                or completion_evidence.get("manifest_sha256") != attempt_commit.get("manifest_sha256")
                or not isinstance(completion, dict)
                or completion.get("attempt_id") != attempt_commit.get("attempt_id")
                or completion.get("review_event_id") != completion_evidence.get("review_event_id")
                or completion.get("reviewer_id") != completion_evidence.get("reviewer_id")
                or completion.get("reviewer_attestation_sha256") is None):
            raise VideoAdmissionBlocked("Runtime-verified completion receipt does not match the pending Attempt")
        attestation = completion_evidence.get("reviewer_attestation")
        if not isinstance(attestation, dict) or not isinstance(attestation.get("nonce"), str):
            raise VideoAdmissionBlocked("Runtime finalization is missing the signed Reviewer nonce")
        attestation_sha256 = hashlib.sha256(json.dumps(
            attestation, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        if attestation_sha256 != completion.get("reviewer_attestation_sha256"):
            raise VideoAdmissionBlocked("Runtime finalization Reviewer attestation digest mismatch")
        nonce_sha256 = hashlib.sha256(attestation["nonce"].encode("utf-8")).hexdigest()
        connection = sqlite3.connect(self.task_database, timeout=10)
        try:
            row = connection.execute(
                "SELECT task_id, attestation_sha256 FROM muse_video_reviewer_attestation_nonces WHERE nonce_sha256=?",
                (nonce_sha256,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise VideoAdmissionBlocked("Runtime has not consumed the Reviewer nonce") from exc
        finally:
            connection.close()
        if row is None or row[0] != execution_task_id or row[1] != attestation_sha256:
            raise VideoAdmissionBlocked("Reviewer nonce was not consumed for this exact execution Ticket")

        with TaskQueue(self.task_database) as queue:
            ticket = queue.get(execution_task_id)
            if not isinstance(ticket, dict) or ticket.get("status") != "running" or ticket.get("lease_owner") != worker_id:
                raise VideoAdmissionBlocked("execution Ticket lease changed before final Attempt commit")
            try:
                ticket_expiry = datetime.fromisoformat(str(ticket.get("lease_expires_at", "")).replace("Z", "+00:00"))
            except ValueError as exc:
                raise VideoAdmissionBlocked("execution Ticket lease expiry is malformed at final commit") from exc
            if ticket_expiry.tzinfo is None or ticket_expiry <= datetime.now(timezone.utc):
                raise VideoAdmissionBlocked("execution Ticket lease expired before final Attempt commit")
            ticket_payload = ticket.get("payload")
            if (not isinstance(ticket_payload, dict)
                    or ticket_payload.get("attempt_id") != attempt_commit["attempt_id"]
                    or ticket_payload.get("initiative_id") != attempt_commit["initiative_id"]
                    or ticket_payload.get("approval_task_id") != completion_evidence.get("approval_task_id")
                    or ticket_payload.get("approval_event_id") != completion_evidence.get("approval_event_id")
                    or ticket_payload.get("approved_by") != completion_evidence.get("approved_by")):
                raise VideoAdmissionBlocked("final Attempt commit does not match the persisted execution Ticket")
            approval_ticket = queue.get(ticket_payload["approval_task_id"])
            decisions = [e for e in queue.events(ticket_payload["approval_task_id"], limit=500)
                         if e.get("event_type") == "human.decision"]
            if (approval_ticket is None or approval_ticket.get("status") != "queued"
                    or not decisions or decisions[-1].get("event_id") != ticket_payload.get("approval_event_id")
                    or decisions[-1].get("actor") != ticket_payload.get("approved_by")
                    or decisions[-1].get("payload", {}).get("decision") != "approved"):
                raise VideoAdmissionBlocked("human approval changed after Runtime signature verification")
            review_events = [e for e in queue.events(execution_task_id, limit=500)
                             if e.get("event_type") == "video_pipeline.review_approved"]
            if (len(review_events) != 1 or review_events[0].get("event_id") != completion_evidence.get("review_event_id")
                    or review_events[0].get("payload", {}).get("reviewer_attestation") != attestation
                    or review_events[0].get("payload", {}).get("review_record_sha256") != attestation.get("review_record_sha256")):
                raise VideoAdmissionBlocked("persisted Reviewer event changed before final Attempt commit")
            artifact_events = [e for e in queue.events(execution_task_id, limit=500)
                               if e.get("event_type") == "video_pipeline.artifacts_verified"]
            if (len(artifact_events) != 1
                    or artifact_events[0].get("event_id") != completion.get("artifact_event_id")
                    or artifact_events[0].get("payload", {}).get("execution_task_id") != execution_task_id
                    or artifact_events[0].get("payload", {}).get("attempt_id") != attempt_commit["attempt_id"]
                    or artifact_events[0].get("payload", {}).get("initiative_id") != attempt_commit["initiative_id"]
                    or artifact_events[0].get("payload", {}).get("version_id") != attempt_commit["version_id"]
                    or artifact_events[0].get("payload", {}).get("manifest_sha256") != attempt_commit["manifest_sha256"]
                    or artifact_events[0].get("payload", {}).get("mp4_sha256") != attempt_commit["mp4_sha256"]
                    or artifact_events[0].get("payload", {}).get("package_sha256") != attempt_commit["package_sha256"]):
                raise VideoAdmissionBlocked("exact artifact evidence is unavailable or changed at final Attempt commit")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt(attempt_commit["attempt_id"])
            if (not isinstance(attempt, dict) or attempt.get("status") != "running"
                    or attempt.get("worker_id") != worker_id
                    or int(attempt.get("attempt_count") or 0) != attempt_commit["lease_epoch"]):
                raise VideoAdmissionBlocked("InitiativeStore Attempt lease changed before final commit")
            completed = store.complete_video_attempt(
                attempt_commit["attempt_id"], product_id=attempt_commit["initiative_id"],
                version_id=attempt_commit["version_id"], mp4_sha256=attempt_commit["mp4_sha256"],
                package_sha256=attempt_commit["package_sha256"], manifest_sha256=attempt_commit["manifest_sha256"],
                worker_id=worker_id, lease_epoch=attempt_commit["lease_epoch"],
            )
            if completed.get("status") != "succeeded":
                raise VideoAdmissionBlocked("InitiativeStore rejected the Runtime-verified Attempt commit")
            try:
                cleanup_video_attempt_staging_directory(self.workspace, attempt_commit["attempt_id"], store)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Could not clean committed video Attempt staging %s",
                    attempt_commit["attempt_id"], exc_info=True,
                )
        return {"status": "succeeded", "attempt_id": completed["attempt_id"],
                "product_id": completed["product_id"], "version_id": completed["version_id"]}

    def abort_approved_ticket(
        self, *, attempt_commit: dict[str, Any], worker_id: str, reason: str = "runtime_validation_failed",
    ) -> dict[str, Any]:
        """Release, never complete, an Attempt rejected by the Runtime's independent verification."""
        if (worker_id != self.worker_id or not isinstance(attempt_commit, dict)
                or not isinstance(attempt_commit.get("attempt_id"), str)
                or isinstance(attempt_commit.get("lease_epoch"), bool)
                or not isinstance(attempt_commit.get("lease_epoch"), int)):
            raise VideoAdmissionBlocked("Attempt abort requires the exact Worker lease receipt")
        with InitiativeStore(self.database) as store:
            released = store.release_video_attempt(
                attempt_commit["attempt_id"], worker_id=worker_id,
                lease_epoch=attempt_commit["lease_epoch"], reason=reason,
            )
        return {"status": "released" if released else "not_released", "attempt_id": attempt_commit["attempt_id"]}

    def _run_approved_attempt(
        self, *, execution_task_id: str | None = None, defer_completion: bool = False,
    ) -> dict[str, Any]:
        """Internal bounded generator; production dispatch enters through run_approved_ticket."""
        if self.pause_check():
            return {"status": "paused", "claimed": False}
        if self.admission_provider is None or self.admission_request_factory is None:
            return {
                "status": "blocked_admission", "claimed": False,
                "reason": "commander_admission_provider_missing",
            }
        with InitiativeStore(self.database) as store:
            candidates = store.list_queued_approval_video_attempts(limit=200)
            if execution_task_id is not None:
                with TaskQueue(self.task_database) as execution_queue:
                    execution_ticket = execution_queue.get(execution_task_id)
                if (not isinstance(execution_ticket, dict) or execution_ticket.get("status") != "running"
                        or execution_ticket.get("lease_owner") != self.worker_id
                        or not isinstance(execution_ticket.get("payload"), dict)):
                    raise VideoAdmissionBlocked("exact execution Ticket is not held by this Worker")
                expected_attempt_id = execution_ticket["payload"].get("attempt_id")
                candidates = [item for item in candidates if item.get("attempt_id") == expected_attempt_id]
            if not candidates or self.task_database.is_symlink() or not self.task_database.is_file():
                return {"status": "idle", "claimed": False, "approval_required": True}

            from muse.task_queue import TaskQueue
            attempt = None
            blocked = 0
            with TaskQueue(self.task_database) as approvals:
                for candidate in candidates:
                    try:
                        valid = self._commander_approval_is_valid(store, approvals, candidate, self.workspace)
                    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError):
                        valid = False
                    if not valid:
                        blocked += 1
                        continue
                    attempt = store.claim_next_video_attempt(
                        self.worker_id, lease_seconds=self.lease_seconds, attempt_id=candidate["attempt_id"],
                    )
                    if attempt is not None:
                        break
            if attempt is None:
                return {"status": "idle", "claimed": False, "approval_required": True, "approval_blocked": blocked}
            attempt_id = attempt["attempt_id"]
            lease_worker_id = attempt["worker_id"]
            lease_epoch = int(attempt["attempt_count"])
            if self.pause_check():
                try:
                    store.release_video_attempt(
                        attempt_id, worker_id=lease_worker_id, lease_epoch=lease_epoch,
                        reason="runtime_paused",
                    )
                except ValueError:
                    return {"status": "lease_lost", "claimed": False, "attempt_id": attempt_id, "release_recorded": False}
                return {"status": "paused", "claimed": False, "attempt_id": attempt_id}
            heartbeat = _VideoAttemptLeaseHeartbeat(
                self.database, attempt_id, lease_worker_id, lease_epoch,
                lease_seconds=self.lease_seconds,
                interval_seconds=self.lease_renew_interval_seconds,
            )
            try:
                heartbeat.start()
                with TaskQueue(self.task_database) as approvals:
                    if not self._commander_approval_is_valid(store, approvals, attempt, self.workspace):
                        heartbeat.stop()
                        if heartbeat.lease_lost:
                            return {"status": "lease_lost", "attempt_id": attempt_id, "release_recorded": False}
                        try:
                            store.release_video_attempt(
                                attempt_id, worker_id=lease_worker_id, lease_epoch=lease_epoch,
                                reason="commander_approval_invalidated",
                            )
                        except ValueError:
                            return {"status": "lease_lost", "claimed": False, "attempt_id": attempt_id, "release_recorded": False}
                        return {"status": "blocked_approval", "claimed": False, "attempt_id": attempt_id}
                if attempt["mode"] != "prompt_to_video":

                    raise ValueError("video worker currently supports prompt_to_video attempts only")
                initiative = store.get_by_id(attempt["initiative_id"])
                if initiative is None:
                    raise KeyError("video attempt initiative is missing")
                concept = json.loads(initiative["concept"])
                brief = concept.get("creator_brief") if isinstance(concept, dict) else None
                video = brief.get("video") if isinstance(brief, dict) else None
                if not isinstance(video, dict):
                    raise ValueError("initiative has no valid creator_brief.video")
                if "brief_path" in video or "brief_sha256" in video:
                    video = _load_video_brief_reference(self.workspace, video)
                title = video.get("title")
                prompt = video.get("prompt")
                if not isinstance(title, str) or not title.strip() or not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("video brief requires a non-empty title and prompt")
                aspect_ratio = video.get("aspect_ratio", "9:16")
                duration = video.get("duration_seconds", 5.0)
                steps = video.get("steps", 20)
                scenes = video.get("scenes")
                if aspect_ratio not in {"16:9", "9:16", "1:1"}:
                    raise ValueError("unsupported video aspect ratio in creator brief")
                if scenes is None:
                    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 1 <= duration <= 5:
                        raise ValueError("creator brief duration must be between 1 and 5 seconds")
                elif not isinstance(scenes, list) or not 2 <= len(scenes) <= 20:
                    raise ValueError("creator brief scenes must contain between 2 and 20 scene specifications")
                if isinstance(steps, bool) or not isinstance(steps, int) or not 8 <= steps <= 30:
                    raise ValueError("creator brief steps must be between 8 and 30")
                negative_prompt = video.get("negative_prompt", "")
                if not isinstance(negative_prompt, str) or len(negative_prompt) > 1000:
                    raise ValueError("creator brief negative_prompt must be at most 1000 characters")
                normalized_scenes = None
                if scenes is not None:
                    normalized_scenes = []
                    total_scene_duration = 0.0
                    for index, scene in enumerate(scenes, start=1):
                        if not isinstance(scene, dict) or set(scene) - {"prompt", "duration_seconds", "seed", "negative_prompt"}:
                            raise ValueError(f"scene {index} contains unsupported fields")
                        scene_prompt = scene.get("prompt")
                        scene_duration = scene.get("duration_seconds")
                        scene_seed = scene.get("seed")
                        scene_negative_prompt = scene.get("negative_prompt", "")
                        if not isinstance(scene_negative_prompt, str) or len(scene_negative_prompt) > 1000:
                            raise ValueError(f"scene {index} negative_prompt must be at most 1000 characters")
                        combined_negative_prompt = "\n".join(
                            item for item in (negative_prompt, scene_negative_prompt) if item
                        )
                        if len(combined_negative_prompt) > 1000:
                            raise ValueError(f"scene {index} combined negative_prompt exceeds 1000 characters")
                        if not isinstance(scene_prompt, str) or not scene_prompt.strip() or len(scene_prompt.strip()) > 2000:
                            raise ValueError(f"scene {index} prompt must contain 1-2000 characters")
                        if (isinstance(scene_duration, bool) or not isinstance(scene_duration, (int, float))
                                or not 1 <= scene_duration <= 5):
                            raise ValueError(f"scene {index} duration must be between 1 and 5 seconds")
                        if scene_seed is None:
                            scene_seed = int(hashlib.sha256(
                                f"{attempt_id}:scene:{index}".encode("utf-8")
                            ).hexdigest()[:8], 16)
                        if isinstance(scene_seed, bool) or not isinstance(scene_seed, int) or not 0 <= scene_seed <= 2**32 - 1:
                            raise ValueError(f"scene {index} seed must be an unsigned 32-bit integer")
                        total_scene_duration += float(scene_duration)
                        normalized_scenes.append({
                            "prompt": scene_prompt.strip(),
                            "duration_seconds": float(scene_duration),
                            "seed": scene_seed,
                            "negative_prompt": scene_negative_prompt,
                        })
                    if total_scene_duration > 60:
                        raise ValueError("creator brief scene duration must not exceed 60 seconds")
                prebuilt_narration_script = verify_video_package_artifacts(self.workspace, video)
                narration_script = None
                narration_model_dir = None
                narration_asr_model_dir = None
                pending_evidence_contract = None
                narration_prosody_plan = None

                narration = video.get("narration")
                if isinstance(narration, dict) and narration.get("enabled") is True:
                    if narration.get("language", "zh-HK") != "zh-HK":
                        raise ValueError("video narration currently supports zh-HK only")
                    style = narration.get("style", "yujie_canto")
                    segments = narration.get("segments", 3)
                    if isinstance(segments, bool) or not isinstance(segments, int) or not 3 <= segments <= 16:
                        raise ValueError("Cantonese narration segments must be between 3 and 16")
                    tts_packages = self.workspace / ".muse-canto-tts-py314"
                    tts_model = self.workspace / "data" / "models" / "canto-tts-nano"
                    if (tts_packages.is_symlink() or not (tts_packages / "canto_tts").is_dir()
                            or (tts_packages / "canto_tts").is_symlink()
                            or tts_model.is_symlink() or not (tts_model / "browser_poc_manifest.json").is_file()):
                        raise RuntimeError("Cantonese narration was requested but its pinned local TTS runtime/model is unavailable; video generation was not started")
                    from muse.cantonese_tts import preflight_cantonese_tts_runtime
                    preflight_cantonese_tts_runtime(tts_packages, tts_model)
                    from muse.narration_generator import NarrationGenerator
                    research_text = narration.get("research_text") or prompt.strip()
                    inspiration = narration.get("inspiration") or "以清晰、克制、可信的香港粤语介绍，不把估算说成已验证成果"
                    claim_grounded = narration.get("claim_grounded") is True
                    generator_options = {}
                    if claim_grounded:
                        claim_mapping = narration.get("claim_ids_by_segment")
                        if not isinstance(claim_mapping, list):
                            raise ValueError("claim-grounded narration requires claim_ids_by_segment")
                        factual_mapping = narration.get("factual_by_segment", [True] * segments)
                        generator_options = {
                            "claim_ids_by_segment": claim_mapping,
                            "factual_by_segment": factual_mapping,
                        }
                    generated_script = prebuilt_narration_script
                    if generated_script is None:
                        generated_script = NarrationGenerator().generate(
                            title.strip(), research_text, inspiration, style, segments=segments,
                            **generator_options,
                        )
                    elif len(generated_script.get("segments", [])) != segments:
                        raise ValueError("approved prebuilt narration script segment count differs from the video brief")
                    from muse.narration_prosody_bridge import build_cantonese_narration_prosody_plan
                    narration_prosody_plan = build_cantonese_narration_prosody_plan(generated_script)
                    narration_script = "\n".join(segment["text"].strip() for segment in generated_script["segments"])
                    if claim_grounded:
                        knowledge_db = self.workspace / "data" / "research" / "knowledge.sqlite3"
                        if not knowledge_db.is_file():
                            raise RuntimeError("claim-grounded narration requires the existing ResearchKnowledgeStore database")
                        from muse.narration_evidence_pipeline import persist_claim_grounded_narration_run
                        from muse.narration_sync import NarrationSyncStore
                        from muse.research_knowledge import ResearchKnowledgeStore
                        with ResearchKnowledgeStore(knowledge_db) as research_store:
                            with NarrationSyncStore(
                                self.workspace / "data" / "narration" / "narration.sqlite3"
                            ) as narration_store:
                                persisted = persist_claim_grounded_narration_run(
                                    generated_script, research_store, narration_store,
                                )
                        evidence_contract = persisted["evidence_contract"]
                        if evidence_contract.get("publishable") is not True:
                            raise RuntimeError(
                                "claim-grounded narration blocked before GPU generation: "
                                + ",".join(sorted({
                                    reason
                                    for segment in evidence_contract["segments"]
                                    for reason in segment["blocking_reasons"]
                                }))
                            )
                        pending_evidence_contract = evidence_contract
                    narration_model_dir = tts_model
                    if narration.get("asr_enabled") is True:
                        if sys.version_info[:2] != (3, 14):
                            raise RuntimeError("Cantonese ASR currently requires the Python 3.14 main runtime; video generation was not started")
                        package_path = str(tts_packages.resolve())
                        if package_path not in sys.path:
                            sys.path.insert(0, package_path)
                        asr_model = self.workspace / "models" / "whisper-small-cantonese" / "cts"
                        if asr_model.is_symlink() or not (asr_model / "model.bin").is_file():
                            raise RuntimeError("Cantonese narration ASR was requested but the pinned local model is unavailable")
                        narration_asr_model_dir = asr_model
                elif narration is not None and not isinstance(narration, dict):
                    raise ValueError("creator_brief.video.narration must be an object")
                build_options = {
                    "negative_prompt": negative_prompt,
                    "aspect_ratio": aspect_ratio, "steps": steps,
                    "timeout_seconds": 1800, "motion_profile": "none",
                    "creator_initiative_id": attempt["initiative_id"],
                    "evolution_cycle_id": attempt["evolution_cycle_id"] or None,
                }
                if narration_script is not None:
                    build_options.update({
                        "narration_script": narration_script,
                        "narration_prosody_plan": narration_prosody_plan,
                        "narration_model_dir": narration_model_dir,
                        "narration_asr_model_dir": narration_asr_model_dir,
                    })
                    if narration.get("claim_grounded") is True:
                        build_options["narration_evidence_contract"] = pending_evidence_contract
                # A lease lost during long TTS/evidence preflight must not begin GPU work.
                if heartbeat.lease_lost:
                    return {
                        "status": "lease_lost", "attempt_id": attempt_id,
                        "error_type": heartbeat.error_type or "LeaseRenewalFailed",
                        "failure_recorded": False,
                    }
                consumed_admission_steps: set[str] = set()
                consumed_admission_grants: list[dict[str, str]] = []
                admission_input_sha_by_step: dict[str, str] = {}
                admission_plan_context: dict[str, Any] = {"plan": None, "batches": None, "workflow_ticket_id": None}

                # Restore the exact persisted Commander DAG from the execution Ticket.
                # A locally synthesized one-step plan is never an authorization source.
                _workflow_commander_plan_id: str = ""
                _workflow_commander_plan_source: str = ""
                _workflow_commander_plan_snapshot: dict[str, Any] | None = None
                _workflow_dag_sha256: str = ""
                _workflow_ticket_id: str = ""
                _approval_id = attempt.get("approval_task_id")
                if isinstance(_approval_id, str) and _approval_id:
                    with TaskQueue(self.task_database) as _wq:
                        for _wt in _wq.list_tasks(kind="video_pipeline", limit=50):
                            _wp = _wt.get("payload") or {}
                            if (isinstance(_wp, dict)
                                    and _wp.get("approval_task_id") == _approval_id
                                    and _wp.get("attempt_id") == attempt_id
                                    and (execution_task_id is None or _wt.get("id") == execution_task_id)
                                    and (execution_task_id is None or (
                                        _wt.get("status") == "running" and _wt.get("lease_owner") == self.worker_id
                                    ))):
                                _workflow_commander_plan_id = _wp.get("commander_plan_id") or ""
                                _workflow_commander_plan_source = _wp.get("commander_plan_source") or ""
                                _workflow_commander_plan_snapshot = _wp.get("commander_plan_snapshot")
                                _workflow_dag_sha256 = _wp.get("dag_sha256") or ""
                                _workflow_ticket_id = _wt.get("id") or ""
                                break
                if not _workflow_ticket_id:
                    raise VideoAdmissionBlocked("video_pipeline execution ticket is missing for the approved attempt")
                if (not _workflow_commander_plan_id or _workflow_commander_plan_source != "model"
                        or not isinstance(_workflow_commander_plan_snapshot, dict)
                        or not _workflow_dag_sha256):
                    raise VideoAdmissionBlocked(
                        "video generation requires a persisted model-authored Commander DAG; "
                        "synthetic or safe_fallback plans cannot authorize GPU dispatch"
                    )
                try:
                    from muse.unified_runtime import MuseRuntime
                    persisted_plan = MuseRuntime._restore_commander_plan_snapshot(
                        _workflow_commander_plan_snapshot, _workflow_dag_sha256,
                    )
                except Exception as exc:
                    raise VideoAdmissionBlocked(
                        "persisted Commander DAG failed digest/schema verification"
                    ) from exc
                if (persisted_plan.plan_id != _workflow_commander_plan_id
                        or persisted_plan.source != _workflow_commander_plan_source):
                    raise VideoAdmissionBlocked(
                        "persisted Commander DAG identity differs from its execution Ticket binding"
                    )
                admission_plan_context["plan"] = persisted_plan
                admission_plan_context["workflow_ticket_id"] = _workflow_ticket_id

                def admission_context_for_step(step_id: str):
                    """Return only a step that exists in the persisted Commander DAG."""
                    plan = admission_plan_context["plan"]
                    if plan is None:
                        raise VideoAdmissionBlocked("persisted Commander DAG is unavailable")
                    batches = admission_plan_context["batches"]
                    if batches is None:
                        from muse.video_studio import build_video_studio_batches
                        batches = build_video_studio_batches(plan, max_steps=2)
                        admission_plan_context["batches"] = batches
                    matches = [batch for batch in batches if tuple(batch.step_ids) == (step_id,)]
                    if len(matches) != 1:
                        raise VideoAdmissionBlocked(
                            f"persisted Commander DAG has no unique standalone batch for video step {step_id}"
                        )
                    batch = matches[0]
                    if tuple(batch.required_completed_steps):
                        raise VideoAdmissionBlocked(
                            f"{step_id} depends on unverified predecessor steps and cannot receive a standalone Wan grant"
                        )
                    return plan, batches, batch

                def pre_builder_approval_guard(step_id: str) -> None:
                    if heartbeat.lease_lost:
                        raise VideoAdmissionBlocked(f"video attempt lease lost before {step_id} generation")
                    with TaskQueue(self.task_database) as live_approvals:
                        if not self._commander_approval_is_valid(store, live_approvals, attempt, self.workspace):
                            raise VideoAdmissionBlocked(f"Commander approval was revoked before {step_id} generation")
                    if heartbeat.lease_lost:
                        raise VideoAdmissionBlocked(f"video attempt lease lost during {step_id} approval check")

                def scene_generation_guard(
                    step_id: str, scene_context: Mapping[str, Any] | None = None,
                    lease_context: Any | None = None, *,
                    input_sha256_override: str | None = None,
                ) -> None:
                    if heartbeat.lease_lost:
                        raise VideoAdmissionBlocked(f"video attempt lease lost before {step_id} generation")
                    if step_id in consumed_admission_steps:
                        raise VideoAdmissionBlocked(f"one-shot grant for {step_id} was already consumed")
                    with TaskQueue(self.task_database) as live_approvals:
                        approval_evidence = self._commander_approval_evidence(store, live_approvals, attempt, self.workspace)
                    if approval_evidence is None:
                        raise VideoAdmissionBlocked(f"Commander approval is no longer valid for {step_id}")
                    if input_sha256_override is not None:
                        input_sha256 = input_sha256_override
                    elif step_id in admission_input_sha_by_step:
                        input_sha256 = admission_input_sha_by_step[step_id]
                    elif isinstance(scene_context, Mapping):
                        effective_negative = scene_context.get("effective_negative_prompt")
                        if not isinstance(effective_negative, str):
                            scene_negative = scene_context.get("negative_prompt", "")
                            if not isinstance(scene_negative, str):
                                raise VideoAdmissionBlocked(f"{step_id} has invalid negative prompt context")
                            effective_negative = "\n".join(item for item in (negative_prompt, scene_negative) if item)
                        from muse.video_product_builder import VideoProductBuilder
                        input_sha256 = _video_generation_input_sha256(
                            attempt_id=attempt_id, recipe_sha256=attempt.get("recipe_sha256"),
                            title=title.strip(), prompt=scene_context.get("prompt"),
                            negative_prompt=effective_negative, aspect_ratio=aspect_ratio,
                            duration_seconds=float(scene_context.get("duration_seconds")),
                            steps=steps, seed=scene_context.get("seed"),
                            builder_version=str(VideoProductBuilder.BUILDER_VERSION),
                            narration_script=narration_script,
                            narration_prosody_plan=narration_prosody_plan,
                        )
                    else:
                        raise VideoAdmissionBlocked(f"exact generation input fingerprint is unavailable for {step_id}")
                    commander_plan, commander_batches, admission_batch = admission_context_for_step(step_id)
                    from muse.wan_video import generate_wan21_t2v
                    consumed = self._consume_commander_admission_grant(
                        store, attempt, video, step_id=step_id, input_sha256=input_sha256,
                        approval_evidence=approval_evidence, actual_handler=generate_wan21_t2v,
                        lease_lost=lambda: heartbeat.lease_lost,
                        commander_plan=commander_plan, commander_batches=commander_batches,
                        admission_batch=admission_batch,
                        scene_context=dict(scene_context) if isinstance(scene_context, Mapping) else {},
                        lease_context=lease_context,
                        commander_plan_id=_workflow_commander_plan_id,
                        commander_plan_source=_workflow_commander_plan_source,
                        workflow_ticket_id=_workflow_ticket_id,
                    )
                    if heartbeat.lease_lost:
                        raise VideoAdmissionBlocked(f"video attempt lease was lost after consuming {step_id} grant")
                    consumed_admission_steps.add(step_id)
                    consumed_admission_grants.append(consumed)

                if normalized_scenes is not None:
                    build_options["scene_generation_guard"] = scene_generation_guard
                    build_options["scenes"] = normalized_scenes
                    recipe_sha256 = attempt.get("recipe_sha256")
                    from muse.video_checkpoint_adapter import build_video_scene_checkpoint_plan
                    from muse.video_product_builder import VideoProductBuilder
                    checkpoint_recipe_sha = (
                        recipe_sha256 if isinstance(recipe_sha256, str)
                        and re.fullmatch(r"[0-9a-f]{64}", recipe_sha256) else None
                    )
                    checkpoint_plan = build_video_scene_checkpoint_plan(
                        title.strip(), _checkpoint_scene_fingerprint_inputs(normalized_scenes),
                        negative_prompt=build_options["negative_prompt"],
                        aspect_ratio=aspect_ratio, steps=steps,
                        recipe_sha256=checkpoint_recipe_sha,
                        builder_version=str(VideoProductBuilder.BUILDER_VERSION),
                    )
                    # The same complete Commander DAG is required for every scene grant,
                    # including narration paths where durable scene checkpoints are disabled.
                    admission_plan_context["plan"] = checkpoint_plan["plan"]
                    if narration_script is None and checkpoint_recipe_sha is not None:
                        from muse.video_checkpoint_adapter import (
                            VideoSceneCheckpointSession,
                            prepare_video_attempt_staging_directory,
                        )
                        build_options["recipe_sha256"] = recipe_sha256
                        admission_input_sha_by_step.update(checkpoint_plan["expected_input_sha256"])
                        attempt_staging = prepare_video_attempt_staging_directory(
                            self.workspace, attempt_id,
                        )
                        epoch_staging = attempt_staging / f"epoch_{lease_epoch}"
                        epoch_staging.mkdir(exist_ok=True)
                        epoch_info = epoch_staging.lstat()
                        if (epoch_staging.is_symlink()
                                or bool(getattr(epoch_info, "st_file_attributes", 0) & 0x400)
                                or not epoch_staging.is_dir()
                                or epoch_staging.resolve(strict=True).parent != attempt_staging.resolve(strict=True)):
                            raise ValueError("video attempt epoch staging must be a regular child directory")
                        build_options["staging_directory"] = epoch_staging
                        with TaskQueue(self.task_database) as checkpoint_queue:
                            checkpoint_session = VideoSceneCheckpointSession(
                                checkpoint_plan["batches"], store, checkpoint_queue,
                                attempt_id=attempt_id, worker_id=lease_worker_id,
                                lease_epoch=lease_epoch, workspace_root=self.workspace,
                                expected_input_sha256=checkpoint_plan["expected_input_sha256"],
                            )
                            if checkpoint_session.same_epoch_rejected_steps:
                                raise RuntimeError(
                                    "same-epoch rejected scene checkpoints require a new retry lease: "
                                    + ",".join(checkpoint_session.same_epoch_rejected_steps)
                                )
                            resume_map = {}
                            for step_id in checkpoint_session.reusable_step_ids:
                                try:
                                    artifact = checkpoint_session.resume_scene_artifacts[step_id]
                                    artifact_path = artifact["artifact_relative_path"]
                                    generation = dict(artifact["generation"])
                                except (KeyError, TypeError, ValueError) as exc:
                                    raise RuntimeError(f"verified checkpoint {step_id} lacks its public artifact resume entry") from exc
                                if not isinstance(artifact_path, str) or not artifact_path:
                                    raise RuntimeError(f"verified checkpoint {step_id} has an invalid artifact path")
                                resume_map[step_id] = {
                                    "artifact_relative_path": artifact_path,
                                    "generation": generation,
                                }
                            build_options.update({
                                "resume_scene_metadata": resume_map,
                                "reusable_step_ids": checkpoint_session.reusable_step_ids,
                                "scene_checkpoint_callback": checkpoint_session.record,
                            })
                            result = self._builder().build_from_scenes(title.strip(), **build_options)
                    else:
                        # TTS may quantize scene durations after synthesis; do not bind
                        # checkpoints to the pre-TTS scene digest.
                        result = self._builder().build_from_scenes(title.strip(), **build_options)
                else:
                    from muse.video_product_builder import VideoProductBuilder
                    single_seed = int(hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:8], 16)
                    single_input_sha256 = _video_generation_input_sha256(
                        attempt_id=attempt_id, recipe_sha256=attempt.get("recipe_sha256"),
                        title=title.strip(), prompt=prompt.strip(), negative_prompt=negative_prompt,
                        aspect_ratio=aspect_ratio, duration_seconds=float(duration), steps=steps,
                        seed=single_seed, builder_version=str(VideoProductBuilder.BUILDER_VERSION),
                        narration_script=narration_script,
                        narration_prosody_plan=narration_prosody_plan,
                    )
                    pre_builder_approval_guard("shot_001")
                    build_options.update({
                        "duration_seconds": float(duration),
                        "seed": single_seed,
                        "generation_guard": lambda lease_context=None: scene_generation_guard(
                            "shot_001", lease_context=lease_context,
                            input_sha256_override=single_input_sha256,
                        ),
                    })
                    result = self._builder().build_from_prompt(title.strip(), prompt.strip(), **build_options)

                required = ("initiative_id", "version_id", "mp4_sha256", "package_sha256", "manifest_sha256")
                if any(not isinstance(result.get(key), str) or not result[key] for key in required):
                    raise RuntimeError("video product builder returned incomplete artifact identity")
                for key in ("mp4_sha256", "package_sha256", "manifest_sha256"):
                    if not re.fullmatch(r"[0-9a-f]{64}", result[key]):
                        raise RuntimeError(f"video product builder returned an invalid {key} digest")
                verified = self._builder().verify_path(
                    self.workspace / "data" / "products", result["initiative_id"], result["version_id"],
                )
                if not isinstance(verified, dict) or verified.get("validated") is not True:
                    raise RuntimeError("video product filesystem reverification did not pass")
                for key in (
                    "initiative_id", "version_id", "relative_path",
                    "mp4_sha256", "package_sha256", "manifest_sha256",
                ):
                    if verified.get(key) != result.get(key):
                        raise RuntimeError(f"video product changed after build: {key}")
                heartbeat.stop()
                if heartbeat.lease_lost:
                    return {
                        "status": "lease_lost", "attempt_id": attempt_id,
                        "error_type": heartbeat.error_type or "LeaseRenewalFailed",
                        "failure_recorded": False,
                    }
                if self.pause_check():
                    try:
                        store.release_video_attempt(
                            attempt_id, worker_id=lease_worker_id, lease_epoch=lease_epoch,
                            reason="runtime_paused_after_render",
                        )
                    except ValueError:
                        return {"status": "lease_lost", "attempt_id": attempt_id, "release_recorded": False}
                    return {"status": "paused", "attempt_id": attempt_id, "product_created": True}
                with TaskQueue(self.task_database) as final_approvals:
                    if not self._commander_approval_is_valid(store, final_approvals, attempt, self.workspace):
                        try:
                            store.release_video_attempt(
                                attempt_id, worker_id=lease_worker_id, lease_epoch=lease_epoch,
                                reason="commander_approval_invalidated_before_completion",
                            )
                        except ValueError:
                            return {"status": "lease_lost", "attempt_id": attempt_id, "release_recorded": False}
                        return {
                            "status": "blocked_approval", "attempt_id": attempt_id,
                            "product_created": True, "completion_recorded": False,
                        }
                if defer_completion:
                    return {
                        "status": "awaiting_independent_review", "attempt_id": attempt_id,
                        "initiative_id": result["initiative_id"], "version_id": result["version_id"],
                        "relative_path": result["relative_path"], "manifest_sha256": result["manifest_sha256"],
                        "mp4_sha256": result["mp4_sha256"], "package_sha256": result["package_sha256"],
                        "lease_worker_id": lease_worker_id, "lease_epoch": lease_epoch,
                        "quality_gate": result.get("quality_gate", {"status": "not_reported"}),
                        "narration_status": (
                            result.get("narration_synthesis", {}).get("status", "not_requested")
                            if isinstance(result.get("narration_synthesis"), dict) else "not_requested"
                        ),
                        "admission_grants_consumed": list(consumed_admission_grants),
                    }
                completed = store.complete_video_attempt(
                    attempt_id, product_id=result["initiative_id"], version_id=result["version_id"],
                    mp4_sha256=result["mp4_sha256"], package_sha256=result["package_sha256"],
                    manifest_sha256=result["manifest_sha256"],
                    worker_id=lease_worker_id, lease_epoch=lease_epoch,
                )
                # Cleanup is post-commit best-effort only. Preserve every resumable or
                # non-terminal staging tree, and never reverse a committed success.
                if completed.get("status") == "succeeded" and re.fullmatch(r"[0-9a-f]{32}", attempt_id):
                    try:
                        persisted_attempt = store.get_video_attempt(attempt_id)
                        if (isinstance(persisted_attempt, dict)
                                and persisted_attempt.get("status") == "succeeded"
                                and all(re.fullmatch(r"[0-9a-f]{64}", str(persisted_attempt.get(field, "")))
                                        for field in ("mp4_sha256", "package_sha256", "manifest_sha256"))):
                            cleanup_video_attempt_staging_directory(self.workspace, attempt_id, store)
                    except Exception:
                        logging.getLogger(__name__).warning(
                            "Could not clean verified video Attempt staging for %s; committed success is retained",
                            attempt_id, exc_info=True,
                        )
                return {
                    "status": completed["status"], "attempt_id": attempt_id,
                    "product_id": completed["product_id"], "version_id": completed["version_id"],
                    "quality_gate": result.get("quality_gate", {"status": "not_reported"}),
                    "content_review_required": True, "auto_publish": False,
                    "narration_status": (
                        result.get("narration_synthesis", {}).get("status", "not_requested")
                        if isinstance(result.get("narration_synthesis"), dict) else "not_requested"
                    ),
                    "admission_grants_consumed": list(consumed_admission_grants),
                }
            except VideoAdmissionBlocked as exc:
                heartbeat.stop()
                if heartbeat.lease_lost:
                    return {
                        "status": "lease_lost", "attempt_id": attempt_id,
                        "error_type": heartbeat.error_type or type(exc).__name__,
                        "failure_recorded": False,
                    }
                try:
                    store.release_video_attempt(
                        attempt_id, worker_id=lease_worker_id, lease_epoch=lease_epoch,
                        reason="commander_admission_blocked",
                    )
                except ValueError:
                    return {"status": "lease_lost", "attempt_id": attempt_id, "release_recorded": False, "failure_recorded": False}
                return {
                    "status": "blocked_admission", "attempt_id": attempt_id,
                    "reason": str(exc)[:500], "admission_grants_consumed": list(consumed_admission_grants),
                }
            except Exception as exc:
                heartbeat.stop()
                if heartbeat.lease_lost:
                    return {
                        "status": "lease_lost", "attempt_id": attempt_id,
                        "error_type": heartbeat.error_type or type(exc).__name__,
                        "failure_recorded": False,
                    }
                try:
                    failed = store.fail_video_attempt(
                        attempt_id, error_type=type(exc).__name__, failure_code="video.worker_execution_failed",
                        worker_id=lease_worker_id, lease_epoch=lease_epoch,
                    )
                except ValueError:
                    # A newer worker may have reclaimed the attempt while this renderer was running.
                    # Never let the stale worker overwrite that newer lease with a failure record.
                    return {
                        "status": "lease_lost", "attempt_id": attempt_id,
                        "error_type": type(exc).__name__, "failure_recorded": False,
                    }
                return {
                    "status": failed["status"], "attempt_id": attempt_id,
                    "error_type": type(exc).__name__, "error_message": str(exc)[:500],
                    "failure_code": failed["failure_code"],
                }
            finally:
                heartbeat.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded Muse video task or explicitly retry a failed attempt")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--retry-attempt", default=None)
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args(argv)
    workspace = Path(args.workspace).resolve()
    try:
        database = workspace / "data" / "initiatives" / "muse.sqlite3"
        if args.retry_attempt:
            with InitiativeStore(database) as store:
                result = store.retry_failed_video_attempt(args.retry_attempt, max_attempts=args.max_attempts)
            print(json.dumps({"status": result["status"], "attempt_id": result["attempt_id"]}, ensure_ascii=False))
        else:
            result = VideoTaskWorker(
                workspace, worker_id=args.worker_id, pause_check=lambda: _runtime_is_paused(workspace),
            ).run_once()
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())