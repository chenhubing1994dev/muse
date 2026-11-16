from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.adapter_handlers import build_adapter_handlers
from muse.task_queue import TaskQueue
from muse.worker import run_once


class FakeAdapter:
    def __init__(self, result="completed"):
        self.result = result
        self.executed = False
        self.cancelled = False

    def execute(self, request, *, approved=False):
        self.executed = approved
        return {"status": "running", "job_id": "job-1"}

    def verify(self, job_id, *, timeout=None):
        return {"status": self.result, "artifacts": ["D:/output.mp4"] if self.result == "completed" else []}

    def cancel(self, job_id):
        self.cancelled = True
        return {"status": "cancelled"}


class AdapterWorkerHandlerTests(unittest.TestCase):
    def test_enqueue_transcode_validates_preview_and_creates_approval_gated_task(self):
        import tempfile
        from muse.capabilities import CapabilityRegistry
        from muse.adapter_handlers import enqueue_ffmpeg_task

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mov"
            source.write_bytes(b"source")
            output = root / "output.mp4"
            registry_path = root / "capabilities.sqlite3"
            with CapabilityRegistry(registry_path) as registry:
                registry.register(
                    capability_id="media.ffmpeg", domain="media", name="FFmpeg",
                    description="Verified", executable="ffmpeg.exe",
                    capabilities=("video_transcode",), status="ready",
                    trust="local_path_discovered", license="not_applicable",
                    metadata={"hardware_encoders": ["h264_amf"]},
                )
                queued, preview = enqueue_ffmpeg_task(
                    root / "tasks.sqlite3", registry,
                    {"input": str(source), "output": str(output)},
                )
            self.assertEqual(preview["status"], "planned")
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["payload"]["kind"], "ffmpeg_transcode")
            self.assertNotIn("_approved", queued["payload"])

    def test_worker_holds_media_task_until_explicit_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            adapter = FakeAdapter()
            handlers = build_adapter_handlers(
                object(), adapter_factory=lambda registry, capability_id: adapter
            )
            with TaskQueue(database) as queue:
                queued = queue.enqueue(
                    "transcode", {"kind": "ffmpeg_transcode", "request": {"input": "a.mov", "output": "a.mp4"}}
                )
            result = run_once(database, handlers, approval_required_kinds={"ffmpeg_transcode"})
            self.assertEqual(result["status"], "waiting_approval")
            self.assertFalse(adapter.executed)

    def test_approved_worker_task_executes_and_returns_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            adapter = FakeAdapter()
            handlers = build_adapter_handlers(
                object(), adapter_factory=lambda registry, capability_id: adapter
            )
            with TaskQueue(database) as queue:
                queue.enqueue(
                    "transcode", {"kind": "ffmpeg_transcode", "_approved": True,
                                   "request": {"input": "a.mov", "output": "a.mp4"}}
                )
            result = run_once(database, handlers, approval_required_kinds={"ffmpeg_transcode"})
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(adapter.executed)
            self.assertEqual(result["payload"]["_result"]["artifacts"], ["D:/output.mp4"])

    def test_worker_cancels_adapter_when_execution_times_out(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            adapter = FakeAdapter(result="running")
            handlers = build_adapter_handlers(
                object(), adapter_factory=lambda registry, capability_id: adapter
            )
            with TaskQueue(database) as queue:
                queue.enqueue(
                    "transcode", {"kind": "ffmpeg_transcode", "_approved": True,
                                   "request": {"input": "a.mov", "output": "a.mp4"}}
                )
            result = run_once(database, handlers, approval_required_kinds={"ffmpeg_transcode"})
            self.assertEqual(result["status"], "failed")
            self.assertTrue(adapter.cancelled)


    def test_enqueue_transcode_uses_manual_recovery_policy(self):
        import tempfile
        from muse.capabilities import CapabilityRegistry
        from muse.adapter_handlers import enqueue_ffmpeg_task

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mov"
            source.write_bytes(b"source")
            with CapabilityRegistry(root / "capabilities.sqlite3") as registry:
                registry.register(
                    capability_id="media.ffmpeg", domain="media", name="FFmpeg",
                    description="Verified", executable="ffmpeg.exe",
                    capabilities=("video_transcode",), status="ready",
                    trust="local_path_discovered", license="not_applicable",
                    metadata={"hardware_encoders": ["h264_amf"]},
                )
                queued, _ = enqueue_ffmpeg_task(
                    root / "tasks.sqlite3", registry,
                    {"input": str(source), "output": str(root / "output.mp4")},
                )
            self.assertEqual(queued["recovery_policy"], "manual")

if __name__ == "__main__":
    unittest.main()
