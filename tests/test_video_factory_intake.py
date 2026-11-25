from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.initiative_engine import InitiativeStore
from muse.task_queue import TaskQueue
from muse.unified_runtime import MuseRuntime
from muse.video_factory import main as video_factory_main
from muse.video_factory_intake import (
    _fetch_public_https_url,
    _validate_public_https_url,
    create_video_task,
    queue_approved_video_pipeline,
)
from test_video_task_worker import strict_test_planner
from muse.video_task_worker import (
    VideoTaskWorker,
    load_prebuilt_narration_script,
    verify_video_package_artifacts,
)


class VideoFactoryIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_topic(self) -> dict:
        return create_video_task(
            self.workspace,
            commander_planner=strict_test_planner(),
            topic="人工智能如何改变未来十年",
            duration_seconds=30,
            aspect_ratio="9:16",
        )

    def test_topic_intake_creates_immutable_review_package_and_commander_approval(self) -> None:
        result = self._create_topic()
        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["execution"], "not_started")
        self.assertEqual(result["dispatch_authority"], "Muse Commander")
        self.assertFalse(result["auto_publish"])
        self.assertGreaterEqual(result["scene_count"], 6)
        self.assertGreaterEqual(result["narration_segment_count"], 3)

        manifest_path = self.workspace / result["artifact_paths"]["manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["approval_task_id"], result["approval_task_id"])
        self.assertEqual(manifest["execution"], "not_started")
        self.assertTrue(manifest["requires_fact_check"])

        script_wrapper = json.loads((self.workspace / result["artifact_paths"]["script"]).read_text(encoding="utf-8"))
        storyboard_wrapper = json.loads((self.workspace / result["artifact_paths"]["storyboard"]).read_text(encoding="utf-8"))
        bible_wrapper = json.loads((self.workspace / result["artifact_paths"]["story_bible"]).read_text(encoding="utf-8"))
        brief_wrapper = json.loads((self.workspace / result["artifact_paths"]["video_brief"]).read_text(encoding="utf-8"))
        self.assertEqual(script_wrapper["script_sha256"], manifest["script_sha256"])
        self.assertEqual(len(storyboard_wrapper["storyboard"]["scenes"]), result["scene_count"])
        self.assertTrue(bible_wrapper["story_bible"]["continuity"]["rules"])
        self.assertFalse(brief_wrapper["video_brief"]["execution_policy"]["cloud_paid_enabled"])
        self.assertTrue(brief_wrapper["video_brief"]["quality_policy"]["reject_nonconforming_shots"])
        self.assertFalse(brief_wrapper["video_brief"]["quality_policy"]["allow_quality_downgrade"])
        self.assertEqual(brief_wrapper["video_brief_sha256"], result["video_brief_sha256"])

        task_db = self.workspace / "data" / "tasks" / "muse.sqlite3"
        with TaskQueue(task_db) as queue:
            approval = queue.get(result["approval_task_id"])
            self.assertEqual(approval["status"], "waiting_approval")
            self.assertEqual(approval["payload"]["kind"], "creator_initiative_video_approval")
            self.assertIs(approval["payload"]["auto_publish"], False)
            self.assertIn("commander_plan_snapshot", approval["payload"])
            self.assertEqual(approval["payload"]["video_brief"], brief_wrapper["video_brief"])

        verified_script = verify_video_package_artifacts(
            self.workspace, brief_wrapper["video_brief"],
        )
        self.assertEqual(len(verified_script["segments"]), result["narration_segment_count"])

    def test_human_approval_creates_commander_execution_ticket_and_passes_runtime_gate(self) -> None:
        result = self._create_topic()
        task_db = self.workspace / "data" / "tasks" / "muse.sqlite3"
        initiative_db = self.workspace / "data" / "initiatives" / "muse.sqlite3"
        with TaskQueue(task_db) as queue:
            queue.approve(result["approval_task_id"], approved_by="module3-test")
            queued = queue_approved_video_pipeline(self.workspace, result["approval_task_id"])
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["gpu_execution"], "not_started")
            self.assertFalse(queued["auto_publish"])
            execution_ticket = queue.get(queued["execution_task_id"])
            self.assertEqual(execution_ticket["payload"]["kind"], "video_pipeline")
            self.assertEqual(execution_ticket["payload"]["approval_task_id"], result["approval_task_id"])
            validation = MuseRuntime._validate_video_pipeline_execution_ticket(queue, execution_ticket)
            self.assertTrue(validation)

            with InitiativeStore(initiative_db) as store:
                attempt = store.get_video_attempt(result["attempt_id"])
                evidence = VideoTaskWorker._commander_approval_evidence(
                    store, queue, attempt, self.workspace,
                )
                self.assertIsNotNone(evidence)
                self.assertEqual(evidence["approval_task_id"], result["approval_task_id"])

            second = queue_approved_video_pipeline(self.workspace, result["approval_task_id"])
            self.assertEqual(second["execution_task_id"], queued["execution_task_id"])

    def test_unapproved_ticket_cannot_be_queued(self) -> None:
        result = self._create_topic()
        with self.assertRaisesRegex(ValueError, "must be approved"):
            queue_approved_video_pipeline(self.workspace, result["approval_task_id"])

    def test_prebuilt_script_tampering_is_rejected(self) -> None:
        result = self._create_topic()
        brief_wrapper = json.loads(
            (self.workspace / result["artifact_paths"]["video_brief"]).read_text(encoding="utf-8")
        )
        brief = brief_wrapper["video_brief"]
        script_path = self.workspace / brief["narration"]["script_path"]
        wrapper = json.loads(script_path.read_text(encoding="utf-8"))
        wrapper["script"]["segments"][0]["text"] += "篡改"
        script_path.write_text(json.dumps(wrapper, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_prebuilt_narration_script(self.workspace, brief["narration"])

    def test_same_topic_is_idempotent(self) -> None:
        first = self._create_topic()
        second = self._create_topic()
        self.assertEqual(first["approval_task_id"], second["approval_task_id"])
        self.assertEqual(first["attempt_id"], second["attempt_id"])
        self.assertEqual(first["video_brief_sha256"], second["video_brief_sha256"])

    def test_script_file_input_preserves_user_text(self) -> None:
        script_path = self.workspace / "user_script.md"
        source = "第一段，先观察变化。第二段，再看真实影响。第三段，最后讨论风险与选择。"
        script_path.write_text(source, encoding="utf-8")
        result = create_video_task(
            self.workspace,
            commander_planner=strict_test_planner(),
            script_file=script_path,
            title="用户提供的短片脚本",
            duration_seconds=30,
        )
        wrapper = json.loads((self.workspace / result["artifact_paths"]["script"]).read_text(encoding="utf-8"))
        joined = "".join(segment["text"] for segment in wrapper["script"]["segments"])
        self.assertEqual(joined, source)
        self.assertEqual(wrapper["script"]["source"], "user_supplied_script")

    def test_url_extraction_uses_readable_html_and_ignores_script_content(self) -> None:
        from email.message import Message
        import socket

        class FakeResponse:
            def __init__(self) -> None:
                self.headers = Message()
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://example.com/article"

            def read(self, _limit):
                return (
                    b"<html><head><title>Muse Video Source</title>"
                    b"<script>secret_script_payload()</script></head>"
                    b"<body><article><h1>Article</h1><p>"
                    b"This is enough readable article text for a source extraction smoke test. "
                    b"It contains context for a short video without invoking any external model."
                    b"</p></article></body></html>"
                )

        class FakeOpener:
            def open(self, *_args, **_kwargs):
                return FakeResponse()

        dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("muse.video_factory_intake.socket.getaddrinfo", return_value=dns):
            with patch("muse.video_factory_intake.build_opener", return_value=FakeOpener()):
                result = _fetch_public_https_url("https://example.com/article")
        self.assertEqual(result["title"], "Muse Video Source")
        self.assertIn("readable article text", result["text"])
        self.assertNotIn("secret_script_payload", result["text"])

    def test_url_input_creates_a_source_attributed_package(self) -> None:
        with patch("muse.video_factory_intake._fetch_public_https_url", return_value={
            "url": "https://example.com/article",
            "title": "来源页面标题",
            "text": "这是一段用于本地测试的来源摘要。" * 30,
        }):
            result = create_video_task(
                self.workspace, commander_planner=strict_test_planner(), source_url="https://example.com/article",
                duration_seconds=30,
            )
        manifest = json.loads(
            (self.workspace / result["artifact_paths"]["manifest"]).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["source_kind"], "url")
        self.assertEqual(manifest["source_url"], "https://example.com/article")
        self.assertEqual(result["status"], "approval_required")

    def test_url_guard_blocks_private_and_non_https_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            _validate_public_https_url("http://example.com/article")
        with patch("muse.video_factory_intake.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]):
            with self.assertRaisesRegex(ValueError, "blocks private"):
                _validate_public_https_url("https://example.com/article")

    def test_cli_create_and_queue_approved(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = video_factory_main([
                "create", "--topic", "AI 与未来生活", "--workspace", str(self.workspace),
            ], commander_planner=strict_test_planner())
        self.assertEqual(code, 0)
        created = json.loads(output.getvalue())
        self.assertEqual(created["status"], "approval_required")

        with TaskQueue(self.workspace / "data" / "tasks" / "muse.sqlite3") as queue:
            queue.approve(created["approval_task_id"], approved_by="cli-test")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = video_factory_main([
                "queue-approved", created["approval_task_id"], "--workspace", str(self.workspace),
            ])
        self.assertEqual(code, 0)
        queued = json.loads(output.getvalue())
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["gpu_execution"], "not_started")


if __name__ == "__main__":
    unittest.main()
