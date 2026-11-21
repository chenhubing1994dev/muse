from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from muse.video_studio import (
    PIPELINE_PROFILES,
    assess_video_studio_commander_admission,
    build_video_studio_batches,
    build_video_studio_plan,
    build_video_studio_preview,
    list_video_pipeline_profiles,
    load_video_pipeline_profiles,
    load_video_stage_specs,
    main,
)


def sample_storyboard() -> dict:
    return {
        "schema_version": 1,
        "title": "山海奇旅",
        "aspect_ratio": "9:16",
        "fps": 30,
        "style": "cinematic",
        "scenes": [
            {"id": "opening", "prompt": "雾中远山", "duration_seconds": 3},
            {"id": "reveal", "prompt": "古城门开启", "duration_seconds": 4},
            {"id": "ending", "prompt": "主角走向天光", "duration_seconds": 5, "depends_on": ["reveal"]},
        ],
    }


class VideoStudioTests(unittest.TestCase):
    def test_catalog_contains_twelve_unique_profiles(self):
        profiles = list_video_pipeline_profiles()
        self.assertEqual(len(profiles), 12)
        self.assertEqual(len({item["pipeline_id"] for item in profiles}), 12)
        self.assertTrue(all(item["execution_mode"] == "plan_only" for item in profiles))
        self.assertTrue(all(item["commander_is_scheduler"] for item in profiles))
        self.assertEqual(len(PIPELINE_PROFILES), 12)

    def test_documentary_plan_has_research_archive_rights_and_human_gates(self):
        profile, plan = build_video_studio_plan(sample_storyboard(), "documentary_montage")
        by_id = {step.step_id: step for step in plan.steps}
        self.assertEqual(profile.coverage, "partial")
        self.assertIn("research", by_id)
        self.assertIn("archive_retrieval", by_id)
        self.assertIn("source_rights", by_id)
        self.assertEqual(by_id["archive_retrieval"].depends_on, ("research",))
        self.assertEqual(by_id["source_rights"].depends_on, ("archive_retrieval",))
        self.assertEqual(by_id["shot_opening"].required_capabilities, ("stock_media_search",))
        self.assertIn("source_rights", profile.creative_gates)
        self.assertEqual(plan.source, "openmontage_inspired_video_studio:documentary_montage")

    def test_independent_shots_parallelize_and_scene_dependencies_are_preserved(self):
        _, plan = build_video_studio_plan(sample_storyboard(), "animated_explainer")
        by_id = {step.step_id: step for step in plan.steps}
        self.assertEqual(plan.parallel_waves[0], ("intake",))
        self.assertIn(("shot_opening", "shot_reveal"), plan.parallel_waves)
        self.assertEqual(by_id["shot_ending"].depends_on, ("preflight", "shot_reveal"))
        self.assertEqual(plan.parallel_waves[-1], ("publish_gate",))

    def test_clip_factory_uses_existing_highlight_capability_not_generation(self):
        _, plan = build_video_studio_plan(sample_storyboard(), "clip_factory")
        by_id = {step.step_id: step for step in plan.steps}
        self.assertEqual(by_id["shot_opening"].required_capabilities, ("video_highlights",))
        self.assertEqual(by_id["shot_opening"].role, "developer")

    def test_profile_plan_is_preview_only_and_keeps_commander_as_scheduler(self):
        preview = build_video_studio_preview(sample_storyboard(), "product_ad")
        self.assertEqual(preview["status"], "preview_only")
        self.assertEqual(preview["storyboard_validation"], "passed")
        self.assertEqual(preview["pipeline"]["pipeline_id"], "product_ad")
        self.assertEqual(preview["execution"], "not_started")
        self.assertEqual(preview["commander_submission"], "not_submitted")
        self.assertIn("publish_gate", [step["id"] for step in preview["commander_plan"]["steps"]])
        self.assertEqual(preview["commander_admission"]["status"], "blocked_step_limit")
        self.assertFalse(preview["commander_admission"]["execution_eligible"])
        self.assertGreater(preview["commander_admission"]["step_count"], 16)
        self.assertGreater(preview["commander_batching"]["batch_count"], 1)
        self.assertFalse(preview["commander_batching"]["checkpoint_persistence_available"])
        self.assertFalse(preview["commander_batching"]["execution_eligible"])
        self.assertTrue(all(not batch["execution_eligible"] for batch in preview["commander_batching"]["batches"]))

    def test_long_plan_is_split_into_deterministic_dependency_safe_preview_batches(self):
        _, plan = build_video_studio_plan(sample_storyboard(), "product_ad")
        batches = build_video_studio_batches(plan)
        repeated = build_video_studio_batches(plan)
        self.assertGreater(len(batches), 1)
        self.assertEqual([item.batch_id for item in batches], [item.batch_id for item in repeated])
        self.assertEqual([item.checkpoint_key_template for item in batches], [item.checkpoint_key_template for item in repeated])
        self.assertEqual([item.dag_sha256 for item in batches], [item.dag_sha256 for item in repeated])
        self.assertEqual([item.batch_fingerprint for item in batches], [item.batch_fingerprint for item in repeated])
        self.assertTrue(all(len(item.dag_sha256) == len(item.batch_fingerprint) == 64 for item in batches))
        self.assertTrue(all(dict(item.step_stage_ids).keys() == set(item.step_ids) for item in batches))
        self.assertEqual(dict(next(item for item in batches if "shot_opening" in item.step_ids).step_stage_ids)["shot_opening"], "shot_generation")
        self.assertGreater(len({item.batch_fingerprint for item in batches}), 1)
        serialized = [item.as_dict() for item in batches]
        for batch, payload in zip(batches, serialized):
            self.assertEqual(payload["dag_sha256"], batch.dag_sha256)
            self.assertEqual(payload["batch_fingerprint"], batch.batch_fingerprint)
            self.assertEqual(payload["batch_id"], batch.batch_id)
            self.assertEqual(payload["checkpoint_key_template"], batch.checkpoint_key_template)
            self.assertEqual(payload["step_stage_ids"], dict(batch.step_stage_ids))
            self.assertTrue(all(len(payload[key]) == 64 for key in ("dag_sha256", "batch_fingerprint")))
            for step in payload["commander_plan"]["steps"]:
                if step["step_id"] == "__video_batch_entry__":
                    self.assertIsNone(step["stage_id"])
                else:
                    self.assertNotEqual(step["step_id"], step["stage_id"] if step["step_id"].startswith("shot_") else None)
                    self.assertIn(step["stage_id"], {value for _, value in batch.step_stage_ids})
        changed_storyboard = sample_storyboard()
        changed_storyboard["title"] = "山海奇旅·修订版"
        _, changed_plan = build_video_studio_plan(changed_storyboard, "product_ad")
        changed_batches = build_video_studio_batches(changed_plan)
        self.assertNotEqual(batches[0].dag_sha256, changed_batches[0].dag_sha256)
        self.assertNotEqual(batches[0].batch_fingerprint, changed_batches[0].batch_fingerprint)
        _, regenerated_plan = build_video_studio_plan(sample_storyboard(), "product_ad")
        regenerated = build_video_studio_batches(regenerated_plan)
        self.assertEqual([item.batch_id for item in batches], [item.batch_id for item in regenerated])
        tiny_batches = build_video_studio_batches(plan, max_steps=2)
        self.assertTrue(all(len(item.plan.steps) == 2 and len(item.step_ids) == 1 for item in tiny_batches))
        self.assertTrue(all(2 <= len(item.plan.steps) <= 16 for item in batches))
        flattened = [step_id for item in batches for step_id in item.step_ids]
        original = {step.step_id: step for step in plan.steps}
        self.assertEqual(set(flattened), set(original))
        self.assertEqual(len(flattened), len(set(flattened)))

        for item in batches:
            batch_ids = set(item.step_ids)
            batch_steps = {step.step_id: step for step in item.plan.steps}
            gate = item.plan.steps[0]
            self.assertEqual(gate.role, "operator")
            self.assertIn("video_attempt_checkpoint_read", gate.required_capabilities)
            self.assertFalse(item.as_dict()["execution_eligible"])
            serialized = item.as_dict()
            self.assertEqual(serialized["dag_sha256"], item.dag_sha256)
            self.assertEqual(serialized["batch_fingerprint"], item.batch_fingerprint)
            self.assertEqual(serialized["step_stage_ids"], dict(item.step_stage_ids))
            serialized_steps = {step["step_id"]: step for step in serialized["commander_plan"]["steps"]}
            stage_by_step_id = dict(item.step_stage_ids)
            self.assertIsNone(serialized_steps[gate.step_id]["stage_id"])
            for step_id in item.step_ids:
                self.assertEqual(serialized_steps[step_id]["id"], step_id)
                self.assertEqual(serialized_steps[step_id]["stage_id"], stage_by_step_id[step_id])
            self.assertTrue(all(gate.step_id in batch_steps[step_id].depends_on for step_id in item.step_ids))
            for step_id in item.step_ids:
                actual_internal = {dep for dep in batch_steps[step_id].depends_on if dep != gate.step_id}
                expected_internal = {dep for dep in original[step_id].depends_on if dep in batch_ids}
                self.assertEqual(actual_internal, expected_internal)
                self.assertTrue(all(dep in batch_steps for dep in batch_steps[step_id].depends_on))
            expected_external = {
                dep for step_id in item.step_ids for dep in original[step_id].depends_on if dep not in batch_ids
            }
            self.assertEqual(set(item.required_completed_steps), expected_external)

    def test_preview_binds_canonical_storyboard_content_to_dag_and_batch_fingerprints(self):
        storyboard = sample_storyboard()
        preview = build_video_studio_preview(storyboard, "product_ad")
        changed = sample_storyboard()
        changed["scenes"][0]["prompt"] = "雨后群山与流动云海，镜头缓慢推进"
        changed_preview = build_video_studio_preview(changed, "product_ad")

        first_batches = preview["commander_batching"]["batches"]
        changed_batches = changed_preview["commander_batching"]["batches"]
        self.assertRegex(preview["storyboard_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(first_batches[0]["input_sha256"], preview["storyboard_sha256"])
        self.assertEqual(changed_batches[0]["input_sha256"], changed_preview["storyboard_sha256"])
        self.assertNotEqual(preview["storyboard_sha256"], changed_preview["storyboard_sha256"])
        self.assertNotEqual(first_batches[0]["dag_sha256"], changed_batches[0]["dag_sha256"])
        self.assertNotEqual(first_batches[0]["batch_fingerprint"], changed_batches[0]["batch_fingerprint"])
        self.assertTrue(all(batch["execution_eligible"] is False for batch in first_batches + changed_batches))
        _, plan = build_video_studio_plan(storyboard, "product_ad")
        with self.assertRaises(ValueError):
            build_video_studio_batches(plan, input_sha256="not-a-sha256")

    def test_video_studio_batch_satisfies_checkpoint_adapter_store_contract(self):
        from muse.video_checkpoint_adapter import record_video_checkpoint_artifact

        _, plan = build_video_studio_plan(sample_storyboard(), "product_ad")
        batches = build_video_studio_batches(plan)
        batch = batches[0]
        step_id = batch.step_ids[0]
        attempt_id = "a" * 32
        worker_id = "studio-test-worker"
        lease_epoch = 1
        expected_input = "b" * 64

        class FakeStore:
            def __init__(self):
                self.recorded = None

            def get_video_attempt(self, requested_attempt_id):
                self.assert_attempt_id = requested_attempt_id
                return {
                    "status": "running", "worker_id": worker_id,
                    "attempt_count": lease_epoch, "approval_task_id": "c" * 32,
                    "recipe_sha256": "d" * 64,
                }

            def list_video_stage_checkpoints(self, *args, **kwargs):
                return []

            def record_video_stage_checkpoint(self, requested_attempt_id, **kwargs):
                self.recorded = {"attempt_id": requested_attempt_id, **kwargs}
                return self.recorded

        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact = workspace / "verified-shot.bin"
            artifact.write_bytes(b"verified video stage artifact")
            with patch(
                "muse.video_task_worker.VideoTaskWorker._commander_approval_is_valid",
                return_value=True,
            ):
                result = record_video_checkpoint_artifact(
                    batch, store, object(), batches=batches,
                    expected_input_sha256={item: expected_input for current in batches for item in current.step_ids},
                    attempt_id=attempt_id, worker_id=worker_id, lease_epoch=lease_epoch,
                    step_id=step_id, workspace_root=workspace,
                    artifact_relative_path="verified-shot.bin", input_sha256=expected_input,
                )

        self.assertEqual(result["status"], "checkpoint_persisted")
        self.assertFalse(result["execution_eligible"])
        self.assertFalse(result["dispatch_started"])
        self.assertEqual(store.recorded["attempt_id"], attempt_id)
        self.assertEqual(store.recorded["worker_id"], worker_id)
        self.assertEqual(store.recorded["lease_epoch"], lease_epoch)
        self.assertEqual(store.recorded["dag_sha256"], batch.dag_sha256)
        self.assertEqual(store.recorded["batch_id"], batch.batch_id)
        self.assertEqual(store.recorded["batch_fingerprint"], batch.batch_fingerprint)
        self.assertEqual(store.recorded["step_id"], step_id)
        self.assertEqual(store.recorded["stage_id"], dict(batch.step_stage_ids)[step_id])
        self.assertIn(store.recorded["stage_id"], load_video_stage_specs())
        self.assertRegex(store.recorded["output_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(store.recorded["checkpoint_key"].startswith(f"video-studio:{attempt_id}:"))

    def test_all_pipeline_scene_modes_resolve_dynamic_steps_to_fixed_manifest_stages(self):
        for profile in PIPELINE_PROFILES:
            with self.subTest(pipeline=profile.pipeline_id):
                _, plan = build_video_studio_plan(sample_storyboard(), profile.pipeline_id)
                batches = build_video_studio_batches(plan)
                mapping = {step_id: stage_id for batch in batches for step_id, stage_id in batch.step_stage_ids}
                self.assertEqual(set(mapping), {step.step_id for step in plan.steps})
                self.assertTrue(set(mapping.values()) <= set(load_video_stage_specs()))
                self.assertTrue(all(len(batch.dag_sha256) == 64 and len(batch.batch_fingerprint) == 64 for batch in batches))

    def test_dag_fingerprint_changes_when_stage_manifest_bytes_change(self):
        from muse.video_studio import STAGE_MANIFEST_PATH
        _, plan = build_video_studio_plan(sample_storyboard(), "product_ad")
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "video_stages.yaml"
            manifest.write_bytes(STAGE_MANIFEST_PATH.read_bytes())
            with patch("muse.video_studio.STAGE_MANIFEST_PATH", manifest):
                original = build_video_studio_batches(plan)[0].dag_sha256
            manifest.write_bytes(manifest.read_bytes() + b" ")
            with patch("muse.video_studio.STAGE_MANIFEST_PATH", manifest):
                changed = build_video_studio_batches(plan)[0].dag_sha256
        self.assertNotEqual(original, changed)

    def test_ambiguous_step_capability_mapping_fails_closed(self):
        from muse.commander_planner import CommanderPlan, PlannedStep
        ambiguous = CommanderPlan(
            plan_id="ambiguous-stage-map", goal="ambiguous", source="test",
            steps=(PlannedStep(
                "shot_ambiguous", "developer", "ambiguous capability mapping", (),
                ("comfyui_generation", "motion_graphics"),
            ),),
            parallel_waves=(("shot_ambiguous",),),
        )
        with self.assertRaisesRegex(ValueError, "no unique fixed video stage mapping"):
            build_video_studio_batches(ambiguous)

    def test_video_batcher_rejects_dependency_cycles(self):
        from muse.commander_planner import CommanderPlan, PlannedStep
        cyclic = CommanderPlan(
            plan_id="cycle", goal="cycle", source="test",
            steps=(
                PlannedStep("a", "operator", "A", ("b",), ()),
                PlannedStep("b", "operator", "B", ("a",), ()),
            ),
            parallel_waves=(),
        )
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            build_video_studio_batches(cyclic)

    def test_rejects_unknown_profile_and_invalid_storyboard(self):
        with self.assertRaisesRegex(ValueError, "unknown video pipeline"):
            build_video_studio_plan(sample_storyboard(), "not-a-pipeline")
        with self.assertRaises(ValueError):
            build_video_studio_plan({"schema_version": 99}, "animated_explainer")

    def test_cli_lists_profiles_and_previews_plan_without_execution(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["pipelines"]), 0)
        catalogue = json.loads(output.getvalue())
        self.assertEqual(catalogue["profile_count"], 12)
        self.assertEqual(catalogue["execution"], "not_started")
        self.assertEqual(catalogue["manifest_format"], "yaml-1.2-json-subset")

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "storyboard.json"
            source.write_text(json.dumps(sample_storyboard(), ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["plan", str(source), "--pipeline", "podcast_repurpose"]), 0)
            preview = json.loads(output.getvalue())
        self.assertEqual(preview["pipeline"]["pipeline_id"], "podcast_repurpose")
        self.assertEqual(preview["execution"], "not_started")


    def test_admission_fails_closed_without_capability_registry(self):
        _, plan = build_video_studio_plan(sample_storyboard(), "product_ad")
        admission = assess_video_studio_commander_admission(plan, max_steps=32)
        self.assertEqual(admission["status"], "pending_capability_registry")
        self.assertFalse(admission["capability_registry_checked"])
        self.assertFalse(admission["execution_eligible"])
        self.assertFalse(admission["dispatch_started"])

    def test_admission_requires_ready_capabilities_and_scoped_role_grants(self):
        _, plan = build_video_studio_plan(sample_storyboard(), "animated_explainer")

        class ReadyCapabilities:
            def find_by_capability(self, capability, *, status=None):
                return [{"capability_id": f"test.{capability}", "status": status}]

        class ScopedRoles:
            def __init__(self):
                self.calls = []

            def authorize(self, role, requested_capabilities, *, scope=None):
                self.calls.append((role, tuple(requested_capabilities), scope))
                if scope != "video_studio":
                    raise PermissionError("wrong scope")
                return {"role": role, "allowed_capabilities": list(requested_capabilities)}

        roles = ScopedRoles()
        admission = assess_video_studio_commander_admission(
            plan, max_steps=32, capability_registry=ReadyCapabilities(), role_registry=roles,
        )
        self.assertEqual(admission["status"], "pending_execution_handler")
        self.assertTrue(admission["capability_registry_checked"])
        self.assertTrue(admission["role_capability_authorization_checked"])
        self.assertFalse(admission["handler_registry_checked"])
        self.assertFalse(admission["execution_eligible"])
        self.assertFalse(admission["dispatch_started"])
        self.assertEqual(len(roles.calls), len(plan.steps))
        self.assertTrue(all(call[2] == "video_studio" for call in roles.calls))

    def test_preview_accepts_existing_commander_registries_but_never_dispatches(self):
        from muse.capabilities import CapabilityRegistry
        from muse.orchestration import AgentRoleRegistry

        _, plan = build_video_studio_plan(sample_storyboard(), "animated_explainer")
        capabilities = sorted({cap for step in plan.steps for cap in step.required_capabilities})
        role_capabilities = {}
        for step in plan.steps:
            role_capabilities.setdefault(step.role, set()).update(step.required_capabilities)

        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "capabilities.sqlite3") as capability_registry:
                for capability in capabilities:
                    capability_registry.register(
                        capability_id=f"video.{capability}", domain="video",
                        name=capability, description=f"Verified test capability {capability}",
                        status="ready", capabilities=[capability], trust="local_test",
                        license="not_applicable",
                    )
                with AgentRoleRegistry(Path(directory) / "roles.sqlite3") as role_registry:
                    for role, allowed in role_capabilities.items():
                        role_registry.register_scoped(
                            "video_studio", role, allowed_capabilities=allowed,
                            trust_tier="standard", description="Explicit test-only scoped grant",
                        )
                    preview = build_video_studio_preview(
                        sample_storyboard(), "animated_explainer", max_steps=32,
                        capability_registry=capability_registry, role_registry=role_registry,
                        handler_registry={capability: (lambda _payload: {}) for capability in capabilities},
                    )

        self.assertEqual(preview["status"], "preview_only")
        self.assertEqual(preview["commander_admission"]["status"], "pending_resource_and_human_gates")
        self.assertTrue(preview["commander_admission"]["handler_registry_checked"])
        self.assertFalse(preview["commander_admission"]["execution_eligible"])
        self.assertFalse(preview["commander_admission"]["dispatch_started"])
        self.assertEqual(preview["execution"], "not_started")
        self.assertEqual(preview["commander_submission"], "not_submitted")
        self.assertEqual(preview["commander_batching"]["max_steps_per_batch"], 32)

    def test_admission_requires_a_callable_handler_for_every_ready_capability(self):
        _, plan = build_video_studio_plan(sample_storyboard(), "animated_explainer")
        capabilities = sorted({cap for step in plan.steps for cap in step.required_capabilities})

        class ReadyCapabilities:
            def find_by_capability(self, capability, *, status=None):
                return [{"capability_id": f"test.{capability}", "status": "ready"}]

        class ScopedRoles:
            def authorize(self, role, requested_capabilities, *, scope=None):
                if scope != "video_studio":
                    raise PermissionError("wrong scope")
                return {"role": role, "allowed_capabilities": list(requested_capabilities)}

        registries = {
            "capability_registry": ReadyCapabilities(),
            "role_registry": ScopedRoles(),
        }
        missing = assess_video_studio_commander_admission(
            plan, max_steps=32, **registries,
            handler_registry={cap: (lambda _payload: {}) for cap in capabilities[:-1]},
        )
        self.assertEqual(missing["status"], "blocked_missing_execution_handlers")
        self.assertTrue(missing["handler_registry_checked"])
        self.assertEqual(missing["missing_handlers"], [capabilities[-1]])
        self.assertFalse(missing["execution_eligible"])

        admitted = assess_video_studio_commander_admission(
            plan, max_steps=32, **registries,
            handler_registry={cap: (lambda _payload: {}) for cap in capabilities},
        )
        self.assertEqual(admitted["status"], "pending_resource_and_human_gates")
        self.assertTrue(admitted["capability_registry_checked"])
        self.assertTrue(admitted["role_capability_authorization_checked"])
        self.assertTrue(admitted["handler_registry_checked"])
        self.assertFalse(admitted["execution_eligible"])
        self.assertFalse(admitted["dispatch_started"])

    def test_admission_uses_real_capability_and_scoped_role_registries(self):
        from muse.capabilities import CapabilityRegistry
        from muse.orchestration import AgentRoleRegistry

        _, plan = build_video_studio_plan(sample_storyboard(), "animated_explainer")
        capabilities = sorted({cap for step in plan.steps for cap in step.required_capabilities})
        role_capabilities = {}
        for step in plan.steps:
            role_capabilities.setdefault(step.role, set()).update(step.required_capabilities)

        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "capabilities.sqlite3") as capability_registry:
                for capability in capabilities:
                    capability_registry.register(
                        capability_id=f"video.{capability}", domain="video",
                        name=capability, description=f"Verified test capability {capability}",
                        status="ready", capabilities=[capability], trust="local_test",
                        license="not_applicable",
                    )
                with AgentRoleRegistry(Path(directory) / "roles.sqlite3") as role_registry:
                    for role, allowed in role_capabilities.items():
                        role_registry.register_scoped(
                            "video_studio", role, allowed_capabilities=allowed,
                            trust_tier="standard", description="Explicit test-only scoped grant",
                        )
                    admission = assess_video_studio_commander_admission(
                        plan, max_steps=32, capability_registry=capability_registry,
                        role_registry=role_registry,
                        handler_registry={capability: (lambda _payload: {}) for capability in capabilities},
                    )

        self.assertEqual(admission["status"], "pending_resource_and_human_gates")
        self.assertTrue(admission["capability_registry_checked"])
        self.assertTrue(admission["role_capability_authorization_checked"])
        self.assertTrue(admission["handler_registry_checked"])
        self.assertFalse(admission["execution_eligible"])
        self.assertFalse(admission["dispatch_started"])

    def test_admission_blocks_missing_capability_and_denied_role(self):
        _, plan = build_video_studio_plan(sample_storyboard(), "animated_explainer")

        class MissingCapability:
            def find_by_capability(self, capability, *, status=None):
                return [] if capability == "comfyui_generation" else [{"status": "ready"}]

        class AllowRoles:
            def authorize(self, role, requested_capabilities, *, scope=None):
                return {"role": role}

        missing = assess_video_studio_commander_admission(
            plan, max_steps=32, capability_registry=MissingCapability(), role_registry=AllowRoles(),
        )
        self.assertEqual(missing["status"], "blocked_missing_capabilities")
        self.assertIn("comfyui_generation", missing["missing_capabilities"])
        self.assertFalse(missing["role_capability_authorization_checked"])

        class DenyRoles:
            def authorize(self, role, requested_capabilities, *, scope=None):
                raise PermissionError("no scoped grant")

        denied = assess_video_studio_commander_admission(
            plan, max_steps=32, capability_registry=type("Ready", (), {
                "find_by_capability": lambda self, capability, status=None: [{"status": status}]
            })(), role_registry=DenyRoles(),
        )
        self.assertEqual(denied["status"], "blocked_role_capability_authorization")
        self.assertTrue(denied["role_capability_authorization_checked"])
        self.assertTrue(denied["unauthorized_steps"])
        self.assertFalse(denied["execution_eligible"])

    def test_stage_specs_are_loaded_from_manifest_and_roles_are_commander_admitted(self):
        from muse.commander_planner import CommanderPlanner
        specs = load_video_stage_specs()
        self.assertGreaterEqual(len(specs), 20)
        self.assertIn("publish_gate", specs)
        self.assertTrue(all(spec.role in CommanderPlanner.ALLOWED_ROLES for spec in specs.values()))

    def test_profiles_are_loaded_from_dependency_free_yaml_manifest(self):
        loaded = load_video_pipeline_profiles()
        self.assertEqual(len(loaded), 12)
        self.assertEqual(loaded[2].pipeline_id, "documentary_montage")
        self.assertTrue(all(profile.pipeline_id for profile in loaded))

    def test_manifest_loader_rejects_duplicate_keys_and_invalid_stage_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.yaml"
            source.write_text(
                '{"schema_version":1,"schema_version":1,"format":"yaml-1.2-json-subset","pipelines":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate key"):
                load_video_pipeline_profiles(source)

            bad = json.loads((Path(__file__).parents[1] / "src" / "muse" / "video_pipelines.yaml").read_text(encoding="utf-8"))
            bad["pipelines"][0]["before_shots"][0] = "not_a_real_stage"
            source.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown or duplicate stage"):
                load_video_pipeline_profiles(source)

            bad["pipelines"][0]["before_shots"][0] = "preflight"
            source.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reserved"):
                load_video_pipeline_profiles(source)

    def test_stage_manifest_rejects_unknown_commander_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "stages.yaml"
            data = json.loads((Path(__file__).parents[1] / "src" / "muse" / "video_stages.yaml").read_text(encoding="utf-8"))
            data["stages"][0]["role"] = "superuser"
            source.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not admitted by CommanderPlanner"):
                load_video_stage_specs(source)

if __name__ == "__main__":
    unittest.main()
