from __future__ import annotations

import json
import hashlib
import zipfile
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from types import SimpleNamespace
from unittest.mock import patch

from muse.agent_registry import AgentRegistry
from muse.capabilities import CapabilityRegistry
from muse.progress import ProgressEventStore
from muse.status_api import build_agents, build_capabilities, build_initiative_timeline, build_overview, build_products, build_improvement_detail, create_status_server
from muse.improvement import ImprovementStore
from muse.task_queue import TaskQueue
from muse.commander_planner import CommanderPlanner
from muse.workflow_profiles import WorkflowController
from muse.initiative_engine import InitiativeStore
from muse.product_builder import LocalProductBuilder


class StatusApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.task_db = root / "tasks.sqlite3"
        self.progress_db = root / "progress.sqlite3"
        self.capability_db = root / "capabilities.sqlite3"
        self.agent_db = root / "agents.sqlite3"
        with AgentRegistry(self.agent_db) as agents:
            agents.register("researcher", role="research", capabilities=("web.search",), source="local:fixture")
            agents.heartbeat("researcher")
            agents.record_outcome("researcher", task_kind="research", succeeded=True)
        with TaskQueue(self.task_db) as queue:
            queue.enqueue("safe task", {"private_payload": "must-not-leak"})
        with ProgressEventStore(self.progress_db) as progress:
            progress.record("task", "task-1", "started", summary="Task started")
        with CapabilityRegistry(self.capability_db) as registry:
            registry.register(
                capability_id="web.search", domain="web", name="Search",
                description="Search public sources", executable="C:/private/tool.exe",
                capabilities=("web.search",), status="ready",
                metadata={"secret_path": "C:/private"},
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_overview_never_contains_task_payload(self) -> None:
        result = build_overview(self.task_db, self.progress_db)
        encoded = json.dumps(result)
        self.assertIn("safe task", encoded)
        self.assertNotIn("must-not-leak", encoded)
        self.assertNotIn("private_payload", encoded)

    def test_overview_exposes_safe_live_commander_workflow_state(self) -> None:
        with TaskQueue(self.task_db) as queue:
            task = queue.enqueue("Commander workflow", {"private_payload": "do-not-expose"})
            queue.claim_next(worker_id="commander", task_id=task["id"])
            plan = CommanderPlanner().plan("Build a feature")
            workflow = WorkflowController(queue)
            workflow.bind_plan_to_ticket(plan, task["id"], worker_id="commander")
            workflow.record_stage(
                task["id"], worker_id="commander", stage="plan", attempt=1,
                expected_revision=1, checkpoint={"private_checkpoint": "hide-me"},
                next_action="execute",
            )
        result = build_overview(self.task_db, self.progress_db)
        self.assertEqual(len(result["workflow_runs"]), 1)
        self.assertEqual(result["workflow_runs"][0]["stage"], "plan")
        self.assertEqual(result["workflow_runs"][0]["status"], "active")
        encoded = json.dumps(result)
        self.assertNotIn("do-not-expose", encoded)
        self.assertNotIn("private_checkpoint", encoded)
        self.assertNotIn("hide-me", encoded)

    def test_capabilities_hide_executable_and_opaque_metadata(self) -> None:
        result = build_capabilities(self.capability_db)
        encoded = json.dumps(result)
        self.assertIn("web.search", encoded)
        self.assertNotIn("C:/private", encoded)
        self.assertNotIn("secret_path", encoded)

    def test_gpu_resource_endpoint_exposes_redacted_lease_and_supervisor_state(self) -> None:
        from muse.gpu_resources import GpuResourceCoordinator
        gpu_db = Path(self.temp.name) / "gpu-resources.sqlite3"
        coordinator = GpuResourceCoordinator(gpu_db)
        lease_id = coordinator.acquire("video_generation", "private-job-reference")
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, gpu_resource_db=gpu_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/gpu-resources",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                payload = json.loads(response.read())
            self.assertEqual(payload["status"], "reserved")
            self.assertEqual(payload["lease"]["owner_kind"], "video_generation")
            self.assertNotIn(lease_id, json.dumps(payload))
            self.assertNotIn("private-job-reference", json.dumps(payload))
            self.assertNotIn("owner_pid", json.dumps(payload))
            self.assertEqual(payload["model_supervisor"]["status"], "not_observed")
        finally:
            coordinator.release(lease_id)
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_video_engines_endpoint_reports_local_media_and_generation_readiness(self) -> None:
        inventory = SimpleNamespace(
            tools=[SimpleNamespace(tool_id="ffmpeg", executable="ffmpeg.exe"), SimpleNamespace(tool_id="ffprobe", executable="ffprobe.exe")],
            ffmpeg_version="ffmpeg test", hardware_encoders=("h264_amf",),
        )
        engines = {"comfyui": {"status": "unavailable", "reason": "not running"}, "velorn": {"status": "unavailable", "reason": "not running"}}
        with patch("muse.status_api.discover_media_tools", return_value=inventory), patch("muse.status_api.inspect_video_engines", return_value=engines):
            server, token = create_status_server(task_db=self.task_db, progress_db=self.progress_db, capability_db=self.capability_db)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(f"http://127.0.0.1:{server.server_address[1]}/api/v1/video-engines", headers={"Authorization": f"Bearer {token}"})
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read())
                self.assertTrue(payload["ffmpeg_available"])
                self.assertTrue(payload["ffprobe_available"])
                self.assertEqual(payload["hardware_encoders"], ["h264_amf"])
                self.assertEqual(payload["comfyui"]["status"], "unavailable")
                self.assertEqual(payload["velorn"]["status"], "unavailable")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_agents_view_exposes_status_and_metrics_without_private_fields(self) -> None:
        result = build_agents(self.agent_db)
        encoded = json.dumps(result)
        self.assertIn("researcher", encoded)
        self.assertIn("online", encoded)
        self.assertIn("success_rate", encoded)
        self.assertNotIn("executable", encoded)
        self.assertEqual(result[0]["effectiveness"]["success_rate"], 1.0)
        self.assertEqual(result[0]["stale_after_seconds"], 90)

    def test_improvement_detail_exposes_review_material_but_not_arbitrary_metadata_or_notes(self) -> None:
        improvement_db = Path(self.temp.name) / "improvements.sqlite3"
        with ImprovementStore(improvement_db) as store:
            proposal = store.create_proposal(
                title="Improve routing", problem="Routing gap", hypothesis="Use evidence",
                priority=88, metadata={
                    "confidence": 0.82, "impact": "Better assignment",
                    "acceptance_criteria": ["A deterministic test passes"],
                    "rollback_plan": "Disable the new route", "evidence": {"signal_count": 3},
                    "private_token": "must-not-leak",
                },
            )
            store.add_source(proposal["id"], title="Public docs", url="https://example.com",
                             license_status="permissive", security_status="reviewed", notes="private researcher note")
        detail = build_improvement_detail(improvement_db, proposal["id"])
        encoded = json.dumps(detail)
        self.assertEqual(detail["status"], "proposed")
        self.assertEqual(detail["acceptance_criteria"], ["A deterministic test passes"])
        self.assertEqual(detail["sources"][0]["security_status"], "reviewed")
        self.assertNotIn("must-not-leak", encoded)
        self.assertNotIn("private researcher note", encoded)
        self.assertNotIn("private_token", encoded)
        self.assertIsNone(build_improvement_detail(improvement_db, "0" * 32))
        with self.assertRaises(ValueError):
            build_improvement_detail(improvement_db, "../bad")

    def test_sse_event_stream_is_authenticated_bounded_and_redacted(self) -> None:
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            denied = Request(base + "/api/v1/events/stream", headers={"Accept": "text/event-stream"})
            with self.assertRaises(HTTPError) as rejected:
                urlopen(denied, timeout=3)
            self.assertEqual(rejected.exception.code, 401)
            rejected.exception.close()
            request = Request(base + "/api/v1/events/stream", headers={
                "Authorization": f"Bearer {token}", "Accept": "text/event-stream",
                "Origin": "tauri://localhost",
            })
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.headers["Content-Type"].split(";", 1)[0], "text/event-stream")
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "tauri://localhost")
            self.assertIn("event: muse-progress", body)
            self.assertIn('"category":"task"', body)
            self.assertNotIn("task-1", body)
            self.assertNotIn("Task started", body)
            self.assertLess(len(body), 16384)
            stale_cursor = Request(base + "/api/v1/events/stream", headers={
                "Authorization": f"Bearer {token}", "Last-Event-ID": "f" * 32,
            })
            with urlopen(stale_cursor, timeout=3) as response:
                reset_body = response.read().decode("utf-8")
            self.assertIn("event: muse-reset", reset_body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_http_api_requires_bearer_and_rejects_mutations(self) -> None:
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, agent_db=self.agent_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with self.assertRaises(HTTPError) as unauthorized:
                urlopen(base + "/api/v1/health", timeout=3)
            self.assertEqual(unauthorized.exception.code, 401)
            unauthorized.exception.close()
            request = Request(base + "/api/v1/overview", headers={"Authorization": f"Bearer {token}"})
            with urlopen(request, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["api_version"], 1)
            self.assertIn("queue", payload)
            health_request = Request(base + "/api/v1/health", headers={"Authorization": f"Bearer {token}"})
            with urlopen(health_request, timeout=3) as response:
                health_payload = json.loads(response.read())
            self.assertTrue(health_payload["agent_registry_configured"])
            agents_request = Request(base + "/api/v1/agents", headers={
                "Authorization": f"Bearer {token}", "Origin": "tauri://localhost",
            })
            with urlopen(agents_request, timeout=3) as response:
                agents_payload = json.loads(response.read())
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "tauri://localhost")
            self.assertTrue(agents_payload["registry_configured"])
            self.assertEqual(agents_payload["count"], 1)
            self.assertEqual(agents_payload["agents"][0]["agent_id"], "researcher")
            mutation = Request(base + "/api/v1/overview", data=b"{}", method="POST",
                               headers={"Authorization": f"Bearer {token}"})
            with self.assertRaises(HTTPError) as forbidden:
                urlopen(mutation, timeout=3)
            self.assertEqual(forbidden.exception.code, 405)
            forbidden.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_feedback_api_accepts_only_allowlisted_bounded_structured_feedback(self) -> None:
        improvement_db = Path(self.temp.name) / "improvements.sqlite3"
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, agent_db=self.agent_db,
            improvement_db=improvement_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        headers = {
            "Authorization": f"Bearer {token}", "Origin": "tauri://localhost",
            "Content-Type": "application/json", "Accept": "application/json",
        }
        try:
            request = Request(
                base + "/api/v1/feedback",
                data=json.dumps({"category": "usability", "tag": "repeated_manual_fix", "severity": 4}).encode(),
                headers=headers, method="POST",
            )
            with urlopen(request, timeout=3) as response:
                saved = json.loads(response.read())
                self.assertEqual(response.status, 201)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "tauri://localhost")
            self.assertEqual(saved["feedback"]["tag"], "repeated_manual_fix")
            listed = Request(base + "/api/v1/feedback?limit=10", headers={
                "Authorization": f"Bearer {token}", "Origin": "tauri://localhost",
            })
            with urlopen(listed, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["feedback"][0]["severity"], 4)

            scan = Request(
                base + "/api/v1/radar/scan", data=b"", headers={
                    "Authorization": f"Bearer {token}", "Origin": "tauri://localhost",
                }, method="POST",
            )
            with urlopen(scan, timeout=3) as response:
                scan_payload = json.loads(response.read())
            self.assertTrue(scan_payload["approval_required"])
            self.assertEqual(scan_payload["trend"]["direction"], "insufficient_data")
            self.assertGreaterEqual(len(scan_payload["created"]), 1)
            self.assertIn("user_feedback", [item["key"] for item in scan_payload["signals_seen"]])

            proposals_request = Request(base + "/api/v1/improvements", headers={
                "Authorization": f"Bearer {token}", "Origin": "tauri://localhost",
            })
            with urlopen(proposals_request, timeout=3) as response:
                proposal_payload = json.loads(response.read())
            self.assertGreaterEqual(proposal_payload["count"], 1)
            self.assertEqual(proposal_payload["proposals"][0]["status"], "proposed")

            invalid = Request(
                base + "/api/v1/feedback",
                data=json.dumps({"category": "usability", "tag": "free form personal details", "severity": 4}).encode(),
                headers=headers, method="POST",
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(invalid, timeout=3)
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()

            oversized = Request(
                base + "/api/v1/feedback",
                data=b" " * 4097, headers=headers, method="POST",
            )
            with self.assertRaises(HTTPError) as too_large:
                urlopen(oversized, timeout=3)
            self.assertEqual(too_large.exception.code, 413)
            too_large.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_feedback_api_requires_auth_and_rejects_unconfigured_store(self) -> None:
        server, _token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/feedback",
                data=b'{"category":"usability","tag":"workflow_gap","severity":3}',
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=3)
            self.assertEqual(rejected.exception.code, 401)
            rejected.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_tauri_preflight_allows_only_readonly_authorized_requests(self) -> None:
        server, _token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, agent_db=self.agent_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            allowed = Request(
                base + "/api/v1/agents", method="OPTIONS",
                headers={
                    "Origin": "tauri://localhost",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization, accept",
                },
            )
            with urlopen(allowed, timeout=3) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "tauri://localhost")
                self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET, POST")
                self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])
            post_allowed = Request(
                base + "/api/v1/feedback", method="OPTIONS",
                headers={
                    "Origin": "tauri://localhost",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization, content-type",
                },
            )
            with urlopen(post_allowed, timeout=3) as response:
                self.assertEqual(response.status, 204)
                self.assertIn("Content-Type", response.headers["Access-Control-Allow-Headers"])
            denied = Request(
                base + "/api/v1/agents", method="OPTIONS",
                headers={
                    "Origin": "tauri://localhost",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization, x-untrusted",
                },
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(denied, timeout=3)
            self.assertEqual(rejected.exception.code, 403)
            rejected.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    def test_windows_tauri_webview_origin_is_allowed_for_readonly_requests(self) -> None:
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, agent_db=self.agent_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        origin = "http://tauri.localhost"
        try:
            request = Request(
                base + "/api/v1/health",
                headers={"Authorization": f"Bearer {token}", "Origin": origin},
            )
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
            preflight = Request(
                base + "/api/v1/health", method="OPTIONS",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization, accept",
                },
            )
            with urlopen(preflight, timeout=3) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_disallowed_origin_is_rejected(self) -> None:
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/health",
                headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.example"},
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=3)
            rejected.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


    def test_agents_endpoint_distinguishes_unconfigured_registry_from_empty_registry(self) -> None:
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/agents",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertFalse(payload["registry_configured"])
            self.assertEqual(payload["count"], 0)
            self.assertEqual(payload["agents"], [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    def test_agents_endpoint_distinguishes_empty_configured_registry(self) -> None:
        empty_agent_db = Path(self.temp.name) / "empty-agents.sqlite3"
        with AgentRegistry(empty_agent_db):
            pass
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, agent_db=empty_agent_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/agents",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertTrue(payload["registry_configured"])
            self.assertEqual(payload["count"], 0)
            self.assertEqual(payload["agents"], [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_legacy_initiative_timeline_is_read_only_and_marks_unknown_history(self) -> None:
        import sqlite3

        database = Path(self.temp.name) / "legacy-initiatives.sqlite3"
        initiative_id = "a" * 32
        connection = sqlite3.connect(database)
        connection.execute(
            """CREATE TABLE creator_initiatives(
                id TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,
                status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO creator_initiatives VALUES(?,?,?,?,?,?)",
            (initiative_id, "Legacy idea", "product", "testing", "2026-01-01", "2026-01-02"),
        )
        connection.commit()
        connection.close()

        timeline = build_initiative_timeline(database, initiative_id)
        self.assertEqual(timeline["initiative"]["title"], "Legacy idea")
        self.assertEqual(timeline["events"], [])
        self.assertFalse(timeline["history_complete"])
        verify = sqlite3.connect(database)
        try:
            event_table = verify.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='initiative_events'"
            ).fetchone()
        finally:
            verify.close()
        self.assertIsNone(event_table)

    def test_video_product_listing_exposes_verified_quality_warnings_and_timestamps(self) -> None:
        products_root = Path(self.temp.name) / "data" / "products"
        initiative_id = "a" * 32
        version_id = "b" * 20
        product_dir = products_root / initiative_id / version_id
        product_dir.mkdir(parents=True)
        video = bytes.fromhex("000000186674797069736f6d66697874757265")
        readme = b"# QA fixture"
        storyboard = json.dumps({"title": "QA fixture"}).encode("utf-8")
        qa_report = {
            "schema_version": 1, "status": "passed", "path": "video.mp4",
            "artifact_sha256": hashlib.sha256(video).hexdigest(),
            "warnings": [
                {"code": "sustained_black", "message": "black frames", "start_seconds": 2.5,
                 "duration_seconds": 1.25, "review_threshold_seconds": 0.5,
                 "private_detail": "must-not-leak"},
            ],
        }
        qa_bytes = json.dumps(qa_report).encode("utf-8")
        for name, body in (("README.md", readme), ("storyboard.json", storyboard),
                           ("video.mp4", video), ("qa-report.json", qa_bytes)):
            (product_dir / name).write_bytes(body)
        with zipfile.ZipFile(product_dir / "product.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ("README.md", "storyboard.json", "qa-report.json", "video.mp4"):
                archive.write(product_dir / name, name)
        files = {
            name: hashlib.sha256((product_dir / name).read_bytes()).hexdigest()
            for name in ("README.md", "storyboard.json", "video.mp4", "qa-report.json", "product.zip")
        }
        manifest = {
            "schema_version": 1, "builder_version": 2, "product_kind": "video_creator",
            "initiative_id": initiative_id, "version_id": version_id, "title": "QA fixture",
            "status": "testing", "files": files,
            "video_metadata": {"duration_seconds": 1, "frames": 13, "width": 1080, "height": 1920, "fps": 12,
                               "motion": {"profile": "brand_intro_outro", "renderer": "remotion",
                                          "remotion_version": "4.0.543", "transition": "crossfade",
                                          "transition_seconds": 0.45, "duration_seconds": 4.416667,
                                          "source_duration_seconds": 1.083333, "audio_mode": "music",
                                          "private_detail": "must-not-leak"}},
            "quality_gate": {
                "status": "passed", "passed": True, "ready_to_publish": False,
                "report": "qa-report.json", "review_warning_count": 1,
                "review_warning_codes": ["sustained_black"], "warning_review_required": True,
            },
        }
        (product_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        rows = build_products(products_root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quality_gate"]["review_warning_count"], 1)
        self.assertTrue(rows[0]["quality_gate"]["warning_review_required"])
        self.assertEqual(rows[0]["quality_warnings"][0]["start_seconds"], 2.5)
        self.assertEqual(rows[0]["motion"]["renderer"], "remotion")
        self.assertEqual(rows[0]["motion"]["audio_mode"], "music")
        self.assertAlmostEqual(rows[0]["video_metrics"]["duration_seconds"], 4.416667)
        self.assertEqual(rows[0]["video_metrics"]["frames"], 53)
        self.assertNotIn("private_detail", json.dumps(rows))
        self.assertEqual(rows[0]["quality_warnings"][0]["duration_seconds"], 1.25)
        self.assertNotIn("private_detail", json.dumps(rows))

    def test_creator_inspiration_inbox_and_research_report_download(self) -> None:
        workspace = Path(self.temp.name)
        knowledge_db = workspace / "knowledge.sqlite3"
        report_dir = workspace / "reports"
        products_dir = workspace / "data" / "products"
        report_dir.mkdir()
        report_name = "autonomous-research-example-20261110.md"
        with InitiativeStore(workspace / "data" / "initiatives" / "muse.sqlite3") as initiatives:
            initiative = initiatives.propose(
                "状态变化实验工具", kind="tool", creator_value="把灵感变成实验",
                concept=json.dumps({"research_question": "如何识别变化？",
                                    "key_discoveries": {"creator_inspired_hypotheses": ["分层状态有助于复盘。"]}}, ensure_ascii=False),
                first_step="记录状态", expected_output="离线实验工具",
            )
            product = LocalProductBuilder(workspace).build(initiatives, initiative["id"])
        (report_dir / report_name).write_text("# Muse report\\n\\nFinished report body", encoding="utf-8")
        (report_dir / "ignore.txt").write_text("not an artifact", encoding="utf-8")
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, knowledge_db=knowledge_db,
            report_dir=report_dir, products_dir=products_dir,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        headers = {
            "Authorization": f"Bearer {token}", "Origin": "tauri://localhost",
            "Content-Type": "application/json", "Accept": "application/json",
        }
        try:
            submit = Request(
                base + "/api/v1/inspirations",
                data=json.dumps({
                    "content": "研究一个新的灵感：把知识图谱用于创意进化。",
                    "allow_external_research": True,
                    "tags": ["cross-domain", "creation"],
                    "research_domains": ["knowledge-graphs", "creative-ai"],
                    "source": "chatgpt_daily_inspiration",
                }).encode("utf-8"),
                headers=headers, method="POST",
            )
            with urlopen(submit, timeout=3) as response:
                self.assertEqual(response.status, 201)
                saved = json.loads(response.read())["inspiration"]
            self.assertEqual(saved["status"], "not_researched")
            self.assertTrue(saved["allow_external_research"])
            self.assertEqual(saved["source"], "chatgpt_daily_inspiration")

            duplicate = Request(
                base + "/api/v1/inspirations",
                data=json.dumps({
                    "content": "研究一个新的灵感：把知识图谱用于创意进化。",
                    "allow_external_research": True,
                    "tags": ["cross-domain", "creation"],
                    "research_domains": ["knowledge-graphs", "creative-ai"],
                    "source": "chatgpt_daily_inspiration",
                }).encode("utf-8"),
                headers=headers, method="POST",
            )
            with urlopen(duplicate, timeout=3) as response:
                repeated = json.loads(response.read())["inspiration"]
            self.assertEqual(repeated["id"], saved["id"], "retries must not create duplicate inspiration nodes")

            invalid_source = Request(
                base + "/api/v1/inspirations",
                data=json.dumps({"content": "Invalid source payload", "allow_external_research": False, "source": []}).encode("utf-8"),
                headers=headers, method="POST",
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(invalid_source, timeout=3)
            self.assertEqual(rejected.exception.code, 400)

            listing = Request(base + "/api/v1/inspirations", headers=headers)
            with urlopen(listing, timeout=3) as response:
                inspirations = json.loads(response.read())["inspirations"]
            self.assertEqual(len(inspirations), 1)
            self.assertEqual(inspirations[0]["tags"], ["cross-domain", "creation"])
            self.assertEqual(inspirations[0]["research_domains"], ["knowledge-graphs", "creative-ai"])
            self.assertEqual(inspirations[0]["source"], "chatgpt_daily_inspiration")
            from muse.research_knowledge import ResearchKnowledgeStore
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                persisted = knowledge.get_node(saved["id"])
            self.assertFalse(persisted["metadata"]["creator_context_is_evidence"])
            self.assertTrue(persisted["metadata"]["requires_support_and_counter_evidence"])
            self.assertIn("知识图谱", inspirations[0]["content"])

            from muse.autonomous_research import AutonomousResearchEngine
            with patch("muse.autonomous_research.load_creator_bookmarks", return_value=[]):
                engine = AutonomousResearchEngine(
                    evidence_db=workspace / "evidence.sqlite3", knowledge_db=knowledge_db,
                    progress_db=workspace / "progress.sqlite3", reminder_db=workspace / "reminders.sqlite3",
                    search=object(), reader=object(), resource_aware=False, workspace=workspace,
                )
                topic = engine._next_topic()
            self.assertEqual(topic["topic_origin"], "creator_inspired_hypothesis_backlog")
            self.assertEqual(topic["creator_inspired_hypothesis_node_id"], saved["id"])

            artifacts = Request(base + "/api/v1/artifacts", headers=headers)
            with urlopen(artifacts, timeout=3) as response:
                items = json.loads(response.read())["artifacts"]
            self.assertEqual([item["name"] for item in items], [report_name])
            products = Request(base + "/api/v1/products", headers=headers)
            with urlopen(products, timeout=3) as response:
                product_items = json.loads(response.read())["products"]
            self.assertEqual(len(product_items), 1)
            self.assertEqual(product_items[0]["initiative_id"], initiative["id"])
            self.assertEqual(product_items[0]["name"], initiative["title"])
            self.assertEqual(product_items[0]["version"], "v2")
            self.assertEqual(product_items[0]["status"], "testing")
            timeline_request = Request(
                base + f"/api/v1/initiatives/{initiative['id']}/timeline?limit=20",
                headers=headers,
            )
            with urlopen(timeline_request, timeout=3) as response:
                timeline = json.loads(response.read())
            self.assertEqual(timeline["initiative"]["id"], initiative["id"])
            self.assertEqual(
                [event["event_type"] for event in timeline["events"]],
                ["created", "status_changed", "product_built"],
            )
            self.assertEqual(timeline["events"][-1]["details"]["package_sha256"], product["package_sha256"])
            self.assertTrue(timeline["history_complete"])


            download_product = Request(base + product_items[0]["download_path"], headers={"Authorization": f"Bearer {token}"})
            with urlopen(download_product, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("attachment", response.headers["Content-Disposition"])
                product_zip = response.read()
            self.assertTrue(product_zip.startswith(b"PK"))

            preview_product = Request(base + product_items[0]["preview_path"], headers={"Authorization": f"Bearer {token}"})
            with urlopen(preview_product, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("text/html", response.headers["Content-Type"])
                self.assertIn("灵感实验台".encode("utf-8"), response.read())
            traversal_product = Request(base + "/api/v1/products/%2e%2e%2fsecret/0123456789abcdef0123/download", headers={"Authorization": f"Bearer {token}"})
            with self.assertRaises(HTTPError) as rejected_product:
                urlopen(traversal_product, timeout=3)
            self.assertIn(rejected_product.exception.code, {400, 404})
            rejected_product.exception.close()

            product_dir = workspace / product["relative_path"]
            product_html = product_dir / "index.html"
            original_html = product_html.read_text(encoding="utf-8")
            malicious_html = original_html.replace("</body>", "<script>alert(1)</script></body>")
            malicious_bytes = malicious_html.encode("utf-8")
            product_html.write_bytes(malicious_bytes)
            package_path = product_dir / "product.zip"
            with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("index.html", malicious_bytes)
                archive.writestr("README.md", (product_dir / "README.md").read_bytes())
            manifest_path = product_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["index.html"] = hashlib.sha256(malicious_bytes).hexdigest()
            manifest["files"]["product.zip"] = hashlib.sha256(package_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            corrupt_download = Request(base + product_items[0]["download_path"], headers={"Authorization": f"Bearer {token}"})
            with self.assertRaises(HTTPError) as corrupt:
                urlopen(corrupt_download, timeout=3)
            self.assertEqual(corrupt.exception.code, 409)
            corrupt.exception.close()

            download = Request(base + "/api/v1/artifacts/" + report_name, headers={
                "Authorization": f"Bearer {token}",
            })
            with urlopen(download, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("attachment", response.headers["Content-Disposition"])
                self.assertIn(b"Finished report body", response.read())

            traversal = Request(base + "/api/v1/artifacts/%2e%2e%2fsecret.md", headers={
                "Authorization": f"Bearer {token}",
            })
            with self.assertRaises(HTTPError) as rejected:
                urlopen(traversal, timeout=3)
            self.assertIn(rejected.exception.code, {400, 404})
            rejected.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    def test_initiative_timeline_exposes_allowlisted_research_and_video_artifact_links(self):
        import tempfile
        from pathlib import Path
        from muse.initiative_engine import InitiativeStore

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "initiatives.sqlite3"
            with InitiativeStore(database) as initiatives:
                item = initiatives.propose(
                    "时间线联动", kind="creative_output", creator_value="研究创作一体",
                    concept="验证研究图谱与视频产物关联", first_step="连接节点", expected_output="可追溯时间线",
                )
                initiatives.record_knowledge_link(
                    item["id"], "e" * 32, node_type="claim", relationship="counter_evidence",
                    evolution_cycle_id="cycle_" + "e" * 32,
                )
                initiatives.record_product_artifact(
                    item["id"], relative_path="data/products/video/version", package_sha256="f" * 64,
                    validated=True, artifact_metadata={
                        "mp4_sha256": "a" * 64, "manifest_sha256": "b" * 64,
                        "evolution_cycle_id": "cycle_" + "e" * 32,
                        "validation": {"validated": True},
                    },
                )
            timeline = build_initiative_timeline(database, item["id"])
            self.assertTrue(timeline["history_complete"])
            link = next(event for event in timeline["events"] if event["event_type"] == "knowledge_linked")
            self.assertEqual(link["details"]["node_type"], "claim")
            product = next(event for event in timeline["events"] if event["event_type"] == "product_built")
            self.assertEqual(product["details"]["artifact_metadata"]["mp4_sha256"], "a" * 64)
            self.assertEqual(product["details"]["artifact_metadata"]["manifest_sha256"], "b" * 64)

    def test_initiative_feedback_api_records_explicit_cycle_for_research_sync(self):
        root = Path(self.temp.name)
        initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
        products_dir = root / "data" / "products"
        products_dir.mkdir(parents=True)
        with InitiativeStore(initiatives_db) as initiatives:
            item = initiatives.propose(
                "Video review API target", kind="creative_output", creator_value="value",
                concept="concept", first_step="step", expected_output="output",
            )
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, improvement_db=root / "data" / "improvements" / "muse.sqlite3",
            products_dir=products_dir,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cycle = "cycle_" + "d" * 32
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/initiatives/{item['id']}/feedback",
                data=json.dumps({
                    "feedback": "镜头动作不够明显，需要补充运动验证。",
                    "decision": "revise",
                    "reviewer": "creator",
                    "source": "video_review",
                    "evolution_cycle_id": cycle,
                }, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": "Bearer " + token,
                    "Origin": "tauri://localhost",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(request, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(response.status, 201)
            self.assertEqual(payload["feedback"]["evolution_cycle_id"], cycle)
            with InitiativeStore(initiatives_db) as initiatives:
                feedback = initiatives.list_feedback(initiative_id=item["id"])
            self.assertEqual(len(feedback), 1)
            self.assertEqual(feedback[0]["evolution_cycle_id"], cycle)
            timeline = build_initiative_timeline(initiatives_db, item["id"])
            event = next(item for item in timeline["events"] if item["event_type"] == "feedback_recorded")
            self.assertEqual(event["details"]["evolution_cycle_id"], cycle)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_failed_video_job_is_saved_as_redacted_initiative_failure(self):
        import time

        root = Path(self.temp.name)
        initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
        products_dir = root / "data" / "products"
        products_dir.mkdir(parents=True)
        with InitiativeStore(initiatives_db) as initiatives:
            item = initiatives.propose(
                "Failed video job target", kind="creative_output", creator_value="value",
                concept="concept", first_step="step", expected_output="output",
            )
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, improvement_db=root / "data" / "improvements" / "muse.sqlite3",
            products_dir=products_dir,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cycle = "cycle_" + "9" * 32
        try:
            with patch("muse.status_api.VideoProductBuilder.build_from_prompt", side_effect=RuntimeError("private GPU failure detail")) as build_prompt:
                request = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/v1/video-products",
                    data=json.dumps({
                        "mode": "prompt_to_video", "title": "故障注入视频",
                        "prompt": "A short cinematic test scene",
                        "creator_initiative_id": item["id"], "evolution_cycle_id": cycle,
                        "motion_profile": "brand_intro_outro",
                    }).encode("utf-8"),
                    headers={
                        "Authorization": "Bearer " + token,
                        "Origin": "tauri://localhost",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlopen(request, timeout=3) as response:
                    job_id = json.loads(response.read())["job"]["id"]
                status = None
                for _ in range(100):
                    poll = Request(
                        f"http://127.0.0.1:{server.server_address[1]}/api/v1/video-jobs/{job_id}",
                        headers={"Authorization": "Bearer " + token},
                    )
                    with urlopen(poll, timeout=3) as response:
                        status = json.loads(response.read())["job"]["status"]
                    if status == "failed":
                        break
                    time.sleep(0.02)
            build_prompt.assert_called_once()
            self.assertEqual(build_prompt.call_args.kwargs["motion_profile"], "brand_intro_outro")
            self.assertEqual(status, "failed")
            with InitiativeStore(initiatives_db) as initiatives:
                failures = initiatives.list_failures(initiative_id=item["id"])
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["failure_code"], "video.product_job_failed")
            self.assertEqual(failures[0]["error_type"], "RuntimeError")
            self.assertEqual(failures[0]["evolution_cycle_id"], cycle)
            self.assertNotIn("private GPU failure detail", str(failures))
            timeline = build_initiative_timeline(initiatives_db, item["id"])
            failed_event = next(event for event in timeline["events"] if event["event_type"] == "product_failed")
            self.assertEqual(failed_event["details"]["evolution_cycle_id"], cycle)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_self_improvement_status_exposes_backlog_and_resumable_cycle_without_private_checkpoint(self):
        from muse.status_api import build_self_improvement_status

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            improvement_db = root / "improvements.sqlite3"
            progress_db = root / "progress.sqlite3"
            with ImprovementStore(improvement_db) as improvements:
                researching = improvements.create_proposal("Researching", "problem", "hypothesis")
                improvements.transition(researching["id"], "researching", actor="test")
                review = improvements.create_proposal("Review", "problem", "hypothesis")
                improvements.transition(review["id"], "researching", actor="test")
                improvements.transition(review["id"], "ready_for_review", actor="test")
            cycle_id = "cycle_" + "b" * 32
            with ProgressEventStore(progress_db) as progress:
                progress.record("evolution", cycle_id, "started", summary="Autonomous research cycle started")
                progress.record("evolution", cycle_id, "progress", summary="topic_key:private-proposal-identifier")
            snapshot = build_self_improvement_status(improvement_db, progress_db)
            self.assertEqual(snapshot["status"], "available")
            self.assertEqual(snapshot["proposal_counts"]["researching"], 1)
            self.assertEqual(snapshot["proposal_counts"]["ready_for_review"], 1)
            self.assertEqual(snapshot["pending_research"], 1)
            self.assertEqual(snapshot["pending_human_review"], 1)
            self.assertEqual(snapshot["cycle_state"], "progress")
            self.assertTrue(snapshot["cycle_resumable"])
            self.assertNotIn("private-proposal-identifier", json.dumps(snapshot))

    def test_self_improvement_endpoint_returns_authenticated_safe_snapshot(self):
        improvement_db = self.task_db.parent / "improvements.sqlite3"
        with ImprovementStore(improvement_db) as improvements:
            proposal = improvements.create_proposal("Needs review", "problem", "hypothesis")
            improvements.transition(proposal["id"], "researching", actor="test")
            improvements.transition(proposal["id"], "ready_for_review", actor="test")
        cycle_id = "cycle_" + "c" * 32
        with ProgressEventStore(self.progress_db) as progress:
            progress.record("evolution", cycle_id, "started", summary="Autonomous research cycle started")
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, improvement_db=improvement_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/self-improvement",
                headers={"Authorization": "Bearer " + token},
            )
            with urlopen(request, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["pending_human_review"], 1)
            self.assertEqual(payload["cycle_state"], "started")
            self.assertTrue(payload["approval_required"])
            self.assertFalse(payload["automatic_code_changes"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_video_job_endpoint_recovers_durable_attempt_after_api_restart(self):
        root = Path(self.temp.name)
        initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
        products_dir = root / "data" / "products"
        products_dir.mkdir(parents=True)
        attempt_id = "a" * 32
        with InitiativeStore(initiatives_db) as attempts:
            attempts.start_video_attempt(attempt_id, mode="prompt_to_video")
            attempts.mark_video_attempt_running(attempt_id)
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, products_dir=products_dir,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/video-jobs/{attempt_id}",
                headers={"Authorization": "Bearer " + token},
            )
            with urlopen(request, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["job"]["id"], attempt_id)
            self.assertEqual(payload["job"]["status"], "running")
            self.assertFalse(payload["job"]["worker_available"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_video_attempts_endpoint_exposes_queue_and_lease_metadata(self):
        root = Path(self.temp.name)
        initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
        products_dir = root / "data" / "products"
        products_dir.mkdir(parents=True, exist_ok=True)
        attempt_id = "b" * 32
        with InitiativeStore(initiatives_db) as attempts:
            attempts.start_video_attempt(attempt_id, mode="prompt_to_video")
            attempts.mark_video_attempt_running(attempt_id)
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, products_dir=products_dir,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/video-attempts",
                headers={"Authorization": "Bearer " + token},
            )
            with urlopen(request, timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["metrics"]["running"], 1)
            self.assertEqual(payload["metrics"]["leased"], 1)
            self.assertEqual(payload["attempts"][0]["worker_id"], "status-api")
            self.assertGreaterEqual(payload["attempts"][0]["attempt_count"], 1)
            self.assertIsNotNone(payload["attempts"][0]["lease_expires_at"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

if __name__ == "__main__":
    unittest.main()