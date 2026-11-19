from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.creative_studio_api import (
    CreativeStudioStore,
    STYLE_IDS,
    list_styles,
    plan_render,
    search_media,
    philosophy_inject,
    generate_script,
    submit_render,
    validate_render_request,
)


class CreativeStudioApiTests(unittest.TestCase):
    def test_list_styles_exposes_four_presets_and_narration_choices(self):
        result = list_styles()
        self.assertEqual(result["api_version"], 1)
        self.assertEqual({item["id"] for item in result["styles"]}, STYLE_IDS)
        self.assertIn("hk-retro", STYLE_IDS)
        self.assertIn("pixel", STYLE_IDS)
        self.assertIn("cosmic", STYLE_IDS)
        self.assertIn("mix", STYLE_IDS)
        narration_ids = {item["id"] for item in result["narration_styles"]}
        self.assertEqual(narration_ids, {"yujie_canto", "cute_canto", "news_canto", "none"})

    def test_validate_render_request_normalizes_and_rejects(self):
        good = validate_render_request({"topic": "港风短片", "style": "hk-retro", "fps": 30})
        self.assertEqual(good["title"], "港风短片")
        self.assertEqual(good["aspect_ratio"], "9:16")
        with self.assertRaises(ValueError):
            validate_render_request({"topic": ""})
        with self.assertRaises(ValueError):
            validate_render_request({"topic": "x", "style": "not-a-style"})
        with self.assertRaises(ValueError):
            validate_render_request({"topic": "x", "narration_style": "bad"})
        with self.assertRaises(ValueError):
            validate_render_request({"topic": "x", "fps": 1000})
        with self.assertRaises(ValueError):
            validate_render_request({"topic": "x", "unknown_key": 1})

    def test_plan_render_is_deterministic_and_not_started(self):
        request = validate_render_request({"topic": "宇宙既视感", "style": "cosmic"})
        plan = plan_render(request)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["execution"], "not_started")
        self.assertEqual(plan["style"]["id"], "cosmic")
        self.assertEqual(len(plan["stages"]), 5)
        self.assertEqual([s["order"] for s in plan["stages"]], [1, 2, 3, 4, 5])

    def test_optional_engines_fail_closed_when_absent(self):
        request = validate_render_request({"topic": "测试"})
        script = generate_script(request)
        self.assertIn(script["status"], {"generated", "blocked"})
        media = search_media(request, engine=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(media["status"], "blocked")
        self.assertEqual(media["assets"], [])
        philo = philosophy_inject(request, engine=lambda *a, **k: {"principles": [], "note": "测试"})
        self.assertEqual(philo["status"], "injected")
        self.assertEqual(philo["principles"], [])

    def test_generate_script_with_injected_generator(self):
        request = validate_render_request({"topic": "测试", "narration_style": "yujie_canto"})
        def fake_generator(topic, research, inspiration, *, style):
            self.assertEqual(style, "YUJIE_CANTO")
            return {"segments": [{"index": 0, "text": "第一段", "estimated_duration_seconds": 3.0}]}
        result = generate_script(request, generator=fake_generator)
        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["segment_count"], 1)
        self.assertEqual(result["segments"][0]["text"], "第一段")
        no_narration = generate_script(validate_render_request({"topic": "x", "narration_style": "none"}))
        self.assertEqual(no_narration["status"], "skipped")

    def test_search_media_filters_unsafe_assets(self):
        request = validate_render_request({"topic": "霓虹", "style": "hk-retro"})
        def fake_engine(query, style, *, limit):
            return [
                {"url": "https://example.org/a.jpg", "title": "A", "license": "CC0"},
                {"url": "javascript:alert(1)", "title": "bad", "license": "x"},
            ]
        result = search_media(request, engine=fake_engine)
        self.assertEqual(result["asset_count"], 1)
        self.assertEqual(result["assets"][0]["url"], "https://example.org/a.jpg")

    def test_store_persistence_and_public_shape(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "jobs.sqlite3")
            with CreativeStudioStore(db) as store:
                job = store.create_job(title="t", style="pixel", narration_style="none", request={"topic": "t"})
                self.assertEqual(job["status"], "queued")
                self.assertEqual(job["progress"], 0)
                store.update_job(job["id"], status="running", progress=50, stage="render")
                updated = store.get_job(job["id"])
                self.assertEqual(updated["status"], "running")
                self.assertEqual(updated["progress"], 50)
                self.assertEqual(updated["stage"], "render")
            with CreativeStudioStore(db) as reopened:
                self.assertEqual(reopened.list_jobs()[0]["id"], job["id"])

    def test_submit_render_enqueues_and_records_execution(self):
        with tempfile.TemporaryDirectory() as d:
            store = CreativeStudioStore(Path(d) / "jobs.sqlite3")
            try:
                def fake_execute(job_id, request):
                    return {"status": "succeeded", "progress": 100, "stage": "done", "output": "out.mp4"}
                result = submit_render(
                    {"topic": "成片", "style": "mix"}, store=store, execute=fake_execute,
                )
                self.assertEqual(result["plan"]["status"], "planned")
                self.assertEqual(result["job"]["status"], "succeeded")
                self.assertEqual(result["job"]["progress"], 100)
                self.assertEqual(result["job"]["result"]["output"], "out.mp4")
            finally:
                store.close()

    def test_generate_script_integrates_with_real_phase2_generator(self):
        """End-to-end: optional research/inspiration fields must not block generation."""
        for narration_style, opener in (
            ("yujie_canto", "係"),
            ("cute_canto", "✨"),
            ("news_canto", "本台"),
        ):
            request = validate_render_request({"topic": "港风霓虹", "narration_style": narration_style})
            result = generate_script(request)
            self.assertEqual(result["status"], "generated", result.get("reason"))
            self.assertGreaterEqual(result["segment_count"], 3)
            self.assertIn(opener, result["segments"][0]["text"])

    def test_submit_render_failed_execution_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            store = CreativeStudioStore(Path(d) / "jobs.sqlite3")
            try:
                def boom(job_id, request):
                    raise RuntimeError("renderer missing")
                result = submit_render({"topic": "x"}, store=store, execute=boom)
                self.assertEqual(result["job"]["status"], "failed")
                self.assertIn("renderer missing", result["job"]["error"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
