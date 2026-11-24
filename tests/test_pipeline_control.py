"""Tests for the fail-closed legacy video pipeline adapter."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.pipeline_control import PipelineController, PipelineError, PipelineTaskHandler
from muse.task_queue import TaskQueue


class PipelineControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task_db = self.root / "data" / "tasks" / "muse.sqlite3"
        self.controller = PipelineController(self.root, task_db=self.task_db)
        script = self.root / "scripts" / "muse-high-value-short.py"
        script.parent.mkdir(parents=True)
        script.write_text("pass", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _enqueue_legacy_ticket(self, title: str = "Legacy video") -> dict:
        with TaskQueue(self.task_db) as queue:
            return queue.enqueue(
                title,
                {
                    "kind": "video_pipeline",
                    "recipe_id": "high_value_short_v2",
                    "title": title,
                    "output_dir": "data/prometheus/runs/legacy",
                },
                workload_class="media",
                recovery_policy="manual",
                require_approval=True,
            )

    def test_recipes_mark_every_legacy_dispatch_unavailable(self) -> None:
        recipes = self.controller.recipes()
        self.assertTrue(recipes)
        self.assertTrue(all(item["available"] is False for item in recipes))
        by_id = {item["id"]: item for item in recipes}
        self.assertIn("Legacy video_pipeline dispatch is disabled", by_id["high_value_short_v2"]["reason"])
        self.assertIn("TTS/ASR", by_id["cantonese_pilot_v2"]["reason"])
    def test_unknown_recipe_rejected(self) -> None:
        with self.assertRaisesRegex(PipelineError, "unsupported pipeline recipe"):
            self.controller.submit("../../etc/passwd")

    def test_cantonese_recipe_is_rejected_until_local_tts_asr_prerequisites_exist(self) -> None:
        with self.assertRaisesRegex(PipelineError, "依赖未齐备"):
            self.controller.submit("cantonese_pilot_v2")

    def test_submit_missing_script_rejected_without_ticket(self) -> None:
        (self.root / "scripts" / "muse-high-value-short.py").unlink()
        with self.assertRaisesRegex(PipelineError, "script is unavailable"):
            self.controller.submit("high_value_short_v2")
        with TaskQueue(self.task_db) as queue:
            self.assertEqual(queue.list_tasks(kind="video_pipeline", limit=100), [])

    def test_valid_legacy_submit_is_disabled_without_creating_a_ticket(self) -> None:
        with self.assertRaisesRegex(PipelineError, "Commander-approved VideoTaskWorker"):
            self.controller.submit("high_value_short_v2", title="Test film")
        with TaskQueue(self.task_db) as queue:
            self.assertEqual(queue.list_tasks(kind="video_pipeline", limit=100), [])

    def test_repeated_legacy_submit_preserves_existing_artifacts_and_creates_no_output(self) -> None:
        legacy = self.root / "data" / "prometheus" / "high-value-short-v2"
        legacy.mkdir(parents=True)
        sentinel = legacy / "keep-me.txt"
        sentinel.write_text("existing artifact", encoding="utf-8")
        for _ in range(2):
            with self.assertRaisesRegex(PipelineError, "Commander-approved VideoTaskWorker"):
                self.controller.submit("high_value_short_v2")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing artifact")
        self.assertFalse((self.root / "data" / "prometheus" / "runs").exists())

    def test_legacy_worker_handler_rejects_even_a_valid_recipe_payload(self) -> None:
        handler = PipelineTaskHandler(self.root)
        with self.assertRaisesRegex(PipelineError, "Commander-approved VideoTaskWorker"):
            handler({
                "kind": "video_pipeline",
                "recipe_id": "high_value_short_v2",
                "title": "must not render",
                "output_dir": "data/prometheus/runs/legacy",
            })

    def test_list_and_get_jobs_read_legacy_tickets_without_dispatching(self) -> None:
        ticket = self._enqueue_legacy_ticket()
        job = self.controller.get_job(ticket["id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "waiting_approval")
        self.assertIn("旧版渲染路径已停用", job["progress"])
        self.assertEqual(self.controller.list_jobs()[0]["job_id"], ticket["id"])
    def test_pipeline_history_is_not_hidden_by_newer_unrelated_tickets(self) -> None:
        ticket = self._enqueue_legacy_ticket()
        with TaskQueue(self.task_db) as queue:
            for index in range(6):
                queue.enqueue(f"unrelated-{index}", {"kind": "echo", "input": index})
        self.assertEqual(self.controller.list_jobs(limit=1)[0]["job_id"], ticket["id"])

    def test_cantonese_recipe_stays_disabled_even_when_local_prerequisites_exist(self) -> None:
        target = self.root / ".muse-canto-tts-py314"
        for package in ("canto_tts", "faster_whisper", "ctranslate2", "onnxruntime"):
            (target / package).mkdir(parents=True)
        tts_model = self.root / "data" / "models" / "canto-tts-nano"
        tts_model.mkdir(parents=True)
        (tts_model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
        asr_model = self.root / "models" / "whisper-small-cantonese" / "cts" / "model.bin"
        asr_model.parent.mkdir(parents=True)
        with asr_model.open("wb") as stream:
            stream.truncate(967_072_097)
        with self.assertRaisesRegex(PipelineError, "Commander-approved VideoTaskWorker"):
            self.controller.submit("cantonese_pilot_v2", title="粤语试验")

    def test_limit_bounds(self) -> None:
        with self.assertRaises(PipelineError):
            self.controller.list_jobs(limit=0)
        with self.assertRaises(PipelineError):
            self.controller.list_jobs(limit=101)


if __name__ == "__main__":
    unittest.main()
