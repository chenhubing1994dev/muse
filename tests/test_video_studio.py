from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from muse.video_studio import (
    PIPELINE_PROFILES,
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
        self.assertEqual(preview["status"], "validated")
        self.assertEqual(preview["pipeline"]["pipeline_id"], "product_ad")
        self.assertEqual(preview["execution"], "not_started")
        self.assertEqual(preview["commander_submission"], "not_submitted")
        self.assertIn("publish_gate", [step["id"] for step in preview["commander_plan"]["steps"]])
        self.assertEqual(preview["commander_admission"]["status"], "blocked_step_limit")
        self.assertFalse(preview["commander_admission"]["execution_eligible"])
        self.assertGreater(preview["commander_admission"]["step_count"], 16)

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
