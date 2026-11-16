from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from muse.capabilities import CapabilityRegistry
from muse.task_cli import approve_task, main
from muse.task_queue import TaskQueue


class TaskCliTests(unittest.TestCase):
    def test_approve_task_uses_persistent_queue_and_returns_ready_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("publish", {"kind": "publish"})
                queue.claim_next()
                queue.finish(task["id"], status="waiting_approval")
            approved = approve_task(database, task["id"], "user")
            self.assertEqual(approved["status"], "queued")
            with TaskQueue(database) as queue:
                self.assertTrue(queue.get(task["id"])["payload"]["_approved"])

    def test_enqueue_transcode_creates_previewed_but_unapproved_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mov"
            source.write_bytes(b"source")
            task_db = root / "tasks.sqlite3"
            capability_db = root / "capabilities.sqlite3"
            with CapabilityRegistry(capability_db) as registry:
                registry.register(
                    capability_id="media.ffmpeg", domain="media", name="FFmpeg",
                    description="Verified local FFmpeg", executable="ffmpeg.exe",
                    capabilities=("video_transcode",), status="ready",
                    trust="local_path_discovered", license="not_applicable",
                    metadata={"hardware_encoders": ["h264_amf"]},
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "--db", str(task_db), "enqueue-transcode",
                    "--capability-db", str(capability_db),
                    "--input", str(source), "--output", str(root / "output.mp4"),
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["preview"]["status"], "planned")
            self.assertEqual(result["task"]["status"], "queued")
            self.assertNotIn("_approved", result["task"]["payload"])
            with TaskQueue(task_db) as queue:
                self.assertEqual(queue.snapshot()["counts"]["queued"], 1)


    def test_cancel_command_cancels_queued_task_and_reports_actor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("obsolete", {})
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--db", str(database), "cancel", task["id"], "--by", "user", "--reason", "superseded"])
            self.assertEqual(code, 0)
            self.assertIn("cancelled", output.getvalue())
            with TaskQueue(database) as queue:
                cancelled = queue.get(task["id"])
            self.assertEqual(cancelled["payload"]["_cancellation"]["reason"], "superseded")

    def test_list_command_shows_queue_snapshot_without_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("visible task", {"secret": "private payload"})
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--db", str(database), "list"])
            snapshot = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(snapshot["counts"]["queued"], 1)
            self.assertEqual(snapshot["active"][0]["id"], task["id"])
            self.assertEqual(snapshot["active"][0]["title"], "visible task")
            self.assertNotIn("payload", snapshot["active"][0])
            self.assertNotIn("private payload", output.getvalue())

if __name__ == "__main__":
    unittest.main()
