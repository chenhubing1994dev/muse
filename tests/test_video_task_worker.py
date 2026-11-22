from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from muse.commander_planner import CommanderPlan, PlannedStep
from muse.initiative_engine import InitiativeStore
from muse.task_queue import TaskQueue
from muse.video_commander_admission import VideoAdmissionRequest
from muse.video_task_worker import VideoTaskWorker, _VideoAttemptLeaseHeartbeat
from muse.video_recipe_trace import build_video_recipe_trace
from muse.video_studio import build_video_studio_batches

class FakeVideoBuilder:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "validated": True, "initiative_id": "a" * 32, "version_id": "b" * 20,
            "mp4_sha256": "c" * 64, "package_sha256": "d" * 64,
            "manifest_sha256": "e" * 64,
            "relative_path": "data/products/example/version", "quality_gate": {"status": "passed"},
        }
        self.error = error
        self.calls = []
        self.dispatched_steps = []
        self.verify_calls = []
        self.verify_overrides = {}
        self.lease_context = SimpleNamespace(
            lease_id="fake-gpu-lease", owner_id="fake-gpu-owner",
            coordination={"mode": "test"}, assert_valid=lambda: None,
        )

    def build_from_prompt(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        guard = kwargs.get("generation_guard")
        if callable(guard):
            guard(self.lease_context)
        self.dispatched_steps.append("shot_001")
        return dict(self.result)

    def build_from_scenes(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        guard = kwargs.get("scene_generation_guard")
        scenes = kwargs.get("scenes") or (args[1] if len(args) > 1 else [])
        reusable = set(kwargs.get("reusable_step_ids", ()))
        if callable(guard):
            for index, scene in enumerate(scenes, start=1):
                step_id = f"shot_{index:03d}"
                if step_id not in reusable:
                    if callable(guard):
                        guard(step_id, scene, self.lease_context)
                    self.dispatched_steps.append(step_id)
        return dict(self.result)


    def verify_path(self, products_root, initiative_id, version_id):
        self.verify_calls.append((Path(products_root), initiative_id, version_id))
        verified = {
            "validated": True,
            "initiative_id": self.result.get("initiative_id"),
            "version_id": self.result.get("version_id"),
            "relative_path": self.result.get("relative_path"),
            "mp4_sha256": self.result.get("mp4_sha256"),
            "package_sha256": self.result.get("package_sha256"),
            "manifest_sha256": self.result.get("manifest_sha256"),
        }
        verified.update(self.verify_overrides)
        return verified


class FakeAdmissionProvider:
    def __init__(self):
        self.issued = []
        self.requests = []

    def issue_video_admission_grant(self, store, request):
        grant = store.create_video_admission_grant(
            request.attempt_id, worker_id=request.worker_id, lease_epoch=request.lease_epoch,
            input_sha256=request.input_sha256, dag_sha256=request.dag_sha256,
            batch_id=request.batch_id, batch_fingerprint=request.batch_fingerprint,
            handler_digest=hashlib.sha256(b"test-handler").hexdigest(),
            resource_digest=hashlib.sha256(b"test-resource").hexdigest(),
            cost_digest=hashlib.sha256(b"test-cost").hexdigest(),
            approval_task_id=request.approval_task_id, approval_event_id=request.approval_event_id,
            approved_by=request.approved_by, scope=request.scope, role=request.role,
            required_capabilities=request.required_capabilities, ttl_seconds=60,
        )
        self.issued.append(dict(grant))
        self.requests.append(request)
        return grant


def fake_admission_request_factory(
    *, attempt, video_brief, step_id, input_sha256, approval_evidence,
    actual_handler, workspace_root, commander_plan, commander_batches,
    admission_batch, scene_context, lease_context=None,
):
    batch = admission_batch
    return VideoAdmissionRequest(
        attempt_id=attempt["attempt_id"], worker_id=attempt["worker_id"],
        lease_epoch=attempt["attempt_count"], recipe_sha256=attempt["recipe_sha256"],
        input_sha256=input_sha256, dag_sha256=batch.dag_sha256, batch_id=batch.batch_id,
        batch_fingerprint=batch.batch_fingerprint, scope="video_studio", role="developer",
        required_capabilities=("comfyui_generation",),
        approval_task_id=approval_evidence["approval_task_id"],
        approval_event_id=approval_evidence["approval_event_id"],
        approved_by=approval_evidence["approved_by"], approval_evidence=dict(approval_evidence),
        batch=batch, plan=commander_plan, step_ids=(step_id,), all_batches=commander_batches,
        expected_input_sha256={step_id: input_sha256}, workspace_root=Path(workspace_root).resolve(),
        actual_handler=actual_handler,
        resource_context={
            "requested_generation": dict(scene_context),
            "gpu_lease": ({
                "status": "active", "lease_id": lease_context.lease_id,
                "owner_id": lease_context.owner_id,
                "coordination": dict(lease_context.coordination),
            } if lease_context is not None else {"status": "missing"}),
        },
        cost_context={"requested_generation": dict(scene_context)},
    )


class VideoTaskWorkerTests(unittest.TestCase):
    def _queue_approved_attempt(self, root, initiative_id, attempt_id, video_brief):
        task_database = root / "data" / "tasks" / "muse.sqlite3"
        digest = hashlib.sha256(json.dumps(video_brief, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        payload = {
            "kind": "business_opportunity_video_approval",
            "opportunity_id": "test-opportunity",
            "initiative_id": initiative_id,
            "attempt_id": attempt_id,
            "video_brief_sha256": digest,
            "video_brief": video_brief,
            "approval_scope": "one_video_attempt_only",
        }
        with TaskQueue(task_database) as queue:
            ticket = queue.enqueue(
                "Approve test video", payload, require_approval=True,
                idempotency_key=f"test-video-approval:{attempt_id}", workload_class="media",
            )
            queue.approve(ticket["id"], approved_by="test-operator")
        with InitiativeStore(root / "data" / "initiatives" / "muse.sqlite3") as store:
            store.start_video_attempt(
                attempt_id, mode="prompt_to_video", initiative_id=initiative_id,
                approval_task_id=ticket["id"],
                recipe_sha256=video_brief.get("recipe_sha256") or hashlib.sha256(
                    json.dumps(video_brief, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest(),
            )
        return ticket["id"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "data" / "initiatives" / "muse.sqlite3"
        self.database.parent.mkdir(parents=True)
        self.initiative_id = None
        video_brief = {
            "title": "测试影片", "prompt": "source grounded story", "negative_prompt": "fake facts",
            "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
        }
        with InitiativeStore(self.database) as store:
            item = store.propose(
                "研究创作短片", kind="creative_output", creator_value="研究变成作品",
                concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                first_step="生成预览", expected_output="本地视频产品",
            )
            self.initiative_id = item["id"]
        self.approval_task_id = self._queue_approved_attempt(
            self.root, self.initiative_id, "worker-test", video_brief,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _worker(self, root=None, **kwargs):
        provider = kwargs.pop("admission_provider", FakeAdmissionProvider())
        request_factory = kwargs.pop("admission_request_factory", fake_admission_request_factory)
        return VideoTaskWorker(
            root or self.root, admission_provider=provider,
            admission_request_factory=request_factory, **kwargs,
        )


    def test_worker_fails_closed_before_claim_when_commander_grant_provider_is_missing(self):
        builder = FakeVideoBuilder()
        result = VideoTaskWorker(self.root, builder=builder, worker_id="missing-admission").run_once()
        self.assertEqual(result, {
            "status": "blocked_admission", "claimed": False,
            "reason": "commander_admission_provider_missing",
        })
        self.assertEqual(builder.calls, [])
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
        self.assertEqual(attempt["status"], "queued")

    def test_worker_rejects_missing_live_gpu_lease_before_issuing_a_commander_grant(self):
        builder = FakeVideoBuilder()
        builder.lease_context = None
        provider = FakeAdmissionProvider()
        result = self._worker(
            builder=builder, worker_id="missing-gpu-lease", admission_provider=provider,
        ).run_once()
        self.assertEqual(result["status"], "blocked_admission")
        self.assertEqual(provider.issued, [])
        self.assertEqual(provider.requests, [])
        self.assertEqual(builder.dispatched_steps, [])
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
        self.assertEqual(attempt["status"], "queued")

    def test_worker_consumes_one_shot_grant_per_scene_before_each_wan_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            attempt_id = "e" * 32
            video_brief = {
                "title": "逐镜头授权测试", "prompt": "Two scenes",
                "negative_prompt": "no artifacts", "aspect_ratio": "16:9",
                "duration_seconds": 4, "steps": 8, "recipe_sha256": "b" * 64,
                "scenes": [
                    {"prompt": "First", "duration_seconds": 2, "seed": 1},
                    {"prompt": "Second", "duration_seconds": 2, "seed": 2},
                ],
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "One-shot grant", kind="creative_output", creator_value="逐镜头授权",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="逐镜头生成", expected_output="两镜头视频",
                )
            self._queue_approved_attempt(root, initiative["id"], attempt_id, video_brief)
            lease_context = SimpleNamespace(
                lease_id="wan-lease-test", owner_id="wan-owner-test",
                coordination={"mode": "model_offline_supervisor_not_running"},
                assert_valid=lambda: None,
            )

            class LeasePassingBuilder(FakeVideoBuilder):
                def build_from_scenes(inner_self, *args, **kwargs):
                    inner_self.calls.append((args, kwargs))
                    scenes = kwargs.get("scenes") or (args[1] if len(args) > 1 else [])
                    guard = kwargs["scene_generation_guard"]
                    for index, scene in enumerate(scenes, start=1):
                        guard(f"shot_{index:03d}", scene, lease_context)
                        inner_self.dispatched_steps.append(f"shot_{index:03d}")
                    return dict(inner_self.result)

            captured_lease_contexts = []

            def capturing_request_factory(**kwargs):
                captured_lease_contexts.append(kwargs.get("lease_context"))
                return fake_admission_request_factory(**kwargs)

            builder = LeasePassingBuilder()
            provider = FakeAdmissionProvider()
            result = self._worker(
                root, builder=builder, worker_id="grant-worker", admission_provider=provider,
                admission_request_factory=capturing_request_factory,
            ).run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(builder.dispatched_steps, ["shot_001", "shot_002"])
            self.assertEqual(len(result["admission_grants_consumed"]), 2)
            self.assertEqual(len(provider.issued), 2)
            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(captured_lease_contexts, [lease_context, lease_context])
            self.assertEqual(len({request.dag_sha256 for request in provider.requests}), 1)
            self.assertEqual(
                [request.batch.step_ids for request in provider.requests],
                [("shot_001",), ("shot_002",)],
            )
            self.assertEqual(
                [tuple(step.step_id for step in request.plan.steps) for request in provider.requests],
                [("shot_001", "shot_002"), ("shot_001", "shot_002")],
            )
            self.assertTrue(all(
                any(batch is request.batch for batch in request.all_batches)
                and not request.batch.required_completed_steps
                for request in provider.requests
            ))
            self.assertEqual(len({request.batch.batch_fingerprint for request in provider.requests}), 2)
            with InitiativeStore(database) as store:
                rows = store.connection.execute(
                    "SELECT status, input_sha256 FROM initiative_video_admission_grants WHERE attempt_id=? ORDER BY created_at",                    (attempt_id,),
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual([row["status"] for row in rows], ["consumed", "consumed"])
            self.assertTrue(all(len(row["input_sha256"]) == 64 for row in rows))

    def test_worker_revokes_grant_if_commander_approval_changes_before_consume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            attempt_id = "f" * 32
            video_brief = {
                "title": "授权撤销测试", "prompt": "One scene",
                "aspect_ratio": "16:9", "duration_seconds": 2, "steps": 8,
                "recipe_sha256": "c" * 64,
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Revoke grant", kind="creative_output", creator_value="审批撤销",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成预览", expected_output="不应派发",
                )
            self._queue_approved_attempt(root, initiative["id"], attempt_id, video_brief)

            class RevokingProvider(FakeAdmissionProvider):
                def issue_video_admission_grant(inner_self, store, request):
                    grant = super().issue_video_admission_grant(store, request)
                    with TaskQueue(root / "data" / "tasks" / "muse.sqlite3") as queue:
                        queue._record_event(
                            request.approval_task_id, "human.decision", "test-operator",
                            {"decision": "rejected"},
                        )
                        queue._connection.commit()
                    return grant

            builder = FakeVideoBuilder()
            provider = RevokingProvider()
            result = self._worker(root, builder=builder, worker_id="revoke-worker", admission_provider=provider).run_once()
            self.assertEqual(result["status"], "blocked_admission")
            self.assertEqual(builder.dispatched_steps, [])
            self.assertEqual(result["admission_grants_consumed"], [])
            with InitiativeStore(database) as store:
                row = store.connection.execute(
                    "SELECT status FROM initiative_video_admission_grants WHERE attempt_id=?", (attempt_id,),                ).fetchone()
                attempt = store.get_video_attempt(attempt_id)
            self.assertEqual(row["status"], "revoked")
            self.assertEqual(attempt["status"], "queued")

    def test_lease_heartbeat_renews_the_exact_worker_epoch(self):
        renewed = Event()
        calls = []

        class Store:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def renew_video_attempt_lease(self, attempt_id, *, worker_id, lease_epoch, lease_seconds):
                calls.append((attempt_id, worker_id, lease_epoch, lease_seconds))
                renewed.set()
                return {"status": "renewed"}

        heartbeat = _VideoAttemptLeaseHeartbeat(
            self.database, "heartbeat-attempt", "heartbeat-worker", 7,
            lease_seconds=30, interval_seconds=0.01,
        )
        with patch("muse.video_task_worker.InitiativeStore", return_value=Store()):
            heartbeat.start()
            self.assertTrue(renewed.wait(1.0), "lease heartbeat did not renew")
            heartbeat.stop()

        self.assertFalse(heartbeat.lease_lost)
        self.assertEqual(calls, [("heartbeat-attempt", "heartbeat-worker", 7, 30)])

    def test_lease_heartbeat_fails_closed_when_epoch_renewal_is_rejected(self):
        attempted = Event()

        class Store:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def renew_video_attempt_lease(self, *_args, **_kwargs):
                attempted.set()
                raise ValueError("lease expired or ownership changed")

        heartbeat = _VideoAttemptLeaseHeartbeat(
            self.database, "heartbeat-attempt", "heartbeat-worker", 7,
            lease_seconds=30, interval_seconds=0.01,
        )
        with patch("muse.video_task_worker.InitiativeStore", return_value=Store()):
            heartbeat.start()
            self.assertTrue(attempted.wait(1.0), "lease heartbeat did not attempt renewal")
            heartbeat.stop()

        self.assertTrue(heartbeat.lease_lost)
        self.assertEqual(heartbeat.error_type, "ValueError")

    def test_worker_does_not_start_builder_after_lease_renewal_is_lost_in_preflight(self):
        class LostHeartbeat:
            lease_lost = True
            error_type = "ValueError"

            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                pass

            def stop(self):
                pass

        builder = FakeVideoBuilder()
        with patch("muse.video_task_worker._VideoAttemptLeaseHeartbeat", LostHeartbeat):
            result = self._worker(
                self.root, builder=builder, worker_id="lost-lease-worker",
            ).run_once()

        self.assertEqual(result["status"], "lease_lost")
        self.assertFalse(result["failure_recorded"])
        self.assertEqual(builder.calls, [])
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
        self.assertEqual(attempt["status"], "running")
        self.assertEqual(attempt["worker_id"], "lost-lease-worker")

    def test_worker_routes_approved_scene_brief_to_multi_scene_builder_with_stable_seeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            video_brief = {
                "title": "多镜头测试", "prompt": "A concise summary of the story",
                "negative_prompt": "避免全片出现文字",
                "aspect_ratio": "16:9", "duration_seconds": 4, "steps": 8,
                "recipe_sha256": "a" * 64,
                "scenes": [
                    {"prompt": "Opening: ancient observatory above clouds", "duration_seconds": 2,
                     "negative_prompt": "避免额外人物"},
                    {"prompt": "Close-up: bronze armillary sphere at sunrise", "duration_seconds": 2,
                     "negative_prompt": "避免错误星图"},
                ],
            }
            with InitiativeStore(database) as store:
                item = store.propose(
                    "多镜头 Worker", kind="creative_output", creator_value="测试多镜头接线",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成镜头", expected_output="多镜头 MP4",
                )
            self._queue_approved_attempt(root, item["id"], "e" * 32, video_brief)
            builder = FakeVideoBuilder()
            result = self._worker(root, builder=builder, worker_id="scene-worker").run_once()
            self.assertEqual(result["status"], "succeeded", result)
            self.assertEqual(len(builder.calls), 1)
            args, kwargs = builder.calls[0]
            self.assertEqual(args[0], "多镜头测试")
            self.assertEqual(len(kwargs["scenes"]), 2)
            self.assertNotEqual(kwargs["scenes"][0]["seed"], kwargs["scenes"][1]["seed"])
            self.assertTrue(all(isinstance(scene["seed"], int) for scene in kwargs["scenes"]))
            self.assertEqual(kwargs["scenes"][0]["duration_seconds"], 2.0)
            self.assertEqual(kwargs["negative_prompt"], "避免全片出现文字")
            self.assertEqual(kwargs["scenes"][0]["negative_prompt"], "避免额外人物")
            self.assertEqual(kwargs["scenes"][1]["negative_prompt"], "避免错误星图")
            self.assertFalse((root / "data" / "video_attempt_staging" / ("e" * 32)).exists())

    def test_worker_rejects_combined_scene_negative_prompt_over_limit_before_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            video_brief = {
                "title": "负面提示词长度门禁", "prompt": "A two-shot story",
                "negative_prompt": "g" * 600,
                "aspect_ratio": "16:9", "duration_seconds": 4, "steps": 8,
                "recipe_sha256": "a" * 64,
                "scenes": [
                    {"prompt": "Opening", "duration_seconds": 2, "negative_prompt": "s" * 401},
                    {"prompt": "Closing", "duration_seconds": 2},
                ],
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Negative prompt length", kind="creative_output", creator_value="reject overlong conditioning",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="preflight", expected_output="blocked before builder",
                )
            self._queue_approved_attempt(root, initiative["id"], "a" * 32, video_brief)
            builder = FakeVideoBuilder()
            result = self._worker(root, builder=builder, worker_id="negative-limit-worker").run_once()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(builder.calls, [])
    def test_checkpoint_scene_fingerprint_changes_when_scene_negative_prompt_changes(self):
        from muse.video_task_worker import _checkpoint_scene_fingerprint_inputs

        original = [{
            "prompt": "A bronze armillary sphere at sunrise", "duration_seconds": 2.0,
            "seed": 101, "negative_prompt": "no extra people",
        }]
        changed = [{**original[0], "negative_prompt": "no labels or text"}]
        self.assertNotEqual(
            _checkpoint_scene_fingerprint_inputs(original),
            _checkpoint_scene_fingerprint_inputs(changed),
        )
        self.assertEqual(
            _checkpoint_scene_fingerprint_inputs([{"prompt": original[0]["prompt"], "duration_seconds": 2.0, "seed": 101}]),
            [{"prompt": original[0]["prompt"], "duration_seconds": 2.0, "seed": 101}],
        )

    def test_worker_persists_scene_checkpoint_and_reuses_it_after_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            video_brief = {
                "recipe_sha256": "a" * 64,
                "title": "断点恢复测试", "prompt": "A two-shot cinematic story",
                "aspect_ratio": "16:9", "duration_seconds": 4, "steps": 8,
                "recipe_sha256": "b" * 64,
                "scenes": [
                    {"prompt": "Opening shot", "duration_seconds": 2, "seed": 101,
                     "negative_prompt": "opening-only exclusion"},
                    {"prompt": "Closing shot", "duration_seconds": 2, "seed": 202,
                     "negative_prompt": "closing-only exclusion"},
                ],
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Checkpoint worker", kind="creative_output", creator_value="持久镜头检查点",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成两镜头", expected_output="可恢复视频",
                )
            attempt_id = "f" * 32
            self._queue_approved_attempt(root, initiative["id"], attempt_id, video_brief)
            artifact_bytes = bytes.fromhex("000000186674797069736f6d66697874757265")
            generation = {
                "status": "completed", "prompt_id": "wan-checkpoint",
                "model": "Wan 2.1 T2V 1.3B", "width": 512, "height": 288,
                "fps": 12.0, "frames": 25,
            }

            class CrashAfterFirstCheckpoint(FakeVideoBuilder):
                def build_from_scenes(inner_self, *args, **kwargs):
                    inner_self.calls.append((args, kwargs))
                    kwargs["scene_generation_guard"]("shot_001", kwargs["scenes"][0], inner_self.lease_context)
                    staging = Path(kwargs["staging_directory"])
                    first = staging / "shot_01.mp4"
                    first.write_bytes(artifact_bytes)
                    kwargs["scene_checkpoint_callback"]("shot_001", first, generation)
                    raise RuntimeError("simulated process interruption after durable first-shot checkpoint")

            builder = CrashAfterFirstCheckpoint()
            first_result = self._worker(root, builder=builder, worker_id="checkpoint-worker").run_once()
            self.assertEqual(first_result["status"], "failed")
            with InitiativeStore(database) as store:
                failed_attempt = store.get_video_attempt(attempt_id)
                self.assertEqual(failed_attempt["status"], "failed")
                self.assertEqual(failed_attempt["attempt_count"], 1)
                store.retry_failed_video_attempt(attempt_id, max_attempts=2)
            self.assertTrue((root / "data" / "video_attempt_staging" / attempt_id).is_dir())
            observed_resume = {}

            class ResumeSecondShot(FakeVideoBuilder):
                def build_from_scenes(inner_self, *args, **kwargs):
                    inner_self.calls.append((args, kwargs))
                    kwargs["scene_generation_guard"]("shot_002", kwargs["scenes"][1], inner_self.lease_context)
                    observed_resume["reusable_step_ids"] = tuple(kwargs.get("reusable_step_ids", ()))
                    observed_resume["resume_scene_metadata"] = dict(kwargs.get("resume_scene_metadata", {}))
                    observed_resume["staging_directory"] = str(kwargs.get("staging_directory"))
                    saved_artifact = kwargs["resume_scene_metadata"]["shot_001"]["artifact_relative_path"]
                    observed_resume["reused_artifact_exists"] = (root / saved_artifact).is_file()
                    second = Path(kwargs["staging_directory"]) / "shot_02.mp4"
                    second.write_bytes(artifact_bytes)
                    kwargs["scene_checkpoint_callback"]("shot_002", second, {
                        **generation, "prompt_id": "wan-second",
                    })
                    return dict(inner_self.result)

            resumed_builder = ResumeSecondShot()
            second_result = self._worker(
                root, builder=resumed_builder, worker_id="checkpoint-worker",
            ).run_once()
            self.assertEqual(second_result["status"], "succeeded", second_result)
            self.assertEqual(len(resumed_builder.calls), 1)
            _, resume_kwargs = resumed_builder.calls[0]
            self.assertEqual(resume_kwargs["reusable_step_ids"], ("shot_001",), observed_resume)
            self.assertEqual(set(resume_kwargs["resume_scene_metadata"]), {"shot_001"}, observed_resume)
            saved = resume_kwargs["resume_scene_metadata"]["shot_001"]
            self.assertEqual(saved["generation"]["prompt_id"], "wan-checkpoint")
            self.assertIn("epoch_2", str(resume_kwargs["staging_directory"]))
            self.assertTrue(observed_resume["reused_artifact_exists"])
            self.assertFalse((root / "data" / "video_attempt_staging" / attempt_id).exists())
            with InitiativeStore(database) as store:
                final_attempt = store.get_video_attempt(attempt_id)
                self.assertEqual(final_attempt["status"], "succeeded")
                self.assertEqual(final_attempt["attempt_count"], 2)

    def test_commander_approval_evidence_returns_latest_event_identity(self):
        with InitiativeStore(self.database) as store, TaskQueue(self.root / "data" / "tasks" / "muse.sqlite3") as queue:
            attempt = store.get_video_attempt("worker-test")
            evidence = VideoTaskWorker._commander_approval_evidence(store, queue, attempt)
            events = [
                event for event in queue.events(self.approval_task_id, limit=500)
                if event.get("event_type") == "human.decision"
            ]
            self.assertIsNotNone(evidence)
            self.assertEqual(evidence["approval_task_id"], self.approval_task_id)
            self.assertEqual(evidence["approval_event_id"], events[-1]["event_id"])
            self.assertEqual(evidence["approved_by"], "test-operator")
            self.assertEqual(len(evidence["video_brief_sha256"]), 64)
            self.assertTrue(VideoTaskWorker._commander_approval_is_valid(store, queue, attempt))

    def test_commander_approval_revocation_overrides_an_earlier_approval(self):
        with InitiativeStore(self.database) as store, TaskQueue(self.root / "data" / "tasks" / "muse.sqlite3") as queue:
            attempt = store.get_video_attempt("worker-test")
            queue._record_event(
                self.approval_task_id, "human.decision", "test-operator",
                {"decision": "rejected"},
            )
            queue._connection.commit()
            self.assertFalse(VideoTaskWorker._commander_approval_is_valid(store, queue, attempt))

    def test_worker_guard_blocks_next_scene_when_commander_approval_is_revoked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            video_brief = {
                "title": "审批撤销测试", "prompt": "A two-shot story",
                "aspect_ratio": "16:9", "duration_seconds": 4, "steps": 8,
                "recipe_sha256": "c" * 64,
                "scenes": [
                    {"prompt": "First", "duration_seconds": 2, "seed": 1},
                    {"prompt": "Second", "duration_seconds": 2, "seed": 2},
                ],
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Approval guard", kind="creative_output", creator_value="中途撤销审批",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="逐镜头生成", expected_output="视频",
                )
            self._queue_approved_attempt(root, initiative["id"], "d" * 32, video_brief)
            generated = []

            class GuardAwareBuilder(FakeVideoBuilder):
                def build_from_scenes(inner_self, *args, **kwargs):
                    inner_self.calls.append((args, kwargs))
                    guard = kwargs["scene_generation_guard"]
                    guard("shot_001", None, inner_self.lease_context)
                    generated.append("shot_001")
                    guard("shot_002", None, inner_self.lease_context)
                    generated.append("shot_002")
                    return dict(inner_self.result)

            builder = GuardAwareBuilder()
            with InitiativeStore(database) as store, TaskQueue(root / "data" / "tasks" / "muse.sqlite3") as queue:
                approval_attempt = store.get_video_attempt("d" * 32)
                evidence = VideoTaskWorker._commander_approval_evidence(store, queue, approval_attempt)
            with patch.object(
                VideoTaskWorker, "_commander_approval_evidence",
                side_effect=[evidence, evidence, evidence, evidence, None],
            ):
                result = self._worker(root, builder=builder, worker_id="approval-guard").run_once()

            self.assertEqual(result["status"], "blocked_admission")
            self.assertEqual(generated, ["shot_001"])
            self.assertEqual(len(result["admission_grants_consumed"]), 1)
            with InitiativeStore(database) as store:
                attempt = store.get_video_attempt("d" * 32)
            self.assertEqual(attempt["status"], "queued")

    def test_single_prompt_guard_blocks_builder_after_commander_approval_is_revoked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            video_brief = {
                "title": "单镜头审批撤销", "prompt": "A concise cinematic story",
                "aspect_ratio": "16:9", "duration_seconds": 2, "steps": 8,
                "recipe_sha256": "f" * 64,
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Single prompt guard", kind="creative_output", creator_value="单镜头生成前撤销",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成预览", expected_output="视频",
                )
            self._queue_approved_attempt(root, initiative["id"], "9" * 32, video_brief)
            builder = FakeVideoBuilder()
            with patch.object(
                VideoTaskWorker, "_commander_approval_is_valid",
                side_effect=[True, True, False],
            ):
                result = self._worker(root, builder=builder, worker_id="single-approval").run_once()

            self.assertEqual(result["status"], "blocked_admission")
            self.assertEqual(builder.calls, [])
            with InitiativeStore(database) as store:
                attempt = store.get_video_attempt("9" * 32)
            self.assertEqual(attempt["status"], "queued")

    def test_worker_rechecks_commander_approval_before_marking_product_succeeded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            video_brief = {
                "title": "最终审批复核", "prompt": "A short grounded story",
                "aspect_ratio": "16:9", "duration_seconds": 2, "steps": 8,
                "recipe_sha256": "d" * 64,
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Final approval guard", kind="creative_output", creator_value="终态审批复核",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成影片", expected_output="视频",
                )
            self._queue_approved_attempt(root, initiative["id"], "c" * 32, video_brief)
            builder = FakeVideoBuilder()
            with patch.object(
                VideoTaskWorker, "_commander_approval_is_valid",
                side_effect=[True, True, True, False],
            ):
                result = self._worker(root, builder=builder, worker_id="final-approval").run_once()

            self.assertEqual(result["status"], "blocked_approval")
            self.assertFalse(result["completion_recorded"])
            with InitiativeStore(database) as store:
                attempt = store.get_video_attempt("c" * 32)
            self.assertNotEqual(attempt["status"], "succeeded")

    def test_worker_builds_one_queued_video_and_records_verified_artifact(self):
        builder = FakeVideoBuilder()
        worker = self._worker(self.root, builder=builder, worker_id="test-worker")
        result = worker.run_once()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["attempt_id"], "worker-test")
        self.assertEqual(len(builder.calls), 1)
        args, kwargs = builder.calls[0]
        self.assertEqual(args[0], "测试影片")
        self.assertEqual(args[1], "source grounded story")
        self.assertEqual(kwargs["creator_initiative_id"], self.initiative_id)
        self.assertEqual(kwargs["aspect_ratio"], "9:16")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "succeeded")
            self.assertEqual(attempt["product_id"], "a" * 32)
            self.assertEqual(attempt["manifest_sha256"], "e" * 64)
            self.assertEqual(builder.verify_calls, [((self.root / "data" / "products").resolve(), "a" * 32, "b" * 20)])

    def test_cleanup_error_does_not_reverse_committed_attempt_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            attempt_id = "a" * 32
            video_brief = {
                "title": "清理异常保护", "prompt": "A short two-shot story",
                "aspect_ratio": "16:9", "duration_seconds": 4, "steps": 8,
                "recipe_sha256": "b" * 64,
                "scenes": [
                    {"prompt": "Opening shot", "duration_seconds": 2, "seed": 1},
                    {"prompt": "Closing shot", "duration_seconds": 2, "seed": 2},
                ],
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Cleanup failure", kind="creative_output", creator_value="成功后清理异常",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成两镜头", expected_output="已验证视频",
                )
            self._queue_approved_attempt(root, initiative["id"], attempt_id, video_brief)

            class StagingBuilder(FakeVideoBuilder):
                def build_from_scenes(inner_self, *args, **kwargs):
                    inner_self.calls.append((args, kwargs))
                    guard = kwargs.get("scene_generation_guard")
                    if callable(guard):
                        for index, scene in enumerate(kwargs.get("scenes", ()), start=1):
                            if f"shot_{index:03d}" not in set(kwargs.get("reusable_step_ids", ())):
                                guard(f"shot_{index:03d}", scene, inner_self.lease_context)
                    staging = Path(kwargs["staging_directory"])
                    (staging / "retained-for-cleanup-error.bin").write_bytes(b"keep")
                    return dict(inner_self.result)

            builder = StagingBuilder()
            with patch(
                "muse.video_task_worker.cleanup_video_attempt_staging_directory",
                side_effect=OSError("simulated cleanup failure"),
            ), self.assertLogs("muse.video_task_worker", level="WARNING"):
                result = self._worker(root, builder=builder, worker_id="cleanup-worker").run_once()
            self.assertEqual(result["status"], "succeeded")
            with InitiativeStore(database) as store:
                attempt = store.get_video_attempt(attempt_id)
            self.assertEqual(attempt["status"], "succeeded")
            self.assertTrue((root / "data" / "video_attempt_staging" / attempt_id).is_dir())

    def test_worker_renews_attempt_lease_while_builder_is_running(self):
        started = threading.Event()
        release = threading.Event()
        renewed = threading.Event()
        class BlockingBuilder(FakeVideoBuilder):
            def build_from_prompt(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                started.set()
                if not release.wait(5):
                    raise TimeoutError("test builder was not released")
                guard = kwargs.get("generation_guard")
                if callable(guard):
                    guard(self.lease_context)
                return dict(self.result)

        worker = self._worker(self.root, builder=BlockingBuilder(), worker_id="heartbeat-worker")
        worker.lease_renew_interval_seconds = 0.01
        original_renew = InitiativeStore.renew_video_attempt_lease

        def renew_and_signal(store, *args, **kwargs):
            result = original_renew(store, *args, **kwargs)
            renewed.set()
            return result

        outcomes = []
        runner = threading.Thread(target=lambda: outcomes.append(worker.run_once()))
        with patch.object(InitiativeStore, "renew_video_attempt_lease", new=renew_and_signal):
            runner.start()
            self.assertTrue(started.wait(2))
            self.assertTrue(renewed.wait(2))
            release.set()
            runner.join(10)
        self.assertFalse(runner.is_alive())
        self.assertEqual(outcomes[0]["status"], "succeeded")
        with InitiativeStore(self.database) as store:
            self.assertEqual(store.get_video_attempt("worker-test")["status"], "succeeded")

    def test_worker_fails_closed_when_lease_renewal_is_lost_during_render(self):
        started = threading.Event()
        release = threading.Event()
        renewal_failed = threading.Event()

        class BlockingBuilder(FakeVideoBuilder):
            def build_from_prompt(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                started.set()
                if not release.wait(5):
                    raise TimeoutError("test builder was not released")
                guard = kwargs.get("generation_guard")
                if callable(guard):
                    guard(self.lease_context)
                return dict(self.result)

        def lose_lease(_store, *_args, **_kwargs):
            renewal_failed.set()
            raise ValueError("simulated lease ownership loss")

        worker = self._worker(self.root, builder=BlockingBuilder(), worker_id="lost-heartbeat-worker")
        worker.lease_renew_interval_seconds = 0.01
        outcomes = []
        runner = threading.Thread(target=lambda: outcomes.append(worker.run_once()))
        with patch.object(InitiativeStore, "renew_video_attempt_lease", new=lose_lease):
            runner.start()
            self.assertTrue(started.wait(2))
            self.assertTrue(renewal_failed.wait(2))
            release.set()
            runner.join(10)
        self.assertFalse(runner.is_alive())
        self.assertEqual(outcomes[0]["status"], "lease_lost")
        self.assertFalse(outcomes[0]["failure_recorded"])
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
        self.assertEqual(attempt["status"], "running")
        self.assertEqual(attempt["worker_id"], "lost-heartbeat-worker")
        self.assertEqual(attempt["attempt_count"], 1)

    def test_approved_creator_brief_recipe_fingerprint_survives_worker_and_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            recipe_hash = "a" * 64
            video_brief = {
                "candidate_id": "candidate-01-abcdef123456",
                "recipe_sha256": recipe_hash,
                "title": "获批配方追溯测试",
                "prompt": "A restrained cinematic dawn over an ancient observatory",
                "negative_prompt": "unverified claims, text artifacts",
                "aspect_ratio": "16:9",
                "duration_seconds": 2,
                "steps": 8,
                "mapping_report": {"status": "ready", "mapped": [{"source": "scenes", "target": "scenes"}]},
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "已审批配方端到端测试", kind="creative_output", creator_value="配方指纹贯通",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成测试预览", expected_output="可追溯视频产品",
                )
            self._queue_approved_attempt(root, initiative["id"], "recipe-e2e-attempt", video_brief)
            builder = FakeVideoBuilder()
            result = self._worker(root, builder=builder, worker_id="recipe-e2e-worker").run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(len(builder.calls), 1)
            with InitiativeStore(database) as store:
                attempt = store.get_video_attempt("recipe-e2e-attempt")
                self.assertEqual(attempt["status"], "succeeded")
                self.assertEqual(attempt["recipe_sha256"], recipe_hash)
                self.assertEqual(attempt["manifest_sha256"], "e" * 64)
            verified_product = {
                "validated": True,
                "recipe_sha256": recipe_hash,
                "manifest_sha256": "e" * 64,
                "mp4_sha256": "c" * 64,
                "package_sha256": "d" * 64,
                "quality_gate": {"status": "passed"},
            }
            with patch("muse.video_product_builder.VideoProductBuilder.verify_path", return_value=verified_product):
                trace = build_video_recipe_trace(root, "recipe-e2e-attempt")
            self.assertTrue(trace["approval"]["human_decision_verified"])
            self.assertEqual(trace["approval"]["decision_actor"], "test-operator")
            self.assertTrue(trace["candidate"]["brief_fingerprint_matches_attempt"])
            self.assertTrue(trace["product"]["manifest_matches_attempt"])
            self.assertTrue(trace["product"]["recipe_matches_attempt"])
            self.assertEqual(trace["product"]["status"], "verified")
            self.assertTrue(trace["chain_consistent"])
            self.assertFalse(trace["release_review"]["can_publish"])
            self.assertEqual(trace["publishing"], "not_performed")

    def test_worker_fails_closed_when_builder_omits_manifest_digest(self):
        builder = FakeVideoBuilder()
        builder.result.pop("manifest_sha256")
        result = self._worker(self.root, builder=builder, worker_id="missing-manifest").run_once()
        self.assertEqual(result["status"], "failed")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "failed")

    def test_worker_fails_closed_when_builder_returns_invalid_manifest_digest(self):
        builder = FakeVideoBuilder()
        builder.result["manifest_sha256"] = "invalid"
        result = self._worker(self.root, builder=builder, worker_id="invalid-manifest").run_once()
        self.assertEqual(result["status"], "failed")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "failed")

    def test_claim_grounded_worker_blocks_unreviewed_claim_before_gpu_or_builder(self):
        from muse.narration_sync import NarrationSyncStore
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            tts_packages = root / ".muse-canto-tts-py314" / "canto_tts"
            tts_packages.mkdir(parents=True)
            tts_model = root / "data" / "models" / "canto-tts-nano"
            tts_model.mkdir(parents=True)
            (tts_model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            claim_id = "a" * 32
            with ResearchKnowledgeStore(root / "data" / "research" / "knowledge.sqlite3") as research:
                claim = research.upsert_node(
                    "claim", "未审核事实主张", summary="候选 Claim",
                    external_ref="test:claim-grounded-worker",
                )
                claim_id = claim["id"]
            concept = {"creator_brief": {"video": {
                "title": "证据门禁测试", "prompt": "A simple evidence gate test",
                "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
                "narration": {
                    "enabled": True, "language": "zh-HK", "style": "yujie_canto", "segments": 3,
                    "research_text": "这是一项未经审核的主张。它仍然需要核实。不能直接作为事实发布。",
                    "claim_grounded": True,
                    "claim_ids_by_segment": [[claim_id], [claim_id], [claim_id]],
                    "asr_enabled": False,
                },
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "Claim-grounded worker", kind="creative_output", creator_value="测试旁白证据门禁",
                    concept=json.dumps(concept, ensure_ascii=False), first_step="验证门禁", expected_output="阻断记录",
                )
            self._queue_approved_attempt(root, item["id"], "claim-grounded-worker", concept["creator_brief"]["video"])
            builder = FakeVideoBuilder()
            with patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}):
                result = self._worker(root, builder=builder, worker_id="claim-grounded-worker").run_once()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_type"], "RuntimeError")
            self.assertEqual(builder.calls, [])
            with NarrationSyncStore(root / "data" / "narration" / "narration.sqlite3") as sync:
                runs = sync.list_runs()
            self.assertEqual(len(runs), 1)
            self.assertFalse(runs[0]["evidence_contract"]["publishable"])
            self.assertTrue(all(
                "no_verified_support" in segment["blocking_reasons"]
                for segment in runs[0]["evidence_contract"]["segments"]
            ))

    def test_claim_grounded_worker_passes_publishable_evidence_contract_to_builder(self):
        from muse.narration_evidence_contract import build_narration_evidence_contract
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            with ResearchKnowledgeStore(root / "data" / "research" / "knowledge.sqlite3"):
                pass
            tts_packages = root / ".muse-canto-tts-py314" / "canto_tts"
            tts_packages.mkdir(parents=True)
            tts_model = root / "data" / "models" / "canto-tts-nano"
            tts_model.mkdir(parents=True)
            (tts_model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            claim_id = "a" * 32
            evidence_by_claim = {
                claim_id: [{
                    "evidence_id": "b" * 32,
                    "verdict": "supports",
                    "citation_anchor_verified": True,
                    "source_current": True,
                    "source_ref": "https://example.org/verified",
                }],
            }
            concept = {"creator_brief": {"video": {
                "title": "Claim-grounded pass test", "prompt": "Explain a verified test result",
                "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
                "narration": {
                    "enabled": True, "language": "zh-HK", "style": "yujie_canto", "segments": 3,
                    "research_text": "经核验的来源支持这项结论。旁白必须逐句关联主张。第三句也必须有证据支持。",
                    "claim_grounded": True,
                    "claim_ids_by_segment": [[claim_id], [claim_id], [claim_id]],
                    "asr_enabled": False,
                },
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "Claim-grounded pass", kind="creative_output",
                    creator_value="验证已审核旁白的 Worker 接线",
                    concept=json.dumps(concept, ensure_ascii=False),
                    first_step="验证证据契约传递", expected_output="Builder 收到可发布证据契约",
                )
            self._queue_approved_attempt(
                root, item["id"], "claim-grounded-ready-worker", concept["creator_brief"]["video"],
            )
            builder = FakeVideoBuilder()

            def persist_ready_contract(generated_script, research_store, narration_store):
                contract = build_narration_evidence_contract(
                    generated_script["segments"], evidence_by_claim,
                )
                self.assertTrue(contract["publishable"])
                return {"evidence_contract": contract}

            with (
                patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}),
                patch(
                    "muse.narration_evidence_pipeline.persist_claim_grounded_narration_run",
                    side_effect=persist_ready_contract,
                ) as persist,
            ):
                result = self._worker(
                    root, builder=builder, worker_id="claim-grounded-ready-worker",
                ).run_once()

            self.assertEqual(result["status"], "succeeded")
            persist.assert_called_once()
            self.assertEqual(len(builder.calls), 1)
            _, kwargs = builder.calls[0]
            contract = kwargs["narration_evidence_contract"]
            self.assertTrue(contract["publishable"])
            self.assertGreaterEqual(len(contract["segments"]), 3)
            self.assertTrue(all(segment["evidence"] for segment in contract["segments"]))

    def test_worker_generates_cantonese_script_and_passes_local_tts_paths_after_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            tts_packages = root / ".muse-canto-tts-py314" / "canto_tts"
            tts_packages.mkdir(parents=True)
            tts_model = root / "data" / "models" / "canto-tts-nano"
            tts_model.mkdir(parents=True)
            (tts_model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            concept = {"creator_brief": {"video": {
                "title": "粵語測試片", "prompt": "Explain a verified local service pilot",
                "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
                "narration": {
                    "enabled": True, "language": "zh-HK", "style": "yujie_canto", "segments": 3,
                    "research_text": "客戶需要清晰介紹。方案仍在試點驗證。成功信號是取得真實預約。",
                    "inspiration": "沉穩知性，清晰可信。", "asr_enabled": False,
                },
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "粵語測試", kind="creative_output", creator_value="測試旁白接線",
                    concept=json.dumps(concept, ensure_ascii=False), first_step="測試", expected_output="測試產品",
                )
            self._queue_approved_attempt(root, item["id"], "canto-worker-test", concept["creator_brief"]["video"])
            builder = FakeVideoBuilder()
            worker = self._worker(root, builder=builder, worker_id="canto-worker")
            with patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}) as preflight:
                result = worker.run_once()
            preflight.assert_called_once()
            self.assertTrue(Path(preflight.call_args.args[0]).samefile(tts_packages.parent))
            self.assertTrue(Path(preflight.call_args.args[1]).samefile(tts_model))
            self.assertEqual(result["status"], "succeeded")
            _, kwargs = builder.calls[0]
            self.assertTrue(Path(kwargs["narration_model_dir"]).samefile(tts_model))
            self.assertIsNone(kwargs["narration_asr_model_dir"])
            self.assertIn("係㗎", kwargs["narration_script"])
            self.assertGreaterEqual(kwargs["narration_script"].count("。"), 2)
            prosody = kwargs["narration_prosody_plan"]
            self.assertEqual(len(prosody["segments"]), 3)
            self.assertEqual(
                [segment["text"] for segment in prosody["segments"]],
                kwargs["narration_script"].splitlines(),
            )
            self.assertFalse(prosody["audio_effects_verified"])
            self.assertTrue(all("emotion" in segment["unsupported_controls"] for segment in prosody["segments"]))

    def test_multiscene_cantonese_narration_keeps_one_complete_commander_dag_for_each_grant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            (root / ".muse-canto-tts-py314" / "canto_tts").mkdir(parents=True)
            tts_model = root / "data" / "models" / "canto-tts-nano"
            tts_model.mkdir(parents=True)
            (tts_model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            video_brief = {
                "title": "多鏡頭粵語旁白授權測試",
                "prompt": "A grounded two-scene test",
                "aspect_ratio": "16:9", "duration_seconds": 4, "steps": 8,
                "scenes": [
                    {"prompt": "First verified scene", "duration_seconds": 2, "seed": 11},
                    {"prompt": "Second verified scene", "duration_seconds": 2, "seed": 22},
                ],
                "narration": {
                    "enabled": True, "language": "zh-HK", "style": "yujie_canto", "segments": 3,
                    "research_text": "先介紹已知內容。再清楚說明仍待驗證的部分。",
                    "inspiration": "沉穩知性，清晰可信。", "asr_enabled": False,
                },
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "多鏡頭旁白授權", kind="creative_output", creator_value="測試完整 DAG 授權",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="逐鏡頭生成與粵語旁白", expected_output="兩鏡頭旁白影片",
                )
            self._queue_approved_attempt(
                root, initiative["id"], "multiscene-canto-grant", video_brief,
            )
            builder = FakeVideoBuilder()
            provider = FakeAdmissionProvider()
            worker = self._worker(
                root, builder=builder, worker_id="multiscene-canto-worker",
                admission_provider=provider,
            )
            with patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}):
                result = worker.run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(builder.dispatched_steps, ["shot_001", "shot_002"])
            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(len({request.dag_sha256 for request in provider.requests}), 1)
            self.assertEqual(
                [request.batch.step_ids for request in provider.requests],
                [("shot_001",), ("shot_002",)],
            )
            self.assertEqual(
                [tuple(step.step_id for step in request.plan.steps) for request in provider.requests],
                [("shot_001", "shot_002"), ("shot_001", "shot_002")],
            )

    def test_cantonese_tts_preflight_failure_stops_before_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            (root / ".muse-canto-tts-py314" / "canto_tts").mkdir(parents=True)
            model = root / "data" / "models" / "canto-tts-nano"
            model.mkdir(parents=True)
            (model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            concept = {"creator_brief": {"video": {
                "title": "Runtime gate", "prompt": "A grounded test prompt",
                "narration": {"enabled": True, "research_text": "只做测试。仍需验证。"},
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "Runtime gate", kind="creative_output", creator_value="测试 Python 版本门禁",
                    concept=json.dumps(concept, ensure_ascii=False), first_step="测试", expected_output="测试产品",
                )
            self._queue_approved_attempt(root, item["id"], "canto-runtime-gate", concept["creator_brief"]["video"])
            builder = FakeVideoBuilder()
            with patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", side_effect=RuntimeError("Python 3.14 TTS helper unavailable")):
                result = self._worker(root, builder=builder, worker_id="canto-worker").run_once()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_type"], "RuntimeError")
            self.assertEqual(builder.calls, [])

    def test_requested_cantonese_narration_fails_closed_before_builder_when_model_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            tts_packages = root / ".muse-canto-tts-py314" / "canto_tts"
            tts_packages.mkdir(parents=True)
            concept = {"creator_brief": {"video": {
                "title": "缺少模型测试", "prompt": "A grounded test prompt",
                "narration": {"enabled": True, "research_text": "只做测试。仍需验证。"},
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "缺少模型", kind="creative_output", creator_value="测试失败关闭",
                    concept=json.dumps(concept, ensure_ascii=False), first_step="测试", expected_output="测试产品",
                )
            self._queue_approved_attempt(root, item["id"], "canto-missing-model", concept["creator_brief"]["video"])
            builder = FakeVideoBuilder()
            result = self._worker(root, builder=builder, worker_id="canto-worker").run_once()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(builder.calls, [])
            with InitiativeStore(database) as store:
                self.assertEqual(store.get_video_attempt("canto-missing-model")["status"], "failed")

    def test_worker_ignores_legacy_unapproved_research_queue_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            with InitiativeStore(database) as store:
                item = store.propose(
                    "自主研究候选", kind="creative_output", creator_value="未批准不应出片",
                    concept=json.dumps({"creator_brief": {"video": {"title": "候选", "prompt": "research candidate"}}}),
                    first_step="等待审核", expected_output="候选视频",
                )
                store.start_video_attempt("research-unapproved", mode="prompt_to_video", initiative_id=item["id"])
            builder = FakeVideoBuilder()
            result = self._worker(root, builder=builder).run_once()
            self.assertEqual(result["status"], "idle")
            self.assertTrue(result["approval_required"])
            self.assertEqual(builder.calls, [])
            with InitiativeStore(database) as store:
                self.assertEqual(store.get_video_attempt("research-unapproved")["status"], "queued")

    def test_paused_worker_does_not_claim_or_start_gpu_work(self):
        builder = FakeVideoBuilder()
        worker = VideoTaskWorker(self.root, builder=builder, worker_id="test-worker", pause_check=lambda: True)
        result = worker.run_once()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(builder.calls, [])
        with InitiativeStore(self.database) as store:
            self.assertEqual(store.get_video_attempt("worker-test")["status"], "queued")

    def test_pause_after_claim_releases_task_without_invoking_builder(self):
        builder = FakeVideoBuilder()
        checks = iter([False, True])
        worker = self._worker(
            self.root, builder=builder, worker_id="test-worker",
            pause_check=lambda: next(checks),
        )
        result = worker.run_once()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(builder.calls, [])
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "queued")
            self.assertIsNone(attempt["worker_id"])

    def test_builder_failure_is_durable_and_does_not_mark_product_succeeded(self):
        builder = FakeVideoBuilder(error=RuntimeError("ComfyUI unavailable"))
        worker = self._worker(self.root, builder=builder, worker_id="test-worker")
        result = worker.run_once()
        self.assertEqual(result["status"], "failed")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "failed")
            self.assertEqual(attempt["error_type"], "RuntimeError")


    def test_stale_worker_does_not_release_attempt_after_reclaim_during_pause_check(self):
        calls = 0
        reclaimed = None

        def pause_after_reclaim():
            nonlocal calls, reclaimed
            calls += 1
            if calls == 1:
                return False
            with InitiativeStore(self.database) as store:
                reclaimed = store.claim_next_video_attempt(
                    "replacement-worker", lease_seconds=3600, now=10_000_000_000,
                    attempt_id="worker-test",
                )
            return True

        result = self._worker(
            self.root, builder=FakeVideoBuilder(), worker_id="test-worker",
            pause_check=pause_after_reclaim,
        ).run_once()
        self.assertEqual(result["status"], "lease_lost")
        self.assertFalse(result["release_recorded"])
        self.assertEqual(reclaimed["worker_id"], "replacement-worker")
        self.assertEqual(reclaimed["attempt_count"], 2)
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
        self.assertEqual(attempt["status"], "running")
        self.assertEqual(attempt["worker_id"], "replacement-worker")
        self.assertEqual(attempt["attempt_count"], 2)

    def test_stale_worker_does_not_write_failure_after_attempt_is_reclaimed(self):
        class LeaseStealingBuilder(FakeVideoBuilder):
            def __init__(self, database):
                super().__init__()
                self.database = database
                self.reclaimed = None

            def build_from_prompt(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                with InitiativeStore(self.database) as store:
                    self.reclaimed = store.claim_next_video_attempt(
                        "replacement-worker", lease_seconds=3600, now=10_000_000_000,
                        attempt_id="worker-test",
                    )
                guard = kwargs.get("generation_guard")
                if callable(guard):
                    guard(self.lease_context)
                raise RuntimeError("simulated renderer failure after lease was reclaimed")

        builder = LeaseStealingBuilder(self.database)
        result = self._worker(self.root, builder=builder, worker_id="test-worker").run_once()
        self.assertEqual(result["status"], "lease_lost")
        self.assertFalse(result["failure_recorded"])
        self.assertEqual(builder.reclaimed["worker_id"], "replacement-worker")
        self.assertEqual(builder.reclaimed["attempt_count"], 2)
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
        self.assertEqual(attempt["status"], "running")
        self.assertEqual(attempt["worker_id"], "replacement-worker")
        self.assertEqual(attempt["attempt_count"], 2)
        self.assertIsNone(attempt["failure_code"])

    def test_worker_output_is_traceable_to_exact_approved_creator_brief_and_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            recipe_sha256 = "a" * 64
            video_brief = {
                "candidate_id": "candidate-integration-01",
                "recipe_sha256": recipe_sha256,
                "title": "端到端追溯测试", "prompt": "A grounded integration-test scene",
                "negative_prompt": "fabricated claims", "aspect_ratio": "9:16",
                "duration_seconds": 2, "steps": 8,
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Worker trace integration", kind="creative_output",
                    creator_value="验证审批到产品的追溯闭环",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成模拟预览", expected_output="可追溯的视频产品",
                )
            attempt_id = "worker-trace-integration"
            ticket_id = self._queue_approved_attempt(root, initiative["id"], attempt_id, video_brief)
            builder = FakeVideoBuilder()
            result = self._worker(root, builder=builder, worker_id="trace-worker").run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["attempt_id"], attempt_id)
            self.assertEqual(len(builder.calls), 1)
            from muse.video_recipe_trace import build_video_recipe_trace
            verified_product = {
                "validated": True, "recipe_sha256": recipe_sha256,
                "manifest_sha256": builder.result["manifest_sha256"],
                "mp4_sha256": builder.result["mp4_sha256"],
                "package_sha256": builder.result["package_sha256"],
                "quality_gate": {"status": "passed"},
            }
            with patch(
                "muse.video_product_builder.VideoProductBuilder.verify_path",
                return_value=verified_product,
            ):
                trace = build_video_recipe_trace(root, attempt_id)
            self.assertTrue(trace["chain_consistent"])
            self.assertEqual(trace["candidate"]["candidate_id"], "candidate-integration-01")
            self.assertEqual(trace["candidate"]["recipe_sha256"], recipe_sha256)
            self.assertTrue(trace["approval"]["human_decision_verified"])
            self.assertEqual(trace["approval"]["ticket_id"], ticket_id)
            self.assertEqual(trace["attempt"]["status"], "succeeded")
            self.assertEqual(trace["product"]["status"], "verified")
            self.assertFalse(trace["release_review"]["can_publish"])

if __name__ == "__main__":
    unittest.main()
