from __future__ import annotations

import hashlib
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from pathlib import Path

from muse.commander_planner import CommanderPlan, PlannedStep
from muse.initiative_engine import InitiativeStore
from muse.video_checkpoint_adapter import evaluate_video_checkpoint_resume, record_video_checkpoint_artifact
from muse.video_checkpoint_adapter import (
    build_video_scene_checkpoint_plan,
    VideoSceneCheckpointSession,
    _is_link_or_reparse,
    prepare_video_attempt_staging_directory,
    cleanup_video_attempt_staging_directory,
)
from muse.video_studio import build_video_studio_batches


class VideoCheckpointAdapterTests(unittest.TestCase):
    @staticmethod
    def _plan():
        return CommanderPlan(
            plan_id="checkpoint-adapter-plan",
            goal="verify resume",
            source="unit-test",
            steps=(
                PlannedStep("preflight", "operator", "verify source", (), ()),
                PlannedStep("shot_001", "developer", "generate first shot", ("preflight",), ("comfyui_generation",)),
            ),
            parallel_waves=(("preflight",), ("shot_001",)),
        )

    @staticmethod
    def _write_checkpoint(store, attempt_id, batch, step_id, root, relative_path, input_digest):
        artifact = root / relative_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        payload = f"artifact:{step_id}".encode("utf-8")
        artifact.write_bytes(payload)
        stage_id = dict(batch.step_stage_ids)[step_id]
        return store.record_video_stage_checkpoint(
            attempt_id,
            worker_id="adapter-worker",
            lease_epoch=1,
            dag_sha256=batch.dag_sha256,
            batch_id=batch.batch_id,
            batch_fingerprint=batch.batch_fingerprint,
            step_id=step_id,
            stage_id=stage_id,
            checkpoint_key=f"video-studio:{attempt_id}:adapter:{batch.index:03d}",
            input_sha256=input_digest,
            output_sha256=hashlib.sha256(payload).hexdigest(),
            metadata={"artifact_relative_path": relative_path},
        )

    def test_scene_plan_maps_dynamic_shots_and_binds_inputs_separately_from_dag_identity(self):
        scenes = [
            {"prompt": f"scene {index}", "duration_seconds": 2.0, "seed": 1000 + index}
            for index in range(1, 21)
        ]
        first = build_video_scene_checkpoint_plan(
            "checkpoint scenes", scenes, negative_prompt="no artifacts",
            aspect_ratio="9:16", steps=12, recipe_sha256="a" * 64,
            builder_version="builder-1",
        )
        changed_scenes = [dict(item) for item in scenes]
        changed_scenes[0]["prompt"] = "changed scene prompt"
        changed = build_video_scene_checkpoint_plan(
            "checkpoint scenes", changed_scenes, negative_prompt="no artifacts",
            aspect_ratio="9:16", steps=12, recipe_sha256="a" * 64,
            builder_version="builder-1",
        )
        self.assertEqual(len(first["batches"]), 2)
        self.assertEqual(first["step_ids"], tuple(f"shot_{index:03d}" for index in range(1, 21)))
        self.assertTrue(all(set(dict(batch.step_stage_ids).values()) == {"shot_generation"} for batch in first["batches"]))
        self.assertEqual(first["batches"][0].dag_sha256, changed["batches"][0].dag_sha256)
        self.assertNotEqual(
            first["expected_input_sha256"]["shot_001"],
            changed["expected_input_sha256"]["shot_001"],
        )
        self.assertFalse(first["execution_eligible"])
        self.assertFalse(first["dispatch_started"])

    def test_attempt_staging_directory_is_durable_and_attempt_scoped(self):
        self.assertTrue(_is_link_or_reparse(SimpleNamespace(
            st_mode=stat.S_IFDIR, st_file_attributes=0x400,
        )))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = prepare_video_attempt_staging_directory(root, "a" * 32)
            second = prepare_video_attempt_staging_directory(root, "b" * 32)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())
            self.assertNotEqual(first, second)
            self.assertEqual(first.resolve().relative_to(root.resolve()).as_posix(), f"data/video_attempt_staging/{'a' * 32}")
            with self.assertRaisesRegex(ValueError, "attempt_id"):
                prepare_video_attempt_staging_directory(root, "../unsafe")

    def test_attempt_staging_cleanup_requires_committed_success_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt_id = "a" * 32
            attempt_root = prepare_video_attempt_staging_directory(root, attempt_id)
            epoch = attempt_root / "epoch_1"
            epoch.mkdir()
            (epoch / "shot_01.mp4").write_bytes(b"checkpoint")
            succeeded = {
                "status": "succeeded", "mp4_sha256": "1" * 64,
                "package_sha256": "2" * 64, "manifest_sha256": "3" * 64,
            }
            store = SimpleNamespace(get_video_attempt=lambda requested: succeeded)
            self.assertTrue(cleanup_video_attempt_staging_directory(root, attempt_id, store))
            self.assertFalse(attempt_root.exists())
            self.assertFalse(cleanup_video_attempt_staging_directory(root, attempt_id, store))

            blocked_root = prepare_video_attempt_staging_directory(root, "b" * 32)
            blocked_epoch = blocked_root / "epoch_1"
            blocked_epoch.mkdir()
            (blocked_epoch / "keep.bin").write_bytes(b"keep")
            running = {**succeeded, "status": "running"}
            blocked_store = SimpleNamespace(get_video_attempt=lambda requested: running)
            with self.assertRaisesRegex(ValueError, "only after Store success"):
                cleanup_video_attempt_staging_directory(root, "b" * 32, blocked_store)
            self.assertTrue(blocked_root.exists())

            link_root = prepare_video_attempt_staging_directory(root, "c" * 32)
            try:
                (link_root / "outside-link").symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable on this host")
            with self.assertRaisesRegex(ValueError, "must not contain links"):
                cleanup_video_attempt_staging_directory(root, "c" * 32, store)
            self.assertTrue(link_root.exists())

    def test_reuses_only_predecessor_safe_checkpoints_with_matching_input_and_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = build_video_studio_batches(self._plan(), max_steps=3)[0]
            with InitiativeStore(root / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    "adapter-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-adapter", recipe_sha256="1" * 64,
                )
                store.claim_next_video_attempt("adapter-worker", lease_seconds=120)
                self._write_checkpoint(store, "adapter-attempt", batch, "preflight", root, "artifacts/preflight.bin", "2" * 64)
                self._write_checkpoint(store, "adapter-attempt", batch, "shot_001", root, "artifacts/shot.bin", "3" * 64)

                result = evaluate_video_checkpoint_resume(
                    (batch,), store, attempt_id="adapter-attempt", worker_id="adapter-worker",
                    lease_epoch=1, workspace_root=root,
                    expected_input_sha256={"preflight": "2" * 64, "shot_001": "3" * 64},
                )
                self.assertEqual(result["verified_steps"], ["preflight", "shot_001"])
                self.assertEqual(result["status"], "verified_resume_evidence")
                self.assertFalse(result["execution_eligible"])
                self.assertFalse(result["dispatch_started"])

                (root / "artifacts" / "preflight.bin").write_bytes(b"tampered")
                tampered = evaluate_video_checkpoint_resume(
                    (batch,), store, attempt_id="adapter-attempt", worker_id="adapter-worker",
                    lease_epoch=1, workspace_root=root,
                    expected_input_sha256={"preflight": "2" * 64, "shot_001": "3" * 64},
                )
                self.assertEqual(tampered["missing_steps"], ["preflight"])
                self.assertEqual(tampered["blocked_steps"], ["shot_001"])
                self.assertEqual(tampered["rejected"]["preflight"], "artifact_digest_mismatch")

    def test_later_batch_cannot_resume_when_required_predecessor_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batches = build_video_studio_batches(self._plan(), max_steps=2)
            self.assertEqual(len(batches), 2)
            first, second = batches
            with InitiativeStore(root / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    "adapter-boundary-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-boundary", recipe_sha256="4" * 64,
                )
                store.claim_next_video_attempt("adapter-worker", lease_seconds=120)
                self._write_checkpoint(store, "adapter-boundary-attempt", second, "shot_001", root, "artifacts/shot.bin", "5" * 64)
                result = evaluate_video_checkpoint_resume(
                    batches, store, attempt_id="adapter-boundary-attempt", worker_id="adapter-worker",
                    lease_epoch=1, workspace_root=root,
                    expected_input_sha256={"preflight": "6" * 64, "shot_001": "5" * 64},
                )
                self.assertEqual(result["missing_steps"], ["preflight"])
                self.assertEqual(result["blocked_steps"], ["shot_001"])
                self.assertEqual(result["verified_steps"], [])
                self.assertEqual(result["rejected"]["shot_001"], "required_predecessor_not_verified")

    def test_unsafe_artifact_path_is_never_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = build_video_studio_batches(self._plan(), max_steps=3)[0]
            with InitiativeStore(root / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    "adapter-path-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-path", recipe_sha256="7" * 64,
                )
                store.claim_next_video_attempt("adapter-worker", lease_seconds=120)
                payload = b"outside artifact"
                outside = root.parent / "outside-video-checkpoint.bin"
                outside.write_bytes(payload)
                try:
                    store.record_video_stage_checkpoint(
                        "adapter-path-attempt", worker_id="adapter-worker", lease_epoch=1,
                        dag_sha256=batch.dag_sha256, batch_id=batch.batch_id,
                        batch_fingerprint=batch.batch_fingerprint, step_id="preflight",
                        stage_id=dict(batch.step_stage_ids)["preflight"],
                        checkpoint_key="video-studio:adapter-path-attempt:adapter:001",
                        input_sha256="8" * 64, output_sha256=hashlib.sha256(payload).hexdigest(),
                        metadata={"artifact_relative_path": "../outside-video-checkpoint.bin"},
                    )
                    result = evaluate_video_checkpoint_resume(
                        (batch,), store, attempt_id="adapter-path-attempt", worker_id="adapter-worker",
                        lease_epoch=1, workspace_root=root,
                        expected_input_sha256={"preflight": "8" * 64, "shot_001": "9" * 64},
                    )
                    self.assertEqual(result["missing_steps"], ["preflight"])
                    self.assertEqual(result["rejected"]["preflight"], "artifact_missing_or_unsafe")
                finally:
                    outside.unlink(missing_ok=True)



    def test_artifact_writer_requires_commander_approval_and_persists_only_verified_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = build_video_studio_batches(self._plan(), max_steps=3)[0]
            preflight = root / "artifacts" / "preflight.bin"
            preflight.parent.mkdir(parents=True)
            preflight.write_bytes(b"completed preflight")
            shot = root / "artifacts" / "shot.bin"
            shot.write_bytes(b"completed shot")
            expected = {"preflight": "b" * 64, "shot_001": "c" * 64}
            with InitiativeStore(root / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    "writer-approved-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-writer", recipe_sha256="a" * 64,
                )
                store.claim_next_video_attempt("adapter-worker", lease_seconds=120)
                with patch("muse.video_task_worker.VideoTaskWorker._commander_approval_is_valid", return_value=True):
                    result = record_video_checkpoint_artifact(
                        batch, store, object(), batches=(batch,), expected_input_sha256=expected,
                        attempt_id="writer-approved-attempt", worker_id="adapter-worker", lease_epoch=1,
                        step_id="preflight", workspace_root=root,
                        artifact_relative_path="artifacts/preflight.bin", input_sha256="b" * 64,
                    )
                    shot_result = record_video_checkpoint_artifact(
                        batch, store, object(), batches=(batch,), expected_input_sha256=expected,
                        attempt_id="writer-approved-attempt", worker_id="adapter-worker", lease_epoch=1,
                        step_id="shot_001", workspace_root=root,
                        artifact_relative_path="artifacts/shot.bin", input_sha256="c" * 64,
                        artifact_metadata={
                            "status": "completed", "width": 832, "height": 480,
                            "fps": 16.0, "frames": 48, "prompt_id": "prompt-1",
                            "model": "Wan 2.1 T2V 1.3B",
                        },
                    )
                self.assertEqual(result["status"], "checkpoint_persisted")
                self.assertEqual(result["verified_predecessors"], [])
                self.assertEqual(shot_result["verified_predecessors"], ["preflight"])
                self.assertEqual(shot_result["checkpoint"]["metadata"]["generation"]["frames"], 48)
                self.assertFalse(result["execution_eligible"])
                self.assertFalse(result["dispatch_started"])
                self.assertEqual(result["checkpoint"]["output_sha256"], hashlib.sha256(b"completed preflight").hexdigest())
                resume = evaluate_video_checkpoint_resume(
                    (batch,), store, attempt_id="writer-approved-attempt", worker_id="adapter-worker",
                    lease_epoch=1, workspace_root=root, expected_input_sha256=expected,
                )
                self.assertEqual(resume["verified_steps"], ["preflight", "shot_001"])
                self.assertEqual(resume["reusable_steps"]["shot_001"]["metadata"]["generation"]["prompt_id"], "prompt-1")

    def test_scene_checkpoint_session_persists_and_rehydrates_generation_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt_id = "e" * 32
            staging = prepare_video_attempt_staging_directory(root, attempt_id)
            artifact = staging / "shot_001.mp4"
            artifact.write_bytes(b"verified generated shot")
            plan = build_video_scene_checkpoint_plan(
                "scene session", [
                    {"prompt": "first shot", "duration_seconds": 2.0, "seed": 17},
                    {"prompt": "second shot", "duration_seconds": 2.0, "seed": 18},
                ], negative_prompt="", aspect_ratio="16:9", steps=12,
                recipe_sha256="f" * 64, builder_version="builder-1",
            )
            with InitiativeStore(root / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    attempt_id, mode="prompt_to_video", approval_task_id="commander-scene-session",
                    recipe_sha256="f" * 64,
                )
                store.claim_next_video_attempt("adapter-worker", lease_seconds=120)
                generation = {
                    "status": "completed", "width": 832, "height": 480,
                    "fps": 16.0, "frames": 32, "prompt_id": "scene-prompt-1",
                    "model": "Wan 2.1 T2V 1.3B",
                }
                with patch("muse.video_task_worker.VideoTaskWorker._commander_approval_is_valid", return_value=True):
                    session = VideoSceneCheckpointSession(
                        plan["batches"], store, object(), attempt_id=attempt_id,
                        worker_id="adapter-worker", lease_epoch=1, workspace_root=root,
                        expected_input_sha256=plan["expected_input_sha256"],
                    )
                    self.assertEqual(session.resume_scene_metadata, {})
                    self.assertFalse(session.execution_eligible)
                    self.assertFalse(session.dispatch_started)
                    self.assertEqual(session.pending_step_ids, ("shot_001", "shot_002"))
                    result = session.record("shot_001", artifact, generation)
                    self.assertEqual(result["status"], "checkpoint_persisted")
                    resumed = VideoSceneCheckpointSession(
                        plan["batches"], store, object(), attempt_id=attempt_id,
                        worker_id="adapter-worker", lease_epoch=1, workspace_root=root,
                        expected_input_sha256=plan["expected_input_sha256"],
                    )
                self.assertEqual(resumed.resume_scene_metadata["shot_001"]["frames"], 32)
                self.assertEqual(resumed.reusable_step_ids, ("shot_001",))
                self.assertEqual(resumed.resume_scene_metadata["shot_001"]["prompt_id"], "scene-prompt-1")

    def test_rejected_old_epoch_checkpoint_can_be_replaced_but_same_epoch_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt_id = "d" * 32
            staging = prepare_video_attempt_staging_directory(root, attempt_id)
            old_artifact = staging / "shot_001_old.mp4"
            new_artifact = staging / "shot_001_new.mp4"
            old_artifact.write_bytes(b"first epoch shot")
            new_artifact.write_bytes(b"new epoch regenerated shot")
            old_scenes = [
                {"prompt": "old prompt", "duration_seconds": 2.0, "seed": 31},
                {"prompt": "second shot", "duration_seconds": 2.0, "seed": 32},
            ]
            new_scenes = [dict(item) for item in old_scenes]
            new_scenes[0]["prompt"] = "new prompt"
            kwargs = dict(
                negative_prompt="", aspect_ratio="16:9", steps=12,
                recipe_sha256="c" * 64, builder_version="builder-1",
            )
            old_plan = build_video_scene_checkpoint_plan("retry scene", old_scenes, **kwargs)
            new_plan = build_video_scene_checkpoint_plan("retry scene", new_scenes, **kwargs)
            generation_old = {
                "status": "completed", "width": 832, "height": 480,
                "fps": 16.0, "frames": 32, "prompt_id": "old-prompt",
            }
            generation_new = {**generation_old, "prompt_id": "new-prompt"}
            with InitiativeStore(root / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    attempt_id, mode="prompt_to_video", approval_task_id="commander-retry-scene",
                    recipe_sha256="c" * 64,
                )
                store.claim_next_video_attempt("worker-a", lease_seconds=3600)
                with patch("muse.video_task_worker.VideoTaskWorker._commander_approval_is_valid", return_value=True):
                    first = VideoSceneCheckpointSession(
                        old_plan["batches"], store, object(), attempt_id=attempt_id,
                        worker_id="worker-a", lease_epoch=1, workspace_root=root,
                        expected_input_sha256=old_plan["expected_input_sha256"],
                    )
                    first.record("shot_001", old_artifact, generation_old)
                    old_artifact.write_bytes(b"tampered after checkpoint")
                    same_epoch = VideoSceneCheckpointSession(
                        old_plan["batches"], store, object(), attempt_id=attempt_id,
                        worker_id="worker-a", lease_epoch=1, workspace_root=root,
                        expected_input_sha256=old_plan["expected_input_sha256"],
                    )
                    self.assertEqual(same_epoch.same_epoch_rejected_steps, ("shot_001",))
                    with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
                        same_epoch.record("shot_001", old_artifact, generation_old)
                store.fail_video_attempt(
                    attempt_id, worker_id="worker-a", lease_epoch=1,
                    failure_code="video.test_retry", now=__import__("time").time(),
                )
                store.retry_failed_video_attempt(attempt_id, max_attempts=2)
                store.claim_next_video_attempt("worker-b", lease_seconds=3600)
                with patch("muse.video_task_worker.VideoTaskWorker._commander_approval_is_valid", return_value=True):
                    second = VideoSceneCheckpointSession(
                        new_plan["batches"], store, object(), attempt_id=attempt_id,
                        worker_id="worker-b", lease_epoch=2, workspace_root=root,
                        expected_input_sha256=new_plan["expected_input_sha256"],
                    )
                    self.assertEqual(second.same_epoch_rejected_steps, ())
                    self.assertIn("shot_001", second.resume["rejected"])
                    self.assertIn("shot_001", second.pending_step_ids)
                    second.record("shot_001", new_artifact, generation_new)
                    resumed = VideoSceneCheckpointSession(
                        new_plan["batches"], store, object(), attempt_id=attempt_id,
                        worker_id="worker-b", lease_epoch=2, workspace_root=root,
                        expected_input_sha256=new_plan["expected_input_sha256"],
                    )
                self.assertEqual(resumed.resume_scene_metadata["shot_001"]["prompt_id"], "new-prompt")
                history = store.list_video_stage_checkpoints(
                    attempt_id, worker_id="worker-b", lease_epoch=2,
                    dag_sha256=new_plan["batches"][0].dag_sha256,
                )
                self.assertEqual({row["lease_epoch"] for row in history}, {1, 2})

    def test_artifact_writer_fails_closed_without_valid_commander_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = build_video_studio_batches(self._plan(), max_steps=3)[0]
            artifact = root / "preflight.bin"
            artifact.write_bytes(b"completed preflight")
            with InitiativeStore(root / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    "writer-unapproved-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-unapproved", recipe_sha256="d" * 64,
                )
                store.claim_next_video_attempt("adapter-worker", lease_seconds=120)
                with patch("muse.video_task_worker.VideoTaskWorker._commander_approval_is_valid", return_value=False):
                    with self.assertRaisesRegex(PermissionError, "Commander approval"):
                        record_video_checkpoint_artifact(
                            batch, store, object(), batches=(batch,), expected_input_sha256={"preflight": "e" * 64},
                            attempt_id="writer-unapproved-attempt", worker_id="adapter-worker", lease_epoch=1,
                            step_id="preflight", workspace_root=root,
                            artifact_relative_path="preflight.bin", input_sha256="e" * 64,
                        )
                self.assertEqual(store.list_video_stage_checkpoints(
                    "writer-unapproved-attempt", worker_id="adapter-worker", lease_epoch=1,
                    dag_sha256=batch.dag_sha256, batch_id=batch.batch_id,
                    batch_fingerprint=batch.batch_fingerprint,
                ), [])

if __name__ == "__main__":
    unittest.main()
