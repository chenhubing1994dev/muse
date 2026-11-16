from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.video_task_worker import VideoTaskWorker


class FakeVideoBuilder:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "validated": True, "initiative_id": "a" * 32, "version_id": "b" * 20,
            "mp4_sha256": "c" * 64, "package_sha256": "d" * 64,
            "relative_path": "data/products/example/version", "quality_gate": {"status": "passed"},
        }
        self.error = error
        self.calls = []

    def build_from_prompt(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return dict(self.result)


class VideoTaskWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "data" / "initiatives" / "muse.sqlite3"
        self.database.parent.mkdir(parents=True)
        self.initiative_id = None
        with InitiativeStore(self.database) as store:
            item = store.propose(
                "研究创作短片", kind="creative_output", creator_value="研究变成作品",
                concept=json.dumps({"creator_brief": {"video": {
                    "title": "测试影片", "prompt": "source grounded story", "negative_prompt": "fake facts",
                    "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
                }}}), first_step="生成预览", expected_output="本地视频产品",
            )
            self.initiative_id = item["id"]
            store.start_video_attempt("worker-test", mode="prompt_to_video", initiative_id=item["id"])

    def tearDown(self):
        self.temp.cleanup()

    def test_worker_builds_one_queued_video_and_records_verified_artifact(self):
        builder = FakeVideoBuilder()
        worker = VideoTaskWorker(self.root, builder=builder, worker_id="test-worker")
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
        worker = VideoTaskWorker(
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
        worker = VideoTaskWorker(self.root, builder=builder, worker_id="test-worker")
        result = worker.run_once()
        self.assertEqual(result["status"], "failed")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "failed")
            self.assertEqual(attempt["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
