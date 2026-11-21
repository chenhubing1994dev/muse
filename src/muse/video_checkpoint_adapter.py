"""Fail-closed validation of historical VideoStudio checkpoint evidence.

This module only decides which completed step artifacts may be reused. It never
dispatches Commander work, invokes media providers, or changes Attempt state.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_link_or_reparse(info: Any) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
    )


def _artifact_sha256(workspace_root: Path, relative_path: str) -> str:
    if _is_link_or_reparse(workspace_root.lstat()):
        raise ValueError("workspace_root must not be a symlink or reparse point")
    if (not isinstance(relative_path, str) or not relative_path or len(relative_path) > 1024
            or "\\" in relative_path or ":" in relative_path):
        raise ValueError("artifact path must be a bounded workspace-relative POSIX path")
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("artifact path must not be absolute or traverse directories")
    root = workspace_root.resolve(strict=True)
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        info = candidate.lstat()
        if _is_link_or_reparse(info):
            raise ValueError("artifact path must not contain symlinks or reparse points")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("artifact path escapes workspace root")
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_ARTIFACT_BYTES:
        raise ValueError("artifact must be a regular file within the size limit")
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        opened = stream.fileno()
        opened_before = os.fstat(opened)
        if (not stat.S_ISREG(opened_before.st_mode) or opened_before.st_size != before.st_size
                or opened_before.st_ino != before.st_ino or opened_before.st_dev != before.st_dev):
            raise ValueError("artifact changed while it was being opened")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        opened_after = os.fstat(opened)
    after = resolved.stat()
    if (opened_after.st_size > _MAX_ARTIFACT_BYTES
            or opened_before.st_size != opened_after.st_size or opened_after.st_size != after.st_size
            or opened_before.st_mtime_ns != opened_after.st_mtime_ns
            or opened_before.st_ino != opened_after.st_ino or opened_before.st_dev != opened_after.st_dev
            or opened_after.st_mtime_ns != after.st_mtime_ns
            or opened_after.st_ino != after.st_ino or opened_after.st_dev != after.st_dev):
        raise ValueError("artifact changed while it was being hashed")
    return digest.hexdigest()


def prepare_video_attempt_staging_directory(workspace_root: str | Path, attempt_id: str) -> Path:
    """Create a durable, attempt-scoped staging directory without following symlinks."""
    if not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("attempt_id must be a 32-character lowercase UUID hex string")
    supplied_root = Path(workspace_root)
    if _is_link_or_reparse(supplied_root.lstat()):
        raise ValueError("workspace_root must not be a symlink or reparse point")
    root = supplied_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace_root must be an existing directory")
    current = root
    for component in ("data", "video_attempt_staging", attempt_id):
        current = current / component
        try:
            current.mkdir()
        except FileExistsError:
            pass
        info = current.lstat()
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("attempt staging path must contain only regular directories")
        if not current.resolve(strict=True).is_relative_to(root):
            raise ValueError("attempt staging path escapes workspace root")
    return current


def cleanup_video_attempt_staging_directory(
    workspace_root: str | Path, attempt_id: str, store: Any,
) -> bool:
    """Remove an Attempt's durable staging only after its verified package is committed."""
    if not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("attempt_id must be a 32-character lowercase UUID hex string")
    supplied_root = Path(workspace_root)
    if _is_link_or_reparse(supplied_root.lstat()):
        raise ValueError("workspace_root must not be a symlink or reparse point")
    root = supplied_root.resolve(strict=True)
    attempt = store.get_video_attempt(attempt_id)
    if not isinstance(attempt, Mapping) or attempt.get("status") != "succeeded":
        raise ValueError("video Attempt staging may be cleaned only after Store success")
    for field in ("mp4_sha256", "package_sha256", "manifest_sha256"):
        if not isinstance(attempt.get(field), str) or not _SHA256.fullmatch(attempt[field]):
            raise ValueError("video Attempt success record lacks verified artifact digests")

    attempt_root = root / "data" / "video_attempt_staging" / attempt_id
    current = root
    for component in ("data", "video_attempt_staging", attempt_id):
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            return False
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("attempt staging path must contain only regular directories")
        if not current.resolve(strict=True).is_relative_to(root):
            raise ValueError("attempt staging path escapes workspace root")

    pending = [attempt_root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if _is_link_or_reparse(info):
                    raise ValueError("attempt staging contents must not contain links or reparse points")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif not stat.S_ISREG(info.st_mode):
                    raise ValueError("attempt staging contents must contain only regular files and directories")
    shutil.rmtree(attempt_root)
    return True


def build_video_scene_checkpoint_plan(
    title: str,
    scenes: Sequence[Mapping[str, Any]],
    *,
    negative_prompt: str,
    aspect_ratio: str,
    steps: int,
    recipe_sha256: str | None,
    builder_version: str,
    model_id: str = "Wan 2.1 T2V 1.3B",
) -> dict[str, Any]:
    """Build a deterministic per-shot DAG and recomputable stage-input digests."""
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError("title must contain 1-160 characters")
    if not isinstance(scenes, (tuple, list)) or not 2 <= len(scenes) <= 20:
        raise ValueError("scenes must contain between 2 and 20 specifications")
    if not isinstance(negative_prompt, str) or len(negative_prompt) > 1000:
        raise ValueError("negative_prompt must be at most 1000 characters")
    if aspect_ratio not in {"16:9", "9:16", "1:1"}:
        raise ValueError("aspect_ratio must be 16:9, 9:16 or 1:1")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 8 <= steps <= 30:
        raise ValueError("steps must be between 8 and 30")
    if recipe_sha256 is not None and (not isinstance(recipe_sha256, str) or not _SHA256.fullmatch(recipe_sha256)):
        raise ValueError("recipe_sha256 must be a lowercase SHA-256 digest or None")
    if not isinstance(builder_version, str) or not builder_version.strip() or len(builder_version) > 64:
        raise ValueError("builder_version must be non-empty bounded text")
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 128:
        raise ValueError("model_id must be non-empty bounded text")

    normalized: list[dict[str, Any]] = []
    step_ids: list[str] = []
    expected_inputs: dict[str, str] = {}
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, Mapping) or set(scene) != {"prompt", "duration_seconds", "seed"}:
            raise ValueError(f"scene {index} must contain prompt, duration_seconds and seed only")
        prompt = scene.get("prompt")
        duration = scene.get("duration_seconds")
        seed = scene.get("seed")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 2000:
            raise ValueError(f"scene {index} prompt must contain 1-2000 characters")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or not 1 <= duration <= 5):
            raise ValueError(f"scene {index} duration_seconds must be between 1 and 5")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
            raise ValueError(f"scene {index} seed must be an unsigned 32-bit integer")
        normalized_scene = {"prompt": prompt.strip(), "duration_seconds": float(duration), "seed": seed}
        normalized.append(normalized_scene)
        step_id = f"shot_{index:03d}"
        step_ids.append(step_id)
        input_payload = {
            "stage_id": "shot_generation", "scene": normalized_scene,
            "negative_prompt": negative_prompt, "aspect_ratio": aspect_ratio,
            "steps": steps, "recipe_sha256": recipe_sha256,
            "builder_version": builder_version, "model_id": model_id,
        }
        expected_inputs[step_id] = hashlib.sha256(json.dumps(
            input_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

    from muse.commander_planner import CommanderPlan, PlannedStep
    from muse.video_studio import build_video_studio_batches

    planned_steps = tuple(PlannedStep(
        step_id, "developer", f"生成并验证镜头 {index}", (), ("comfyui_generation",),
    ) for index, step_id in enumerate(step_ids, start=1))
    plan_seed = json.dumps([title.strip(), step_ids], ensure_ascii=False, separators=(",", ":"))
    plan = CommanderPlan(
        plan_id=hashlib.sha256(plan_seed.encode("utf-8")).hexdigest()[:32],
        goal=f"{title.strip()}｜本地镜头生成与检查点",
        steps=planned_steps,
        source="video_task_worker_scene_generation",
        parallel_waves=(tuple(step_ids),),
    )
    batches = build_video_studio_batches(plan)
    if any(set(dict(batch.step_stage_ids).values()) != {"shot_generation"} for batch in batches):
        raise ValueError("scene generation plan did not resolve exclusively to shot_generation")
    return {
        "plan": plan, "batches": batches, "step_ids": tuple(step_ids),
        "expected_input_sha256": expected_inputs,
        "execution_eligible": False, "dispatch_started": False,
    }


def evaluate_video_checkpoint_resume(
    batches: Sequence[Any],
    store: Any,
    *,
    attempt_id: str,
    worker_id: str,
    lease_epoch: int,
    workspace_root: str | Path,
    expected_input_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Return only reusable artifacts after lease, DAG, dependency, input and byte checks.

    expected_input_sha256 must be recomputed by the caller from current stage inputs
    and already verified predecessor output digests. Missing digests fail closed.
    """
    if not isinstance(batches, (tuple, list)) or not batches:
        raise ValueError("batches must be a non-empty ordered sequence")
    if not isinstance(expected_input_sha256, Mapping):
        raise ValueError("expected_input_sha256 must be a mapping")
    root = Path(workspace_root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("workspace_root must be an existing non-symlink directory")

    reusable: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    blocked: list[str] = []
    rejected: dict[str, str] = {}
    same_epoch_rejected: list[str] = []
    verified_steps: set[str] = set()

    for batch in batches:
        dag_sha256 = getattr(batch, "dag_sha256", None)
        batch_id = getattr(batch, "batch_id", None)
        batch_fingerprint = getattr(batch, "batch_fingerprint", None)
        step_ids = getattr(batch, "step_ids", None)
        required = getattr(batch, "required_completed_steps", None)
        step_stage_ids = getattr(batch, "step_stage_ids", None)
        plan = getattr(batch, "plan", None)
        if (not isinstance(dag_sha256, str) or not _SHA256.fullmatch(dag_sha256)
                or not isinstance(batch_fingerprint, str) or not _SHA256.fullmatch(batch_fingerprint)
                or not isinstance(batch_id, str) or not batch_id or not isinstance(step_ids, tuple)
                or not isinstance(required, tuple) or not isinstance(step_stage_ids, tuple)
                or not step_ids or len(step_ids) != len(set(step_ids))
                or plan is None):
            raise ValueError("batch identity or plan is incomplete")
        if not set(required) <= verified_steps:
            blocked.extend(step_id for step_id in step_ids if step_id not in blocked)
            for step_id in step_ids:
                rejected[step_id] = "required_predecessor_not_verified"
            continue

        stage_map = dict(step_stage_ids)
        plan_steps = {step.step_id: step for step in plan.steps if step.step_id in step_ids}
        if set(plan_steps) != set(step_ids) or set(stage_map) != set(step_ids):
            raise ValueError("batch step and fixed-stage mapping is incomplete")
        gate_id = plan.steps[0].step_id if plan.steps else None
        history = store.list_video_stage_checkpoints(
            attempt_id,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
            dag_sha256=dag_sha256,
            batch_id=batch_id,
            batch_fingerprint=batch_fingerprint,
        )
        by_step: dict[str, list[dict[str, Any]]] = {}
        for row in history:
            by_step.setdefault(row.get("step_id"), []).append(row)

        for step_id in step_ids:
            step = plan_steps[step_id]
            dependencies = set(step.depends_on) - ({gate_id} if gate_id else set())
            if not dependencies <= verified_steps:
                blocked.append(step_id)
                rejected[step_id] = "step_predecessor_not_verified"
                continue
            expected_input = expected_input_sha256.get(step_id)
            if not isinstance(expected_input, str) or not _SHA256.fullmatch(expected_input):
                missing.append(step_id)
                rejected[step_id] = "expected_input_digest_missing_or_invalid"
                continue
            candidates = sorted(by_step.get(step_id, []), key=lambda row: row.get("sequence", 0), reverse=True)
            reason = "checkpoint_missing_or_mismatched"
            accepted = None
            for row in candidates:
                if (row.get("batch_id") != batch_id
                        or row.get("batch_fingerprint") != batch_fingerprint
                        or row.get("stage_id") != stage_map[step_id]
                        or row.get("input_sha256") != expected_input):
                    reason = "checkpoint_identity_or_input_mismatch"
                    continue
                metadata = row.get("metadata")
                artifact_path = metadata.get("artifact_relative_path") if isinstance(metadata, dict) else None
                try:
                    actual_output = _artifact_sha256(root, artifact_path)
                except (OSError, ValueError):
                    reason = "artifact_missing_or_unsafe"
                    continue
                if actual_output != row.get("output_sha256"):
                    reason = "artifact_digest_mismatch"
                    continue
                accepted = row
                break
            if accepted is None:
                missing.append(step_id)
                if candidates:
                    rejected[step_id] = reason
                    if any(row.get("lease_epoch") == lease_epoch for row in candidates):
                        same_epoch_rejected.append(step_id)
                continue
            reusable[step_id] = accepted
            verified_steps.add(step_id)

    return {
        "status": "verified_resume_evidence" if reusable else "no_reusable_checkpoints",
        "reusable_steps": reusable,
        "verified_steps": sorted(verified_steps),
        "missing_steps": list(dict.fromkeys(missing)),
        "blocked_steps": list(dict.fromkeys(blocked)),
        "rejected": rejected,
        "same_epoch_rejected_steps": list(dict.fromkeys(same_epoch_rejected)),
        "execution_eligible": False,
        "dispatch_started": False,
    }

def record_video_checkpoint_artifact(
    batch: Any,
    store: Any,
    queue: Any,
    *,
    batches: Sequence[Any],
    expected_input_sha256: Mapping[str, str],
    attempt_id: str,
    worker_id: str,
    lease_epoch: int,
    step_id: str,
    workspace_root: str | Path,
    artifact_relative_path: str,
    input_sha256: str,
    artifact_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one artifact under the exact live Commander-approved Attempt.

    Predecessors are re-read and re-hashed here; caller-supplied completion claims
    are not accepted. This writes evidence only and never dispatches work.
    """
    if batch is None or not isinstance(batches, (tuple, list)) or not batches:
        raise ValueError("batch and ordered batches are required")
    if not isinstance(expected_input_sha256, Mapping):
        raise ValueError("expected_input_sha256 must be a mapping")
    if not isinstance(input_sha256, str) or not _SHA256.fullmatch(input_sha256):
        raise ValueError("input_sha256 must be a lowercase SHA-256 digest")
    generation_metadata: dict[str, Any] = {}
    if artifact_metadata is not None:
        allowed = {"status", "prompt_id", "model", "width", "height", "fps", "frames"}
        if not isinstance(artifact_metadata, Mapping) or set(artifact_metadata) - allowed:
            raise ValueError("artifact_metadata contains unsupported fields")
        if artifact_metadata.get("status") != "completed":
            raise ValueError("artifact_metadata status must be completed")
        for field in ("width", "height", "frames"):
            value = artifact_metadata.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"artifact_metadata.{field} must be a positive integer")
        fps = artifact_metadata.get("fps")
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
            raise ValueError("artifact_metadata.fps must be a positive finite number")
        for field in ("prompt_id", "model"):
            value = artifact_metadata.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 128):
                raise ValueError(f"artifact_metadata.{field} must be bounded text or None")
        generation_metadata = {
            "status": "completed", "width": artifact_metadata["width"],
            "height": artifact_metadata["height"], "fps": float(fps),
            "frames": artifact_metadata["frames"],
            "prompt_id": artifact_metadata.get("prompt_id"),
            "model": artifact_metadata.get("model", "Wan 2.1 T2V 1.3B"),
        }
    step_ids = getattr(batch, "step_ids", None)
    stage_pairs = getattr(batch, "step_stage_ids", None)
    plan = getattr(batch, "plan", None)
    dag_sha256 = getattr(batch, "dag_sha256", None)
    batch_id = getattr(batch, "batch_id", None)
    batch_fingerprint = getattr(batch, "batch_fingerprint", None)
    checkpoint_template = getattr(batch, "checkpoint_key_template", None)
    if (not isinstance(step_ids, tuple) or step_id not in step_ids
            or not isinstance(stage_pairs, tuple) or plan is None
            or not isinstance(dag_sha256, str) or not _SHA256.fullmatch(dag_sha256)
            or not isinstance(batch_fingerprint, str) or not _SHA256.fullmatch(batch_fingerprint)
            or not isinstance(batch_id, str) or not isinstance(checkpoint_template, str)
            or not any(getattr(item, "batch_id", None) == batch_id
                       and getattr(item, "batch_fingerprint", None) == batch_fingerprint
                       for item in batches)):
        raise ValueError("batch identity or requested step is invalid")
    attempt = store.get_video_attempt(attempt_id)
    if not isinstance(attempt, dict):
        raise KeyError(attempt_id)
    if (attempt.get("status") != "running" or attempt.get("worker_id") != worker_id
            or attempt.get("attempt_count") != lease_epoch):
        raise PermissionError("checkpoint write requires the current owning worker and lease epoch")
    from muse.video_task_worker import VideoTaskWorker
    if not VideoTaskWorker._commander_approval_is_valid(store, queue, attempt):
        raise PermissionError("Commander approval is missing, stale, or not bound to this exact video attempt")
    resume = evaluate_video_checkpoint_resume(
        batches, store, attempt_id=attempt_id, worker_id=worker_id,
        lease_epoch=lease_epoch, workspace_root=workspace_root,
        expected_input_sha256=expected_input_sha256,
    )
    verified = set(resume["verified_steps"])
    if step_id in resume["same_epoch_rejected_steps"]:
        raise ValueError("an existing rejected checkpoint in this lease epoch cannot be overwritten")
    step = next((item for item in plan.steps if item.step_id == step_id), None)
    if step is None:
        raise ValueError("requested step is absent from the batch Commander plan")
    gate_id = plan.steps[0].step_id if plan.steps else None
    dependencies = set(step.depends_on) - ({gate_id} if gate_id else set())
    required = set(getattr(batch, "required_completed_steps", ()))
    if not (dependencies | required) <= verified:
        raise PermissionError("checkpoint write requires verified predecessor evidence")
    stage_id = dict(stage_pairs).get(step_id)
    if not isinstance(stage_id, str):
        raise ValueError("requested step has no fixed video stage mapping")
    output_sha256 = _artifact_sha256(Path(workspace_root), artifact_relative_path)
    checkpoint_key = checkpoint_template.format(attempt_id=attempt_id)
    metadata = {
        "artifact_relative_path": artifact_relative_path,
        **({"generation": generation_metadata} if generation_metadata else {}),
    }
    result = store.record_video_stage_checkpoint(
        attempt_id,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
        dag_sha256=dag_sha256,
        batch_id=batch_id,
        batch_fingerprint=batch_fingerprint,
        step_id=step_id,
        stage_id=stage_id,
        checkpoint_key=checkpoint_key,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        metadata=metadata,
    )
    return {
        "status": "checkpoint_persisted",
        "checkpoint": result,
        "verified_predecessors": sorted(dependencies | required),
        "execution_eligible": False,
        "dispatch_started": False,
    }


class VideoSceneCheckpointSession:
    """Attempt-scoped bridge for a scene builder's resume map and artifact callback."""

    def __init__(
        self,
        batches: Sequence[Any],
        store: Any,
        queue: Any,
        *,
        attempt_id: str,
        worker_id: str,
        lease_epoch: int,
        workspace_root: str | Path,
        expected_input_sha256: Mapping[str, str],
    ) -> None:
        if not isinstance(batches, (tuple, list)) or not batches:
            raise ValueError("batches must be a non-empty ordered sequence")
        self.batches = tuple(batches)
        self.store = store
        self.queue = queue
        self.attempt_id = attempt_id
        self.worker_id = worker_id
        self.lease_epoch = lease_epoch
        supplied_root = Path(workspace_root)
        if _is_link_or_reparse(supplied_root.lstat()):
            raise ValueError("workspace_root must not be a symlink or reparse point")
        self.workspace_root = supplied_root.resolve(strict=True)
        self.expected_input_sha256 = dict(expected_input_sha256)
        self.resume = evaluate_video_checkpoint_resume(
            self.batches, self.store, attempt_id=attempt_id, worker_id=worker_id,
            lease_epoch=lease_epoch, workspace_root=self.workspace_root,
            expected_input_sha256=self.expected_input_sha256,
        )
        self.resume_scene_metadata: dict[str, dict[str, Any]] = {}
        self.execution_eligible = False
        self.dispatch_started = False
        self.reusable_step_ids = tuple(self.resume["reusable_steps"])
        self.pending_step_ids = tuple(self.resume["missing_steps"])
        self.same_epoch_rejected_steps = tuple(self.resume["same_epoch_rejected_steps"])
        for step_id, checkpoint in self.resume["reusable_steps"].items():
            metadata = checkpoint.get("metadata")
            generation = metadata.get("generation") if isinstance(metadata, dict) else None
            if not isinstance(generation, dict) or generation.get("status") != "completed":
                raise ValueError(f"checkpoint {step_id} lacks trusted generation metadata")
            self.resume_scene_metadata[step_id] = dict(generation)

    def record(
        self,
        step_id: str,
        artifact_path: str | Path,
        generation_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        if step_id not in self.expected_input_sha256:
            raise ValueError("scene checkpoint step is not part of the current plan")
        if step_id in self.resume["same_epoch_rejected_steps"]:
            raise ValueError("an existing rejected checkpoint in this lease epoch cannot be overwritten")
        raw_path = Path(artifact_path)
        if any(part == ".." for part in raw_path.parts):
            raise ValueError("artifact path must not contain parent traversal")
        absolute = raw_path if raw_path.is_absolute() else self.workspace_root / raw_path
        try:
            relative_path = absolute.relative_to(self.workspace_root).as_posix()
        except ValueError as exc:
            raise ValueError("artifact path must be inside the current workspace") from exc
        batch = next((item for item in self.batches if step_id in getattr(item, "step_ids", ())), None)
        if batch is None:
            raise ValueError("scene checkpoint step is absent from all batches")
        return record_video_checkpoint_artifact(
            batch, self.store, self.queue, batches=self.batches,
            expected_input_sha256=self.expected_input_sha256,
            attempt_id=self.attempt_id, worker_id=self.worker_id,
            lease_epoch=self.lease_epoch, step_id=step_id,
            workspace_root=self.workspace_root, artifact_relative_path=relative_path,
            input_sha256=self.expected_input_sha256[step_id],
            artifact_metadata=generation_metadata,
        )
