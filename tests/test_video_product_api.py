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
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render):
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
        manifest = json.loads((self.products / job["product"]["initiative_id"] / job["product"]["version_id"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["video_metadata"]["audio_mode"], "narration")

    def test_ai_prompt_video_job_runs_local_wan_and_publishes_product(self):
        def fake_generate(prompt, output, **kwargs):
            output.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return {"status": "completed", "prompt_id": "wan-prompt-123", "model": "Wan 2.1 T2V 1.3B",                    "seed": kwargs["seed"], "aspect_ratio": kwargs["aspect_ratio"], "fps": 12,
                    "duration_seconds": 2.0, "frames": 25, "width": 512, "height": 288,
                    "steps": kwargs["steps"], "bytes": output.stat().st_size, "output": str(output)}
        with patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_generate):
            with urlopen(self._request("/api/v1/video-products", method="POST", payload={
                "mode": "prompt_to_video", "title": "AI 狐狸短片", "prompt": "一只狐狸穿过晨雾竹林",
                "negative_prompt": "模糊、水印", "duration_seconds": 2, "steps": 8, "seed": 42,
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
        product_dir = self.products / job["product"]["initiative_id"] / job["product"]["version_id"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        storyboard = json.loads((product_dir / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["execution"]["ai_generation"], "completed")
        self.assertEqual(storyboard["prompt"], "一只狐狸穿过晨雾竹林")

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
