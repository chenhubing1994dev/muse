"""Tests for Commander-queued video pipeline jobs (no real render)."""
from __future__ import annotations

import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from muse.pipeline_control import PipelineController, PipelineError, PipelineTaskHandler
from muse.task_queue import TaskQueue
from muse.worker import run_once


class PipelineControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task_db = self.root / "data" / "tasks" / "muse.sqlite3"
        self.controller = PipelineController(self.root, task_db=self.task_db)
        script = self.root / "scripts" / "muse-high-value-short.py"
        script.parent.mkdir(parents=True)
        script.write_text("print('{}')", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_recipes_disable_cantonese_when_local_prerequisites_are_missing(self) -> None:
        recipes = self.controller.recipes()
        available = [item for item in recipes if item["available"]]
        unavailable = [item for item in recipes if not item["available"]]
        self.assertEqual([item["id"] for item in available], ["high_value_short_v2"])
        self.assertEqual(unavailable[0]["id"], "cantonese_pilot_v2")
        self.assertIn("TTS/ASR", unavailable[0]["reason"])

    def test_unknown_recipe_rejected(self) -> None:
        with self.assertRaises(PipelineError):
            self.controller.submit("../../etc/passwd")

    def test_cantonese_recipe_is_rejected_until_local_tts_asr_prerequisites_exist(self) -> None:
        with self.assertRaisesRegex(PipelineError, "依赖未齐备"):
            self.controller.submit("cantonese_pilot_v2")

    def test_submit_missing_script_rejected_without_ticket(self) -> None:
        (self.root / "scripts" / "muse-high-value-short.py").unlink()
        with self.assertRaises(PipelineError):
            self.controller.submit("high_value_short_v2")
        with TaskQueue(self.task_db) as queue:
            self.assertEqual(queue.snapshot()["counts"]["queued"], 0)

    def test_submit_creates_commander_ticket_without_private_scheduler_or_json_ledger(self) -> None:
        job = self.controller.submit("high_value_short_v2", title="Test film")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["recipe_id"], "high_value_short_v2")
        with TaskQueue(self.task_db) as queue:
            ticket = queue.get(job["job_id"])
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["payload"]["kind"], "video_pipeline")
        self.assertEqual(ticket["payload"]["recipe_id"], "high_value_short_v2")
        self.assertEqual(ticket["payload"]["title"], "Test film")
        self.assertTrue(ticket["payload"]["output_dir"].startswith("data/prometheus/runs/high_value_short_v2/"))
        self.assertEqual(job["output_dir"], ticket["payload"]["output_dir"])
        self.assertEqual(ticket["workload_class"], "media")
        self.assertEqual(ticket["recovery_policy"], "manual")
        self.assertEqual(list((self.root / "data" / "pipelines" / "jobs").glob("*.json")) if (self.root / "data" / "pipelines" / "jobs").exists() else [], [])


    def test_each_submission_gets_isolated_output_and_preserves_existing_legacy_artifacts(self) -> None:
        legacy = self.root / "data" / "prometheus" / "high-value-short-v2"
        legacy.mkdir(parents=True)
        sentinel = legacy / "keep-me.txt"
        sentinel.write_text("existing artifact", encoding="utf-8")
        first = self.controller.submit("high_value_short_v2")
        second = self.controller.submit("high_value_short_v2")
        self.assertNotEqual(first["output_dir"], second["output_dir"])
        self.assertFalse((self.root / first["output_dir"]).exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "existing artifact")

    def test_worker_handler_rejects_output_path_escape(self) -> None:
        handler = PipelineTaskHandler(self.root)
        with self.assertRaisesRegex(PipelineError, "safe relative path"):
            handler({"kind": "video_pipeline", "recipe_id": "high_value_short_v2",
                     "output_dir": "../../outside"})


    def test_list_and_get_jobs_read_authoritative_task_queue(self) -> None:
        job = self.controller.submit("high_value_short_v2")
        self.assertEqual(self.controller.get_job(job["job_id"])["status"], "queued")
        self.assertEqual(self.controller.list_jobs()[0]["job_id"], job["job_id"])

    def test_pipeline_history_is_not_hidden_by_newer_unrelated_tickets(self) -> None:
        job = self.controller.submit("high_value_short_v2")
        with TaskQueue(self.task_db) as queue:
            for index in range(6):
                queue.enqueue(f"unrelated-{index}", {"kind": "echo", "input": index})
        self.assertEqual(self.controller.list_jobs(limit=1)[0]["job_id"], job["job_id"])

    def test_cantonese_worker_uses_pinned_local_tts_asr_runtime_and_unique_output(self) -> None:
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
        job = self.controller.submit("cantonese_pilot_v2", title="粤语试验")
        self.assertTrue(job["output_dir"].startswith("data/prometheus/runs/cantonese_pilot_v2/"))
        output = self.root / job["output_dir"]
        handler = PipelineTaskHandler(self.root)

        def fake_run(command, *, env, **kwargs):
            self.assertEqual(env["MUSE_PIPELINE_RECIPE_ID"], "cantonese_pilot_v2")
            self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
            python_path_entries = [Path(entry).resolve() for entry in env["PYTHONPATH"].split(os.pathsep)]
            self.assertIn(target.resolve(), python_path_entries)
            output.mkdir(parents=True)
            mp4, bundle = output / "film.mp4", output / "film.zip"
            mp4.write_bytes(b"mp4")
            bundle.write_bytes(b"zip")
            return type("Proc", (), {
                "returncode": 0,
                "stdout": json.dumps({"status": "completed",
                    "mp4": {"path": mp4.relative_to(self.root).as_posix()},
                    "bundle": {"path": bundle.relative_to(self.root).as_posix()}}),
                "stderr": "",
            })()

        with patch("muse.pipeline_control.subprocess.run", side_effect=fake_run):
            result = handler({"kind": "video_pipeline", "recipe_id": "cantonese_pilot_v2",
                              "title": "粤语试验", "output_dir": job["output_dir"]})
        self.assertEqual(result["status"], "completed")

    def test_worker_handler_passes_recipe_output_and_title_and_verifies_artifacts(self) -> None:
        job = self.controller.submit("high_value_short_v2", title="Test film")
        handler = PipelineTaskHandler(self.root)
        output = self.root / job["output_dir"]

        def fake_run(command, *, env, **kwargs):
            self.assertEqual(Path(env["MUSE_PIPELINE_OUTPUT_DIR"]).resolve(), output.resolve())
            self.assertEqual(env["MUSE_PIPELINE_TITLE"], "Test film")
            self.assertEqual(env["MUSE_PIPELINE_RECIPE_ID"], "high_value_short_v2")
            self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
            output.mkdir(parents=True)
            mp4, bundle = output / "film.mp4", output / "film.zip"
            mp4.write_bytes(b"mp4")
            bundle.write_bytes(b"zip")
            return type("Proc", (), {
                "returncode": 0,
                "stdout": json.dumps({"status": "completed",
                    "mp4": {"path": mp4.relative_to(self.root).as_posix()},
                    "bundle": {"path": bundle.relative_to(self.root).as_posix()}}),
                "stderr": "",
            })()

        with patch("muse.pipeline_control.subprocess.run", side_effect=fake_run):
            result = handler({"kind": "video_pipeline", "recipe_id": "high_value_short_v2",
                              "title": "Test film", "output_dir": job["output_dir"]})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["mp4"]["path"], (Path(job["output_dir"]) / "film.mp4").as_posix())

    def test_worker_handler_rejects_completed_result_without_real_artifacts(self) -> None:
        job = self.controller.submit("high_value_short_v2")
        handler = PipelineTaskHandler(self.root)
        fake = type("Proc", (), {
            "returncode": 0,
            "stdout": json.dumps({"status": "completed", "artifact": "film.zip"}),
            "stderr": "",
        })()
        with patch("muse.pipeline_control.subprocess.run", return_value=fake):
            result = run_once(self.task_db, {"video_pipeline": handler}, task_id=job["job_id"])
        self.assertEqual(result["status"], "failed")
        failed_job = self.controller.get_job(job["job_id"])
        self.assertEqual(failed_job["status"], "failed")
        self.assertTrue(failed_job["error"])

    def test_limit_bounds(self) -> None:
        with self.assertRaises(PipelineError):
            self.controller.list_jobs(limit=0)
        with self.assertRaises(PipelineError):
            self.controller.list_jobs(limit=101)


if __name__ == "__main__":
    unittest.main()

