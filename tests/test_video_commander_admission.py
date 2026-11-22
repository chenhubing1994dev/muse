from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from muse.commander_planner import CommanderPlan, PlannedStep
from muse.initiative_engine import InitiativeStore
from muse.video_commander_admission import (
    RuntimeVideoAdmissionProvider,
    VideoAdmissionBlocked,
    VideoAdmissionRequest,
    build_video_admission_request,
)
from muse.video_studio import build_video_studio_batches

def _video_handler(*_args, **_kwargs):
    return {"status": "test_only"}


def _resource_gate(request):
    lease = request.resource_context["gpu_lease"]
    return {
        "status": "approved",
        "evidence": {
            "fixture": "bounded-local-resource-test",
            "gpu_lease_status": lease["status"],
            "gpu_lease_id": lease["lease_id"],
            "gpu_lease_owner_id": lease["owner_id"],
            "gpu_lease_coordination": lease["coordination"],
        },
    }


def _cost_gate(_request):
    return {"status": "approved", "evidence": {"fixture": "bounded-local-cost-test"}}


def _human_gate(request):
    return {
        "status": "approved",
        "approval_task_id": request.approval_task_id,
        "approval_event_id": request.approval_event_id,
        "approved_by": request.approved_by,
    }


class _CapabilityRegistry:
    def find_by_capability(self, capability, *, status=None):
        if status != "ready":
            return []
        return [{"capability_id": f"test:{capability}", "status": "ready", "capabilities": [capability]}]


class _RoleRegistry:
    def authorize(self, role, requested_capabilities, *, scope=None):
        if scope != "video_studio" or role != "developer" or not set(requested_capabilities) <= {"comfyui_generation"}:
            raise PermissionError("test scoped role denied")
        return {"role": role, "scope": scope, "authorized": True}

    def get_scoped(self, scope, role):
        if scope != "video_studio" or role != "developer":
            return None
        return {
            "role": role,
            "scope": scope,
            "allowed_capabilities": ["comfyui_generation"],
            "trust_tier": "standard",
        }


