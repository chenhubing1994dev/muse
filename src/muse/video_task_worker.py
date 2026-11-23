"""Bounded executor for durable Creator Initiative video attempts.

The worker claims at most one task per invocation. Wan generation obtains its own
cross-process GPU lease in wan_video; this module never starts or stops services.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import threading
import uuid
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
    def _commander_approval_evidence(store, queue, attempt) -> dict[str, str] | None:
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
    def _commander_approval_is_valid(store, queue, attempt) -> bool:
        """Compatibility predicate for a live approval bound to this exact attempt."""
        return VideoTaskWorker._commander_approval_evidence(store, queue, attempt) is not None

    def _consume_commander_admission_grant(
        self, store, attempt, video_brief, *, step_id: str, input_sha256: str,
        approval_evidence: Mapping[str, str], actual_handler: Callable[..., Any],
        lease_lost: Callable[[], bool], commander_plan: Any, commander_batches: tuple[Any, ...],
        admission_batch: Any, scene_context: Mapping[str, Any],
        lease_context: Any | None = None,
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
            latest_approval = self._commander_approval_evidence(store, live_approvals, attempt)
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
        """Run one queued prompt-to-video task; never loops or auto-publishes."""
        if self.pause_check():
            return {"status": "paused", "claimed": False}
        if self.admission_provider is None or self.admission_request_factory is None:
            return {
                "status": "blocked_admission", "claimed": False,
                "reason": "commander_admission_provider_missing",
            }
        with InitiativeStore(self.database) as store:
            candidates = store.list_queued_approval_video_attempts(limit=200)
            if not candidates or self.task_database.is_symlink() or not self.task_database.is_file():
                return {"status": "idle", "claimed": False, "approval_required": True}

            from muse.task_queue import TaskQueue
            attempt = None
            blocked = 0
            with TaskQueue(self.task_database) as approvals:
                for candidate in candidates:
                    try:
                        valid = self._commander_approval_is_valid(store, approvals, candidate)
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
                    if not self._commander_approval_is_valid(store, approvals, attempt):
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
                title = video.get("title")
                prompt = video.get("prompt")
                if not isinstance(title, str) or not title.strip() or not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("video brief requires a non-empty title and prompt")
                aspect_ratio = video.get("aspect_ratio", "9:16")
                duration = video.get("duration_seconds", 2.0)
                steps = video.get("steps", 8)
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
                    generated_script = NarrationGenerator().generate(
                        title.strip(), research_text, inspiration, style, segments=segments,
                        **generator_options,
                    )
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
                admission_plan_context: dict[str, Any] = {"plan": None, "batches": None}

                def admission_context_for_step(step_id: str):
                    plan = admission_plan_context["plan"]
                    if plan is None:
                        from muse.commander_planner import CommanderPlan, PlannedStep
                        plan_seed = json.dumps(
                            [attempt_id, attempt.get("recipe_sha256"), step_id, "single-video-step-v1"],
                            ensure_ascii=False, separators=(",", ":"),
                        )
                        plan = CommanderPlan(
                            plan_id=hashlib.sha256(plan_seed.encode("utf-8")).hexdigest()[:32],
                            goal=f"{title.strip()}｜单镜头生成授权",
                            steps=(PlannedStep(
                                step_id, "developer", f"生成并验证镜头 {step_id}", (),
                                ("comfyui_generation",),
                            ),),
                            source="video_task_worker_single_step_v1",
                            parallel_waves=((step_id,),),
                        )
                        admission_plan_context["plan"] = plan
                    batches = admission_plan_context["batches"]
                    if batches is None:
                        from muse.video_studio import build_video_studio_batches
                        batches = build_video_studio_batches(plan, max_steps=2)
                        admission_plan_context["batches"] = batches
                    matches = [batch for batch in batches if tuple(batch.step_ids) == (step_id,)]
                    if len(matches) != 1:
                        raise VideoAdmissionBlocked(
                            f"Commander DAG must yield exactly one stable one-step batch for {step_id}"
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
                        if not self._commander_approval_is_valid(store, live_approvals, attempt):
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
                        approval_evidence = self._commander_approval_evidence(store, live_approvals, attempt)
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
                    if not self._commander_approval_is_valid(store, final_approvals, attempt):
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