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
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from muse.agent_registry import AgentRegistry
from muse.capabilities import CapabilityRegistry
from muse.progress import ProgressEventStore
from muse.status_api import _find_product_subtitle_review, build_agents, build_agent_org_chart, build_capabilities, build_initiative_timeline, build_mempalace_status, build_overview, build_products, build_improvement_detail, create_status_server
from muse.improvement import ImprovementStore
from muse.task_queue import TaskQueue
from muse.commander_planner import CommanderPlanner
from muse.workflow_profiles import WorkflowController
from muse.initiative_engine import InitiativeStore
from muse.product_builder import LocalProductBuilder


class MemPalaceStatusApiTests(unittest.TestCase):
    def test_status_is_readonly_privacy_minimal_and_reports_outbox_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "muse.toml").write_text(
                '[mempalace]\nenabled = false\n', encoding="utf-8",
            )
            memory_db = root / "memory.sqlite3"
            import sqlite3
            connection = sqlite3.connect(memory_db)
            connection.execute(
                "CREATE TABLE mempalace_projection_outbox (status TEXT, created_at TEXT, delivered_at TEXT, payload_json TEXT)"
            )
            connection.executemany(
                "INSERT INTO mempalace_projection_outbox VALUES (?, ?, ?, ?)",
                [("pending", "2026-11-19T10:00:00Z", None, "private-secret"),
                 ("failed", "2026-11-19T11:00:00Z", None, "private-secret"),
                 ("delivered", "2026-11-19T09:00:00Z", "2026-11-19T09:05:00Z", "private-secret")],
            )
            connection.commit()
            connection.close()
            before = memory_db.read_bytes()
            status = build_mempalace_status(root, memory_db)
            self.assertEqual(status["connection_state"], "disabled")
            self.assertEqual(status["memory_store_state"], "ready")
            self.assertEqual(status["projection_outbox"]["pending"], 1)
            self.assertEqual(status["projection_outbox"]["failed"], 1)
            self.assertEqual(status["projection_outbox"]["delivered"], 1)
            self.assertEqual(status["projection_outbox"]["status"], "attention_required")
            self.assertEqual(status["projection_outbox"]["total"], 3)
            self.assertEqual(status["projection_outbox"]["oldest_pending_at"], "2026-11-19T10:00:00Z")
            self.assertEqual(status["projection_outbox"]["latest_delivered_at"], "2026-11-19T09:05:00Z")
            self.assertFalse(status["projection_outbox"]["content_included"])
            self.assertFalse(status["remote_process_started"])
            self.assertFalse(status["private_content_exposed"])
            self.assertNotIn("private-secret", json.dumps(status))
            self.assertEqual(memory_db.read_bytes(), before)

    def test_status_marks_missing_configured_palace_without_starting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            missing = root / "not-created-palace"
            (config / "muse.toml").write_text(
                f'[mempalace]\nenabled = true\npalace_path = "{missing.as_posix()}"\n', encoding="utf-8",
            )
            status = build_mempalace_status(root, root / "missing.sqlite3")
            self.assertEqual(status["connection_state"], "configured_store_missing")
            self.assertFalse(missing.exists())
            self.assertEqual(status["index_freshness"], "not_measured")

    def test_status_http_endpoint_requires_bearer_and_returns_readonly_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "muse.toml").write_text('[mempalace]\nenabled = false\n', encoding="utf-8")
            memory_db = root / "memory.sqlite3"
            import sqlite3
            connection = sqlite3.connect(memory_db)
            connection.execute("CREATE TABLE mempalace_projection_outbox (status TEXT, created_at TEXT, delivered_at TEXT)")
            connection.execute("INSERT INTO mempalace_projection_outbox VALUES ('pending', '2026-11-19T12:00:00Z', NULL)")
            connection.commit()
            connection.close()
            server, token = create_status_server(
                task_db=root / "tasks.sqlite3", progress_db=root / "progress.sqlite3",
                capability_db=root / "capabilities.sqlite3", memory_db=memory_db,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(base + "/api/v1/mempalace/status", timeout=3)
                self.assertEqual(rejected.exception.code, 401)
                rejected.exception.close()
                request = Request(base + "/api/v1/mempalace/status", headers={"Authorization": f"Bearer {token}"})
                with urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["projection_outbox"]["pending"], 1)
                self.assertFalse(payload["remote_process_started"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class ClaimDossierApiTests(unittest.TestCase):
    def test_claim_dossier_builder_returns_reviewed_evidence_and_no_auto_verdict(self) -> None:
        from muse.status_api import build_claim_dossier
        from muse.research_knowledge import ResearchKnowledgeStore
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "knowledge.sqlite3"
            with ResearchKnowledgeStore(database) as store:
                source = store.upsert_node("source", "Primary source", external_ref="https://example.test/source")
                claim = store.upsert_node("claim", "Test claim", summary="Claim summary", metadata={"epistemic_status": "hypothesis"})
                evidence = store.record_claim_evidence(
                    claim["id"], source["id"], direction="support_search_candidate", evidence_type="primary",
                    quality="unassessed", independence="unassessed",
                )
            dossier = build_claim_dossier(database, claim["id"])
            self.assertEqual(dossier["claim_id"], claim["id"])
            self.assertEqual(dossier["epistemic_status"], "hypothesis")
            self.assertEqual(dossier["evidence"][0]["id"], evidence["id"])
            self.assertTrue(dossier["retrieval_direction_is_not_evidence_verdict"])
            self.assertFalse(dossier["confidence"]["automatic_scoring"])
            self.assertIsNone(build_claim_dossier(database, "f" * 32))

    def test_claim_dossier_builder_rejects_invalid_identifiers_and_limits(self) -> None:
        from muse.status_api import build_claim_dossier
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "knowledge.sqlite3"
            with self.assertRaises(ValueError):
                build_claim_dossier(database, "../claim")
            with self.assertRaises(ValueError):
                build_claim_dossier(database, "a" * 32, evidence_limit=0)


    def test_claim_dossier_http_endpoint_returns_bounded_readonly_dossier(self) -> None:
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge_db = root / "knowledge.sqlite3"
            with ResearchKnowledgeStore(knowledge_db) as store:
                source = store.upsert_node("source", "Reviewed source", external_ref="https://example.test/paper")
                claim = store.upsert_node(
                    "claim", "A testable hypothesis", summary="A claim that remains unverified.",
                    metadata={"epistemic_status": "hypothesis"},
                )
                store.record_claim_evidence(
                    claim["id"], source["id"], direction="support_search_candidate",
                    evidence_type="primary", quality="unassessed", independence="unassessed",
                )

            server, token = create_status_server(
                task_db=root / "tasks.sqlite3", progress_db=root / "progress.sqlite3",
                capability_db=root / "capabilities.sqlite3", knowledge_db=knowledge_db,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            headers = {"Authorization": f"Bearer {token}"}
            try:
                request = Request(
                    f"{base}/api/v1/knowledge/claims/{claim['id']}/dossier?evidence_limit=1",
                    headers=headers,
                )
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read().decode("utf-8"))
                dossier = payload["dossier"]
                self.assertEqual(dossier["claim_id"], claim["id"])
                self.assertEqual(len(dossier["evidence"]), 1)
                self.assertTrue(dossier["retrieval_direction_is_not_evidence_verdict"])
                self.assertFalse(dossier["confidence"]["automatic_scoring"])

                missing = Request(
                    f"{base}/api/v1/knowledge/claims/{'f' * 32}/dossier",
                    headers=headers,
                )
                with self.assertRaises(HTTPError) as not_found:
                    urlopen(missing, timeout=3)
                self.assertEqual(not_found.exception.code, 404)
                not_found.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


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

    def test_contact_sheet_endpoint_serves_only_hash_verified_release_evidence(self) -> None:
        workspace = Path(self.temp.name)
        products_dir = workspace / "data" / "products"
        initiative_id, version_id = "a" * 32, "b" * 20
        product_dir = products_dir / initiative_id / version_id
        product_dir.mkdir(parents=True)
        video_path = product_dir / "video.mp4"
        video_bytes = b"video-evidence-fixture"
        video_path.write_bytes(video_bytes)
        video_hash = hashlib.sha256(video_bytes).hexdigest()
        release_dir = product_dir / "publish-package-v1"
        release_dir.mkdir(parents=True)
        sheet = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (800).to_bytes(4, "big") + (600).to_bytes(4, "big") + b"contact-sheet-fixture"
        report = {
            "can_publish": False,
            "checks": {"visual_anomaly_review": {"contact_sheet": {
                "status": "available", "sha256": hashlib.sha256(sheet).hexdigest(),
            }}},
        }
        report_bytes = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        sheet_path = release_dir / "contact-sheet.png"
        report_path = release_dir / "release-quality-report.json"
        sheet_path.write_bytes(sheet)
        report_path.write_bytes(report_bytes)
        package_manifest = {
            "package_type": "muse_manual_platform_publish_pack",
            "initiative_id": initiative_id, "version_id": version_id,
            "release_state": "draft_only", "can_publish": False,
            "source_video_sha256": video_hash,
            "files": {
                "video.mp4": video_hash,
                "contact-sheet.png": hashlib.sha256(sheet).hexdigest(),
                "release-quality-report.json": hashlib.sha256(report_bytes).hexdigest(),
            },
        }
        (release_dir / "manifest.json").write_text(json.dumps(package_manifest), encoding="utf-8")
        daemon_stub = ModuleType("muse.daemon_control")
        daemon_stub.MuseDaemonController = lambda **kwargs: SimpleNamespace()
        daemon_stub.DaemonError = RuntimeError
        with patch.dict("sys.modules", {"muse.daemon_control": daemon_stub}):
            server, token = create_status_server(
                task_db=self.task_db, progress_db=self.progress_db,
                capability_db=self.capability_db, products_dir=products_dir,
            )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/v1/products/{initiative_id}/{version_id}/contact-sheet",
                headers={"Authorization": f"Bearer {token}"},
            )
            with patch("muse.status_api._verified_product_files", return_value=(
                product_dir, {"product_kind": "video_creator"}, {"video.mp4": (video_path, video_hash)},
            )):
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Content-Type"], "image/png")
                    self.assertEqual(response.read(), sheet)
                sheet_path.write_bytes(sheet + b"tampered")
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(request, timeout=5)
                self.assertEqual(rejected.exception.code, 409)
                rejected.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_visual_review_post_and_get_are_authenticated_hash_bound_and_append_only(self):
        root = Path(self.temp.name)
        products_dir = root / "data" / "products"
        initiative_id, version_id = "a" * 32, "b" * 20
        product_dir = products_dir / initiative_id / version_id
        release_dir = product_dir / "publish-package-v1"
        release_dir.mkdir(parents=True)
        video = product_dir / "video.mp4"
        video.write_bytes(b"video-master")
        video_hash = hashlib.sha256(video.read_bytes()).hexdigest()
        sheet = release_dir / "contact-sheet.png"
        sheet.write_bytes(
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"            + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"fixture"
        )
        sheet_hash = hashlib.sha256(sheet.read_bytes()).hexdigest()
        report = {"can_publish": False, "checks": {"visual_anomaly_review": {
            "contact_sheet": {"status": "available", "sha256": sheet_hash}
        }}}
        report_bytes = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        (release_dir / "release-quality-report.json").write_bytes(report_bytes)
        release_manifest = {
            "package_type": "muse_manual_platform_publish_pack",
            "initiative_id": initiative_id, "version_id": version_id,
            "release_state": "draft_only", "can_publish": False,
            "source_video_sha256": video_hash,
            "files": {
                "video.mp4": video_hash, "contact-sheet.png": sheet_hash,
                "release-quality-report.json": hashlib.sha256(report_bytes).hexdigest(),
            },
        }
        (release_dir / "manifest.json").write_text(json.dumps(release_manifest), encoding="utf-8")
        daemon_stub = ModuleType("muse.daemon_control")
        daemon_stub.MuseDaemonController = lambda **kwargs: SimpleNamespace()
        daemon_stub.DaemonError = RuntimeError
        with patch.dict("sys.modules", {"muse.daemon_control": daemon_stub}):
            server, token = create_status_server(
                task_db=self.task_db, progress_db=self.progress_db,
                capability_db=self.capability_db, products_dir=products_dir,
            )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_address[1]}/api/v1/products/{initiative_id}/{version_id}/visual-review"
        headers = {
            "Authorization": "Bearer " + token, "Origin": "tauri://localhost",
            "Content-Type": "application/json", "Accept": "application/json",
        }
        payload = {
            "decision": "approved", "reviewer": "Local Editor",
            "reviewer_role": "video_editor",
            "rationale": "Reviewed the complete video and every sampled keyframe carefully.",
            "checklist": {
                "subject_framing": True, "shot_continuity": True,
                "visual_anomalies": True, "subtitle_visibility": True,
                "full_video_playback": True,
            },
            "confirmation": "RECORD-VISUAL-REVIEW",
        }
        try:
            with patch(
                "muse.status_api._verified_product_files",
                return_value=(product_dir, {"product_kind": "video_creator"}, {"video.mp4": (video, None)}),
            ):
                with self.assertRaises(HTTPError) as unauth:
                    urlopen(Request(endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
                self.assertEqual(unauth.exception.code, 401)
                unauth.exception.close()
                request = Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    created = json.loads(response.read())
                self.assertTrue(created["append_only"])
                self.assertFalse(created["digital_signature"])
                self.assertEqual(created["record"]["subject"]["video_sha256"], video_hash)
                self.assertEqual(created["record"]["evidence_status"], "verified_human_review_digest_scope_bound")
                self.assertEqual(created["record"]["subject"]["artifact_sha256"]["contact-sheet.png"], sheet_hash)
                get_request = Request(endpoint, headers={"Authorization": "Bearer " + token})
                with urlopen(get_request, timeout=5) as response:
                    history = json.loads(response.read())
                self.assertEqual(history["count"], 1)
                self.assertEqual(history["reviews"][0]["evidence_status"], "verified_human_review_digest_scope_bound")
                self.assertEqual(history["identity_assurance"], "self_asserted_local_session")
                revision_payload = {
                    **payload, "decision": "changes_requested",
                    "findings": [{
                        "timecode_ms": 12500, "shot_id": "shot-03", "severity": "major",
                        "category": "continuity",
                        "description": "镜头切换时人物动作出现明显跳变",
                        "expected_fix": "重新衔接该镜头前后动作并复核切点",
                    }],
                }
                with urlopen(Request(endpoint, data=json.dumps(revision_payload).encode(), headers=headers, method="POST"), timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    revised = json.loads(response.read())
                self.assertEqual(revised["record"]["findings"][0]["timecode_ms"], 12500)
                revision_endpoint = endpoint.replace("/visual-review", "/revision-brief")
                with urlopen(Request(revision_endpoint, headers={"Authorization": "Bearer " + token}), timeout=5) as response:
                    brief_result = json.loads(response.read())
                self.assertTrue(brief_result["active"])
                self.assertFalse(brief_result["can_auto_modify_source"])
                self.assertFalse(brief_result["new_version_created"])
                self.assertEqual(brief_result["brief"]["source_version"], version_id)
                self.assertEqual(brief_result["brief"]["tasks"][0]["timecode_ms"], 12500)
                self.assertEqual(brief_result["brief"]["tasks"][0]["status"], "open")
                with urlopen(Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST"), timeout=5) as response:
                    self.assertEqual(response.status, 201)
                with urlopen(Request(revision_endpoint, headers={"Authorization": "Bearer " + token}), timeout=5) as response:
                    cleared_brief = json.loads(response.read())
                self.assertFalse(cleared_brief["active"])
                self.assertIsNone(cleared_brief["brief"])
                with self.assertRaises(HTTPError) as invalid:
                    urlopen(Request(endpoint, data=json.dumps({**payload, "checklist": {}}).encode(), headers=headers, method="POST"), timeout=5)
                self.assertEqual(invalid.exception.code, 400)
                invalid.exception.close()
                release_manifest_path = release_dir / "manifest.json"
                release_manifest_document = json.loads(release_manifest_path.read_text(encoding="utf-8"))
                release_manifest_document["created_at"] = "changed-after-review"
                release_manifest_path.write_text(json.dumps(release_manifest_document), encoding="utf-8")
                with urlopen(get_request, timeout=5) as response:
                    changed_manifest_history = json.loads(response.read())
                self.assertEqual(
                    changed_manifest_history["reviews"][0]["evidence_status"],
                    "evidence_mismatch_or_incomplete",
                )
                with self.assertRaises(HTTPError) as stale_brief:
                    urlopen(Request(revision_endpoint, headers={"Authorization": "Bearer " + token}), timeout=5)
                self.assertEqual(stale_brief.exception.code, 409)
                stale_brief.exception.close()
                sheet.write_bytes(sheet.read_bytes() + b"tampered")
                with self.assertRaises(HTTPError) as mismatch:
                    urlopen(get_request, timeout=5)
                self.assertEqual(mismatch.exception.code, 409)
                mismatch.exception.close()
                with self.assertRaises(HTTPError) as mismatch_post:
                    urlopen(Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST"), timeout=5)
                self.assertEqual(mismatch_post.exception.code, 409)
                mismatch_post.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_subtitle_review_lookup_binds_exact_product_audio_script_and_srt(self) -> None:
        workspace = Path(self.temp.name)
        run_dir = workspace / "data" / "prometheus" / "runs" / "pilot" / "run-1"
        run_dir.mkdir(parents=True)
        srt = b"1\n00:00:00,000 --> 00:00:01,000\nhello\n"
        (run_dir / "narration-subtitles.srt").write_bytes(srt)
        initiative_id, version_id = "1" * 32, "2" * 20
        review = {
            "schema_version": 1,
            "review_status": "pending_human_review",
            "subtitle_cue_count": 1,
            "human_approved": False,
            "subtitle_file": "narration-subtitles.srt",
            "reference_script": "hello",
            "raw_asr_transcript": "hello",
            "asr": {
                "audio_sha256": "a" * 64,
                "model_repo": "local/cantonese",
                "model_revision": "b" * 40,
            },
            "segments": [{"index": 1, "start_seconds": 0.0, "end_seconds": 1.0, "asr_text": "hello"}],
        }
        (run_dir / "subtitle-review.json").write_text(json.dumps(review), encoding="utf-8")
        (run_dir / "generation.json").write_text(json.dumps({"product": {
            "initiative_id": initiative_id, "version_id": version_id,
            "relative_path": f"data\\products\\{initiative_id}\\{version_id}",
        }}), encoding="utf-8")
        manifest = {
            "initiative_id": initiative_id,
            "version_id": version_id,
            "narration": {"script": "hello"},
            "narration_asr": {
                "audio_sha256": "a" * 64,
                "model_repo": "local/cantonese",
                "model_revision": "b" * 40,
                "transcript": "hello",
            },
            "subtitles": {"sha256": hashlib.sha256(srt).hexdigest()},
        }
        self.assertEqual(_find_product_subtitle_review(workspace, manifest), review)

        decoy = workspace / "data" / "prometheus" / "runs" / "pilot" / "other-product"
        decoy.mkdir()
        (decoy / "narration-subtitles.srt").write_bytes(srt)
        (decoy / "subtitle-review.json").write_text(json.dumps(review), encoding="utf-8")
        (decoy / "generation.json").write_text(json.dumps({"product": {
            "initiative_id": "9" * 32, "version_id": "8" * 20,
            "relative_path": "data\\products\\" + "9" * 32 + "\\" + "8" * 20,
        }}), encoding="utf-8")
        self.assertEqual(_find_product_subtitle_review(workspace, manifest), review)

        duplicate = workspace / "data" / "prometheus" / "runs" / "pilot" / "run-2"
        duplicate.mkdir()
        (duplicate / "narration-subtitles.srt").write_bytes(srt)
        (duplicate / "subtitle-review.json").write_text(json.dumps(review), encoding="utf-8")
        (duplicate / "generation.json").write_text(json.dumps({"product": {
            "initiative_id": initiative_id, "version_id": version_id,
            "relative_path": f"data\\products\\{initiative_id}\\{version_id}",
        }}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "multiple subtitle review"):
            _find_product_subtitle_review(workspace, manifest)

        manifest["narration_asr"]["audio_sha256"] = "c" * 64
        with self.assertRaises(FileNotFoundError):
            _find_product_subtitle_review(workspace, manifest)

    def test_subtitle_review_api_requires_explicit_confirmation_and_creates_new_version(self) -> None:
        root = Path(self.temp.name)
        products_dir = root / "data" / "products"
        products_dir.mkdir(parents=True)
        initiative_id = "1" * 32
        version_id = "2" * 20
        review = {
            "schema_version": 1,
            "review_status": "pending_human_review",
            "human_approved": False,
            "subtitle_cue_count": 1,
            "reference_script": "hello",
            "raw_asr_transcript": "hello",
            "asr": {"audio_sha256": "a" * 64, "model_repo": "local/cantonese", "model_revision": "b" * 40},
            "segments": [{
                "index": 1, "start_seconds": 0.0, "end_seconds": 1.0,
                "asr_text": "hello", "review_status": "pending", "corrected_text": None,
            }],
        }
        manifest = {"product_kind": "video_creator"}
        result = {
            "initiative_id": initiative_id, "version_id": "3" * 20,
            "creator_initiative_id": "4" * 32, "validated": True, "status": "testing",
            "subtitle_review": {"human_approved": True}, "quality_gate": {"ready_to_publish": False},
            "relative_path": "data/products/" + initiative_id + "/" + ("3" * 20),
        }
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, products_dir=products_dir,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        headers = {"Authorization": f"Bearer {token}", "Origin": "tauri://localhost"}
        endpoint = base + f"/api/v1/products/{initiative_id}/{version_id}/subtitle-review/approve"
        payload = {
            "reviewed_segment_indexes": [1], "corrections": [{"segment_index": 1, "corrected_text": "hello world"}],
            "reviewer": "human-editor", "reviewer_id": "local-editor-1", "reviewer_role": "subtitle_editor",
            "rationale": "Checked every cue against the narration.",
            "evidence_refs": ["subtitle-review.json#segments", "narration.wav#sha256"],
            "confirmation": "not-the-confirmation",
        }
        try:
            with (
                patch("muse.status_api._verified_product_files", return_value=(products_dir, manifest, {})),
                patch("muse.status_api._find_product_subtitle_review", return_value=review),
                patch("muse.status_api.VideoProductBuilder.package_approved_subtitle_revision", return_value=result) as build,
            ):
                review_request = Request(
                    base + f"/api/v1/products/{initiative_id}/{version_id}/subtitle-review",
                    headers=headers,
                )
                with urlopen(review_request, timeout=5) as response:
                    review_body = json.loads(response.read())
                self.assertEqual(review_body["review"], review)
                rejected = Request(
                    endpoint, data=json.dumps(payload).encode("utf-8"),
                    headers={**headers, "Content-Type": "application/json"}, method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(rejected, timeout=5)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
                build.assert_not_called()

                payload["confirmation"] = "APPROVE-HUMAN-REVIEW"
                approved = Request(
                    endpoint, data=json.dumps(payload).encode("utf-8"),
                    headers={**headers, "Content-Type": "application/json"}, method="POST",
                )
                with urlopen(approved, timeout=5) as response:
                    body = json.loads(response.read())
                    self.assertEqual(response.status, 201)
                self.assertEqual(body["product"]["version_id"], "3" * 20)
                self.assertFalse(body["product"]["quality_gate"]["ready_to_publish"])
                approved_document = build.call_args.args[2]
                self.assertTrue(approved_document["human_approved"])
                self.assertEqual(approved_document["approved_by"], "human-editor")
                self.assertEqual(approved_document["reviewer_identity"]["id"], "local-editor-1")
                self.assertEqual(approved_document["approval_rationale"], payload["rationale"])
                self.assertEqual(approved_document["approval_evidence_refs"], payload["evidence_refs"])
                self.assertEqual(approved_document["segments"][0]["corrected_text"], "hello world")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

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

    def test_agents_view_exposes_herdr_reported_state_without_free_text_message(self) -> None:
        with AgentRegistry(self.agent_db) as registry:
            registry.report_state("researcher", "blocked", message="private prompt text", source="herdr-core")
        result = build_agents(self.agent_db)
        self.assertEqual(result[0]["status"], "blocked")
        self.assertEqual(result[0]["reported_state"], "blocked")
        self.assertEqual(result[0]["reported_source"], "herdr-core")
        self.assertIsNotNone(result[0]["reported_at"])
        self.assertNotIn("private prompt text", json.dumps(result))

    def test_agent_org_chart_exposes_reporting_tree_and_truncation_without_private_fields(self) -> None:
        with AgentRegistry(self.agent_db) as registry:
            registry.register("org-manager", role="architect", capabilities=["design"], display_name="组织经理")
            registry.register("org-worker", role="researcher", capabilities=["research"], display_name="研究员")
            registry.set_org_assignment("org-worker", title="研究专员", reports_to="org-manager")
        chart = build_agent_org_chart(self.agent_db)
        self.assertEqual(chart["total"], 3)
        self.assertFalse(chart["truncated"])
        self.assertEqual(chart["unresolved_parent_count"], 0)
        self.assertIn({"manager_id": "org-manager", "report_id": "org-worker"}, chart["edges"])
        worker = next(item for item in chart["nodes"] if item["agent_id"] == "org-worker")
        self.assertEqual(worker["title"], "研究专员")
        self.assertEqual(worker["capabilities"], ["research"])
        self.assertNotIn("executable", json.dumps(chart))

        partial = build_agent_org_chart(self.agent_db, limit=1)
        self.assertTrue(partial["truncated"])
        self.assertEqual(partial["total"], 3)
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
            self.assertEqual(agents_payload["org_chart"]["total"], 1)
            self.assertEqual(agents_payload["org_chart"]["nodes"][0]["agent_id"], "researcher")
            self.assertFalse(agents_payload["org_chart"]["truncated"])
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

    def test_contextual_inquiry_review_api_is_authenticated_redacted_and_read_only(self) -> None:
        from unittest.mock import patch

        task_id = "a" * 32
        valid_payload = {
            "case_context_envelope_stored": True,
            "case_context_attached": True,
            "requires_context_reentry": False,
            "context_status": "validated_attached",
            "long_term_storage_performed": False,
            "review_result_envelope": {"version": 1, "protection": "dpapi", "ciphertext_b64": "opaque"},
        }
        tasks = [
            {
                "id": task_id, "title": "PRIVATE USER GOAL", "status": "waiting_approval",
                "payload": valid_payload, "created_at": "2026-11-21T10:00:00+00:00",
                "updated_at": "2026-11-21T10:05:00+00:00", "lease_owner": None,
                "lease_expires_at": None,
            },
            {
                "id": "b" * 32, "title": "not ready", "status": "queued",
                "payload": valid_payload, "created_at": "2026-11-21T10:00:00+00:00",
                "updated_at": "2026-11-21T10:05:00+00:00", "lease_owner": None,
                "lease_expires_at": None,
            },
        ]
        review = {
            "schema_version": 1, "inquiry_id": "inquiry-1", "summary": "Review summary",
            "findings": [{
                "finding_id": "finding-1", "statement": "Candidate only",
                "observation_refs": ["observation-1"], "hypothesis_refs": ["hypothesis-1"],
                "assessment": "support_candidate", "rationale": "Needs human verification",
            }],
            "unknowns": ["Still unknown"],
        }
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db, capability_db=self.capability_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with patch("muse.task_queue.TaskQueue") as queue_class:
                queue = queue_class.return_value.__enter__.return_value
                queue.list_tasks.return_value = tasks
                queue.load_contextual_inquiry_review_result.return_value = review
                listing = Request(
                    base + "/api/v1/contextual-inquiry-reviews?limit=20",
                    headers={"Authorization": f"Bearer {token}", "Origin": "tauri://localhost"},
                )
                with urlopen(listing, timeout=3) as response:
                    listed = json.loads(response.read())
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], "tauri://localhost")
                self.assertEqual(listed["count"], 1)
                self.assertEqual(listed["reviews"], [{
                    "task_id": task_id, "created_at": tasks[0]["created_at"],
                    "updated_at": tasks[0]["updated_at"],
                }])
                self.assertNotIn("PRIVATE USER GOAL", json.dumps(listed))

                with self.assertRaises(HTTPError) as unauthorized:
                    urlopen(base + f"/api/v1/contextual-inquiry-reviews/{task_id}", timeout=3)
                self.assertEqual(unauthorized.exception.code, 401)
                unauthorized.exception.close()

                detail = Request(
                    base + f"/api/v1/contextual-inquiry-reviews/{task_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                with urlopen(detail, timeout=3) as response:
                    payload = json.loads(response.read())
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(payload["review"], review)
                self.assertNotIn("PRIVATE USER GOAL", json.dumps(payload))
                self.assertNotIn("case_context_envelope", payload)
                queue.load_contextual_inquiry_review_result.assert_called_once_with(task_id)
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
        storyboard = json.dumps({"title": "QA fixture", "scenes": [
            {"id": "shot-01", "source_asset": "shot-01.mp4", "camera_move": "push_in",
             "duration_seconds": 2.0, "timeline_start_seconds": 0.0, "timeline_end_seconds": 2.0,
             "transition_in_seconds": 0.0, "transition_out_seconds": 0.45},
            {"id": "shot-02", "source_asset": "../private.mp4", "camera_move": "shake",
             "timeline_start_seconds": -1.0, "timeline_end_seconds": 3.0},
        ]}).encode("utf-8")
        qa_report = {
            "schema_version": 1, "status": "passed", "path": "video.mp4",
            "artifact_sha256": hashlib.sha256(video).hexdigest(),
            "warnings": [
                {"code": "sustained_black", "message": "black frames", "start_seconds": 2.5,
                 "duration_seconds": 1.25, "review_threshold_seconds": 0.5,
                 "private_detail": "must-not-leak"},
                {"code": "audio_loudness_low", "message": "Integrated loudness is below the review target",
                 "unit": "LUFS", "value": -24.3},
            ],
            "storyboard_mapping": [
                {"code": "sustained_black", "start_seconds": 2.5, "duration_seconds": 1.25,
                 "mapping_status": "mapped", "storyboard_matches": [
                     {"scene_id": "shot-02", "source_file": "shot-02.mp4",
                      "scene_start_seconds": 2.0, "scene_end_seconds": 4.0,
                      "overlap_start_seconds": 2.5, "overlap_end_seconds": 3.75,
                      "overlap_seconds": 1.25, "private_detail": "must-not-leak"},
                     {"scene_id": "shot-03", "source_file": "C:/private/shot-03.mp4",
                      "scene_start_seconds": 3.5, "scene_end_seconds": 5.0,
                      "overlap_start_seconds": 3.5, "overlap_end_seconds": 3.75,
                      "overlap_seconds": 0.25},
                 ]},
                {"code": "audio_loudness_low", "mapping_status": "not_time_based", "storyboard_matches": []},
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
                "report": "qa-report.json", "review_warning_count": 2,
                "review_warning_codes": ["sustained_black", "audio_loudness_low"], "warning_review_required": True,
            },
        }
        (product_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        rows = build_products(products_root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quality_gate"]["review_warning_count"], 2)
        self.assertTrue(rows[0]["quality_gate"]["warning_review_required"])
        self.assertEqual(rows[0]["quality_warnings"][0]["start_seconds"], 2.5)
        self.assertEqual(rows[0]["motion"]["renderer"], "remotion")
        self.assertEqual(rows[0]["motion"]["audio_mode"], "music")
        self.assertEqual(rows[0]["scene_timeline"], [{
            "scene_id": "shot-01", "source_asset": "shot-01.mp4", "camera_move": "push_in",
            "timeline_start_seconds": 0.0, "timeline_end_seconds": 2.0, "duration_seconds": 2.0,
            "transition_in_seconds": 0.0, "transition_out_seconds": 0.45,
        }])
        self.assertAlmostEqual(rows[0]["video_metrics"]["duration_seconds"], 4.416667)
        self.assertEqual(rows[0]["video_metrics"]["frames"], 53)
        self.assertNotIn("private_detail", json.dumps(rows))
        self.assertEqual(rows[0]["quality_warnings"][0]["duration_seconds"], 1.25)
        mapped_warning = rows[0]["quality_warnings"][0]
        self.assertEqual(mapped_warning["mapping_status"], "mapped")
        self.assertEqual(mapped_warning["storyboard_matches"][0]["scene_id"], "shot-02")
        self.assertEqual(mapped_warning["storyboard_matches"][0]["source_file"], "shot-02.mp4")
        self.assertEqual(mapped_warning["storyboard_matches"][0]["overlap_start_seconds"], 2.5)
        self.assertEqual(len(mapped_warning["storyboard_matches"]), 1)
        self.assertEqual(len(rows[0]["quality_warnings"]), 2)
        metric_warning = rows[0]["quality_warnings"][1]
        self.assertEqual(metric_warning["code"], "audio_loudness_low")
        self.assertEqual(metric_warning["mapping_status"], "not_time_based")
        self.assertEqual(metric_warning["storyboard_matches"], [])
        self.assertNotIn("start_seconds", metric_warning)


    def test_build_products_only_exposes_subtitle_review_for_exact_run_product(self) -> None:
        workspace = Path(self.temp.name)
        products_root = workspace / "data" / "products"
        initiative_id, version_id = "a" * 32, "b" * 20
        product_dir = products_root / initiative_id / version_id
        product_dir.mkdir(parents=True)
        files = {}
        for name, body in (
            ("README.md", b"fixture"), ("storyboard.json", b"{}"),
            ("video.mp4", b"fixture-video"), ("product.zip", b"fixture-zip"),
            ("qa-report.json", b'{"warnings":[]}'),
        ):
            path = product_dir / name
            path.write_bytes(body)
            files[name] = (path, body if name != "video.mp4" and name != "product.zip" else None)
        srt = b"1\n00:00:00,000 --> 00:00:01,000\nhello\n"
        review_dir = workspace / "data" / "prometheus" / "runs" / "pilot" / "run-1"
        review_dir.mkdir(parents=True)
        (review_dir / "narration-subtitles.srt").write_bytes(srt)
        review = {
            "schema_version": 1, "review_status": "pending_human_review",
            "human_approved": False, "subtitle_cue_count": 1,
            "subtitle_file": "narration-subtitles.srt", "reference_script": "hello",
            "raw_asr_transcript": "hello",
            "asr": {"audio_sha256": "c" * 64, "model_repo": "local/cantonese", "model_revision": "d" * 40},
            "segments": [{"index": 1, "start_seconds": 0.0, "end_seconds": 1.0, "asr_text": "hello"}],
        }
        (review_dir / "subtitle-review.json").write_text(json.dumps(review), encoding="utf-8")
        generation = {"product": {
            "initiative_id": initiative_id, "version_id": version_id,
            "relative_path": f"data/products/{initiative_id}/{version_id}",
        }}
        generation_path = review_dir / "generation.json"
        generation_path.write_text(json.dumps(generation), encoding="utf-8")
        manifest = {
            "product_kind": "video_creator", "initiative_id": initiative_id,
            "version_id": version_id, "title": "Subtitle provenance fixture", "status": "testing",
            "subtitles": {"sha256": hashlib.sha256(srt).hexdigest()},
            "narration": {"script": "hello"},
            "narration_asr": {
                "audio_sha256": "c" * 64, "model_repo": "local/cantonese",
                "model_revision": "d" * 40, "transcript": "hello",
            },
        }
        with patch("muse.status_api._verified_product_files", return_value=(product_dir, manifest, files)):
            rows = build_products(products_root)
            self.assertEqual(rows[0]["subtitle_review"]["status"], "pending_human_review")
            generation["product"]["initiative_id"] = "e" * 32
            generation["product"]["relative_path"] = "data/products/" + "e" * 32 + "/" + version_id
            generation_path.write_text(json.dumps(generation), encoding="utf-8")
            rows = build_products(products_root)
        self.assertEqual(rows[0]["subtitle_review"]["status"], "unavailable")
        self.assertFalse(rows[0]["subtitle_review"]["review_available"])

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

    def test_initiative_performance_api_records_and_reads_verified_metrics(self):
        root = Path(self.temp.name)
        initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
        products_dir = root / "data" / "products"
        products_dir.mkdir(parents=True)
        with InitiativeStore(initiatives_db) as initiatives:
            item = initiatives.propose(
                "Performance API target", kind="creative_output", creator_value="measurable output",
                concept="track real results", first_step="publish", expected_output="metrics",
            )
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db, products_dir=products_dir,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_address[1]}/api/v1/initiatives/{item['id']}/performance"
        body = {
            "idempotency_key": "manual-youtube-20261117-1", "platform": "youtube",
            "source": "official_analytics", "currency": "SGD",
            "published_at": "2026-11-16T12:00:00+08:00",
            "source_reference": "studio-export-001",
            "metrics": {"views": 2400, "three_second_retention_pct": 68.5,
                        "completion_rate_pct": 27.0, "orders": 2,
                        "reported_revenue_minor": 12000, "settled_revenue_minor": 6000,
                        "production_cost_minor": 1800},
            "content_profile": {"topic": "AI 创作", "hook_style": "反常识提问", "voice_language": "粤语（香港）"},
        }
        try:
            request = Request(endpoint, data=json.dumps(body).encode("utf-8"), headers={
                "Authorization": "Bearer " + token, "Origin": "tauri://localhost",
                "Content-Type": "application/json",
            }, method="POST")
            with urlopen(request, timeout=3) as response:
                created = json.loads(response.read())
            self.assertEqual(response.status, 201)
            self.assertEqual(created["snapshot"]["views"], 2400)
            self.assertEqual(created["snapshot"]["content_profile"]["voice_language"], "粤语（香港）")
            replay = Request(endpoint, data=json.dumps(body).encode("utf-8"), headers={
                "Authorization": "Bearer " + token, "Origin": "tauri://localhost",
                "Content-Type": "application/json",
            }, method="POST")
            with urlopen(replay, timeout=3) as response:
                replayed = json.loads(response.read())
            self.assertEqual(replayed["snapshot"]["id"], created["snapshot"]["id"])
            read = Request(endpoint, headers={"Authorization": "Bearer " + token})
            with urlopen(read, timeout=3) as response:
                result = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(result["snapshots"][0]["views"], 2400)
            self.assertEqual(result["snapshots"][0]["content_profile"]["topic"], "AI 创作")
            self.assertEqual(result["summary_by_currency"][0]["settled_revenue_minor"], 6000)
            self.assertEqual(result["summary_by_currency"][0]["currency"], "SGD")
            self.assertEqual(result["analysis"]["status"], "ready")
            self.assertEqual(result["analysis"]["cohorts"][0]["sample_count"], 1)
            self.assertEqual(result["analysis"]["cohorts"][0]["status"], "insufficient_sample")
            self.assertEqual(result["analysis"]["cohorts"][0]["item_comparisons"]["three_second_retention_pct"]["status"], "insufficient_item_samples")
            invalid_body = dict(body, metrics={"completion_rate_pct": 150})
            invalid = Request(endpoint, data=json.dumps(invalid_body).encode("utf-8"), headers={
                "Authorization": "Bearer " + token, "Origin": "tauri://localhost",
                "Content-Type": "application/json",
            }, method="POST")
            with self.assertRaises(HTTPError) as error:
                urlopen(invalid, timeout=3)
            self.assertEqual(error.exception.code, 400)
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
        recipe_sha256 = "f" * 64
        try:
            with patch("muse.status_api.VideoProductBuilder.build_from_prompt", side_effect=RuntimeError("private GPU failure detail")) as build_prompt:
                request = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/v1/video-products",
                    data=json.dumps({
                        "mode": "prompt_to_video", "title": "故障注入视频",
                        "prompt": "A short cinematic test scene",
                        "creator_initiative_id": item["id"], "evolution_cycle_id": cycle,
                        "motion_profile": "brand_intro_outro",
                        "recipe_sha256": recipe_sha256,
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
            self.assertEqual(build_prompt.call_args.kwargs["recipe_sha256"], recipe_sha256)
            self.assertEqual(status, "failed")
            with InitiativeStore(initiatives_db) as initiatives:
                failures = initiatives.list_failures(initiative_id=item["id"])
                attempt = initiatives.get_video_attempt(job_id)
            self.assertEqual(attempt["recipe_sha256"], recipe_sha256)
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
        attempt_id = "f" * 32
        with InitiativeStore(initiatives_db) as attempts:
            attempts.start_video_attempt(attempt_id, mode="prompt_to_video")
            attempts.mark_video_attempt_running(attempt_id)
            for index in range(205):
                attempts.start_video_attempt(f"{index:032x}", mode="local_assets")
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
            self.assertEqual(len(payload["attempts"]), 200)
            self.assertTrue(payload["attempts_truncated"])
            running_attempt = next(item for item in payload["attempts"] if item["attempt_id"] == attempt_id)
            self.assertEqual(running_attempt["worker_id"], "status-api")
            self.assertGreaterEqual(running_attempt["attempt_count"], 1)
            self.assertIsNotNone(running_attempt["lease_expires_at"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_pipeline_submit_api_enqueues_commander_ticket_and_marks_cantonese_unavailable(self):
        root = Path(self.temp.name)
        script = root / "scripts" / "muse-high-value-short.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("print(\\\"{}\\\")", encoding="utf-8")
        server, token = create_status_server(
            task_db=self.task_db, progress_db=self.progress_db,
            capability_db=self.capability_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        try:
            request = Request(base + "/api/v1/pipelines/submit",
                data=json.dumps({"recipe_id": "high_value_short_v2", "title": "API pipeline test"}).encode(),
                headers=headers, method="POST")
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 202)
                job = json.loads(response.read())["job"]
            with TaskQueue(self.task_db) as queue:
                ticket = queue.get(job["job_id"])
            self.assertEqual(ticket["payload"]["kind"], "video_pipeline")
            self.assertEqual(ticket["payload"]["title"], "API pipeline test")
            self.assertEqual(job["output_dir"], ticket["payload"]["output_dir"])
            self.assertTrue(job["output_dir"].startswith("data/prometheus/runs/high_value_short_v2/"))
            recipes_request = Request(base + "/api/v1/pipelines/recipes", headers=headers)
            with urlopen(recipes_request, timeout=3) as response:
                recipes = json.loads(response.read())["recipes"]
            cantonese = next(item for item in recipes if item["id"] == "cantonese_pilot_v2")
            self.assertFalse(cantonese["available"])
            rejected = Request(base + "/api/v1/pipelines/submit",
                data=json.dumps({"recipe_id": "cantonese_pilot_v2"}).encode(),
                headers=headers, method="POST")
            with self.assertRaises(HTTPError) as error:
                urlopen(rejected, timeout=3)
            self.assertEqual(error.exception.code, 409)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

if __name__ == "__main__":
    unittest.main()
