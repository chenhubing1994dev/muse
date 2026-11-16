from __future__ import annotations

import hashlib
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from muse.initiative_engine import InitiativeStore
from muse.research_knowledge import ResearchKnowledgeStore
from muse.status_api import build_products, create_status_server
from muse.video_product_builder import VideoProductBuilder


class VideoDashboardIntegrationTests(unittest.TestCase):
    def test_video_product_lists_and_downloads_under_its_creator_initiative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            initiative_db = workspace / "data" / "initiatives" / "muse.sqlite3"
            with InitiativeStore(initiative_db) as store:
                initiative = store.propose(
                    "Dashboard video initiative", kind="creative_output",
                    creator_value="Make a verified local video discoverable",
                    concept="A video product folder has its own identity and must point to its creator initiative.",
                    first_step="Generate and verify a short MP4 package",
                    expected_output="MP4, ZIP and timeline association",
                )
            source = workspace / "fixture.mp4"
            source.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2avc1mp41")
            built = VideoProductBuilder(workspace).package_generated_clip(
                "Dashboard integration clip", "A short test clip",
                source,
                generation_metadata={
                    "status": "completed", "model": "fixture", "seed": 7,
                    "aspect_ratio": "16:9", "fps": 12, "duration_seconds": 1.0,
                    "frames": 2, "width": 64, "height": 64, "steps": 8,
                },
                creator_initiative_id=initiative["id"],
            )
            products_dir = workspace / "data" / "products"
            knowledge_db = workspace / "data" / "knowledge.sqlite3"
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                node = knowledge.upsert_node(
                    "decision", "Independent review evidence",
                    summary="A reviewed decision is reachable from the creator timeline.",
                    metadata={"epistemic_status": "engineering_validation_not_external_evidence"},
                )
                with InitiativeStore(initiative_db) as initiatives:
                    knowledge.link_node_to_initiative(
                        initiatives, initiative["id"], node["id"], relationship="related",
                    )
            server, token = create_status_server(
                task_db=initiative_db, progress_db=initiative_db,
                capability_db=initiative_db, products_dir=products_dir,
                knowledge_db=knowledge_db,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            headers = {"Authorization": f"Bearer {token}", "Origin": "tauri://localhost"}
            try:
                with urlopen(Request(base + "/api/v1/products", headers=headers), timeout=5) as response:
                    listing = json.loads(response.read())
                product = next(
                    item for item in listing["products"]
                    if item["initiative_id"] == built["initiative_id"]
                    and item["version_id"] == built["version_id"]
                )
                self.assertEqual(product["creator_initiative_id"], initiative["id"])
                self.assertTrue(product["creator_feedback_enabled"])
                self.assertEqual(product["video_metrics"]["duration_seconds"], 1.0)
                self.assertEqual(product["video_metrics"]["fps"], 12)
                self.assertEqual(product["video_metrics"]["width"], 64)
                self.assertEqual(product["video_metrics"]["height"], 64)
                self.assertEqual(product["video_mp4_sha256"], built["mp4_sha256"])
                self.assertEqual(product["video_package_sha256"], built["package_sha256"])
                review_request = Request(
                    base + f"/api/v1/initiatives/{product['creator_initiative_id']}/feedback",
                    data=json.dumps({
                        "feedback": "请改善镜头运动连贯性",
                        "decision": "revise",
                        "source": "dashboard",
                        "reviewer": "creator",
                    }).encode("utf-8"),
                    headers={**headers, "Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(review_request, timeout=5) as response:
                    review = json.loads(response.read())["feedback"]
                self.assertEqual(review["decision"], "revise")
                self.assertIsNone(review["evolution_cycle_id"])
                with urlopen(Request(
                    base + f"/api/v1/initiatives/{product['creator_initiative_id']}/timeline",
                    headers=headers,
                ), timeout=5) as response:
                    reviewed_timeline = json.loads(response.read())
                self.assertTrue(any(
                    item["event_type"] == "feedback_recorded"
                    and item["details"].get("decision") == "revise"
                    for item in reviewed_timeline["events"]
                ))
                with urlopen(Request(
                    base + f"/api/v1/initiatives/{product['creator_initiative_id']}/timeline",
                    headers=headers,
                ), timeout=5) as response:
                    timeline = json.loads(response.read())
                self.assertEqual(timeline["initiative"]["id"], initiative["id"])
                event = next(item for item in timeline["events"] if item["id"] == built["ledger_event_id"])
                self.assertEqual(event["details"]["artifact_metadata"]["mp4_sha256"], built["mp4_sha256"])
                linked = next(item for item in timeline["events"] if item["event_type"] == "knowledge_linked")
                self.assertEqual(linked["details"]["knowledge_node_id"], node["id"])
                with urlopen(Request(
                    base + f"/api/v1/knowledge/nodes/{node['id']}", headers=headers,
                ), timeout=5) as response:
                    node_detail = json.loads(response.read())["node"]
                self.assertEqual(node_detail["title"], "Independent review evidence")
                self.assertEqual(node_detail["metadata"]["epistemic_status"], "engineering_validation_not_external_evidence")
                with urlopen(Request(base + product["download_path"], headers=headers), timeout=5) as response:
                    archive_bytes = response.read()
                self.assertEqual(hashlib.sha256(archive_bytes).hexdigest(), built["package_sha256"])
                with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertEqual(set(archive.namelist()), {"README.md", "storyboard.json", "qa-report.json", "video.mp4"})
                with urlopen(Request(base + product["video_path"], headers=headers), timeout=5) as response:
                    video_bytes = response.read()
                    self.assertEqual(response.headers.get_content_type(), "video/mp4")
                self.assertEqual(hashlib.sha256(video_bytes).hexdigest(), built["mp4_sha256"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


    def test_video_product_without_explicit_initiative_gets_reviewable_creator_initiative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "unlinked.mp4"
            source.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2avc1mp41")
            VideoProductBuilder(workspace).package_generated_clip(
                "Unlinked dashboard clip", "A short clip without a creator initiative",
                source,
                generation_metadata={
                    "status": "completed", "model": "fixture", "fps": 12,
                    "duration_seconds": 1.0, "frames": 2, "width": 64, "height": 64,
                },
            )
            products = build_products(workspace / "data" / "products")
            self.assertEqual(len(products), 1)
            self.assertTrue(products[0]["creator_feedback_enabled"])
            self.assertRegex(products[0]["creator_initiative_id"], r"^[0-9a-f]{32}$")




    def test_video_attempt_metrics_endpoint_reports_only_reconciled_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            initiative_db = workspace / "data" / "initiatives" / "muse.sqlite3"
            with InitiativeStore(initiative_db) as store:
                initiative = store.propose(
                    "Attempt metrics initiative", kind="creative_output",
                    creator_value="Measure verified video delivery",
                    concept="Count persisted attempts only.",
                    first_step="Record attempts", expected_output="Accurate metrics",
                )
                cycle_id = "cycle_" + "e" * 32
                store.start_video_attempt(
                    "metric-ok", mode="prompt_to_video",
                    initiative_id=initiative["id"], evolution_cycle_id=cycle_id,
                )
                store.complete_video_attempt(
                    "metric-ok", initiative_id=initiative["id"],
                    product_id="product-metric", version_id="version-metric",
                    mp4_sha256="a" * 64, package_sha256="b" * 64,
                )
                store.start_video_attempt("metric-failed", mode="local_assets")
                store.fail_video_attempt("metric-failed", error_type="RuntimeError")
                store.start_video_attempt("metric-pending", mode="prompt_to_video")
            server, token = create_status_server(
                task_db=initiative_db, progress_db=initiative_db,
                capability_db=initiative_db, products_dir=workspace / "data" / "products",
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            headers = {"Authorization": f"Bearer {token}", "Origin": "tauri://localhost"}
            try:
                with urlopen(Request(base + "/api/v1/video-attempts", headers=headers), timeout=5) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["metrics"]["total"], 3)
                self.assertEqual(payload["metrics"]["succeeded"], 1)
                self.assertEqual(payload["metrics"]["failed"], 1)
                self.assertEqual(payload["metrics"]["pending"], 1)
                self.assertEqual(payload["metrics"]["terminal"], 2)
                self.assertEqual(payload["metrics"]["success_rate"], 0.5)
                self.assertEqual(payload["metrics"]["success_rate_numerator"], 1)
                self.assertEqual(payload["metrics"]["success_rate_denominator"], 2)
                success_attempt = next(
                    item for item in payload["attempts"] if item["attempt_id"] == "metric-ok"
                )
                self.assertEqual(success_attempt["initiative_id"], initiative["id"])
                self.assertEqual(success_attempt["evolution_cycle_id"], cycle_id)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
