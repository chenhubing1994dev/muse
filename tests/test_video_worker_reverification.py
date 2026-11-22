from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.task_queue import TaskQueue
from muse.video_task_worker import VideoTaskWorker
from test_video_task_worker import FakeAdmissionProvider, fake_admission_request_factory


class ReverifyBuilder:
    def __init__(self, mismatch=None):
        self.mismatch = mismatch
        self.verify_calls = []
        self.result = {
            "validated": True,
            "initiative_id": "a" * 32,
            "version_id": "b" * 20,
            "mp4_sha256": "c" * 64,
            "package_sha256": "d" * 64,
            "manifest_sha256": "e" * 64,
            "relative_path": f"data/products/{'a' * 32}/{'b' * 20}",
            "quality_gate": {"status": "passed"},
        }

    def build_from_prompt(self, *args, **kwargs):
        return dict(self.result)

    def build_from_scenes(self, *args, **kwargs):
        return dict(self.result)

    def verify_path(self, products_root, initiative_id, version_id):
        self.verify_calls.append((Path(products_root), initiative_id, version_id))
        verified = {
            "validated": True,
            "initiative_id": self.result["initiative_id"],
            "version_id": self.result["version_id"],
            "relative_path": self.result["relative_path"],
            "mp4_sha256": self.result["mp4_sha256"],
            "package_sha256": self.result["package_sha256"],
            "manifest_sha256": self.result["manifest_sha256"],
        }
        if self.mismatch:
            verified[self.mismatch] = "f" * 64
        return verified


class VideoWorkerReverificationTests(unittest.TestCase):
    def _prepare(self, root: Path, attempt_id: str):
        database = root / "data" / "initiatives" / "muse.sqlite3"
        task_database = root / "data" / "tasks" / "muse.sqlite3"
        database.parent.mkdir(parents=True)
        video_brief = {
            "title": "复核测试短片",
            "prompt": "A short test story",
            "aspect_ratio": "9:16",
            "duration_seconds": 2,
            "steps": 8,
            "recipe_sha256": "a" * 64,
        }
        with InitiativeStore(database) as store:
            initiative = store.propose(
                "Worker 二次验收", kind="creative_output",
                creator_value="防止 Builder 返回后文件被替换",
                concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                first_step="重新校验视频产品", expected_output="哈希一致的产品记录",
            )
        digest = hashlib.sha256(
            json.dumps(video_brief, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        payload = {
            "kind": "business_opportunity_video_approval",
            "opportunity_id": "reverify-test",
            "initiative_id": initiative["id"],
            "attempt_id": attempt_id,
            "video_brief_sha256": digest,
            "video_brief": video_brief,
            "approval_scope": "one_video_attempt_only",
        }
        with TaskQueue(task_database) as queue:
            ticket = queue.enqueue(
                "Approve reverify test", payload, require_approval=True,
                idempotency_key=f"reverify:{attempt_id}", workload_class="media",
            )
            queue.approve(ticket["id"], approved_by="test-operator")
        with InitiativeStore(database) as store:
            store.start_video_attempt(
                attempt_id, mode="prompt_to_video",
                initiative_id=initiative["id"], approval_task_id=ticket["id"],
                recipe_sha256=video_brief["recipe_sha256"],
            )
        return database

    def test_worker_reverifies_exact_product_version_before_recording_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._prepare(root, "reverify-success")
            builder = ReverifyBuilder()
            result = VideoTaskWorker(
                root, builder=builder, worker_id="reverify-worker",
                admission_provider=FakeAdmissionProvider(),
                admission_request_factory=fake_admission_request_factory,
            ).run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(
                builder.verify_calls,
                [((root / "data" / "products").resolve(), "a" * 32, "b" * 20)],
            )
            with InitiativeStore(database) as store:
                attempt = store.get_video_attempt("reverify-success")
            self.assertEqual(attempt["manifest_sha256"], "e" * 64)

    def test_worker_fails_closed_when_any_on_disk_digest_changes_after_build(self):
        for field in ("manifest_sha256", "mp4_sha256", "package_sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                database = self._prepare(root, f"reverify-{field}")
                builder = ReverifyBuilder(mismatch=field)
                result = VideoTaskWorker(
                    root, builder=builder, worker_id="reverify-worker",
                    admission_provider=FakeAdmissionProvider(),
                    admission_request_factory=fake_admission_request_factory,
                ).run_once()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(len(builder.verify_calls), 1)
                with InitiativeStore(database) as store:
                    attempt = store.get_video_attempt(f"reverify-{field}")
                self.assertEqual(attempt["status"], "failed")


if __name__ == "__main__":
    unittest.main()
