from __future__ import annotations

import unittest

from muse.video_recipe import adapt_moneyprinterturbo_batch, adapt_moneyprinterturbo_params


class MoneyPrinterTurboRecipeAdapterTests(unittest.TestCase):
    def _params(self):
        return {
            "video_subject": "山海奇旅",
            "video_aspect": "portrait",
            "video_fps": 30,
            "video_style": "cinematic",
            "scenes": [
                {"id": "opening", "prompt": "雾中远山", "duration_seconds": 4},
            ],
        }

    def test_maps_only_unambiguous_fields_and_validates_storyboard(self):
        result = adapt_moneyprinterturbo_params(self._params())
        self.assertEqual(result["recipe"]["title"], "山海奇旅")
        self.assertEqual(result["recipe"]["aspect_ratio"], "9:16")
        self.assertEqual(result["recipe"]["fps"], 30)
        self.assertEqual(result["mapping_report"]["status"], "ready")
        self.assertTrue(result["mapping_report"]["ready_for_commander_plan"])
        self.assertEqual(
            {item["source"] for item in result["mapping_report"]["mapped"]},
            {"video_subject", "video_aspect", "video_fps", "video_style", "scenes"},
        )

    def test_audio_subtitle_provider_and_batch_options_are_not_silently_dropped(self):
        params = self._params()
        params.update({
            "voice_name": "voice-a", "bgm_file": "music.mp3",
            "subtitle_enabled": True, "video_count": 3,
            "video_model": "remote-model", "video_api_key": "never-echo-this",
        })
        result = adapt_moneyprinterturbo_params(params)
        report = result["mapping_report"]
        self.assertEqual(report["status"], "needs_review")
        self.assertFalse(report["ready_for_commander_plan"])
        fields = {item["field"] for item in report["requires_confirmation"]}
        self.assertTrue({"voice_name", "bgm_file", "subtitle_enabled", "video_count", "video_model", "video_api_key"} <= fields)
        self.assertNotIn("never-echo-this", repr(result))

    def test_unknown_fields_are_reported_as_unsupported(self):
        params = self._params()
        params["mystery_switch"] = True
        result = adapt_moneyprinterturbo_params(params)
        self.assertEqual(result["mapping_report"]["unsupported"], ["mystery_switch"])
        self.assertFalse(result["mapping_report"]["ready_for_commander_plan"])

    def test_rejects_alias_collisions_and_invalid_scene_payloads(self):
        params = self._params()
        params["title"] = "另一个标题"
        with self.assertRaisesRegex(ValueError, "same Muse field"):
            adapt_moneyprinterturbo_params(params)
        params = self._params()
        params["scenes"] = [{"id": "../escape", "prompt": "bad", "duration_seconds": 4}]
        with self.assertRaises(ValueError):
            adapt_moneyprinterturbo_params(params)

    def test_missing_scenes_never_claims_commander_readiness(self):
        result = adapt_moneyprinterturbo_params({"video_subject": "仅有标题"})
        self.assertEqual(result["mapping_report"]["status"], "incomplete")
        self.assertFalse(result["mapping_report"]["ready_for_commander_plan"])

    def test_video_cli_previews_mapping_and_commander_plan_without_execution(self):
        import json
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from muse.video_factory import main

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mpt-params.json"
            source.write_text(json.dumps(self._params(), ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["adapt-mpt", str(source)])
            result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["commander_plan_preview"]["source"], "deterministic_video_storyboard")
        self.assertEqual(result["execution"], "not_started")

    def test_rejects_unsafe_or_unbounded_input_shapes(self):
        with self.assertRaises(ValueError):
            adapt_moneyprinterturbo_params([])
        with self.assertRaises(ValueError):
            adapt_moneyprinterturbo_params({"x" * 81: 1})
        with self.assertRaises(ValueError):
            adapt_moneyprinterturbo_params({f"field_{index}": index for index in range(129)})


    def test_batch_assigns_stable_candidate_ids_and_reports_partial_readiness(self):
        ready = self._params()
        review = self._params()
        review.update({"video_count": 2, "voice_name": "voice-a"})
        first = adapt_moneyprinterturbo_batch([ready, review])
        second = adapt_moneyprinterturbo_batch([ready, review])
        self.assertEqual(first["status"], "needs_review")
        self.assertEqual(first["summary"], {
            "candidate_count": 2, "ready_count": 1, "needs_review_count": 1,
            "incomplete_count": 0, "blocked_count": 1,
        })
        self.assertEqual(
            [item["candidate_id"] for item in first["candidates"]],
            [item["candidate_id"] for item in second["candidates"]],
        )
        self.assertTrue(first["candidates"][0]["mapping_report"]["ready_for_commander_plan"])
        self.assertFalse(first["candidates"][1]["mapping_report"]["ready_for_commander_plan"])
        self.assertEqual(first["commander_submission"], "not_submitted")
        self.assertEqual(first["execution"], "not_started")
        for candidate in first["candidates"]:
            self.assertEqual(len(candidate["recipe_sha256"]), 64)
            self.assertTrue(all(char in "0123456789abcdef" for char in candidate["recipe_sha256"]))

    def test_batch_rejects_empty_oversized_and_invalid_candidates(self):
        for value in ([], [{}] * 9, "not-a-list"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError):
                    adapt_moneyprinterturbo_batch(value)
        with self.assertRaisesRegex(ValueError, "candidate 2"):
            adapt_moneyprinterturbo_batch([self._params(), {"video_fps": 120}])

    def test_cli_previews_only_ready_candidates_in_batch(self):
        import json
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from muse.video_factory import main

        ready = self._params()
        review = self._params()
        review["bgm_file"] = "track.mp3"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mpt-batch.json"
            source.write_text(json.dumps([ready, review], ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["adapt-mpt", str(source)])
            result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["summary"]["ready_count"], 1)
        self.assertIsNotNone(result["candidates"][0]["commander_plan_preview"])
        self.assertIsNone(result["candidates"][1]["commander_plan_preview"])
        self.assertEqual(result["commander_submission"], "not_submitted")


if __name__ == "__main__":
    unittest.main()
