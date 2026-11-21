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
from typing import Any, Callable

from muse.initiative_engine import InitiativeStore
from muse.video_checkpoint_adapter import cleanup_video_attempt_staging_directory


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
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.worker_id = worker_id or ("video-" + uuid.uuid4().hex[:16])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.worker_id):
            raise ValueError("worker_id must be 1-64 safe identifier characters")
        self.builder = builder
        self.pause_check = pause_check or (lambda: False)
        self.database = self.workspace / "data" / "initiatives" / "muse.sqlite3"
        self.task_database = self.workspace / "data" / "tasks" / "muse.sqlite3"
        self.lease_seconds = 3600
        self.lease_renew_interval_seconds = 60.0

    @staticmethod
    def _commander_approval_is_valid(store, queue, attempt) -> bool:
        """Require a live, persisted human approval bound to this exact brief and attempt."""
        task_id = attempt.get("approval_task_id")
        initiative_id = attempt.get("initiative_id")
        if not isinstance(task_id, str) or not task_id or not isinstance(initiative_id, str):
            return False
        initiative = store.get_by_id(initiative_id)
        if initiative is None or initiative.get("status") in {"rejected", "parked"}:
            return False
        try:
            concept = json.loads(initiative["concept"])
        except (TypeError, json.JSONDecodeError):
            return False
        brief = concept.get("creator_brief", {}).get("video") if isinstance(concept, dict) else None
        if not isinstance(brief, dict):
            return False
        digest = hashlib.sha256(json.dumps(brief, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        ticket = queue.get(task_id)
        if ticket is None or ticket.get("status") != "queued":
            return False
        payload = ticket.get("payload", {})
        if not isinstance(payload, dict):
            return False
        approval = payload.get("_approval", {})
        approved_by = approval.get("approved_by") if isinstance(approval, dict) else None
        if (payload.get("kind") != "business_opportunity_video_approval"
                or payload.get("initiative_id") != initiative_id
                or payload.get("attempt_id") != attempt.get("attempt_id")
                or payload.get("approval_scope") != "one_video_attempt_only"
                or payload.get("_approved") is not True
                or payload.get("video_brief_sha256") != digest
                or not isinstance(payload.get("video_brief"), dict)
                or hashlib.sha256(json.dumps(payload["video_brief"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != digest
                or not isinstance(approved_by, str) or not approved_by.strip()):
            return False
        decisions = [
            event for event in queue.events(task_id, limit=500)
            if event.get("event_type") == "human.decision"
        ]
        if not decisions:
            return False
        latest_decision = decisions[-1]
        latest_payload = latest_decision.get("payload")
        return (
            latest_decision.get("actor") == approved_by
            and isinstance(latest_payload, dict)
            and latest_payload.get("decision") == "approved"
        )

    def _builder(self):
        if self.builder is None:
            from muse.video_product_builder import VideoProductBuilder
            self.builder = VideoProductBuilder(self.workspace)
        return self.builder

    def run_once(self) -> dict[str, Any]:
        """Run one queued prompt-to-video task; never loops or auto-publishes."""
        if self.pause_check():
            return {"status": "paused", "claimed": False}
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
                normalized_scenes = None
                if scenes is not None:
                    normalized_scenes = []
                    total_scene_duration = 0.0
                    for index, scene in enumerate(scenes, start=1):
                        if not isinstance(scene, dict) or set(scene) - {"prompt", "duration_seconds", "seed"}:
                            raise ValueError(f"scene {index} contains unsupported fields")
                        scene_prompt = scene.get("prompt")
                        scene_duration = scene.get("duration_seconds")
                        scene_seed = scene.get("seed")
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
                        })
                    if total_scene_duration > 60:
                        raise ValueError("creator brief scene duration must not exceed 60 seconds")
                narration_script = None
                narration_model_dir = None
                narration_asr_model_dir = None
                pending_evidence_contract = None
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
                    narration_script = "\n".join(segment["text"] for segment in generated_script["segments"])
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
                    "negative_prompt": str(video.get("negative_prompt", ""))[:1000],
                    "aspect_ratio": aspect_ratio, "steps": steps,
                    "timeout_seconds": 1800, "motion_profile": "none",
                    "creator_initiative_id": attempt["initiative_id"],
                    "evolution_cycle_id": attempt["evolution_cycle_id"] or None,
                }
                if narration_script is not None:
                    build_options.update({
                        "narration_script": narration_script,
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
                def scene_generation_guard(step_id: str) -> None:
                    if heartbeat.lease_lost:
                        raise RuntimeError(f"video attempt lease lost before {step_id} generation")
                    with TaskQueue(self.task_database) as live_approvals:
                        if not self._commander_approval_is_valid(store, live_approvals, attempt):
                            raise PermissionError(
                                f"Commander approval was revoked before {step_id} generation"
                            )
                    if heartbeat.lease_lost:
                        raise RuntimeError(f"video attempt lease lost during {step_id} approval check")

                if normalized_scenes is not None:
                    build_options["scene_generation_guard"] = scene_generation_guard
                    build_options["scenes"] = normalized_scenes
                    recipe_sha256 = attempt.get("recipe_sha256")
                    if (narration_script is None and isinstance(recipe_sha256, str)
                            and re.fullmatch(r"[0-9a-f]{64}", recipe_sha256)):
                        from muse.video_checkpoint_adapter import (
                            VideoSceneCheckpointSession,
                            build_video_scene_checkpoint_plan,
                            prepare_video_attempt_staging_directory,
                        )
                        from muse.video_product_builder import VideoProductBuilder

                        build_options["recipe_sha256"] = recipe_sha256
                        checkpoint_plan = build_video_scene_checkpoint_plan(
                            title.strip(), normalized_scenes,
                            negative_prompt=build_options["negative_prompt"],
                            aspect_ratio=aspect_ratio, steps=steps,
                            recipe_sha256=recipe_sha256,
                            builder_version=str(VideoProductBuilder.BUILDER_VERSION),
                        )
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
                                saved = checkpoint_session.resume["reusable_steps"].get(step_id, {})
                                metadata = saved.get("metadata") if isinstance(saved, dict) else None
                                if not isinstance(metadata, dict) or not isinstance(metadata.get("artifact_relative_path"), str):
                                    raise RuntimeError(f"verified checkpoint {step_id} lacks its artifact path")
                                resume_map[step_id] = {
                                    "artifact_relative_path": metadata["artifact_relative_path"],
                                    "generation": checkpoint_session.resume_scene_metadata[step_id],
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
                    build_options.update({
                        "duration_seconds": float(duration),
                        "seed": int(hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:8], 16),
                    })
                    scene_generation_guard("shot_001")
                    result = self._builder().build_from_prompt(title.strip(), prompt.strip(), **build_options)
                if result.get("validated") is not True:
                    raise RuntimeError("video product builder did not verify its output package")
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