class RuntimeVideoAdmissionProviderTests(unittest.TestCase):
    def _fixture(self, root: Path, *, steps=None, capabilities=None, role="developer"):
        database = root / "initiatives.sqlite3"
        attempt_id = "admission-attempt-001"
        approval_task_id = "approval-task-001"
        recipe_sha256 = "1" * 64
        input_sha256 = "2" * 64
        dag_sha256 = "3" * 64
        batch_fingerprint = "4" * 64
        plan_steps = tuple(steps or (
            PlannedStep("shot_opening", "developer", "Generate a shot", (), ("comfyui_generation",)),
        ))
        step_ids = tuple(step.step_id for step in plan_steps)
        plan = CommanderPlan(
            plan_id="test-plan-001",
            goal="Test one authorized video batch",
            steps=plan_steps,
            source="test_video_commander_admission",
            parallel_waves=(step_ids,),
        )
        batch = SimpleNamespace(
            batch_id="batch_001",
            batch_fingerprint=batch_fingerprint,
            dag_sha256=dag_sha256,
            step_ids=step_ids,
        )
        with InitiativeStore(database) as store:
            store.start_video_attempt(
                attempt_id,
                mode="prompt_to_video",
                approval_task_id=approval_task_id,
                recipe_sha256=recipe_sha256,
            )
            attempt = store.claim_next_video_attempt("video-test-worker", lease_seconds=300)
        request = VideoAdmissionRequest(
            attempt_id=attempt_id,
            worker_id="video-test-worker",
            lease_epoch=attempt["attempt_count"],
            recipe_sha256=recipe_sha256,
            input_sha256=input_sha256,
            dag_sha256=dag_sha256,
            batch_id=batch.batch_id,
            batch_fingerprint=batch_fingerprint,
            scope="video_studio",
            role=role,
            required_capabilities=tuple(sorted(set(
                capabilities or (cap for step in plan_steps for cap in step.required_capabilities)
            ))),
            approval_task_id=approval_task_id,
            approval_event_id="approval-event-001",
            approved_by="test-operator",
            approval_evidence={
                "approval_task_id": approval_task_id,
                "approval_event_id": "approval-event-001",
                "approved_by": "test-operator",
            },
            batch=batch,
            plan=plan,
            step_ids=step_ids,
            all_batches=(batch,),
            expected_input_sha256={step_id: input_sha256 for step_id in step_ids},
            workspace_root=root,
            actual_handler=_video_handler,
            resource_context={
                "fixture": "resource",
                "gpu_lease": {
                    "status": "active", "lease_id": "test-gpu-lease",
                    "owner_id": "test-gpu-owner", "coordination": {"status": "ready"},
                },
            },
            cost_context={"fixture": "cost"},
        )
        provider = RuntimeVideoAdmissionProvider(
            capability_registry=_CapabilityRegistry(),
            role_registry=_RoleRegistry(),
            handler_registry={"comfyui_generation": _video_handler},
            resource_gate=_resource_gate,
            cost_gate=_cost_gate,
            human_gate=_human_gate,
        )
        return database, request, provider

    @staticmethod
    def _consume(store, grant, request):
        return store.consume_video_admission_grant(
            grant["grant_id"],
            attempt_id=request.attempt_id,
            worker_id=request.worker_id,
            lease_epoch=request.lease_epoch,
            recipe_sha256=request.recipe_sha256,
            input_sha256=request.input_sha256,
            dag_sha256=request.dag_sha256,
            batch_id=request.batch_id,
            batch_fingerprint=request.batch_fingerprint,
            handler_digest=grant["handler_digest"],
            resource_digest=grant["resource_digest"],
            cost_digest=grant["cost_digest"],
            approval_task_id=request.approval_task_id,
            approval_event_id=request.approval_event_id,
            approved_by=request.approved_by,
            scope=request.scope,
            role=request.role,
            required_capabilities=request.required_capabilities,
        )

    def test_provider_persists_grant_and_store_consumes_exact_binding_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database, request, provider = self._fixture(Path(directory))
            with InitiativeStore(database) as store:
                grant = provider.issue_video_admission_grant(store, request)
                self.assertEqual(grant["status"], "active")
                self.assertEqual(grant["attempt_id"], request.attempt_id)
                self.assertEqual(grant["batch_id"], request.batch_id)
                self.assertEqual(grant["required_capabilities"], ["comfyui_generation"])
                self.assertEqual(self._consume(store, grant, request)["status"], "consumed")
                with self.assertRaisesRegex(ValueError, "not active"):
                    self._consume(store, grant, request)

    def test_provider_rejects_mixed_role_batch_before_grant_creation(self):
        steps = (
            PlannedStep("shot_opening", "developer", "Generate", (), ("comfyui_generation",)),
            PlannedStep("review_output", "reviewer", "Review", ("shot_opening",), ("review",)),
        )
        with tempfile.TemporaryDirectory() as directory:
            database, request, provider = self._fixture(Path(directory), steps=steps)
            with InitiativeStore(database) as store:
                with self.assertRaisesRegex(VideoAdmissionBlocked, "exactly one explicitly authorized role"):
                    provider.issue_video_admission_grant(store, request)
                count = store.connection.execute(
                    "SELECT COUNT(*) FROM initiative_video_admission_grants"
                ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_provider_rejects_recipe_fingerprint_not_matching_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            database, request, provider = self._fixture(Path(directory))
            request = replace(request, recipe_sha256="9" * 64)
            with InitiativeStore(database) as store:
                with self.assertRaisesRegex(VideoAdmissionBlocked, "recipe or approval task does not match"):
                    provider.issue_video_admission_grant(store, request)
                count = store.connection.execute(
                    "SELECT COUNT(*) FROM initiative_video_admission_grants"
                ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_provider_fails_closed_when_resource_gate_has_no_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            database, request, provider = self._fixture(Path(directory))
            provider.resource_gate = lambda _request: {"status": "approved", "evidence": {}}
            with InitiativeStore(database) as store:
                with self.assertRaisesRegex(VideoAdmissionBlocked, "no auditable evidence"):
                    provider.issue_video_admission_grant(store, request)
                count = store.connection.execute(
                    "SELECT COUNT(*) FROM initiative_video_admission_grants"
                ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_provider_rejects_missing_gpu_lease_binding_before_calling_resource_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            database, request, provider = self._fixture(Path(directory))
            request = replace(request, resource_context={"fixture": "resource"})
            gate_calls = []
            provider.resource_gate = lambda candidate: (
                gate_calls.append(candidate) or {
                    "status": "approved", "evidence": {"fixture": "must-not-be-used"},
                }
            )
            with InitiativeStore(database) as store:
                with self.assertRaisesRegex(VideoAdmissionBlocked, "active GPU lease binding"):
                    provider.issue_video_admission_grant(store, request)
                count = store.connection.execute(
                    "SELECT COUNT(*) FROM initiative_video_admission_grants"
                ).fetchone()[0]
                self.assertEqual(count, 0)
            self.assertEqual(gate_calls, [])

    def test_provider_rejects_resource_evidence_bound_to_a_different_gpu_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            database, request, provider = self._fixture(Path(directory))
            provider.resource_gate = lambda _request: {
                "status": "approved",
                "evidence": {
                    "gpu_lease_status": "active", "gpu_lease_id": "another-lease",
                    "gpu_lease_owner_id": "test-gpu-owner", "gpu_lease_coordination": {"status": "ready"},
                },
            }
            with InitiativeStore(database) as store:
                with self.assertRaisesRegex(VideoAdmissionBlocked, "resource evidence does not bind the active GPU lease"):
                    provider.issue_video_admission_grant(store, request)
                count = store.connection.execute(
                    "SELECT COUNT(*) FROM initiative_video_admission_grants"
                ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_provider_rejects_resource_evidence_missing_exact_gpu_lease_owner_and_coordination(self):
        with tempfile.TemporaryDirectory() as directory:
            database, request, provider = self._fixture(Path(directory))
            provider.resource_gate = lambda _request: {
                "status": "approved",
                "evidence": {
                    "gpu_lease_status": "active", "gpu_lease_id": "test-gpu-lease",
                    "fixture": "insufficient-lease-evidence",
                },
            }
            with InitiativeStore(database) as store:
                with self.assertRaisesRegex(VideoAdmissionBlocked, "resource evidence does not bind the active GPU lease"):
                    provider.issue_video_admission_grant(store, request)
                count = store.connection.execute(
                    "SELECT COUNT(*) FROM initiative_video_admission_grants"
                ).fetchone()[0]
                self.assertEqual(count, 0)


class VideoAdmissionRequestFactoryTests(unittest.TestCase):
    def _context(self, *, dependent=False):
        steps = (
            PlannedStep("shot_001", "developer", "Generate first scene", (), ("comfyui_generation",)),
            PlannedStep(
                "shot_002", "developer", "Generate second scene",
                ("shot_001",) if dependent else (), ("comfyui_generation",),
            ),
        )
        plan = CommanderPlan(
            plan_id="factory-plan", goal="Two-scene local video", steps=steps,
            source="test_video_admission_factory", parallel_waves=(("shot_001",), ("shot_002",)),
        )
        batches = build_video_studio_batches(plan, max_steps=2)
        attempt = {
            "attempt_id": "factory-attempt", "worker_id": "factory-worker",
            "attempt_count": 3, "recipe_sha256": "a" * 64,
            "approval_task_id": "factory-approval",
        }
        approval = {
            "approval_task_id": "factory-approval", "approval_event_id": "factory-event",
            "approved_by": "human-operator",
        }
        video_brief = {"aspect_ratio": "16:9", "duration_seconds": 4, "steps": 12}
        return plan, batches, attempt, approval, video_brief

    def test_factory_preserves_original_dag_and_worker_supplied_singleton_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, batches, attempt, approval, video_brief = self._context()
            requests = []
            for step_id in ("shot_001", "shot_002"):
                batch = next(item for item in batches if item.step_ids == (step_id,))
                request = build_video_admission_request(
                    attempt=attempt, video_brief=video_brief, step_id=step_id,
                    input_sha256=("b" if step_id == "shot_001" else "c") * 64,
                    approval_evidence=approval, actual_handler=_video_handler,
                    workspace_root=directory, commander_plan=plan,
                    commander_batches=batches, admission_batch=batch,
                    scene_context={"prompt": f"Prompt for {step_id}", "duration_seconds": 2},
                )
                requests.append(request)
            self.assertTrue(all(request.plan is plan for request in requests))
            self.assertTrue(all(request.all_batches is batches for request in requests))
            self.assertEqual([request.batch.step_ids for request in requests], [("shot_001",), ("shot_002",)])
            self.assertEqual(len({request.dag_sha256 for request in requests}), 1)
            self.assertEqual(len({request.batch_fingerprint for request in requests}), 2)
            self.assertEqual(requests[0].resource_context["requested_generation"]["scene"]["prompt"], "Prompt for shot_001")
            self.assertTrue(requests[0].cost_context["cost_assessment_required"])

    def test_factory_binds_and_revalidates_the_actual_gpu_lease_context(self):
        class Lease:
            lease_id = "wan-lease-123"
            owner_id = "wan-owner-123"
            coordination = {"status": "ready", "model_probe": "healthy"}

            def __init__(self):
                self.assertions = 0

            def assert_valid(self):
                self.assertions += 1

        with tempfile.TemporaryDirectory() as directory:
            plan, batches, attempt, approval, video_brief = self._context()
            batch = next(item for item in batches if item.step_ids == ("shot_001",))
            lease = Lease()
            request = build_video_admission_request(
                attempt=attempt, video_brief=video_brief, step_id="shot_001",
                input_sha256="e" * 64, approval_evidence=approval,
                actual_handler=_video_handler, workspace_root=directory,
                commander_plan=plan, commander_batches=batches, admission_batch=batch,
                scene_context={"prompt": "Prompt for shot_001", "duration_seconds": 2},
                lease_context=lease,
            )

        self.assertEqual(lease.assertions, 1)
        self.assertEqual(request.resource_context["gpu_lease"], {
            "status": "active", "lease_id": "wan-lease-123", "owner_id": "wan-owner-123",
            "coordination": {"status": "ready", "model_probe": "healthy"},
        })

    def test_factory_rejects_dependency_bearing_or_substituted_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, batches, attempt, approval, video_brief = self._context(dependent=True)
            dependent_batch = next(item for item in batches if item.step_ids == ("shot_002",))
            kwargs = {
                "attempt": attempt, "video_brief": video_brief, "step_id": "shot_002",
                "input_sha256": "d" * 64, "approval_evidence": approval,
                "actual_handler": _video_handler, "workspace_root": directory,
                "commander_plan": plan, "commander_batches": batches,
                "admission_batch": dependent_batch, "scene_context": {"prompt": "second"},
            }
            with self.assertRaisesRegex(VideoAdmissionBlocked, "dependency-free singleton"):
                build_video_admission_request(**kwargs)
            independent_plan, independent_batches, attempt, approval, video_brief = self._context()
            selected = next(item for item in independent_batches if item.step_ids == ("shot_001",))
            kwargs.update({
                "commander_plan": independent_plan,
                "commander_batches": independent_batches,
                "admission_batch": SimpleNamespace(
                    batch_id=selected.batch_id, dag_sha256=selected.dag_sha256,
                    batch_fingerprint=selected.batch_fingerprint, step_ids=selected.step_ids,
                    required_completed_steps=(), role=selected.role,
                    required_capabilities=selected.required_capabilities,
                ),
                "step_id": "shot_001",
            })
            with self.assertRaisesRegex(VideoAdmissionBlocked, "not from the Worker-supplied"):
                build_video_admission_request(**kwargs)

if __name__ == "__main__":
    unittest.main()
