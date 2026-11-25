from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.autonomous_runtime import AutonomousSupervisor
from muse.capabilities import CapabilityRegistry
from muse.orchestration import AgentRoleRegistry
from muse.task_queue import TaskQueue
from muse.video_admission_runtime import (
    VideoAdmissionRuntimeUnavailable, build_video_admission_runtime,
)
from muse.video_commander_admission import VideoAdmissionBlocked
from muse.daemon_control import MuseDaemonController


class VideoAdmissionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task_db = self.root / "data" / "tasks" / "muse.sqlite3"
        self.capability_db = self.root / "data" / "capabilities" / "muse.sqlite3"
        self.resource_db = self.root / "data" / "gpu-resources.sqlite3"
        self.task_db.parent.mkdir(parents=True)
        self.capability_db.parent.mkdir(parents=True)
        with TaskQueue(self.task_db):
            pass
        with CapabilityRegistry(self.capability_db):
            pass
        with AgentRoleRegistry(self.task_db):
            pass
        self.resource_db.touch()
        self.runtime = None

    def tearDown(self) -> None:
        if self.runtime is not None:
            self.runtime.close()
        self.temp.cleanup()

    def _build(self):
        self.runtime = build_video_admission_runtime(
            self.root,
            task_database=self.task_db,
            capability_database=self.capability_db,
            resource_database=self.resource_db,
        )
        return self.runtime

    def _register_minimum_policy(self) -> None:
        with CapabilityRegistry(self.capability_db) as registry:
            registry.register(
                capability_id="video.comfyui",
                domain="video",
                name="Verified local Wan handler",
                description="Bound to the built-in local Wan generation callable.",
                capabilities=("comfyui_generation",),
                trust="verified_local_handler",
                license="not_applicable",
                status="ready",
            )
        with AgentRoleRegistry(self.task_db) as roles:
            roles.register_scoped(
                "video_studio", "developer",
                allowed_capabilities=("comfyui_generation",),
                trust_tier="restricted",
                description="One local video generation capability only.",
            )

    def test_runtime_does_not_auto_register_missing_capability_or_role(self) -> None:
        runtime = self._build()
        readiness = runtime.readiness()
        self.assertEqual(readiness["status"], "blocked")
        self.assertIn("comfyui_generation", readiness["reason"])
        self.assertEqual(runtime.capability_registry.find_by_capability("comfyui_generation"), [])
        self.assertIsNone(runtime.role_registry.get_scoped("video_studio", "developer"))

    def test_readiness_requires_explicit_cost_policy_after_capability_and_role(self) -> None:
        self._register_minimum_policy()
        with patch.dict(os.environ, {
            "MUSE_VIDEO_SYSTEM_POWER_WATTS": "",
            "MUSE_ELECTRICITY_USD_PER_KWH": "",
            "MUSE_VIDEO_COST_CAP_USD": "",
            "MUSE_VIDEO_MAX_RUNTIME_SECONDS": "",
        }):
            runtime = self._build()
            readiness = runtime.readiness()
        self.assertEqual(readiness["status"], "blocked")
        self.assertIn("MUSE_VIDEO_SYSTEM_POWER_WATTS", readiness["reason"])

    def test_readiness_blocks_queued_video_approval_without_commander_dag_provenance(self) -> None:
        self._register_minimum_policy()
        with TaskQueue(self.task_db) as queue:
            queue.enqueue(
                "Video approval missing Commander DAG",
                {
                    "kind": "creator_initiative_video_approval",
                    "attempt_id": "attempt-1",
                    "approval_scope": "one_video_attempt_only",
                },
                require_approval=True,
                recovery_policy="manual",
            )
            tasks = queue.list_tasks(limit=10)
            task = next(item for item in tasks if item["status"] == "waiting_approval")
            task_id = task["id"]
        env = {
            "MUSE_VIDEO_SYSTEM_POWER_WATTS": "1000",
            "MUSE_ELECTRICITY_USD_PER_KWH": "0.2",
            "MUSE_VIDEO_COST_CAP_USD": "0.2",
            "MUSE_VIDEO_MAX_RUNTIME_SECONDS": "1800",
        }
        with patch.dict(os.environ, env):
            runtime = self._build()
            unapproved = runtime.readiness()
            self.assertEqual(unapproved["status"], "blocked")
            self.assertIn("approved and queued", unapproved["reason"])
            with TaskQueue(self.task_db) as queue:
                queue.approve(task_id, approved_by="reviewer")
            readiness = runtime.readiness()
        self.assertEqual(readiness["status"], "blocked")
        self.assertIn("Commander-issued persisted DAG provenance", readiness["reason"])

    def test_request_factory_rejects_worker_and_checkpoint_local_fallback_plans(self) -> None:
        runtime = self._build()
        for source in (
            "safe_fallback",
            "video_task_worker_single_step_v1",
            "video_task_worker_scene_generation",
            "openmontage_inspired_video_studio:default",
        ):
            with self.subTest(source=source):
                with self.assertRaises(VideoAdmissionBlocked):
                    runtime.request_factory(
                        commander_plan=SimpleNamespace(plan_id="local-plan", source=source),
                    )

    def test_request_factory_accepts_commander_planner_source(self) -> None:
        with patch(
            "muse.video_commander_admission.build_video_admission_request",
            return_value={"built": True},
        ):
            runtime = build_video_admission_runtime(
                self.root,
                task_database=self.task_db,
                capability_database=self.capability_db,
                resource_database=self.resource_db,
            )
        self.assertEqual(
            runtime.request_factory(
                commander_plan=SimpleNamespace(plan_id="commander-plan", source="model"),
            ),
            {"built": True},
        )
        runtime.close()

    def test_cost_gate_uses_conservative_runtime_and_blocks_over_budget(self) -> None:
        runtime = self._build()
        request = SimpleNamespace(cost_context={
            "execution_mode": "local_gpu",
            "requested_generation": {"video": {"duration_seconds": 2.0, "steps": 8}},
        })
        env = {
            "MUSE_VIDEO_SYSTEM_POWER_WATTS": "1000",
            "MUSE_ELECTRICITY_USD_PER_KWH": "0.2",
            "MUSE_VIDEO_COST_CAP_USD": "0.2",
            "MUSE_VIDEO_MAX_RUNTIME_SECONDS": "1800",
        }
        with patch.dict(os.environ, env):
            approved = runtime.provider.cost_gate(request)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["evidence"]["estimated_energy_kwh"], "0.500000")
        self.assertEqual(approved["evidence"]["estimated_cost_usd"], "0.100000")
        with patch.dict(os.environ, {**env, "MUSE_VIDEO_COST_CAP_USD": "0.05"}):
            blocked = runtime.provider.cost_gate(request)
        self.assertEqual(blocked["status"], "blocked")

    def test_human_gate_requires_the_latest_persisted_decision_for_exact_attempt(self) -> None:
        with TaskQueue(self.task_db) as queue:
            task = queue.enqueue(
                "One-attempt video approval",
                {
                    "kind": "creator_initiative_video_approval",
                    "attempt_id": "attempt-1",
                    "approval_scope": "one_video_attempt_only",
                    "commander_plan_id": "plan-1",
                    "commander_plan_source": "model",
                },
                require_approval=True,
                recovery_policy="manual",
            )
            queue.approve(task["id"], approved_by="reviewer")
            latest = [
                event for event in queue.events(task["id"], limit=100)
                if event["event_type"] == "human.decision"
            ][-1]
        runtime = self._build()
        request = SimpleNamespace(
            approval_task_id=task["id"],
            approval_event_id=latest["event_id"],
            approved_by="reviewer",
            attempt_id="attempt-1",
            plan=SimpleNamespace(plan_id="plan-1", source="model"),
        )
        approved = runtime.provider.human_gate(request)
        self.assertEqual(approved["status"], "approved")
        with TaskQueue(self.task_db) as queue:
            queue._record_event(
                task["id"], "human.decision", "reviewer", {"decision": "rejected"},
            )
            queue._connection.commit()
        blocked = runtime.provider.human_gate(request)
        self.assertEqual(blocked["status"], "blocked")

    def test_resource_gate_binds_live_lease_and_measured_idle_comfyui(self) -> None:
        runtime = self._build()
        coordination = {"mode": "model_offline_supervisor_not_running"}
        request = SimpleNamespace(resource_context={
            "gpu_lease": {
                "status": "active", "lease_id": "lease-1",
                "owner_id": "wan21-owner", "coordination": coordination,
            },
        })
        active_lease = {
            "lease_id": "lease-1", "owner_kind": "video_generation",
            "owner_id": "wan21-owner", "heartbeat_at": time.time(),
            "expires_at": time.time() + 120,
        }

        class FakeCoordinator:
            def __init__(self, _database):
                pass

            def active_lease(self):
                return dict(active_lease)

            def supervisor_state(self):
                return None

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            def health(self):
                return {"status": "ready", "running_jobs": 0, "pending_jobs": 0, "device_count": 1}

        memory = {"vram_total_bytes": 100, "vram_free_bytes": 90, "vram_free_ratio": 0.9}
        with (
            patch("muse.gpu_resources.GpuResourceCoordinator", FakeCoordinator),
            patch("muse.video_engines.ComfyUIClient", FakeClient),
            patch("muse.wan_video._require_gpu_headroom", return_value=memory),
        ):
            approved = runtime.provider.resource_gate(request)
        self.assertEqual(approved["status"], "approved")
        evidence = approved["evidence"]
        self.assertEqual(evidence["gpu_lease_id"], "lease-1")
        self.assertEqual(evidence["gpu_lease_coordination"], coordination)

        active_lease["lease_id"] = "different-lease"
        with patch("muse.gpu_resources.GpuResourceCoordinator", FakeCoordinator):
            blocked = runtime.provider.resource_gate(request)
        self.assertEqual(blocked["status"], "blocked")

    def test_builder_rejects_resource_database_aliasing_commander_databases(self) -> None:
        with self.assertRaises(VideoAdmissionRuntimeUnavailable):
            build_video_admission_runtime(
                self.root,
                task_database=self.task_db,
                capability_database=self.capability_db,
                resource_database=self.task_db,
            )

    def test_runtime_wrapper_rechecks_readiness_before_grant_delegation(self) -> None:
        runtime = self._build()
        with self.assertRaises(VideoAdmissionBlocked):
            runtime.issue_video_admission_grant(None, object())

    def test_resource_gate_requires_exact_fresh_supervisor_acknowledgement(self) -> None:
        runtime = self._build()
        coordination = {
            "mode": "supervisor_paused_owned_model",
            "supervisor_id": "supervisor-1",
        }
        request = SimpleNamespace(resource_context={
            "gpu_lease": {
                "status": "active", "lease_id": "lease-1",
                "owner_id": "wan21-owner", "coordination": coordination,
            },
        })
        active_lease = {
            "lease_id": "lease-1", "owner_kind": "video_generation",
            "owner_id": "wan21-owner", "heartbeat_at": time.time(),
            "expires_at": time.time() + 120,
        }
        supervisor_state = {
            "heartbeat_at": time.time(),
            "action": "paused_for_video",
            "acknowledged_lease_id": "lease-1",
        }

        class FakeCoordinator:
            def __init__(self, _database):
                pass

            def active_lease(self):
                return dict(active_lease)

            def supervisor_state(self):
                return dict(supervisor_state)

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            def health(self):
                return {"status": "ready", "running_jobs": 0, "pending_jobs": 0, "device_count": 1}

        memory = {"vram_total_bytes": 100, "vram_free_bytes": 90, "vram_free_ratio": 0.9}
        with (
            patch("muse.gpu_resources.GpuResourceCoordinator", FakeCoordinator),
            patch("muse.video_engines.ComfyUIClient", FakeClient),
            patch("muse.wan_video._require_gpu_headroom", return_value=memory),
        ):
            approved = runtime.provider.resource_gate(request)
        self.assertEqual(approved["status"], "approved")

        supervisor_state["acknowledged_lease_id"] = "different-lease"
        with patch("muse.gpu_resources.GpuResourceCoordinator", FakeCoordinator):
            blocked = runtime.provider.resource_gate(request)
        self.assertEqual(blocked["status"], "blocked")

    def test_video_worker_does_not_claim_without_injected_admission_runtime(self) -> None:
        supervisor = AutonomousSupervisor(
            task_db=self.task_db,
            routine_db=self.root / "data" / "routines" / "muse.sqlite3",
            state_db=self.root / "data" / "autonomy" / "runtime.sqlite3",
            improvement_db=self.root / "data" / "improvements" / "muse.sqlite3",
            knowledge_db=self.root / "data" / "research" / "knowledge.sqlite3",
            reminder_db=self.root / "data" / "reminders" / "muse.sqlite3",
            memory_db=self.root / "data" / "memory" / "muse.sqlite3",
            conversation_db=self.root / "data" / "conversations" / "muse.sqlite3",
            workspace=self.root,
        )
        with patch.dict(os.environ, {"MUSE_ENABLE_VIDEO_WORKER": "1"}):
            result = supervisor._video_worker({})
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["claimed"])
        self.assertIn("admission runtime", result["reason"])

        class NotReadyProvider:
            def issue_video_admission_grant(self, *_args, **_kwargs):
                return {}

            def readiness(self):
                return {"status": "blocked", "reason": "capability missing"}

        guarded = AutonomousSupervisor(
            task_db=self.task_db,
            routine_db=self.root / "data" / "routines" / "muse.sqlite3",
            state_db=self.root / "data" / "autonomy" / "runtime.sqlite3",
            improvement_db=self.root / "data" / "improvements" / "muse.sqlite3",
            knowledge_db=self.root / "data" / "research" / "knowledge.sqlite3",
            reminder_db=self.root / "data" / "reminders" / "muse.sqlite3",
            memory_db=self.root / "data" / "memory" / "muse.sqlite3",
            conversation_db=self.root / "data" / "conversations" / "muse.sqlite3",
            workspace=self.root,
            video_admission_provider=NotReadyProvider(),
            video_admission_request_factory=lambda **_kwargs: None,
        )
        with patch.dict(os.environ, {"MUSE_ENABLE_VIDEO_WORKER": "1"}):
            blocked = guarded._video_worker({})
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse(blocked["claimed"])
        self.assertEqual(blocked["reason"], "capability missing")

    def test_daemon_run_once_injects_and_closes_the_admission_runtime(self) -> None:
        class FakeRuntime:
            request_factory = staticmethod(lambda **_kwargs: None)

            def __init__(self):
                self.closed = False

            def issue_video_admission_grant(self, *_args, **_kwargs):
                return {}

            def readiness(self):
                return {"status": "ready", "capability": "comfyui_generation"}

            def close(self):
                self.closed = True

        class FakeSupervisor:
            captured = None

            def __init__(self, **kwargs):
                FakeSupervisor.captured = kwargs

            def ensure_default_routines(self):
                return []

            def run_tick(self):
                return {"task": None, "routines": []}

        fake_runtime = FakeRuntime()
        with (
            patch.dict(os.environ, {"MUSE_ENABLE_VIDEO_WORKER": "1"}),
            patch("muse.video_admission_runtime.build_video_admission_runtime", return_value=fake_runtime),
            patch("muse.daemon_control.AutonomousSupervisor", FakeSupervisor),
        ):
            controller = MuseDaemonController(
                workspace=self.root,
                state_db=self.root / "data" / "autonomy" / "runtime.sqlite3",
                task_db=self.task_db,
                routine_db=self.root / "data" / "routines" / "muse.sqlite3",
                improvement_db=self.root / "data" / "improvements" / "muse.sqlite3",
                knowledge_db=self.root / "data" / "research" / "knowledge.sqlite3",
                reminder_db=self.root / "data" / "reminders" / "muse.sqlite3",
            )
            result = controller.run_once()
        self.assertIs(FakeSupervisor.captured["video_admission_provider"], fake_runtime)
        self.assertIs(
            FakeSupervisor.captured["video_admission_request_factory"],
            fake_runtime.request_factory,
        )
        self.assertTrue(fake_runtime.closed)
        self.assertEqual(result["video_admission_setup"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
