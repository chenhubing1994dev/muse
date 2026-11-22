from __future__ import annotations

import base64
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from muse.status_api import create_status_server


def archive_payload() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample/README.md", "# Browser crawler\nA web research tool.")
        archive.writestr("sample/src/crawler.py", "class Crawler: pass")
    return output.getvalue()


class SkillFusionApiTests(unittest.TestCase):
    def test_submit_review_and_read_fusion_through_authenticated_local_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "data" / "tasks" / "muse.sqlite3"
            progress_db = root / "data" / "progress" / "muse.sqlite3"
            capability_db = root / "data" / "capabilities" / "muse.sqlite3"
            task_db.parent.mkdir(parents=True)
            progress_db.parent.mkdir(parents=True)
            capability_db.parent.mkdir(parents=True)
            server, token = create_status_server(
                task_db=task_db, progress_db=progress_db, capability_db=capability_db,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            try:
                body = json.dumps({
                    "archive_base64": base64.b64encode(archive_payload()).decode("ascii"),
                    "file_name": "crawler.zip",
                }).encode("utf-8")
                request = Request(base + "/api/v1/fusion/submit", data=body, headers=headers, method="POST")
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    submitted = json.loads(response.read())
                fusion_id = submitted["fusion"]["fusion_id"]
                self.assertEqual(submitted["fusion"]["status"], "integrated_unverified")
                self.assertEqual(submitted["fusion"]["analysis"]["capabilities"][0]["id"], "web_research")
                self.assertTrue(submitted["fusion"]["registered_capability_ids"])
                with urlopen(Request(base + "/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"}), timeout=5) as response:
                    initial_capabilities = json.loads(response.read())["capabilities"]
                initial_fused = [item for item in initial_capabilities if item["capability_id"].startswith("fusion.")]
                self.assertTrue(initial_fused)
                self.assertTrue(all(item["status"] == "unverified" for item in initial_fused))

                with urlopen(Request(base + "/api/v1/fusion/jobs", headers={"Authorization": f"Bearer {token}"}), timeout=5) as response:
                    jobs = json.loads(response.read())
                self.assertEqual(jobs["jobs"][0]["fusion_id"], fusion_id)

                approval = json.dumps({"confirmation": "FUSE-CAPABILITIES"}).encode("utf-8")
                request = Request(
                    base + f"/api/v1/fusion/jobs/{fusion_id}/approve",
                    data=approval, headers=headers, method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    approved = json.loads(response.read())
                self.assertEqual(approved["fusion"]["status"], "approved_unverified")
                self.assertTrue(approved["fusion"]["registered_capability_ids"])

                with urlopen(Request(base + "/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"}), timeout=5) as response:
                    capabilities = json.loads(response.read())["capabilities"]
                fused = [item for item in capabilities if item["capability_id"].startswith("fusion.")]
                self.assertTrue(fused)
                self.assertTrue(all(item["status"] == "unverified" for item in fused))
                rollback = json.dumps({"confirmation": "ROLLBACK-FUSION"}).encode("utf-8")
                request = Request(
                    base + f"/api/v1/fusion/jobs/{fusion_id}/rollback",
                    data=rollback, headers=headers, method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    rolled_back = json.loads(response.read())
                self.assertEqual(rolled_back["fusion"]["status"], "rolled_back")
                with urlopen(Request(base + "/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"}), timeout=5) as response:
                    capabilities = json.loads(response.read())["capabilities"]
                fused = [item for item in capabilities if item["capability_id"].startswith("fusion.")]
                self.assertTrue(fused)
                self.assertTrue(all(item["status"] == "disabled" for item in fused))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_fusion_submit_rejects_unauthenticated_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_db = root / "data" / "tasks" / "muse.sqlite3"
            task_db.parent.mkdir(parents=True)
            server, _token = create_status_server(
                task_db=task_db, progress_db=root / "progress.sqlite3",
                capability_db=root / "capabilities.sqlite3",
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps({"archive_base64": base64.b64encode(archive_payload()).decode("ascii")}).encode()
                request = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/v1/fusion/submit",
                    data=body, headers={"Content-Type": "application/json"}, method="POST",
                )
                from urllib.error import HTTPError
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(request, timeout=5)
                self.assertEqual(rejected.exception.code, 401)
                rejected.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
