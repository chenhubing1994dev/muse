from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from muse.status_api import create_status_server


class VideoProductApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "data" / "video_sources" / "demo"
        source.mkdir(parents=True)
        (source / "01.png").write_bytes(b"image fixture")
        (source / "voice.wav").write_bytes(b"audio fixture")
        self.products = self.root / "data" / "products"
        self.products.mkdir(parents=True)
        self.server, self.token = create_status_server(
            task_db=self.root / "tasks.sqlite3", progress_db=self.root / "progress.sqlite3",
            capability_db=self.root / "capabilities.sqlite3", products_dir=self.products,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def _inventory(self):
        return SimpleNamespace(tools=(SimpleNamespace(tool_id="ffmpeg", executable="ffmpeg"),
                                      SimpleNamespace(tool_id="ffprobe", executable="ffprobe")),
                               hardware_encoders=("h264_amf",))

    @staticmethod
    def _render(source, output, **kwargs):
        output.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00fixture")
        return {"status": "completed", "output": str(output), "scene_count": 1,
                "duration_seconds": 4.0, "width": 1920, "height": 1080, "fps": 30.0,
                "has_audio": kwargs.get("audio_path") is not None, "encoder": "h264_amf", "bytes": output.stat().st_size,
                "audio_mode": kwargs.get("audio_mode")}

    def _request(self, path, *, method="GET", payload=None):
        headers = {"Authorization": "Bearer " + self.token}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        return Request(self.base + path, data=body, headers=headers, method=method)

    def test_video_product_listing_preview_mp4_and_zip_download(self):
        from muse.video_product_builder import VideoProductBuilder
        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render):
            product = VideoProductBuilder(self.root).build_from_source("demo", "API 测试视频", audio_name="voice.wav")
        listing = json.loads(urlopen(self._request("/api/v1/products")).read())
        item = next(row for row in listing["products"] if row["kind"] == "video_creator")
        self.assertEqual(item["initiative_id"], product["initiative_id"])
        with urlopen(self._request(item["preview_path"])) as response:
            self.assertEqual(response.headers.get_content_type(), "video/mp4")
            self.assertEqual(response.headers.get("Content-Disposition"), "inline; filename=\"muse-video-" + product["initiative_id"][:8] + ".mp4\"")
            self.assertTrue(response.read().startswith(b"\x00\x00\x00\x18ftyp"))
        with urlopen(self._request(item["video_path"])) as response:
            self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
            self.assertTrue(response.read().startswith(b"\x00\x00\x00\x18ftyp"))
        with urlopen(self._request(item["download_path"])) as response:
            self.assertEqual(response.headers.get_content_type(), "application/zip")
            self.assertTrue(response.read().startswith(b"PK"))

    def test_video_job_endpoint_runs_async_and_publishes_product(self):
        with patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render), \
             patch("muse.video_visual_evidence.create_visual_review_evidence_pack", return_value={
                 "status": "available", "evidence_type": "visual-review-evidence-v1",
                 "can_publish": False, "human_review_required": True,
             }):
            with urlopen(self._request("/api/v1/video-products", method="POST", payload={"source": "demo", "title": "异步视频任务", "audio_mode": "narration"})) as response:
                self.assertEqual(response.status, 202)
                job_id = json.loads(response.read())["job"]["id"]
            job = None
            for _ in range(100):
                with urlopen(self._request("/api/v1/video-jobs/" + job_id)) as response:
                    job = json.loads(response.read())["job"]
                if job["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["product"]["kind"], "video_creator")
        self.assertEqual(job["visual_review_evidence"]["status"], "available")
        self.assertFalse(job["visual_review_evidence"]["can_publish"])
        manifest = json.loads((self.products / job["product"]["initiative_id"] / job["product"]["version_id"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["video_metadata"]["audio_mode"], "narration")

    def test_local_video_api_forwards_narration_script_to_local_tts_builder(self):
        captured = {}
        def fake_build(_builder, source_key, title, **kwargs):
            captured.update({"source_key": source_key, "title": title, **kwargs})
            return {
                "validated": True, "kind": "video_creator",
                "initiative_id": "1" * 32, "version_id": "2" * 20,
                "title": title, "creator_initiative_id": None, "ledger_event_id": "evt_local_tts",
                "mp4_sha256": "a" * 64, "package_sha256": "b" * 64,
                "recipe_sha256": None,
            }
        with (
            patch("muse.video_product_builder.VideoProductBuilder.build_from_source", autospec=True, side_effect=fake_build),
            patch("muse.initiative_engine.InitiativeStore.complete_video_attempt", return_value=None),
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_visual_evidence.create_visual_review_evidence_pack", return_value={
                "status": "available", "evidence_type": "visual-review-evidence-v1",
                "can_publish": False, "human_review_required": True,
            }),
        ):
            with urlopen(self._request("/api/v1/video-products", method="POST", payload={
                "mode": "local_assets", "source": "demo", "title": "本地粤语旁白 API 测试",
                "audio_mode": "music", "narration_script": "你好，欢迎使用 Muse 本地粤语配音与字幕校验。",
                "motion_profile": "brand_intro_outro",
            })) as response:
                self.assertEqual(response.status, 202)
                job_id = json.loads(response.read())["job"]["id"]
            job = None
            for _ in range(100):
                with urlopen(self._request("/api/v1/video-jobs/" + job_id)) as response:
                    job = json.loads(response.read())["job"]
                if job["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(captured["narration_script"], "你好，欢迎使用 Muse 本地粤语配音与字幕校验。")
        self.assertEqual(captured["audio_mode"], "music")
        self.assertEqual(captured["motion_profile"], "brand_intro_outro")
        self.assertFalse(job["visual_review_evidence"]["can_publish"])

    def test_ai_prompt_video_api_rejects_direct_generation_without_commander_admission(self):
        with patch("muse.wan_video.generate_wan21_t2v") as generate:
            with self.assertRaises(HTTPError) as raised:
                urlopen(self._request("/api/v1/video-products", method="POST", payload={
                    "mode": "prompt_to_video", "title": "AI 狐狸短片", "prompt": "一只狐狸穿过晨雾竹林",
                    "negative_prompt": "模糊、水印", "duration_seconds": 2, "steps": 8, "seed": 42,
                }))
        self.assertEqual(raised.exception.code, 409)
        body = json.loads(raised.exception.read())
        self.assertEqual(body["code"], "video.commander_admission_required")
        self.assertTrue(body["approval_required"])
        generate.assert_not_called()
        self.assertEqual(list(self.products.iterdir()), [])


    def test_commander_video_approval_roundtrip_queues_one_exact_execution_ticket(self):
        from test_video_task_worker import strict_test_planner
        from muse.task_queue import TaskQueue

        payload = {
            "request_id": "a" * 32,
            "title": "AI 自动化赚钱",
            "prompt": "展示一个真实、可复核的 AI 自动化工作流；不得编造收入、客户或数据。",
            "negative_prompt": "fake testimonials, fabricated earnings, watermarks",
            "aspect_ratio": "9:16",
            "duration_seconds": 2,
            "steps": 8,
            "narration_script": "介绍自动化流程的真实步骤，并明确区分已验证事实与待验证收益。",
        }
        with patch("muse.status_api._build_video_commander_planner", return_value=strict_test_planner()):
            with urlopen(self._request("/api/v1/video-approval-requests", method="POST", payload=payload)) as response:
                self.assertEqual(response.status, 202)
                created = json.loads(response.read())
        approval_id = created["approval"]["task_id"]
        self.assertEqual(created["approval"]["status"], "waiting_approval")
        self.assertEqual(created["approval"]["scope"], "one_video_attempt_only")
        self.assertEqual(created["evidence_state"], "needs_more_evidence")

        with urlopen(self._request("/api/v1/video-approval-requests?limit=12")) as response:
            pending = json.loads(response.read())["approval_requests"]
        request_row = next(item for item in pending if item["id"] == approval_id)
        self.assertTrue(request_row["can_approve"])
        self.assertEqual(request_row["status"], "waiting_approval")

        with self.assertRaises(HTTPError) as rejected:
            urlopen(self._request(
                f"/api/v1/video-approval-requests/{approval_id}/approve",
                method="POST", payload={"confirmation": "yes"},
            ))
        self.assertEqual(rejected.exception.code, 409)
        self.assertFalse(json.loads(rejected.exception.read())["approval_persisted"])

        approve_path = f"/api/v1/video-approval-requests/{approval_id}/approve"
        with urlopen(self._request(
            approve_path, method="POST", payload={"confirmation": "APPROVE-VIDEO-ONCE"},
        )) as response:
            self.assertEqual(response.status, 202)
            approved = json.loads(response.read())
        self.assertTrue(approved["approval_persisted"])
        self.assertTrue(approved["execution_queued"])
        self.assertTrue(approved["attempt_id"].startswith("biz-"))
        self.assertRegex(approved["execution_ticket_id"], r"^[0-9a-f]{32}$")

        # Retrying the same explicit approval is idempotent: no second GPU ticket.
        with urlopen(self._request(
            approve_path, method="POST", payload={"confirmation": "APPROVE-VIDEO-ONCE"},
        )) as response:
            replay = json.loads(response.read())
        self.assertEqual(replay["execution_ticket_id"], approved["execution_ticket_id"])
        self.assertEqual(replay["attempt_id"], approved["attempt_id"])

        with urlopen(self._request("/api/v1/video-approval-requests?limit=12")) as response:
            approved_rows = json.loads(response.read())["approval_requests"]
        approved_row = next(item for item in approved_rows if item["id"] == approval_id)
        self.assertFalse(approved_row["can_approve"])
        self.assertEqual(approved_row["execution_ticket_id"], approved["execution_ticket_id"])
        self.assertEqual(approved_row["execution_status"], "queued")

        with urlopen(self._request("/api/v1/video-jobs/" + approved["attempt_id"])) as response:
            job = json.loads(response.read())["job"]
        self.assertEqual(job["status"], "queued")
        self.assertTrue(job["worker_available"])
        self.assertEqual(job["worker_mode"], "commander_background")
        self.assertEqual(job["execution_ticket_id"], approved["execution_ticket_id"])

        with TaskQueue(self.root / "tasks.sqlite3") as queue:
            execution_tickets = queue.list_tasks(kind="video_pipeline", limit=20)
            self.assertEqual(len(execution_tickets), 1)
            self.assertEqual(execution_tickets[0]["id"], approved["execution_ticket_id"])
            self.assertEqual(execution_tickets[0]["payload"]["attempt_id"], approved["attempt_id"])
            decisions = queue.events(approval_id, limit=50)
        self.assertTrue(any(
            event.get("event_type") == "human.decision"
            and event.get("actor") == "local-dashboard-user"
            and event.get("payload", {}).get("decision") == "approved"
            for event in decisions
        ))

    def test_generic_video_product_contact_sheet_and_visual_review_flow(self):
        import hashlib
        from muse.video_human_review import CHECKLIST_KEYS
        from muse.video_visual_evidence import create_visual_review_evidence_pack

        initiative_id, version_id = "c" * 32, "d" * 20
        product_dir = self.products / initiative_id / version_id
        product_dir.mkdir(parents=True)
        video = product_dir / "video.mp4"
        video.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        qa = {"schema_version": 1, "status": "passed", "path": "video.mp4",
              "artifact_sha256": hashlib.sha256(video.read_bytes()).hexdigest()}
        (product_dir / "qa-report.json").write_text(json.dumps(qa), encoding="utf-8")
        (product_dir / "README.md").write_text("video product", encoding="utf-8")
        (product_dir / "storyboard.json").write_text("{}", encoding="utf-8")
        (product_dir / "product.zip").write_bytes(b"zip fixture")
        names = ("README.md", "storyboard.json", "video.mp4", "product.zip", "qa-report.json")
        manifest = {
            "product_kind": "video_creator", "initiative_id": initiative_id,
            "version_id": version_id, "title": "Generic product", "status": "testing",
            "quality_gate": {"status": "passed", "passed": True, "ready_to_publish": False,
                             "report": "qa-report.json"},
            "files": {name: hashlib.sha256((product_dir / name).read_bytes()).hexdigest() for name in names},
        }
        (product_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        def fake_contact_sheet(source, target, **kwargs):
            target.write_bytes(
                b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"fixture"
            )
            return {"status": "completed", "width": 640, "height": 480, "image_decode": "passed"}

        def verified(*_args, **_kwargs):
            return {"validated": True, "quality_gate": {"status": "passed", "passed": True}}

        with patch("muse.video_product_builder.VideoProductBuilder.verify_path", side_effect=verified):
            pack = create_visual_review_evidence_pack(
                self.products, initiative_id, version_id, contact_sheet_factory=fake_contact_sheet,
            )
            self.assertEqual(pack["status"], "available")
            sheet_path = f"/api/v1/products/{initiative_id}/{version_id}/contact-sheet"
            with urlopen(self._request(sheet_path)) as response:
                self.assertEqual(response.headers.get_content_type(), "image/png")
                self.assertTrue(response.read().startswith(b"\x89PNG"))
            review_path = f"/api/v1/products/{initiative_id}/{version_id}/visual-review"
            with urlopen(self._request(review_path)) as response:
                review_state = json.loads(response.read())
                self.assertEqual(review_state["count"], 0)
                self.assertEqual(review_state["visual_quality"]["status"], "unavailable")
                self.assertIsNotNone(review_state["evidence"]["visual_quality_report_sha256"])
            payload = {
                "decision": "approved", "reviewer": "Local Editor", "reviewer_role": "video_editor",
                "rationale": "Reviewed the complete video and contact sheet carefully.",
                "checklist": {key: True for key in CHECKLIST_KEYS},
                "confirmation": "RECORD-VISUAL-REVIEW",
            }
            with urlopen(self._request(review_path, method="POST", payload=payload)) as response:
                self.assertEqual(response.status, 201)
                self.assertEqual(json.loads(response.read())["record"]["decision"], "approved")
            with urlopen(self._request(review_path)) as response:
                history = json.loads(response.read())
                self.assertEqual(history["count"], 1)
                self.assertEqual(history["reviews"][0]["evidence_status"], "verified_human_review_digest_scope_bound")

    def test_video_job_rejects_missing_source_and_unauthorized_request(self):
        with self.assertRaises(HTTPError) as missing:
            urlopen(self._request("/api/v1/video-products", method="POST", payload={"source": "absent", "title": "bad"}))
        self.assertEqual(missing.exception.code, 400)
        missing.exception.close()
        request = Request(self.base + "/api/v1/video-products", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as unauthorized:
            urlopen(request)
        self.assertEqual(unauthorized.exception.code, 401)
        unauthorized.exception.close()


if __name__ == "__main__":
    unittest.main()
