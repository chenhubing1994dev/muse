from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from muse.status_api import create_status_server


class CreativeStudioRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.server, self.token = create_status_server(
            task_db=self.workspace / "data" / "tasks" / "muse.sqlite3",
            progress_db=self.workspace / "data" / "tasks" / "muse.sqlite3",
            capability_db=self.workspace / "data" / "capabilities.sqlite3",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.headers = {"Authorization": f"Bearer {self.token}", "Origin": "tauri://localhost"}

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urlopen(Request(self.base + path, headers=self.headers), timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8")
        headers = {**self.headers, "Content-Type": "application/json"}
        try:
            with urlopen(Request(self.base + path, data=body, headers=headers, method="POST"), timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def test_styles_route_returns_four_presets(self) -> None:
        status, data = self._get("/api/v1/studio/styles")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["styles"]), 4)
        self.assertEqual(len(data["narration_styles"]), 4)

    def test_render_route_enqueues_and_lists_job(self) -> None:
        status, data = self._post("/api/v1/studio/render", {
            "topic": "霓虹港风测试", "style": "hk-retro", "narration_style": "none",
        })
        self.assertEqual(status, 201)
        job = data["job"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["style"], "hk-retro")
        self.assertEqual(data["plan"]["status"], "planned")

        status, listing = self._get("/api/v1/studio/render")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == job["id"] for item in listing["jobs"]))

        status, detail = self._get(f"/api/v1/studio/render/{job['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["job"]["id"], job["id"])

    def test_render_route_rejects_invalid_request(self) -> None:
        status, data = self._post("/api/v1/studio/render", {"topic": "", "style": "bad"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_generate_script_and_philosophy_inject_routes(self) -> None:
        status, data = self._post("/api/v1/studio/generate-script", {
            "topic": "测试解说", "narration_style": "none",
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "skipped")

        status, data = self._post("/api/v1/studio/philosophy-inject", {"topic": "灵感"})
        self.assertEqual(status, 200)
        self.assertIn(data["status"], {"injected", "blocked"})

    def test_search_media_route_fail_closed_without_engine(self) -> None:
        status, data = self._post("/api/v1/studio/search-media", {"topic": "霓虹", "style": "hk-retro"})
        self.assertEqual(status, 200)
        self.assertIn(data["status"], {"found", "blocked"})


if __name__ == "__main__":
    unittest.main()
