from __future__ import annotations

import unittest

from muse.video_workflow import build_video_commander_plan, validate_storyboard


def sample_storyboard():
    return {
        "schema_version": 1,
        "title": "山海奇旅",
        "aspect_ratio": "16:9",
        "fps": 30,
        "style": "cinematic, mythic, realistic",
        "scenes": [
            {"id": "opening", "prompt": "雾中远山与古老城门", "duration_seconds": 4, "camera": "slow push-in"},
            {"id": "reveal", "prompt": "城门打开，金色光芒涌出", "duration_seconds": 5, "camera": "wide crane shot"},
            {"id": "ending", "prompt": "主角背影走向天光", "duration_seconds": 6},
        ],
    }


class VideoWorkflowTests(unittest.TestCase):
    def test_validates_and_normalizes_storyboard(self):
        result = validate_storyboard(sample_storyboard())
        self.assertEqual(result["title"], "山海奇旅")
        self.assertEqual(result["scenes"][0]["duration_seconds"], 4.0)
        self.assertEqual(result["scenes"][0]["depends_on"], [])
        self.assertEqual(result["scenes"][2]["camera"], "")

    def test_rejects_unknown_fields_and_unsupported_schema(self):
        for mutate in (
            lambda data: data.update({"provider_api_key": "secret"}),
            lambda data: data.update({"schema_version": 2}),
        ):
            value = sample_storyboard()
            mutate(value)
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_storyboard(value)

    def test_rejects_duplicate_or_unsafe_scene_ids(self):
        value = sample_storyboard()
        value["scenes"][1]["id"] = "opening"
        with self.assertRaises(ValueError):
            validate_storyboard(value)
        value = sample_storyboard()
        value["scenes"][0]["id"] = "../outside"
        with self.assertRaises(ValueError):
            validate_storyboard(value)

    def test_rejects_non_finite_or_out_of_range_durations(self):
        for duration in (float("nan"), float("inf"), 0, 21, True):
            value = sample_storyboard()
            value["scenes"][0]["duration_seconds"] = duration
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    validate_storyboard(value)

    def test_bounds_scene_count_and_prompt_length(self):
        value = sample_storyboard()
        value["scenes"] = value["scenes"] * 9
        with self.assertRaises(ValueError):
            validate_storyboard(value)
        value = sample_storyboard()
        value["scenes"][0]["prompt"] = "x" * 2001
        with self.assertRaises(ValueError):
            validate_storyboard(value)

    def test_scene_dependencies_create_parallel_execution_waves(self):
        value = sample_storyboard()
        value["scenes"][1]["depends_on"] = ["opening"]
        value["scenes"][2]["depends_on"] = ["reveal"]
        plan = build_video_commander_plan(value)
        self.assertEqual(plan.parallel_waves, (
            ("preflight",), ("shot_opening",), ("shot_reveal",),
            ("shot_ending",), ("assemble",), ("quality_review",),
        ))
        by_id = {step.step_id: step for step in plan.steps}
        self.assertEqual(by_id["shot_reveal"].depends_on, ("preflight", "shot_opening"))

    def test_independent_scenes_stay_parallel_while_dependent_scenes_wait(self):
        value = sample_storyboard()
        value["scenes"].append({
            "id": "bridge", "prompt": "A bridge over the sea", "duration_seconds": 3,
            "depends_on": ["opening", "reveal"],
        })
        value["scenes"][2]["depends_on"] = ["opening"]
        plan = build_video_commander_plan(value)
        self.assertEqual(plan.parallel_waves[1], ("shot_opening", "shot_reveal"))
        self.assertEqual(plan.parallel_waves[2], ("shot_ending", "shot_bridge"))
        self.assertEqual(len(plan.parallel_waves), 5)
    def test_rejects_unknown_scene_dependencies_and_cycles(self):
        value = sample_storyboard()
        value["scenes"][0]["depends_on"] = ["missing"]
        with self.assertRaises(ValueError):
            validate_storyboard(value)

        value = sample_storyboard()
        value["scenes"][0]["depends_on"] = ["opening"]
        with self.assertRaises(ValueError):
            validate_storyboard(value)

        value = sample_storyboard()
        value["scenes"][0]["depends_on"] = ["ending"]
        value["scenes"][2]["depends_on"] = ["opening"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate_storyboard(value)


    def test_builds_parallel_shots_with_assemble_and_review_dependencies(self):
        plan = build_video_commander_plan(sample_storyboard())
        self.assertEqual(plan.source, "deterministic_video_storyboard")
        self.assertEqual(plan.parallel_waves, (
            ("preflight",),
            ("shot_opening", "shot_reveal", "shot_ending"),
            ("assemble",),
            ("quality_review",),
        ))
        by_id = {step.step_id: step for step in plan.steps}
        self.assertEqual(by_id["shot_reveal"].depends_on, ("preflight",))
        self.assertEqual(set(by_id["assemble"].depends_on), {"shot_opening", "shot_reveal", "shot_ending"})
        self.assertEqual(by_id["quality_review"].depends_on, ("assemble",))

    def test_video_plan_cli_emits_preview_without_execution(self):
        import json
        import tempfile
        from pathlib import Path
        from muse.video_factory import main

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "storyboard.json"
            source.write_text(json.dumps(sample_storyboard(), ensure_ascii=False), encoding="utf-8")
            from io import StringIO
            from contextlib import redirect_stdout
            output = StringIO()
            with redirect_stdout(output):
                code = main(["plan", str(source)])
            result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["execution"], "not_started")
        self.assertEqual(result["scene_count"], 3)

    def test_video_plan_cli_rejects_invalid_storyboard(self):
        import tempfile
        from pathlib import Path
        from muse.video_factory import main
        from contextlib import redirect_stdout
        from io import StringIO

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.json"
            source.write_text('{"schema_version":99}', encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["plan", str(source)])
        self.assertEqual(code, 2)
        self.assertIn("invalid_storyboard", output.getvalue())

    def test_plan_never_submits_or_starts_generation(self):
        plan = build_video_commander_plan(sample_storyboard())
        self.assertTrue(all(step.role in {"operator", "developer", "reviewer"} for step in plan.steps))
        self.assertEqual(len(plan.steps), 6)


if __name__ == "__main__":
    unittest.main()
